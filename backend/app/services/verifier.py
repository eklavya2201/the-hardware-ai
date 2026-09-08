"""Rule-based verifier — PRD §7.4.

Each rule is a pure function (Project) -> list[VerifierIssue]. Rules are stable
across runs (deterministic ids) so the frontend can reconcile UI state.

10 rules implemented for M1:
  R01_decoupling_cap         each VCC pin needs a 100nF nearby
  R02_i2c_pullups            SDA/SCL nets need pull-up resistors
  R03_no_floating_en         EN/CE/RESET pins must not float
  R04_voltage_rail_compat    components rated for the rail they sit on
  R05_crystal_load_caps      crystals need two load caps to GND
  R06_usb_dp_dn_routing      D+/D- of USB connectors must connect to MCU
  R07_required_pins          critical power/ground pins must be connected
  R08_gnd_net_present        a GND net must exist and reach every component
  R09_unique_refdes          refdes uniqueness within the project
  R10_polarized_caps         large caps (≥10µF) tagged for polarity review
"""

from __future__ import annotations

import re

from app.models import Component, LibraryPart, Pin, PinType, Project, VerifierIssue
from app.services import erc_matrix, ipc, library, pin_safety


# ---- helpers ---------------------------------------------------------------


def _is_power_net(name: str) -> bool:
    upper = name.upper()
    return upper in {"VCC", "VDD"} or any(
        upper.startswith(p) for p in ("VCC", "VDD", "+3V", "+5V", "3V3", "5V", "VBUS")
    )


def _is_gnd_net(name: str) -> bool:
    return name.upper() in {"GND", "VSS", "AGND", "DGND", "0V"}


# Pins fed by the chip's OWN on-chip regulator (STM32/PIC32 VCAP, SAMD
# VDDCORE, PIC24/dsPIC VCORE, CC253x DCOUPL): externally they take exactly one
# thing — their datasheet capacitor to ground. They are not a supply input
# (calling their net "unsourced" is a false alarm) and never a rail to feed
# other parts or tie to a supply. The 2026-08-28 framework audit caught the
# agent tying an STM32 VCAP to 3.3 V to silence exactly that false alarm — a
# chip-damaging "fix". REGOUT (ICM-20948, BQ76920) is deliberately NOT listed:
# those are real LDO outputs their datasheets allow to feed external circuits.
_INTERNAL_RAIL_RE = re.compile(r"^(VCAP\w*|VDDCORE|VCORE|DCOUPL)$", re.IGNORECASE)


def _is_internal_rail_pin(part: LibraryPart | None, pin_number: str) -> bool:
    if part is None:
        return False
    pin = next((p for p in part.pins if p.number == pin_number), None)
    return bool(pin and _INTERNAL_RAIL_RE.match(pin.name.strip()))


def _net_for_pin(project: Project, refdes: str, pin: str) -> str | None:
    for net in project.ir.nets:
        for r, p in net.pins:
            if r == refdes and (p == pin):
                return net.name
    return None


# ---- rules -----------------------------------------------------------------


def R01_decoupling_cap(project: Project) -> list[VerifierIssue]:
    """Every IC power pin should have a 100nF cap on the same net to GND."""
    issues: list[VerifierIssue] = []
    cap_mpns = {
        "CC0603KRX7R9BB104",  # 100nF
        "CL10A105KB8NNNC",    # 1µF
    }
    cap_refdeses = {
        c.refdes for c in project.ir.components if c.mpn in cap_mpns
    }
    for comp in project.ir.components:
        part = library.get_part(comp.mpn)
        if not part or part.category not in {"mcu", "sensor", "regulator", "charger"}:
            continue
        for pin in part.pins:
            if pin.type != "power_in":
                continue
            net = _net_for_pin(project, comp.refdes, pin.number)
            if not net:
                continue
            net_caps = [
                r
                for net_o in project.ir.nets
                if net_o.name == net
                for r, _ in net_o.pins
                if r in cap_refdeses
            ]
            if not net_caps:
                issues.append(
                    VerifierIssue(
                        id=f"R01:{comp.refdes}:{pin.number}",
                        severity="warning",
                        message=f"Missing decoupling cap on {comp.refdes} pin {pin.number} ({pin.name})",
                        refdes=comp.refdes,
                        pinNumber=pin.number,
                        rule="R01_decoupling_cap",
                    )
                )
    return issues


def R02_i2c_pullups(project: Project) -> list[VerifierIssue]:
    """SDA / SCL nets must have at least one pull-up resistor present in the design."""
    issues: list[VerifierIssue] = []
    # A pull-up is ANY 2-pin resistor bridging the I2C net to a rail — not a
    # hard-coded pair of MPNs. The old allowlist ({RC0603FR-074K7L,
    # RC0603FR-0710KL}) reported "no pull-up" on every design that used a
    # different value or package (2026-08-20 review round).
    resistors = {
        c.refdes for c in project.ir.components if _is_resistor(library.get_part(c.mpn))
    }
    for net in project.ir.nets:
        upper = net.name.upper()
        is_i2c = "SDA" in upper or "SCL" in upper or "I2C" in upper
        if not is_i2c:
            continue
        net_pullups = [
            r for r, _ in net.pins
            if r in resistors and _bridges_to_rail(project, r, net.name)
        ]
        if not net_pullups:
            issues.append(
                VerifierIssue(
                    id=f"R02:{net.name}",
                    severity="warning",
                    message=f"I2C net {net.name} has no pull-up resistor",
                    net=net.name,
                    rule="R02_i2c_pullups",
                )
            )
    return issues


def _bridges_to_rail(project: Project, refdes: str, from_net: str) -> bool:
    """True when this 2-pin part has one pin on `from_net` and the other on a
    power rail — i.e. it is wired as a pull-up, whatever its value."""
    others: list[str] = []
    for net in project.ir.nets:
        for r, _ in net.pins:
            if r == refdes and net.name != from_net:
                others.append(net.name)
    return any(_is_power_net(n) and not _is_gnd_net(n) for n in others)


def R03_no_floating_en(project: Project) -> list[VerifierIssue]:
    """EN, CE, RESET, BOOT pins must connect to something (pull-up, GPIO, etc.)."""
    issues: list[VerifierIssue] = []
    keywords = ("EN", "CE", "RESET", "RST", "BOOT")
    for comp in project.ir.components:
        part = library.get_part(comp.mpn)
        if not part:
            continue
        for pin in part.pins:
            if not any(k == pin.name.upper() or pin.name.upper().startswith(k + "_") for k in keywords):
                continue
            if _net_for_pin(project, comp.refdes, pin.number) is not None:
                continue
            # A floating ENABLE on a regulator/charger is not a nit: the part may
            # never turn on, so the whole rail below it is dead. Review #14's LDO
            # shipped with EN unwired and the design still read as clean.
            # A floating EN is an error wherever the part cannot RUN without
            # it: a regulator/charger (its whole output rail stays dead) and an
            # MCU module — an ESP32's EN held low is a chip permanently in
            # reset, so the board never boots (2026-08-20 review round: the
            # drone carrier shipped that way as a mere warning).
            enable_names = {"EN", "CE", "ENABLE", "SHDN", "nSHDN"}
            supply_en = (
                part.category in {"regulator", "charger", "mcu"}
                and pin.name.upper() in enable_names
            )
            issues.append(
                VerifierIssue(
                    id=f"R03:{comp.refdes}:{pin.number}",
                    severity="error" if supply_en else "warning",
                    message=(
                        (
                            f"{comp.refdes} ({comp.mpn}) enable pin {pin.name} is floating — "
                            f"the module is held in reset and will never boot. Pull it up to "
                            f"its supply rail through 10 kOhm (RC0603FR-0710KL) with a 100 nF "
                            f"cap to ground, or drive it from a GPIO."
                            if part.category == "mcu" else
                            f"{comp.refdes} ({comp.mpn}) enable pin {pin.name} is floating — "
                            f"the regulator may never turn on and everything on its output "
                            f"rail stays dead. Pull it up to its input rail through 10 kOhm "
                            f"(RC0603FR-0710KL), or drive it from a GPIO."
                        )
                        if supply_en else
                        f"Floating input on {comp.refdes} pin {pin.name}"
                    ),
                    refdes=comp.refdes,
                    pinNumber=pin.number,
                    rule="R03_no_floating_en",
                )
            )
    return issues


def R04_voltage_rail_compat(project: Project) -> list[VerifierIssue]:
    """A part's supply pin on a rail above its rated maximum. Uses the same
    working-voltage model as R31/R32 (name hint, declared input voltage, part
    ratings) — the old six-name table never looked at VBAT/VIN, so an AP2112
    (6 V max) on a 12 V barrel-jack rail passed (review #23). Only supply
    (power_in) pins are judged — a signal pin on a rail is R24/R36's business —
    plus rated passives (a 6.3 V cap on a 12 V rail)."""
    issues: list[VerifierIssue] = []
    comps = {c.refdes: c for c in project.ir.components}
    for net in project.ir.nets:
        if _classify_net(net.name) != "power":
            continue
        rail_v = _net_working_voltage(project, net.name, comps)
        if rail_v is None:
            continue
        for refdes, pin_no in net.pins:
            comp = comps.get(refdes)
            if not comp:
                continue
            part = library.get_part(comp.mpn)
            if not part or part.voltageMax is None:
                continue
            ptype = _pin_type(comp, pin_no)
            if ptype != "power_in" and part.category not in {"passive", "protection"}:
                continue
            if any(i.id == f"R04:{refdes}:{net.name}" for i in issues):
                continue  # one finding per part per rail, not one per supply pin
            # UNDER-voltage: a supply pin on a rail below the part's minimum
            # (A4988 module VMOT — min 8 V — on the 5 V logic rail; the sweep's
            # stepper-0). Regulators are R58's (their voltageMin is min INPUT).
            if (part.voltageMin is not None and part.voltageMin > 0
                    and ptype == "power_in" and part.category != "regulator"
                    and rail_v < part.voltageMin - 0.05):
                issues.append(
                    VerifierIssue(
                        id=f"R04:{refdes}:{net.name}",
                        severity="error",
                        message=(
                            f"{refdes} ({comp.mpn}) needs at least {part.voltageMin:g} V on this "
                            f"supply pin but rail {net.name} is ~{rail_v:g} V — it will not run. "
                            f"Move the pin to the right rail (a motor VMOT to the battery/VIN "
                            f"rail, not the logic rail)."
                        ),
                        refdes=refdes,
                        net=net.name,
                        rule="R04_voltage_rail_compat",
                    )
                )
                continue
            if rail_v > part.voltageMax + 0.05:
                issues.append(
                    VerifierIssue(
                        id=f"R04:{refdes}:{net.name}",
                        severity="error",
                        message=(
                            f"{refdes} ({comp.mpn}) max {part.voltageMax}V "
                            f"is below rail {net.name}={rail_v}V"
                        ),
                        refdes=refdes,
                        net=net.name,
                        rule="R04_voltage_rail_compat",
                    )
                )
    return issues


def R05_crystal_load_caps(project: Project) -> list[VerifierIssue]:
    """A crystal needs two ~22pF caps tied to GND on its two pins."""
    issues: list[VerifierIssue] = []
    crystal_components = [
        c
        for c in project.ir.components
        if (p := library.get_part(c.mpn)) and p.category == "crystal"
    ]
    cap_refdeses = {
        c.refdes
        for c in project.ir.components
        if c.mpn in {"CL10C220JB8NNNC", "CC0603KRX7R9BB104"}
    }
    for comp in crystal_components:
        nets = [n for n in project.ir.nets if any(r == comp.refdes for r, _ in n.pins)]
        nets_with_cap = [
            n for n in nets if any(r in cap_refdeses for r, _ in n.pins)
        ]
        if len(nets_with_cap) < 2:
            issues.append(
                VerifierIssue(
                    id=f"R05:{comp.refdes}",
                    severity="warning",
                    message=f"Crystal {comp.refdes} appears to be missing load capacitors",
                    refdes=comp.refdes,
                    rule="R05_crystal_load_caps",
                )
            )
    return issues


def R06_usb_dp_dn_routing(project: Project) -> list[VerifierIssue]:
    """USB connectors' D+/D- pins must be connected somewhere."""
    issues: list[VerifierIssue] = []
    for comp in project.ir.components:
        part = library.get_part(comp.mpn)
        if not part:
            continue
        if "USB" not in (part.symbol + part.footprint).upper():
            continue
        for pin in part.pins:
            if pin.name.upper() not in {"DP", "DN", "D+", "D-"}:
                continue
            if _net_for_pin(project, comp.refdes, pin.number) is None:
                issues.append(
                    VerifierIssue(
                        id=f"R06:{comp.refdes}:{pin.number}",
                        severity="warning",
                        message=f"Unconnected {pin.name} on {comp.refdes}",
                        refdes=comp.refdes,
                        pinNumber=pin.number,
                        rule="R06_usb_dp_dn_routing",
                    )
                )
    return issues


def R07_required_pins(project: Project) -> list[VerifierIssue]:
    """Required power_in / ground pins on critical parts must be wired."""
    issues: list[VerifierIssue] = []
    critical = {"mcu", "sensor", "regulator", "charger"}
    for comp in project.ir.components:
        part = library.get_part(comp.mpn)
        if not part or part.category not in critical:
            continue
        for pin in part.pins:
            if pin.type not in {"power_in", "ground"}:
                continue
            if _net_for_pin(project, comp.refdes, pin.number) is None:
                issues.append(
                    VerifierIssue(
                        id=f"R07:{comp.refdes}:{pin.number}",
                        severity="error",
                        message=(
                            f"Required {pin.type.replace('_', ' ')} pin "
                            f"{pin.name} unconnected on {comp.refdes}"
                        ),
                        refdes=comp.refdes,
                        pinNumber=pin.number,
                        rule="R07_required_pins",
                    )
                )
    return issues


def R08_gnd_net_present(project: Project) -> list[VerifierIssue]:
    """A GND net must exist when any part is placed."""
    if not project.ir.components:
        return []
    has_gnd = any(_is_gnd_net(n.name) for n in project.ir.nets)
    if has_gnd:
        return []
    return [
        VerifierIssue(
            id="R08:no-gnd",
            severity="error",
            message="No GND net defined in the schematic",
            rule="R08_gnd_net_present",
        )
    ]


def R09_unique_refdes(project: Project) -> list[VerifierIssue]:
    seen: dict[str, int] = {}
    for c in project.ir.components:
        seen[c.refdes] = seen.get(c.refdes, 0) + 1
    return [
        VerifierIssue(
            id=f"R09:{refdes}",
            severity="error",
            message=f"refdes {refdes} appears {n} times",
            refdes=refdes,
            rule="R09_unique_refdes",
        )
        for refdes, n in seen.items()
        if n > 1
    ]


_POLARISED_TOKENS = ("electrolytic", "tantalum", "polymer", "polarized", "polarised")


def _is_polarised_cap(part) -> bool:
    """Only a cap that HAS a polarity: electrolytic, tantalum, polymer. The
    library records this — a ceramic X5R/X7R is not polarised, and asking the
    user to "verify polarity if electrolytic" on a part the tool itself chose
    from its own library is noise that trains people to ignore findings
    (2026-08-20 review, finding 8: three of seven findings were this)."""
    if part is None or len(part.pins) != 2:
        return False
    blob = f"{part.description or ''} {part.symbol or ''} {part.footprint or ''}".lower()
    if "capacitor" not in blob and "cap" not in (part.symbol or "").lower():
        return False
    if "ceramic" in blob or "mlcc" in blob:
        return False
    return any(tok in blob for tok in _POLARISED_TOKENS) or "cp_" in blob


def R10_polarized_caps(project: Project) -> list[VerifierIssue]:
    """A POLARISED capacitor must sit the right way round: its positive pin on
    the more positive net, never on ground.

    Judges orientation instead of asking the user to check — and never fires on
    a ceramic, whose non-polarity the library already records."""
    issues: list[VerifierIssue] = []
    for comp in project.ir.components:
        part = library.get_part(comp.mpn)
        if not _is_polarised_cap(part):
            continue
        pos = next((p for p in part.pins if p.name.strip().upper() in ("+", "A", "P", "POS", "ANODE")), None)
        neg = next((p for p in part.pins if p.name.strip().upper() in ("-", "K", "N", "NEG", "CATHODE")), None)
        if pos is None or neg is None:
            continue
        pos_net = _net_for_pin(project, comp.refdes, pos.number)
        neg_net = _net_for_pin(project, comp.refdes, neg.number)
        if pos_net is None or neg_net is None:
            continue  # R15/R47 own the unwired case
        if _is_gnd_net(pos_net) and not _is_gnd_net(neg_net):
            issues.append(VerifierIssue(
                id=f"R10:{comp.refdes}",
                severity="error",
                message=(
                    f"{comp.refdes} ({comp.mpn}) is a polarised capacitor wired BACKWARDS: "
                    f"its + pin is on {pos_net} (ground) and its - pin on {neg_net}. Reversed "
                    f"electrolytics vent or explode — swap the two connections."),
                refdes=comp.refdes,
                rule="R10_polarized_caps",
            ))
    return issues


def R11_rail_conflict(project: Project) -> list[VerifierIssue]:
    """Catches catastrophic shorts — the same physical pin appearing on two
    different power rails (e.g. VIN of a regulator wired to both VBUS *and*
    VBAT, which is a hard short between USB and battery).

    The connect tool now refuses these at write-time, but this rule still runs
    so existing IRs from before the guard get flagged on next DRC."""
    issues: list[VerifierIssue] = []
    pin_to_nets: dict[tuple[str, str], list[str]] = {}
    for net in project.ir.nets:
        for r, p in net.pins:
            pin_to_nets.setdefault((r, p), []).append(net.name)

    for (refdes, pin_number), net_names in pin_to_nets.items():
        if len(net_names) < 2:
            continue
        roles = [_classify_net(n) for n in net_names]
        # Conflict cases: power vs power (different rails), power vs gnd, gnd vs power.
        power_nets = [n for n, r in zip(net_names, roles) if r == "power"]
        gnd_nets = [n for n, r in zip(net_names, roles) if r == "gnd"]
        if len(power_nets) >= 2:
            issues.append(
                VerifierIssue(
                    id=f"R11:{refdes}:{pin_number}",
                    severity="error",
                    message=(
                        f"Pin {refdes}.{pin_number} is on multiple power nets: "
                        f"{', '.join(power_nets)}. This is a short between rails."
                    ),
                    refdes=refdes,
                    pinNumber=pin_number,
                    rule="R11_rail_conflict",
                )
            )
        elif power_nets and gnd_nets:
            issues.append(
                VerifierIssue(
                    id=f"R11:{refdes}:{pin_number}",
                    severity="error",
                    message=(
                        f"Pin {refdes}.{pin_number} is on both a power net "
                        f"({power_nets[0]}) and a ground net ({gnd_nets[0]}) — "
                        f"this is a direct short."
                    ),
                    refdes=refdes,
                    pinNumber=pin_number,
                    rule="R11_rail_conflict",
                )
            )
    return issues


# VM/VMOT (motor-driver supply), VIN/VSYS/VRAW (board input) are rails too:
# a real motor rail named VM used to classify as a signal, so R19 reported
# "VM is not on a power rail" on a correctly-wired rover (2026-08-16).
_POWER_PREFIXES = ("VCC", "VDD", "VBUS", "VBAT", "+3V", "+5V", "3V3", "5V",
                   "VM", "VMOT", "VIN", "VSYS", "VRAW", "VLOGIC")
_GND_NETS_LOOKUP = {"GND", "VSS", "AGND", "DGND", "0V"}


def _classify_net(name: str) -> str:
    upper = name.upper()
    if upper in _GND_NETS_LOOKUP:
        return "gnd"
    if any(upper.startswith(p) for p in _POWER_PREFIXES):
        return "power"
    return "signal"


def R12_orphan_net(project: Project) -> list[VerifierIssue]:
    """A net with fewer than 2 pins is electrically meaningless — it's a label
    sitting on a single pad. Caught the t3 smartwatch failure where the agent
    named USB_DP_MCU / EN_U2 / VOUT_U2 / MOTOR_DRIVE but never wired the second
    side. Power/GND stubs are exempted (one-pin power-rail labels can be
    legitimate test points)."""
    issues: list[VerifierIssue] = []
    for net in project.ir.nets:
        if len(net.pins) >= 2:
            continue
        # Power/GND nets with 0-1 pins are usually intentional stubs (rare,
        # but plausible for test-point labels) - EXCEPT when the lone pin is one
        # end of a 2-pin part: a diode whose anode sits alone on "VBAT_IN"
        # protects nothing (review #19: the reverse-polarity diode's input net
        # held only the diode; the battery connector was wired after it).
        role = _classify_net(net.name)
        if role in {"power", "gnd"}:
            if len(net.pins) == 1:
                r, p = net.pins[0]
                comp = next((c for c in project.ir.components if c.refdes == r), None)
                if comp is not None and len(comp.pins) == 2:
                    issues.append(
                        VerifierIssue(
                            id=f"R12:{net.name}",
                            severity="error",
                            message=(
                                f"Net {net.name!r} has only one pin ({r}.{p}) and it is "
                                f"one end of a 2-pin part - {r} is effectively dangling "
                                f"(nothing feeds or drains {net.name}). Wire the source "
                                f"(connector / regulator output) onto {net.name}."
                            ),
                            net=net.name,
                            refdes=r,
                            pinNumber=p,
                            rule="R12_orphan_net",
                        )
                    )
            continue

        if not net.pins:
            issues.append(
                VerifierIssue(
                    id=f"R12:{net.name}",
                    severity="warning",
                    message=f"Net {net.name!r} has no pins. Was a component deleted?",
                    net=net.name,
                    rule="R12_orphan_net",
                )
            )
        else:
            r, p = net.pins[0]
            issues.append(
                VerifierIssue(
                    id=f"R12:{net.name}",
                    severity="error",
                    message=(
                        f"Net {net.name!r} has only one pin ({r}.{p}). "
                        f"Signal nets need at least two pins to carry a signal — "
                        f"the other side wasn't wired."
                    ),
                    net=net.name,
                    refdes=r,
                    pinNumber=p,
                    rule="R12_orphan_net",
                )
            )
    return issues


def R13_signal_terminates_at_mcu(project: Project) -> list[VerifierIssue]:
    """Every signal pin (input/output/bidirectional) on a peripheral IC that
    sits on a net should also have an MCU pin on that net. Otherwise the
    signal is wired to a label but never reaches a microcontroller GPIO.

    Caught the t3 smartwatch mistake: GC9A01 SPI nets and motor drive net
    existed, but no ESP32-S3 GPIO was assigned to any of them. The firmware
    generator correctly refused to invent pins, producing unusable code."""
    issues: list[VerifierIssue] = []

    mcu_refdeses = {
        c.refdes
        for c in project.ir.components
        if (p := library.get_part(c.mpn)) and p.category == "mcu"
    }
    if not mcu_refdeses:
        return issues  # No MCU — nothing to terminate at.

    # Index nets by name for fast lookup.
    net_by_name = {n.name: n for n in project.ir.nets}

    flagged: set[str] = set()
    for c in project.ir.components:
        part = library.get_part(c.mpn)
        if not part:
            continue
        if part.category not in {
            "sensor", "display", "actuator", "memory",
            "interface", "audio", "logic", "charger",
        }:
            continue
        if c.refdes in mcu_refdeses or c.refdes in flagged:
            continue
        for pin in part.pins:
            # Skip pins that aren't carrying signals (power_in, power_out,
            # ground, passive) — those don't need to terminate at the MCU.
            if pin.type in {"power_in", "power_out", "ground", "passive"}:
                continue
            # A motor driver's OUTPUT pins drive the motor (a connector/motor),
            # not the MCU — R19 owns their completeness. Don't flag them here.
            if part.category == "actuator" and pin.type == "output":
                continue
            net = _net_for_pin(project, c.refdes, pin.number)
            if net is None:
                continue
            # Skip if the net itself is a power/ground rail.
            if _classify_net(net) in {"power", "gnd"}:
                continue
            net_obj = net_by_name.get(net)
            if net_obj is None:
                continue
            mcu_present = any(r in mcu_refdeses for r, _ in net_obj.pins)
            if mcu_present:
                continue
            issues.append(
                VerifierIssue(
                    id=f"R13:{c.refdes}:{pin.number}",
                    severity="warning",
                    message=(
                        f"{c.refdes}.{pin.name} (signal) is on net {net!r} but "
                        f"no MCU pin is on that net. Signal won't reach the firmware."
                    ),
                    refdes=c.refdes,
                    pinNumber=pin.number,
                    net=net,
                    rule="R13_signal_terminates_at_mcu",
                )
            )
            flagged.add(c.refdes)
            break  # one issue per peripheral — avoids spam
    return issues


# Approximate output-voltage map for known regulators. Updated alongside
# parts.json when new MPNs are added.
_REGULATOR_VOLTAGES: dict[str, float] = {
    "AP2112K-3.3TRG1": 3.3,
    "AMS1117-3.3": 3.3, "AMS1117-5.0": 5.0, "AMS1117-1.8": 1.8,
    "MCP1700-3302E/TO": 3.3,
    "RT9013-33": 3.3, "LD1117-33": 3.3, "NCP1117-33": 3.3, "AP7361-33": 3.3,
    "MIC5219-3.3YM5": 3.3, "MIC5219-3.3": 3.3,
    "TPS61023": 3.3, "TPS62840": 3.3,
}


def R14_excess_regulators(project: Project) -> list[VerifierIssue]:
    """Two or more regulators with the same output voltage usually means
    wasted parts. Sometimes deliberate (analog/digital isolation, separate
    LED supply) — flagged as info, not warning."""
    issues: list[VerifierIssue] = []
    by_voltage: dict[float, list[str]] = {}
    for c in project.ir.components:
        v = _REGULATOR_VOLTAGES.get(c.mpn)
        if v is None:
            continue
        by_voltage.setdefault(v, []).append(c.refdes)

    for v, refdeses in by_voltage.items():
        if len(refdeses) < 2:
            continue
        issues.append(
            VerifierIssue(
                id=f"R14:{v}V",
                severity="info",
                message=(
                    f"{len(refdeses)} regulators output ~{v}V "
                    f"({', '.join(sorted(refdeses))}). Usually one is enough — "
                    f"keep multiple only if you need rail isolation."
                ),
                rule="R14_excess_regulators",
            )
        )
    return issues


def R15_floating_component(project: Project) -> list[VerifierIssue]:
    """A component placed but never wired. R12 catches one-pin *nets*, but a
    part that appears in no net at all (e.g. a decoupling cap the agent added
    and forgot to connect) is invisible to every other rule. Flag any component
    with zero connected pins; for a 2-pin passive, a single connected side is
    equally broken (a cap needs both sides)."""
    issues: list[VerifierIssue] = []

    connected: dict[str, set[str]] = {}
    for net in project.ir.nets:
        for r, p in net.pins:
            connected.setdefault(r, set()).add(p)

    for comp in project.ir.components:
        part = library.get_part(comp.mpn)
        total = len(comp.pins) if comp.pins else (len(part.pins) if part else 0)
        if total == 0:
            continue
        conn = len(connected.get(comp.refdes, set()))

        if conn == 0:
            issues.append(
                VerifierIssue(
                    id=f"R15:{comp.refdes}",
                    severity="error",
                    message=(
                        f"{comp.refdes} ({comp.mpn}) is floating — none of its "
                        f"{total} pins are wired to any net."
                    ),
                    refdes=comp.refdes,
                    rule="R15_floating_component",
                )
            )
        elif total == 2 and conn == 1:
            issues.append(
                VerifierIssue(
                    id=f"R15:{comp.refdes}",
                    severity="error",
                    message=(
                        f"{comp.refdes} ({comp.mpn}) has only one of its two pins "
                        f"wired — the other side is floating."
                    ),
                    refdes=comp.refdes,
                    rule="R15_floating_component",
                )
            )
    return issues


# Pins R03 already owns (chip-enable / reset) — R16 must not double-flag them.
_R03_KEYWORDS = ("EN", "CE", "RESET", "RST", "BOOT")


def R16_unconnected_signal_input(project: Project) -> list[VerifierIssue]:
    """Intended-but-absent connections (feedback #2/#3, Phase 2).

    A peripheral that is *placed and partially wired* but has an input/control
    pin sitting on no net at all is an intended connection the builder forgot —
    e.g. a DRV8833 BIN2 input that was never tied to an MCU GPIO. R13 only sees
    pins that are on a net; R15 only sees fully-floating parts. This fills the
    gap: a used peripheral with a dangling control input. Surfaced as a Missing
    Item so it reads as "you still need to wire this", not a risk on what exists.
    """
    issues: list[VerifierIssue] = []

    # Map every connected (refdes, pin_number) so we can spot the absent ones.
    connected: dict[str, set[str]] = {}
    for net in project.ir.nets:
        for r, p in net.pins:
            connected.setdefault(r, set()).add(p)

    peripheral_cats = {
        "sensor", "display", "actuator", "memory",
        "interface", "audio", "logic", "charger",
    }
    for comp in project.ir.components:
        part = library.get_part(comp.mpn)
        if not part or part.category not in peripheral_cats:
            continue
        wired = connected.get(comp.refdes, set())
        # Only "intended" if the part is in use — at least one pin connected.
        # A part with nothing wired is R15's job, not this one.
        if not wired:
            continue
        for pin in part.pins:
            # Inputs/bidirectional only — an unconnected *output* (a status or
            # FAULT pin) is frequently left open on purpose, so flagging it would
            # be noise. A control input with nothing driving it is the real bug.
            if pin.type not in {"input", "bidirectional"}:
                continue
            name_u = pin.name.upper()
            if any(name_u == k or name_u.startswith(k + "_") for k in _R03_KEYWORDS):
                continue  # R03 owns these
            if pin.number in wired:
                continue
            issues.append(
                VerifierIssue(
                    id=f"R16:{comp.refdes}:{pin.number}",
                    severity="warning",
                    message=(
                        f"{comp.refdes}.{pin.name} ({pin.type}) is not connected — "
                        f"this control pin was placed but never wired to a net."
                    ),
                    refdes=comp.refdes,
                    pinNumber=pin.number,
                    rule="R16_unconnected_signal_input",
                )
            )
    return issues


# ---------------------------------------------------------------------------
# Phase 3 — engineering rules & library intelligence
# ---------------------------------------------------------------------------


def _mcu_components(project: Project) -> dict[str, Component]:
    return {
        c.refdes: c
        for c in project.ir.components
        if (p := library.get_part(c.mpn)) and p.category == "mcu"
    }


def _mcu_or_host_components(project: Project) -> dict[str, Component]:
    """MCU components PLUS shield/HAT host-header connectors (7.3). On a
    form-factor board the compute is off-board through the header, so for the
    pin-level electrical rules (R36/R37) and the pin map the header's GPIO
    pins ARE the MCU interface — pin_safety resolves the header to the host's
    family (shield → 5 V AVR, HAT → 3.3 V Pi)."""
    out = dict(_mcu_components(project))
    for c in project.ir.components:
        if c.refdes not in out and pin_safety.is_mcu_host_header(c.mpn):
            out[c.refdes] = c
    return out


# I2C / open-drain bus tokens: on such a net the PULL-UP sets the high level,
# not any push-pull driver, so a 5 V host on a bus pulled up to 3.3 V is safe.
_OPEN_DRAIN_TOKENS = ("SDA", "SCL", "I2C", "1WIRE", "1-WIRE", "OWIRE")


def _net_is_open_drain(net, comps: dict[str, Component]) -> bool:
    up = net.name.upper()
    if any(tok in up for tok in _OPEN_DRAIN_TOKENS):
        return True
    for r, p in net.pins:
        comp = comps.get(r)
        if comp is None:
            continue
        name = next((pin.name for pin in comp.pins if pin.number == p), "")
        if any(tok in name.upper() for tok in ("SDA", "SCL")):
            return True
    return False


def _net_pullup_rail_v(project: Project, net, comps: dict[str, Component]) -> float | None:
    """Lowest rail voltage a resistor pulls this net up to, or None. A passive
    on the net whose OTHER pin sits on a power rail pulls the net to that rail;
    on an open-drain bus that rail sets the bus's high level."""
    volts: list[float] = []
    for r, p in net.pins:
        comp = comps.get(r)
        part = library.get_part(comp.mpn) if comp else None
        if part is None or part.category != "passive":
            continue
        for pin in comp.pins:
            if pin.number == p:
                continue
            other = _net_for_pin(project, r, pin.number)
            if other and _classify_net(other) == "power":
                v = _net_working_voltage(project, other, comps)
                if v is not None:
                    volts.append(v)
    return min(volts) if volts else None


def _pin_name_map(comp: Component) -> dict[str, str]:
    return {pin.number: pin.name for pin in comp.pins}


def R17_mcu_pin_safety(project: Project) -> list[VerifierIssue]:
    """Escalate peripheral signals wired onto risky MCU pins (#16/#5).

    The agent should call ``allocate_pin``; when something is wired to a
    boot-strapping / flash / input-only GPIO anyway (a hand edit or a legacy
    IR), surface it. Boot-critical and flash pins are warnings; strapping and
    input-only are info. Only fires on real signal nets, never on power/ground.
    """
    issues: list[VerifierIssue] = []
    mcus = _mcu_components(project)
    if not mcus:
        return issues
    name_maps = {ref: _pin_name_map(c) for ref, c in mcus.items()}

    seen: set[tuple[str, str]] = set()
    for net in project.ir.nets:
        if _classify_net(net.name) in {"power", "gnd"}:
            continue
        # Only consider nets that actually carry a peripheral signal.
        has_peripheral = any(r not in mcus for r, _ in net.pins)
        if not has_peripheral:
            continue
        for r, p in net.pins:
            if r not in mcus or (r, p) in seen:
                continue
            pin_name = name_maps[r].get(p, p)
            verdict = pin_safety.risk(mcus[r].mpn, pin_name)
            if not verdict:
                continue
            seen.add((r, p))
            severity = "warning" if verdict["level"] == "high" else "info"
            issues.append(
                VerifierIssue(
                    id=f"R17:{r}:{p}",
                    severity=severity,  # type: ignore[arg-type]
                    message=(
                        f"{r}.{pin_name} on net {net.name!r}: {verdict['note']} "
                        f"Prefer a pin from allocate_pin."
                    ),
                    refdes=r,
                    pinNumber=p,
                    net=net.name,
                    rule="R17_mcu_pin_safety",
                )
            )
    return issues


def _is_level_shifter(part: LibraryPart | None) -> bool:
    if part is None:
        return False
    d = part.description.lower()
    if ("level" in d and ("shift" in d or "translat" in d)) or "translator" in d:
        return True
    return part.mpn.upper() in {
        "PCA9306", "TXB0104", "TXS0108EPWR", "SN74LVC8T245", "BSS138", "NC7WZ04",
    }


def _mcu_logic_voltage(part: LibraryPart | None) -> float | None:
    """The I/O logic level of an MCU. Explicit logicLevelV wins; otherwise infer
    from the rated max (≤4 V parts are 3.3 V logic, ≥4.5 V parts are 5 V)."""
    if part is None:
        return None
    if part.logicLevelV is not None:
        return part.logicLevelV
    if part.voltageMax is None:
        return 3.3
    if part.voltageMax <= 4.0:
        return 3.3
    return 5.0


def R18_voltage_domain(project: Project) -> list[VerifierIssue]:
    """A 5 V sensor output driving a 3.3 V MCU input with no level shifter (#4).

    Fires when a part that runs on a ≥4.5 V supply and whose outputs track that
    supply (e.g. HC-SR04 ECHO) shares a net with a lower-logic-level MCU input
    and no level-shifter part sits on that net. Over-voltage on a 3.3 V GPIO."""
    issues: list[VerifierIssue] = []
    comps = {c.refdes: c for c in project.ir.components}
    shifters = {
        c.refdes
        for c in project.ir.components
        if _is_level_shifter(library.get_part(c.mpn))
    }
    net_by_name = {n.name: n for n in project.ir.nets}
    # Host headers count as the MCU (7.3): on a HAT/shield the victim GPIO is
    # a header pin, resolved to the off-board host's family by pin_safety.
    mcus = _mcu_or_host_components(project)
    name_maps = {ref: _pin_name_map(c) for ref, c in mcus.items()}

    for comp in project.ir.components:
        part = library.get_part(comp.mpn)
        if not part or not part.outputTracksSupply:
            continue
        supply = part.recommendedSupplyV or part.voltageMax
        if not supply or supply < 4.5:
            continue
        for pin in part.pins:
            if pin.type not in {"output", "bidirectional"}:
                continue
            net_name = _net_for_pin(project, comp.refdes, pin.number)
            if not net_name or net_name not in net_by_name:
                continue
            net = net_by_name[net_name]
            if any(r in shifters for r, _ in net.pins):
                continue  # a translator is present — fine
            for r, p in net.pins:
                if r == comp.refdes or r not in mcus:
                    continue
                other = library.get_part(comps[r].mpn) if r in comps else None
                if other and other.category == "mcu":
                    logic = _mcu_logic_voltage(other)
                else:
                    # Host header: only GPIO pins are victims (a 5 V output
                    # tied to the header's own 5V/GND pins is R21's problem),
                    # and only when the host family is NOT 5 V-tolerant.
                    pin_name = name_maps[r].get(p, p)
                    if pin_safety.gpio_number(pin_name, mcus[r].mpn) is None:
                        continue
                    tolerant = pin_safety.five_v_tolerant(mcus[r].mpn)
                    if tolerant is None or tolerant:
                        continue
                    logic = pin_safety.logic_voltage(mcus[r].mpn) or 3.3
                if logic is None or supply - logic <= 0.6:
                    continue
                issues.append(
                    VerifierIssue(
                        id=f"R18:{comp.refdes}:{pin.number}:{r}",
                        severity="error",
                        message=(
                            f"{comp.refdes}.{pin.name} drives ~{supply:g} V logic into "
                            f"{r} ({logic:g} V) on net {net_name!r} with no level "
                            f"shifter — this over-volts the MCU pin. Add a level "
                            f"shifter (e.g. TXS0108EPWR / BSS138) or use a 3.3 V part."
                        ),
                        refdes=comp.refdes,
                        pinNumber=pin.number,
                        net=net_name,
                        rule="R18_voltage_domain",
                    )
                )
                break
    return issues


# Pin-name tokens that mark a high-current motor supply (vs logic VCC).
_MOTOR_SUPPLY_TOKENS = ("VM", "VMOT", "VMOTOR", "VBB", "VS", "VPVDD", "VIN")


def _is_motor_driver(part: LibraryPart | None) -> bool:
    """A motor *driver* (not the motor): an actuator with both control inputs
    and motor outputs. Excludes bare motors/servos (no output stage pins)."""
    if part is None or part.category != "actuator":
        return False
    has_in = any(p.type == "input" for p in part.pins)
    has_out = any(p.type == "output" for p in part.pins)
    return has_in and has_out


def R19_motor_driver_completeness(project: Project) -> list[VerifierIssue]:
    """Motor-driver wiring completeness (feedback #3).

    For each motor driver: (a) its high-current motor supply pin must reach a
    power rail, (b) every control input must reach an MCU GPIO, (c) every motor
    output must reach a connector or a motor. Surfaces the BIN2-style gaps the
    reviewer hit as structured errors instead of leaving them in prose."""
    issues: list[VerifierIssue] = []
    comps = {c.refdes: c for c in project.ir.components}
    mcus = _mcu_components(project)
    net_by_name = {n.name: n for n in project.ir.nets}

    connector_refdes = {
        c.refdes
        for c in project.ir.components
        if (p := library.get_part(c.mpn)) and p.category == "connector"
    }
    # A "motor" load = an actuator that is NOT a driver (NEMA17, servo, etc.).
    motor_refdes = {
        c.refdes
        for c in project.ir.components
        if (p := library.get_part(c.mpn))
        and p.category == "actuator"
        and not _is_motor_driver(p)
    }

    for comp in project.ir.components:
        part = library.get_part(comp.mpn)
        if not _is_motor_driver(part):
            continue
        assert part is not None

        # (a) motor supply
        supply_pins = [
            pin for pin in part.pins
            if pin.type == "power_in"
            and any(tok in pin.name.upper() for tok in _MOTOR_SUPPLY_TOKENS)
        ]
        for pin in supply_pins:
            net = _net_for_pin(project, comp.refdes, pin.number)
            if net is None or _classify_net(net) != "power":
                issues.append(
                    VerifierIssue(
                        id=f"R19:supply:{comp.refdes}:{pin.number}",
                        severity="error",
                        message=(
                            f"{comp.refdes}.{pin.name} (motor supply) is not on a "
                            f"power rail — the driver has no motor voltage."
                        ),
                        refdes=comp.refdes,
                        pinNumber=pin.number,
                        rule="R19_motor_driver_completeness",
                    )
                )

        # (b) control inputs reach the MCU
        for pin in part.pins:
            if pin.type != "input":
                continue
            name_u = pin.name.upper()
            # STBY/EN/SLEEP/RESET are often strapped to VCC, not an MCU GPIO.
            if any(k in name_u for k in ("STBY", "EN", "SLEEP", "RESET", "NRST", "MODE")):
                continue
            net = _net_for_pin(project, comp.refdes, pin.number)
            if net is None:
                continue  # R16 already reports the dangling input as Missing
            net_obj = net_by_name.get(net)
            if net_obj and not any(r in mcus for r, _ in net_obj.pins):
                issues.append(
                    VerifierIssue(
                        id=f"R19:input:{comp.refdes}:{pin.number}",
                        severity="error",
                        message=(
                            f"{comp.refdes}.{pin.name} (control input) is on net "
                            f"{net!r} but no MCU GPIO drives it — the motor can't be "
                            f"controlled."
                        ),
                        refdes=comp.refdes,
                        pinNumber=pin.number,
                        net=net,
                        rule="R19_motor_driver_completeness",
                    )
                )

        # (c0) the driver drives NOTHING: every output pin unconnected. A live
        # rover shipped exactly this — AO1/AO2/BO1/BO2 all unwired while the
        # motor connectors sat on the 5 V and VM rails — with 0 errors. One
        # unused channel of a multi-channel array (ULN2003) is normal, so this
        # fires only when the part drives nothing at all.
        out_pins = [pin for pin in part.pins if pin.type == "output"]
        if out_pins and all(
            _net_for_pin(project, comp.refdes, pin.number) is None for pin in out_pins
        ):
            issues.append(
                VerifierIssue(
                    id=f"R19:unwired:{comp.refdes}",
                    severity="error",
                    message=(
                        f"None of {comp.refdes}'s {len(out_pins)} motor outputs "
                        f"({', '.join(p.name for p in out_pins[:4])}) is connected — the "
                        f"driver drives nothing. Wire each output to its motor connector "
                        f"pin (e.g. AO1 -> J.1, AO2 -> J.2), never to a power rail."
                    ),
                    refdes=comp.refdes,
                    rule="R19_motor_driver_completeness",
                )
            )

        # (c) motor outputs reach a connector or a motor
        for pin in part.pins:
            if pin.type != "output":
                continue
            net = _net_for_pin(project, comp.refdes, pin.number)
            if net is None:
                continue  # an unused channel is fine; see the all-unwired check below
            net_obj = net_by_name.get(net)
            if not net_obj:
                continue
            reaches = any(
                r in connector_refdes or r in motor_refdes
                for r, _ in net_obj.pins
                if r != comp.refdes
            )
            if _classify_net(net) == "power":
                issues.append(
                    VerifierIssue(
                        id=f"R19:railout:{comp.refdes}:{pin.number}",
                        severity="error",
                        message=(
                            f"{comp.refdes}.{pin.name} (motor output) sits on power rail "
                            f"{net!r} — a driver output must drive the motor, not be tied "
                            f"to a supply. Put it on its own net to the motor connector."
                        ),
                        refdes=comp.refdes,
                        pinNumber=pin.number,
                        net=net,
                        rule="R19_motor_driver_completeness",
                    )
                )
                continue
            if not reaches:
                issues.append(
                    VerifierIssue(
                        id=f"R19:output:{comp.refdes}:{pin.number}",
                        severity="warning",
                        message=(
                            f"{comp.refdes}.{pin.name} (motor output) on net {net!r} "
                            f"doesn't reach a motor connector or a motor."
                        ),
                        refdes=comp.refdes,
                        pinNumber=pin.number,
                        net=net,
                        rule="R19_motor_driver_completeness",
                    )
                )
    return issues


def R20_one_source_per_rail(project: Project) -> list[VerifierIssue]:
    """Two power_out sources on the same rail = two regulators fighting (#6).

    Distinct from R14 (which counts regulators by voltage globally): this flags
    a single net physically driven by more than one source component."""
    issues: list[VerifierIssue] = []
    comps = {c.refdes: c for c in project.ir.components}
    for net in project.ir.nets:
        if _classify_net(net.name) != "power":
            continue
        sources: list[str] = []
        for r, p in net.pins:
            comp = comps.get(r)
            if not comp:
                continue
            pin = next((pn for pn in comp.pins if pn.number == p), None)
            if pin and pin.type == "power_out" and r not in sources:
                sources.append(r)
        if len(sources) >= 2:
            issues.append(
                VerifierIssue(
                    id=f"R20:{net.name}",
                    severity="warning",
                    message=(
                        f"Rail {net.name} is driven by multiple sources "
                        f"({', '.join(sorted(sources))}). A rail should have one "
                        f"active source — this can back-drive a regulator."
                    ),
                    net=net.name,
                    rule="R20_one_source_per_rail",
                )
            )
    return issues


def R21_signal_pin_conflict(project: Project) -> list[VerifierIssue]:
    """One pin wired to two or more *different signal* nets (feedback / t3 RC car).

    R11 catches power/ground shorts; this catches the signal case: e.g. an ESP32
    GPIO that appears on both MOTOR1_IN2 and MOTOR2_IN3 — the two signals are
    shorted, so the motors can't be controlled independently. A genuine bug that
    no other rule flagged. Reported as an error against the shared pin."""
    issues: list[VerifierIssue] = []
    pin_to_nets: dict[tuple[str, str], list[str]] = {}
    for net in project.ir.nets:
        for r, p in net.pins:
            pin_to_nets.setdefault((r, p), []).append(net.name)

    comps = {c.refdes: c for c in project.ir.components}
    for (refdes, pin_number), net_names in pin_to_nets.items():
        distinct = sorted(set(net_names))
        if len(distinct) < 2:
            continue
        if not any(_classify_net(n) == "signal" for n in distinct):
            continue  # all power/ground -- R11's job
        # A pin on ANY two nets shorts them (a power rail + a "signal" like VM
        # that is really the same rail under a second name -- seen live: U2.VM
        # on both VBAT and VM). Only the pure power/ground case is left to R11.
        signal_nets = distinct
        comp = comps.get(refdes)
        pin_name = next(
            (pp.name for pp in (comp.pins if comp else []) if pp.number == pin_number),
            pin_number,
        )
        issues.append(
            VerifierIssue(
                id=f"R21:{refdes}:{pin_number}",
                severity="error",
                message=(
                    f"{refdes}.{pin_name} is wired to {len(signal_nets)} different "
                    f"nets ({', '.join(signal_nets)}) — one pin can only be one node, "
                    f"so these nets are shorted together. Keep the pin on ONE net "
                    f"(disconnect the others; use allocate_pin for a free MCU GPIO "
                    f"if a second signal needs its own pin)."
                ),
                refdes=refdes,
                pinNumber=pin_number,
                rule="R21_signal_pin_conflict",
            )
        )
    return issues


# ---------------------------------------------------------------------------
# Phase 4 — power-integrity rules promoted from the LLM critic so they are
# caught deterministically for every project, not just when the (out-of-loop,
# unreliable) critic happens to notice. See R22/R23.
# ---------------------------------------------------------------------------


def _is_resistor(part: LibraryPart | None) -> bool:
    if not part or part.category != "passive":
        return False
    sym = (part.symbol or "").lower()
    return not ("cap" in sym or sym.endswith(":c") or "ind" in sym or sym.endswith(":l"))


_CAP_RE = re.compile(r"([\d.]+)\s*(pf|nf|uf|µf|μf)", re.I)


def _cap_uf(part: LibraryPart | None) -> float | None:
    """Best-effort capacitance in µF, parsed from a passive's description/value.
    A pF/nF/µF unit is itself a reliable capacitor signal (resistors are in Ω,
    inductors in µH), so we key off the unit rather than the symbol/'capacitor'
    word — that way electrolytics ('220µF aluminum electrolytic') and tantalums
    are recognised too. Returns None for non-passives or unparseable parts
    (library values live in the description text, not a numeric field)."""
    if not part or part.category != "passive":
        return None
    m = _CAP_RE.search((part.description or "") + " " + (getattr(part, "value", "") or ""))
    if not m:
        return None
    val, unit = float(m.group(1)), m.group(2).lower()
    if unit == "pf":
        return val * 1e-6
    if unit == "nf":
        return val * 1e-3
    return val  # uf / µf / μf


def _fmt_uf(uf: float) -> str:
    if uf <= 0:
        return "none"
    if uf < 0.001:
        return f"{uf * 1e6:g}pF"
    if uf < 1.0:
        return f"{uf * 1e3:g}nF"
    return f"{uf:g}µF"


def R22_usb_c_cc_termination(project: Project) -> list[VerifierIssue]:
    """USB-C sink connectors must terminate CC1/CC2 through Rd (~5.1kΩ) pulldowns
    to GND — or into a dedicated PD/CC controller IC. A CC pin left floating, or
    pulled to a dead/non-GND node, means a compliant USB-C source never applies
    VBUS, so the board won't power from a USB-C charger at all.

    Caught the t3 'VBUS_CC' bug, where both CC resistors' far ends were tied to a
    floating net instead of GND — a defect that only the out-of-loop LLM critic
    had been noticing. Promoting it to a deterministic rule means every project
    is checked, and the agent's run_drc loop is driven to fix it."""
    issues: list[VerifierIssue] = []
    net_by_name = {n.name: n for n in project.ir.nets}

    def _cc_terminated(conn_refdes: str, cc_net: str) -> bool:
        net = net_by_name.get(cc_net)
        if not net:
            return False
        for refdes, pin in net.pins:
            if refdes == conn_refdes:
                continue
            other = library.get_part(
                next((c.mpn for c in project.ir.components if c.refdes == refdes), "")
            )
            if not other:
                continue
            # A controller IC on the CC line handles termination itself.
            if other.category not in {"passive", "connector"}:
                return True
            # A resistor: does any of its other pins reach GND?
            if _is_resistor(other):
                for op in other.pins:
                    if op.number == pin:
                        continue
                    far = _net_for_pin(project, refdes, op.number)
                    if far and _is_gnd_net(far):
                        return True
        return False

    for comp in project.ir.components:
        part = library.get_part(comp.mpn)
        if not part:
            continue
        upper_id = (comp.mpn + part.symbol + part.footprint).upper()
        is_usb_c = "USB" in upper_id and any("CC" in p.name.upper() for p in part.pins)
        if not is_usb_c:
            continue
        for pin in part.pins:
            nm = pin.name.upper()
            if not (nm == "CC" or nm.startswith("CC")):
                continue
            cc_net = _net_for_pin(project, comp.refdes, pin.number)
            if cc_net is None:
                issues.append(
                    VerifierIssue(
                        id=f"R22:{comp.refdes}:{pin.number}",
                        severity="warning",
                        message=(
                            f"USB-C {pin.name} on {comp.refdes} is floating — it needs "
                            f"a 5.1kΩ Rd pull-down to GND or a USB-C source will never "
                            f"supply power."
                        ),
                        refdes=comp.refdes,
                        pinNumber=pin.number,
                        rule="R22_usb_c_cc_termination",
                    )
                )
                continue
            if _cc_terminated(comp.refdes, cc_net):
                continue
            issues.append(
                VerifierIssue(
                    id=f"R22:{comp.refdes}:{pin.number}",
                    severity="warning",
                    message=(
                        f"USB-C {pin.name} ({cc_net}) is not terminated to GND through "
                        f"an Rd resistor — the board may not negotiate USB-C power. Tie "
                        f"the CC pull-down's far side to GND."
                    ),
                    refdes=comp.refdes,
                    pinNumber=pin.number,
                    net=cc_net,
                    rule="R22_usb_c_cc_termination",
                )
            )
    return issues


_BULK_CAP_MIN_UF = 10.0  # a motor-supply rail needs real bulk, not 100nF decoupling


def R23_motor_supply_bulk_cap(project: Project) -> list[VerifierIssue]:
    """A motor driver's high-current supply rail needs bulk capacitance (≥10µF)
    close to it — 100nF decoupling alone is not enough. Motor stall/inrush draws
    current spikes that sag the rail; when that same rail also feeds the logic
    regulator (the norm in small robots/RC cars) the dip can brown-out and reset
    the MCU.

    Caught the t3 case: VBAT_SW fed both TB6612 drivers *and* the TPS63070 with a
    single 100nF. Previously only the LLM critic flagged it; now it's deterministic."""
    issues: list[VerifierIssue] = []
    net_by_name = {n.name: n for n in project.ir.nets}
    part_by_refdes = {c.refdes: library.get_part(c.mpn) for c in project.ir.components}

    for comp in project.ir.components:
        part = part_by_refdes.get(comp.refdes)
        if not _is_motor_driver(part):
            continue
        assert part is not None
        supply_pins = [
            pin for pin in part.pins
            if pin.type == "power_in"
            and any(tok in pin.name.upper() for tok in _MOTOR_SUPPLY_TOKENS)
        ]
        flagged: set[str] = set()
        for pin in supply_pins:
            net = _net_for_pin(project, comp.refdes, pin.number)
            if not net or net in flagged:
                continue
            if _classify_net(net) != "power":
                continue  # R19 already flags a motor supply that isn't on a rail
            net_obj = net_by_name.get(net)
            if not net_obj:
                continue
            max_uf = 0.0
            for r, _ in net_obj.pins:
                uf = _cap_uf(part_by_refdes.get(r))
                if uf:
                    max_uf = max(max_uf, uf)
            if max_uf < _BULK_CAP_MIN_UF:
                flagged.add(net)
                issues.append(
                    VerifierIssue(
                        id=f"R23:{comp.refdes}:{net}",
                        severity="warning",
                        message=(
                            f"Motor-supply rail {net!r} on {comp.refdes} has no bulk "
                            f"capacitor (largest is {_fmt_uf(max_uf)}). Add ≥10µF "
                            f"(e.g. a 100µF bulk cap) near the driver so motor surges "
                            f"don't brown out the rail and reset the MCU."
                        ),
                        refdes=comp.refdes,
                        net=net,
                        rule="R23_motor_supply_bulk_cap",
                    )
                )
    return issues


# ---------------------------------------------------------------------------
# Engineering-rules Phase 1 — the ERC pin-type connection matrix (R24) and its
# two net-level companions (R25 driver-presence, R26 power-pin-driven). These
# form the exhaustive "correctness floor" the hand-picked R-rules lack: instead
# of a bespoke rule per pattern, EVERY pin pairing on every net is classified by
# `erc_matrix`. Shipped under a severity ramp (erc_matrix.STRICT_ERRORS) so the
# strictness step-change lands as warnings first. See docs/ENGINEERING_RULES_PLAN.
# ---------------------------------------------------------------------------


def _pin_type(comp: Component | None, pin_key: str) -> PinType | None:
    """Resolve a net endpoint (refdes,pin) to its electrical PinType. Net pins
    are keyed by number but occasionally by name, so match number then name."""
    if comp is None:
        return None
    pin = next((p for p in comp.pins if p.number == pin_key), None)
    if pin is None:
        pin = next((p for p in comp.pins if p.name == pin_key), None)
    return pin.type if pin else None


def _typed_net_endpoints(
    project: Project,
) -> list[tuple[str, list[tuple[str, str, PinType]]]]:
    """For each net, its resolved endpoints as (refdes, pin_key, type). Endpoints
    that can't be resolved to a typed pin are dropped (nothing to classify)."""
    comps = {c.refdes: c for c in project.ir.components}
    out: list[tuple[str, list[tuple[str, str, PinType]]]] = []
    for net in project.ir.nets:
        typed: list[tuple[str, str, PinType]] = []
        for r, p in net.pins:
            t = _pin_type(comps.get(r), p)
            if t is not None:
                typed.append((r, p, t))
        out.append((net.name, typed))
    return out


# Same-consumer-type pairings whose legality is a NET-LEVEL question ("is the
# rail driven?" / "is the input driven?"), not a pairwise one — a VCC rail with
# many power_in consumers or a signal fanned out to several inputs is perfectly
# normal. R24 skips these pairwise and defers them to R26 / R25 respectively, so
# a healthy multi-consumer net doesn't get flagged.
_NET_LEVEL_SAME_TYPE = {"power_in", "input"}


# Pins that pick an I2C part's bus mode / address.
_MODE_SELECT_NAMES = {"CSB", "CS", "CS_N", "NCS", "CSN", "SPI_CS"}
_ADDRESS_SELECT_NAMES = {"SDO", "AD0", "ADO", "ADDR", "SA0", "A0", "ASEL"}


def R24_pin_type_matrix(project: Project) -> list[VerifierIssue]:
    """Classify every pin-type pairing on every net against the ERC matrix
    (erc_matrix). Emits one finding per net per distinct offending pin-type
    pairing (deduped, worst-first), listing the endpoints involved. Subsumes and
    generalises R20 (power_out↔power_out) and adds coverage the R-rules never had
    (output↔output contention, power/logic-to-rail, rail↔ground shorts, and
    'unspecified' pin typing surfacing) — for EVERY net, not a coded handful."""
    issues: list[VerifierIssue] = []
    # An I2C part's address/mode strap is DESIGNED to sit on a rail or ground -
    # tying a BME280's SDO to GND is how you select address 0x76, and CSB high
    # is how you select I2C at all. Those pins carry an SPI type in the library
    # (SDO really is an output in SPI mode), so the matrix would read them as
    # "output shorted to ground". Exempt them by name on a rail/ground net.
    strap_pins = {
        (c.refdes, pin.number)
        for c in project.ir.components
        for pin in c.pins
        if pin.name.upper().strip() in (_MODE_SELECT_NAMES | _ADDRESS_SELECT_NAMES)
    }
    # For naming the offender: "U4.3" is a lookup; "U4.3 (OS)" is a finding the
    # user — and the fix-up loop — can act on without the datasheet.
    pin_names = {
        (c.refdes, pin.number): pin.name
        for c in project.ir.components
        for pin in c.pins
    }

    def _ep(r: str, p: str) -> str:
        name = pin_names.get((r, p), "")
        return f"{r}.{p} ({name})" if name and name != p else f"{r}.{p}"

    for net_name, endpoints in _typed_net_endpoints(project):
        if len(endpoints) < 2:
            continue
        rail_net = _classify_net(net_name) != "signal"
        # Worst severity + example endpoints, keyed by the unordered type pair.
        by_pair: dict[frozenset, dict] = {}
        for i in range(len(endpoints)):
            for j in range(i + 1, len(endpoints)):
                ra, pa, ta = endpoints[i]
                rb, pb, tb = endpoints[j]
                if ta == tb and ta in _NET_LEVEL_SAME_TYPE:
                    continue  # net-level question — see R25/R26
                if rail_net and ((ra, pa) in strap_pins or (rb, pb) in strap_pins):
                    continue  # deliberate address/mode strap
                sev = erc_matrix.severity(ta, tb)
                if sev is None:
                    continue
                key = frozenset({ta, tb})
                slot = by_pair.setdefault(
                    key, {"sev": sev, "types": (ta, tb), "eps": set()}
                )
                # Keep the strongest severity seen for this pairing.
                if sev == "error":
                    slot["sev"] = "error"
                # On a ground short, every legitimately grounded pin of every
                # part is one half of some offending pair — listing them all
                # buried the ONE real offender on the RC-ESC board (the LM75's
                # OS output on GND hid inside five pins across four parts, and
                # the fix-up loop never found it). Name only the non-ground
                # side; the ground side is where it belongs.
                if ta != tb and "ground" in (ta, tb):
                    if ta == "ground":
                        slot["eps"].add(_ep(rb, pb))
                    else:
                        slot["eps"].add(_ep(ra, pa))
                else:
                    slot["eps"].add(_ep(ra, pa))
                    slot["eps"].add(_ep(rb, pb))

        for key, slot in by_pair.items():
            ta, tb = slot["types"]
            eps = sorted(slot["eps"])[:6]
            verdict = erc_matrix.verdict(ta, tb)
            hard = verdict == "E"
            pair_id = "-".join(sorted({ta, tb}))
            issues.append(
                VerifierIssue(
                    id=f"R24:{net_name}:{pair_id}",
                    severity=slot["sev"],
                    message=(
                        f"Net {net_name}: {erc_matrix.reason(ta, tb)} "
                        f"({', '.join(eps)})."
                        + (" Likely an electrical error — verify."
                           if hard and slot["sev"] == "warning" else "")
                    ),
                    net=net_name,
                    rule="R24_pin_type_matrix",
                    source="erc",
                )
            )
    return issues


def R25_driver_presence(project: Project) -> list[VerifierIssue]:
    """Every net carrying an `input` pin needs at least one potential driver —
    an `output`, `power_out`, `bidirectional`, or a `passive` (a pull-up/down
    resistor drives the line). A net of pure inputs with nothing driving it is a
    floating input bus. This is the net-level companion to the matrix's
    input↔input cell (closed-form floating-input detection; generalises R16)."""
    issues: list[VerifierIssue] = []
    driver_types = {"output", "power_out", "bidirectional", "passive"}
    for net_name, endpoints in _typed_net_endpoints(project):
        types = {t for _, _, t in endpoints}
        if "input" not in types:
            continue
        if types & driver_types:
            continue
        inputs = sorted(f"{r}.{p}" for r, p, t in endpoints if t == "input")
        issues.append(
            VerifierIssue(
                id=f"R25:{net_name}",
                severity="warning",
                message=(
                    f"Net {net_name} has only input pins ({', '.join(inputs[:6])}) "
                    f"and nothing driving it — a floating input. Add a driver or a "
                    f"pull-up/down."
                ),
                net=net_name,
                rule="R25_driver_presence",
                source="erc",
            )
        )
    return issues


def R26_power_pin_driven(project: Project) -> list[VerifierIssue]:
    """Every `power_in` pin must sit on a net that has a source. A `power_out`
    on the net counts directly; a `passive` counts ONLY when it bridges to a
    source — its other pin sits on a net that has a `power_out` (an LDO output
    through a ferrite/sense resistor). The old rule accepted ANY passive as a
    source, but decoupling caps and pull-ups are passives sitting on virtually
    every rail, so a completely undriven rail passed clean (live-tested on a
    HAT whose 3V3 never reached the 40-pin header — the board was dead on
    arrival and nothing flagged it). Net-level companion to the matrix's
    power_in↔power_in cell; generalises R07/R08 beyond their category list."""
    issues: list[VerifierIssue] = []
    comps = {c.refdes: c for c in project.ir.components}
    endpoint_map = dict(_typed_net_endpoints(project))

    parts_by_ref = {c.refdes: library.get_part(c.mpn) for c in project.ir.components}

    def _net_has_switch_node(name: str) -> bool:
        """A switching regulator's SW/LX/PH ('Output' on the LM2596) pin sits on
        `name`: the rail an inductor leads from there IS regulated — that is the
        converter's output. The prompt sweep had the copilot insisting (rightly)
        that "LM2596 output U1.2 is on that net" while this rule said the rail
        had no source, because a switch node is not `power_out`."""
        for r, p, _t in endpoint_map.get(name, []):
            lp = parts_by_ref.get(r)
            if lp is None or lp.category != "regulator":
                continue
            pin = next((pp for pp in lp.pins if pp.number == p), None)
            if pin and pin.name.upper().strip() in _SW_NODE_NAMES:
                return True
        return False

    def _net_has_power_out(name: str) -> bool:
        return (any(t == "power_out" for _, _, t in endpoint_map.get(name, []))
                or _net_has_switch_node(name))

    def _passive_bridges_source(refdes: str, on_net: str) -> bool:
        """True if this passive's OTHER pin reaches a net with a real source —
        the series-element (ferrite / sense resistor / buck inductor) case."""
        comp = comps.get(refdes)
        if comp is None:
            return False
        for pin in comp.pins:
            net = _net_for_pin(project, refdes, pin.number)
            if net and net != on_net and _net_has_power_out(net):
                return True
        return False

    def _switch_bridges_source(refdes: str, on_net: str) -> bool:
        """A transistor whose OTHER channel pin (D/S, C/E) sits on a sourced or
        connector-fed rail is a load switch / P-FET reverse-polarity stage — its
        output rail (VBAT_PROTECTED behind an AO4407A) is legitimately sourced
        through it. Only channel pins count, never the gate/base."""
        comp = comps.get(refdes)
        lp = parts_by_ref.get(refdes)
        if comp is None or lp is None or lp.category != "transistor":
            return False
        for pin in comp.pins:
            nm = pin.name.upper().strip()
            if nm not in {"D", "S", "C", "E", "DRAIN", "SOURCE", "COLLECTOR", "EMITTER",
                          "D1", "D2", "S1", "S2"}:
                continue
            net = _net_for_pin(project, refdes, pin.number)
            if not net or net == on_net:
                continue
            if _net_has_power_out(net):
                return True
            if any(r in connector_refs and t != "power_in" for r, _, t in endpoint_map.get(net, [])):
                return True
        return False

    # A power-input CONNECTOR on the rail IS the source: a battery screw
    # terminal, a barrel jack, a USB receptacle or a supply header is where the
    # energy comes from. Review #13 flagged this as a false positive - the robot
    # car's battery terminal sat on VBAT and the rule still said "no source".
    connector_refs = {
        c.refdes for c in project.ir.components
        if (lp := library.get_part(c.mpn)) and lp.category == "connector"
    }

    for net_name, endpoints in endpoint_map.items():
        types = {t for _, _, t in endpoints}
        if "power_in" not in types:
            continue
        if "power_out" in types:
            continue
        # A connector pin that is itself typed `power_in` is a CONSUMER (a
        # sensor plugged into a header still needs the rail sourced); only a
        # passive / power_out connector pin is the board's power inlet.
        if any(
            r in connector_refs and t != "power_in"
            for r, _, t in endpoints
        ):
            continue  # fed from off-board through a connector
        passives = {r for r, _, t in endpoints if t == "passive"}
        if any(_passive_bridges_source(r, net_name) for r in passives):
            continue
        if _net_has_switch_node(net_name):
            continue  # the buck's own switch node lands here (inductor missing = R68's finding)
        if any(_switch_bridges_source(r, net_name) for r, _, _ in endpoints):
            continue
        # An internal-regulator pin (VCAP/VDDCORE class) is not a consumer:
        # the chip feeds it from inside, the net legitimately has no external
        # source — only the pin's capacitor (R80 owns what may sit there).
        consumers = sorted(
            f"{r}.{p}" for r, p, t in endpoints
            if t == "power_in" and not _is_internal_rail_pin(parts_by_ref.get(r), p)
        )
        if len(consumers) < 1:
            continue
        # No source, no connector, no bridging passive: this rail is either a
        # dangling label or a missing power path, and the board cannot run.
        # Review #14: the USB-C receptacle's VBUS pins were never wired, so the
        # LDO input floated and every check still passed.
        issues.append(
            VerifierIssue(
                id=f"R26:{net_name}",
                severity="error",
                message=(
                    f"Rail {net_name} feeds power pins ({', '.join(consumers[:6])}) "
                    f"but nothing sources it — no regulator output, no power "
                    f"connector, no passive bridging to one. Wire it to its source "
                    f"(the USB/battery connector's power pin, or the regulator "
                    f"output), or delete the net if the part is powered another way."
                ),
                net=net_name,
                rule="R26_power_pin_driven",
                source="erc",
            )
        )
    return issues


def R42_ground_return_path(project: Project) -> list[VerifierIssue]:
    """GND must reach the connector that carries it off-board (7.2/7.3
    live-test finding). A board is powered THROUGH a connector (HAT header,
    shield header, battery/DC jack with typed ground pins); when the GND net
    feeds parts but never touches any connector ground pin, there is no return
    path to the source — the board is dead even if the rails look wired.
    Fires only when a connector actually exposes ground-typed pins, so
    designs whose connectors are untyped never false-positive."""
    issues: list[VerifierIssue] = []
    comps = {c.refdes: c for c in project.ir.components}
    connector_gnd: set[tuple[str, str]] = set()
    for c in project.ir.components:
        part = library.get_part(c.mpn)
        if part is None or part.category != "connector":
            continue
        for pin in c.pins:
            if pin.type == "ground":
                connector_gnd.add((c.refdes, pin.number))
    if not connector_gnd:
        return issues

    gnd_nets = [n for n in project.ir.nets if _is_gnd_net(n.name)]
    if not gnd_nets:
        return issues  # R08 owns a missing GND net outright

    reached = any(
        (r, p) in connector_gnd for net in gnd_nets for r, p in net.pins
    )
    if reached:
        return issues
    has_consumer = any(
        _pin_type(comps.get(r), p) == "ground"
        for net in gnd_nets
        for r, p in net.pins
    )
    if not has_consumer:
        return issues
    refs = sorted({r for r, _ in connector_gnd})
    issues.append(
        VerifierIssue(
            id=f"R42:{gnd_nets[0].name}",
            severity="warning",
            message=(
                f"GND never reaches the ground pins of {', '.join(refs[:3])} — "
                f"the board has no ground return to its power source. Wire "
                f"{gnd_nets[0].name} to the connector's GND pins."
            ),
            net=gnd_nets[0].name,
            rule="R42_ground_return_path",
            source="erc",
        )
    )
    return issues


# ---------------------------------------------------------------------------
# Engineering-rules Phase 3 — IPC parametric checks we own (formulas, not chart
# lookups; see services/ipc.py). R27 IPC-2221 trace width vs current, R28
# IPC-2221 conductor spacing vs voltage. Voltage/current are inferred from the
# IR here; the physics lives in ipc.py. Tagged source="ipc" so the Review panel
# badges the standard. See docs/ENGINEERING_RULES_PLAN.md §6 Phase 3.
# ---------------------------------------------------------------------------

# Default fab class used when the schematic verifier has no board/profile in
# hand — the conservative "safe at any fab" copper weight + spacing baseline.
_DEFAULT_COPPER_OZ = 1.0
_DEFAULT_DESIGN_CLEARANCE_MM = 0.15
_DELTA_T_C = 10.0

_VOLT_RE = re.compile(r"(?<![A-Za-z0-9])[+-]?(\d+)V(\d+)?", re.IGNORECASE)
# Named rails with a conventional voltage the token parser can't read.
_NAMED_RAIL_V = {"VBUS": 5.0, "USB": 5.0, "VBAT": 3.7, "VSYS": 3.7}


def _parse_rail_voltage(name: str) -> float | None:
    """Infer a rail's working voltage from its net name: 3V3→3.3, +5V→5,
    +12V→12, 24V→24, VBUS→5. None when the name carries no voltage hint."""
    up = name.upper()
    for key, v in _NAMED_RAIL_V.items():
        if key in up:
            return v
    m = _VOLT_RE.search(up)
    if m:
        whole, frac = m.group(1), m.group(2)
        return float(f"{whole}.{frac}") if frac else float(whole)
    return None


# Rails that are the design's raw INPUT (battery pack, barrel jack, VIN) — the
# ones whose voltage the user states, never a regulator's output.
_INPUT_RAIL_PREFIXES = ("VBAT", "VIN", "VM", "VMOT", "VSYS", "VRAW", "VPWR", "VBATT")
_CELLS_RE = re.compile(r"\b(\d)\s*S\b", re.IGNORECASE)                 # "4S LiPo"
_SINGLE_CELL_RE = re.compile(r"\b(single|one|1)[- ]cell\b", re.IGNORECASE)
_INPUT_CONTEXT_RE = re.compile(
    r"\b(input|supply|pack|battery|lipo|li-ion|liion|adapter|barrel|jack|vin|"
    r"source|dc[- ]in|power in|mains)\b", re.IGNORECASE)
_LI_CELL_MAX_V = 4.2
# A Li-ion/LiPo cell is EMPTY at ~3.0 V, not at 3.7. Every battery design has to
# keep working all the way down, and checking only the full-charge voltage is
# how a "3.0-4.2 V cell" + a 3.3 V LDO passed every check and still dies with
# most of the battery unused (review 2026-08-20, finding 1).
_LI_CELL_MIN_V = 3.0
# Below this share of the declared input range the regulator is not a design
# choice with a runtime cost, it simply does not work: error. Above it, the
# board runs and stops early — a warning that names the cut-off voltage.
_DROPOUT_USABLE_ERROR = 0.15
# `_VOLT_RE` is for NET NAMES ("3V3" = 3.3 V, "5V"). Applied to prose it
# mis-reads: "3.3V" contains the substring "3V", so a requirement saying
# "3.3V LDO" was recorded as a 3 V input. Free text gets its own regex.
_VOLT_TEXT_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*V(?![\w])", re.IGNORECASE)
# "3.0 V to 4.2 V" / "3.0-4.2V" — an explicitly stated range.
_RANGE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*V?\s*(?:to|-|–|—|\.\.)\s*(\d+(?:\.\d+)?)\s*V",
    re.IGNORECASE,
)


def _declared_input_voltage(project: Project) -> float | None:
    """The input-rail voltage the USER declared, read from the recorded
    requirements ("4S LiPo battery input" -> 16.8 V fully charged; "12V input
    connector" -> 12; "single-cell Li-ion" -> 4.2). None when nothing was
    stated - the voltage rules then keep their generic assumptions. Reviews
    #19/#21/#23: a 4S pack, a 12 V jack and a 1S cell were ALL named `VBAT`
    and treated as 3.7 V, so an LM7805 on 16.8 V, an AP2112 (6 V max) on 12 V
    and an LM7805 on 4 V each passed."""
    # A stated RANGE or cell count is the authoritative answer for both ends —
    # use its high end rather than re-scanning prose, where "3.3V LDO" would
    # otherwise be read as a 3.3 V input.
    lo, hi = _declared_input_range(project)
    if hi is not None and lo is not None and hi > lo:
        return hi
    texts = [r.description or "" for r in (getattr(project.ir, "requirements", None) or [])]
    ff = getattr(project.ir, "form_factor", None)
    if ff is not None and ff.note:
        texts.append(ff.note)
    best: float | None = None
    for t in texts:
        m = _CELLS_RE.search(t)
        if m and _INPUT_CONTEXT_RE.search(t):
            v = int(m.group(1)) * _LI_CELL_MAX_V
            best = max(best or 0.0, v)
            continue
        if _SINGLE_CELL_RE.search(t) and _INPUT_CONTEXT_RE.search(t):
            best = max(best or 0.0, _LI_CELL_MAX_V)
            continue
        if _INPUT_CONTEXT_RE.search(t):
            mv = _VOLT_TEXT_RE.search(t)
            if mv:
                v = float(mv.group(1))
                if 3.0 <= v <= 60.0:
                    best = max(best or 0.0, v)
            else:
                mv = _VOLT_RE.search(t.upper())
                if mv:
                    v = float(f"{mv.group(1)}.{mv.group(2)}") if mv.group(2) else float(mv.group(1))
                    if 3.0 <= v <= 60.0:
                        best = max(best or 0.0, v)
    return best


def _declared_input_range(project: Project) -> tuple[float | None, float | None]:
    """`(vmin, vmax)` of the input rail the USER declared.

    `_declared_input_voltage` answers "how high can this rail go" (the
    over-voltage question). This answers the OTHER half — "how low does it
    go" — which is the one that kills battery designs: a 3.3 V LDO from a
    single Li-ion cell is fine at 4.2 V and drops out at 3.4 V, so the logger
    stops long before the cell is empty. Sources, in order: an explicitly
    stated range ("3.0 V to 4.2 V"), a cell count ("4S LiPo" -> 12.0..16.8),
    "single-cell" (3.0..4.2), else a single stated voltage (a regulated
    supply — min == max)."""
    texts = [r.description or "" for r in (getattr(project.ir, "requirements", None) or [])]
    ff = getattr(project.ir, "form_factor", None)
    if ff is not None and ff.note:
        texts.append(ff.note)
    vmin = vmax = None
    for raw in texts:
        if not _INPUT_CONTEXT_RE.search(raw):
            continue
        m = _RANGE_RE.search(raw)
        if m:
            lo, hi = float(m.group(1)), float(m.group(2))
            if lo > hi:
                lo, hi = hi, lo
            if 0.5 <= lo <= 60.0 and 0.5 <= hi <= 60.0:
                vmin = lo if vmin is None else min(vmin, lo)
                vmax = hi if vmax is None else max(vmax, hi)
                continue
        cells = _CELLS_RE.search(raw)
        if cells:
            n = int(cells.group(1))
            vmin = n * _LI_CELL_MIN_V if vmin is None else min(vmin, n * _LI_CELL_MIN_V)
            vmax = n * _LI_CELL_MAX_V if vmax is None else max(vmax, n * _LI_CELL_MAX_V)
            continue
        if _SINGLE_CELL_RE.search(raw) or re.search(r"\b(li-?ion|li-?po|lipo|18650|lir?20\d\d)\b", raw, re.IGNORECASE):
            vmin = _LI_CELL_MIN_V if vmin is None else min(vmin, _LI_CELL_MIN_V)
            vmax = _LI_CELL_MAX_V if vmax is None else max(vmax, _LI_CELL_MAX_V)
            continue
        mv = _VOLT_TEXT_RE.search(raw) or _VOLT_RE.search(raw.upper())
        if mv:
            groups = mv.groups()
            if len(groups) == 1:
                v = float(groups[0])
            else:
                v = float(f"{groups[0]}.{groups[1]}") if groups[1] else float(groups[0])
            if 0.5 <= v <= 60.0:
                vmin = v if vmin is None else min(vmin, v)
                vmax = v if vmax is None else max(vmax, v)
    return (vmin, vmax)


def rail_voltage_range(
    project: Project, net_name: str, comps: dict[str, Component] | None = None
) -> tuple[float | None, float | None]:
    """`(vmin, vmax)` for a rail, for display AND for the dropout check.

    A regulated rail is its regulator's fixed output (min == max). An INPUT
    rail carries the declared range. Everything else falls back to the working
    voltage. This is the missing "voltage" column the 2026-08-20 review called
    the cheapest fix that unblocks the most: the power tree showed VBUS, VBAT
    and VCC_3V3 all tagged LOGIC with no voltage at all."""
    if _is_gnd_net(net_name):
        return (0.0, 0.0)
    comps = comps or {c.refdes: c for c in project.ir.components}
    named = _parse_rail_voltage(net_name)
    up = net_name.upper()
    # An INPUT rail takes the user's declared range ahead of the name table:
    # "VBAT" in the table is a nominal 3.7 V guess, but the user said the cell
    # runs 3.0-4.2 V, and 3.0 is the number the dropout check needs. A rail
    # whose NAME carries a voltage ("VBAT_12V") keeps its explicit number.
    if up.startswith(_INPUT_RAIL_PREFIXES) and not _VOLT_RE.search(up):
        lo, hi = _declared_input_range(project)
        if lo is not None or hi is not None:
            return (lo, hi)
    # A rail driven by a fixed regulator IS that regulator's output.
    for comp in project.ir.components:
        part = library.get_part(comp.mpn)
        if part is None or part.category not in {"regulator", "charger"}:
            continue
        for pin in part.pins:
            if pin.type != "power_out":
                continue
            if _net_for_pin(project, comp.refdes, pin.number) == net_name:
                vout = _regulator_out_voltage(part)
                if vout is not None:
                    return (vout, vout)
    if named is not None:
        return (named, named)
    v = _net_working_voltage(project, net_name, comps)
    return (v, v)


def _net_working_voltage(
    project: Project, net_name: str, comps: dict[str, Component]
) -> float | None:
    """Working voltage of a net: the name hint if any, else the max rated
    voltageMax among parts with a power pin on the net. GND → 0. An INPUT rail
    (VBAT/VIN/VM…) with no numeric hint in its name takes the user's declared
    input voltage when one was recorded (see _declared_input_voltage)."""
    if _is_gnd_net(net_name):
        return 0.0
    up = net_name.upper()
    if up.startswith(_INPUT_RAIL_PREFIXES) and not _VOLT_RE.search(up):
        declared = _declared_input_voltage(project)
        if declared is not None:
            return declared
    named = _parse_rail_voltage(net_name)
    if named is not None:
        return named
    volts: list[float] = []
    net = next((n for n in project.ir.nets if n.name == net_name), None)
    if net is None:
        return None
    for r, _ in net.pins:
        part = library.get_part(comps[r].mpn) if r in comps else None
        if part and part.voltageMax is not None:
            volts.append(part.voltageMax)
    return max(volts) if volts else None


# Typical quiescent draw of a driver IC's LOGIC supply pin (VCC/VDD/VLOGIC),
# as opposed to its motor/output supply (VM/VBB/VS). Datasheet Icc figures for
# TB6612FNG / DRV8833 / ULN2003-class parts are ~1-5 mA.
LOGIC_SUPPLY_MA = 5.0
_MOTOR_SUPPLY_TOKENS = ("VM", "VBB", "VS", "VMOT", "VPWR", "VIN")
_LOGIC_SUPPLY_TOKENS = ("VCC", "VDD", "VLOGIC", "VIO", "VDDIO", "VLOG")


def _is_logic_supply_pin(comp: Component, pin) -> bool:
    """True when `pin` is the LOGIC supply of a part that has a SEPARATE
    motor/output supply pin — i.e. the current flowing here is quiescent, not
    the part's output rating."""
    names = {p.name.upper().strip() for p in comp.pins}
    has_motor_rail = any(
        n == t or n.startswith(t + "_") for n in names for t in _MOTOR_SUPPLY_TOKENS
    )
    if not has_motor_rail:
        return False
    up = pin.name.upper().strip()
    return any(up == t or up.startswith(t) for t in _LOGIC_SUPPLY_TOKENS)


def _net_current_ma(
    project: Project, net_name: str, comps: dict[str, Component]
) -> float | None:
    """Estimated current a power rail carries: the sum of the max draw of its
    consumers (parts with a `power_in` pin on the net). Falls back to typical×3
    for consumers with no max. None when no consumer exposes any current data."""
    net = next((n for n in project.ir.nets if n.name == net_name), None)
    if net is None:
        return None
    total = 0.0
    have = False
    counted: set[str] = set()
    for r, p in net.pins:
        comp = comps.get(r)
        if not comp or r in counted:
            continue
        pin = next((pn for pn in comp.pins if pn.number == p or pn.name == p), None)
        if not pin or pin.type != "power_in":
            continue
        part = library.get_part(comp.mpn)
        if not part:
            continue
        draw = part.currentMaxMa
        if draw is None and part.currentTypMa is not None:
            draw = part.currentTypMa * 3.0  # peak estimate from typical
        # A driver IC's currentMaxMa is its OUTPUT rating, drawn from its MOTOR
        # supply (VM), not from the logic rail its VCC sits on. Counting the
        # full rating against VCC_3V3 made a TB6612 look like 1.2 A of logic
        # load, which reported the (correct) LDO as undersized and pushed the
        # agent to swap in ever-bigger regulators — the over-engineering seen
        # in beta reviews #3/#6/#7. On the logic rail count a few mA instead.
        if draw is not None and _is_logic_supply_pin(comp, pin):
            draw = LOGIC_SUPPLY_MA
        if draw is not None:
            total += draw
            have = True
            counted.add(r)
    return total if have else None


def R27_ipc_trace_width(project: Project) -> list[VerifierIssue]:
    """IPC-2221 conductor sizing: a power rail's trace must be wide enough for the
    current it carries (10 °C rise, 1 oz Cu). With a routed board, flag any trace
    on the rail narrower than the computed minimum (a real ampacity defect). At
    schematic stage, advise the recommended width for meaningful rails (≥0.5 A) so
    the layout is right before routing. Only fires where current is inferable."""
    issues: list[VerifierIssue] = []
    comps = {c.refdes: c for c in project.ir.components}
    board = project.board
    for net in project.ir.nets:
        if _classify_net(net.name) != "power":
            continue
        cur_ma = _net_current_ma(project, net.name, comps)
        if cur_ma is None or cur_ma <= 0:
            continue
        req_mm = ipc.trace_width_mm(cur_ma / 1000.0, _DELTA_T_C, _DEFAULT_COPPER_OZ)

        if board is not None:
            traces = [t for t in board.traces if t.net == net.name]
            thin = [t for t in traces if t.width < req_mm * 0.95]
            if traces and thin:
                actual = min(t.width for t in thin)
                issues.append(
                    VerifierIssue(
                        id=f"R27:{net.name}",
                        severity="warning",
                        message=(
                            f"Rail {net.name} carries ~{cur_ma:.0f} mA but its "
                            f"narrowest trace is {actual:.2f} mm — IPC-2221 needs "
                            f"≥{req_mm:.2f} mm (1 oz, 10 °C rise). Widen the trace "
                            f"or lower the current."
                        ),
                        net=net.name,
                        rule="R27_ipc_trace_width",
                        source="ipc",
                    )
                )
            continue

        # No board yet — advise only for rails where width actually matters.
        if cur_ma >= 500.0:
            issues.append(
                VerifierIssue(
                    id=f"R27:{net.name}",
                    severity="info",
                    message=(
                        f"Rail {net.name} will carry ~{cur_ma:.0f} mA — route it at "
                        f"≥{req_mm:.2f} mm (IPC-2221, 1 oz, 10 °C rise). Set a "
                        f"net-class width before/while routing."
                    ),
                    net=net.name,
                    rule="R27_ipc_trace_width",
                    source="ipc",
                )
            )
    return issues


def R28_ipc_clearance(project: Project) -> list[VerifierIssue]:
    """IPC-2221 §6.3 conductor spacing: a rail's required clearance grows with its
    working voltage. Flags rails whose IPC-required spacing exceeds a normal
    design clearance (0.15 mm) — i.e. higher-voltage nets (motor/boost/mains) that
    need extra spacing a flat fab rule won't enforce. Low-voltage rails never
    fire (their required spacing is below the fab minimum)."""
    issues: list[VerifierIssue] = []
    comps = {c.refdes: c for c in project.ir.components}
    for net in project.ir.nets:
        if _is_gnd_net(net.name):
            continue
        v = _net_working_voltage(project, net.name, comps)
        if v is None or v <= 0:
            continue
        req = ipc.clearance_mm(v)
        if req > _DEFAULT_DESIGN_CLEARANCE_MM:
            issues.append(
                VerifierIssue(
                    id=f"R28:{net.name}",
                    severity="warning",
                    message=(
                        f"Rail {net.name} (~{v:.0f} V) needs ≥{req:.2f} mm clearance "
                        f"to adjacent copper per IPC-2221 Table 6-1 — more than the "
                        f"{_DEFAULT_DESIGN_CLEARANCE_MM:.2f} mm default. Give this "
                        f"net extra spacing (a wider-clearance net-class)."
                    ),
                    net=net.name,
                    rule="R28_ipc_clearance",
                    source="ipc",
                )
            )
    return issues


# ---------------------------------------------------------------------------
# Engineering-rules Phase 4 — SI/PI (signal & power integrity). R29 differential
# pair impedance + length matching (USB/Ethernet), R30 decoupling-cap-per-power-
# pin count (promotes R01 from "≥1 cap" to "one per pin"). Best-practice
# advisories tagged source="ipc". See docs/ENGINEERING_RULES_PLAN.md §6 Phase 4.
# ---------------------------------------------------------------------------

# Net-name suffixes that mark the two halves of a differential pair.
_DIFF_SUFFIXES = [("_P", "_N"), ("+", "-"), ("P", "N"), ("DP", "DM"), ("D+", "D-")]


def _diff_pair_base(name: str) -> tuple[str, str] | None:
    """If `name` is one half of a diff pair, return (base, polarity) where
    polarity is 'p' or 'n'. Recognises USB (D+/D-, DP/DM) and *_P/*_N / *+/*-."""
    up = name.upper()
    for pos, neg in _DIFF_SUFFIXES:
        if up.endswith(pos) and len(up) > len(pos):
            return up[: len(up) - len(pos)], "p"
        if up.endswith(neg) and len(up) > len(neg):
            return up[: len(up) - len(neg)], "n"
    return None


def _routed_length_mm(board, net_name: str) -> float:
    import math
    total = 0.0
    for t in board.traces:
        if t.net != net_name:
            continue
        for s in t.segments:
            total += math.hypot(s.x2 - s.x1, s.y2 - s.y1)
    return total


def _diff_pair_standard(base: str, pos: str, neg: str) -> tuple[str, int]:
    """(what this pair IS, its target impedance) from the net names — so the
    advisory talks about the design in front of it. The 2026-08-20 review:
    "the differential-pair advisory references 100 ohm Ethernet on a board with
    no Ethernet"."""
    blob = f"{base} {pos} {neg}".upper()
    if "USB" in blob or "DP" in blob or "DM" in blob or "D+" in blob:
        return ("USB", 90)
    if any(k in blob for k in ("ETH", "RX", "TX", "MDI", "LAN", "PHY")):
        return ("Ethernet", 100)
    if "CAN" in blob:
        return ("CAN", 120)
    if "LVDS" in blob or "MIPI" in blob or "CSI" in blob or "DSI" in blob:
        return ("LVDS/MIPI", 100)
    if "RS485" in blob or "485" in blob:
        return ("RS-485", 120)
    return ("differential", 100)


def R29_diff_pair_integrity(project: Project) -> list[VerifierIssue]:
    """Differential pairs (USB, Ethernet, LVDS) need controlled impedance
    (90/100 Ω) and intra-pair length matching. Detects P/N net pairs by name; with
    a routed board, flags a length mismatch >0.5 mm between the two halves; without
    one, advises impedance + matching when a diff pair is present. Advisory."""
    issues: list[VerifierIssue] = []
    board = project.board
    # Group nets into diff pairs by shared base name.
    bases: dict[str, dict[str, str]] = {}
    for net in project.ir.nets:
        bp = _diff_pair_base(net.name)
        if bp is None:
            continue
        base, pol = bp
        bases.setdefault(base, {})[pol] = net.name

    for base, halves in bases.items():
        if "p" not in halves or "n" not in halves:
            continue
        pos, neg = halves["p"], halves["n"]
        if board is not None and board.traces:
            lp, ln = _routed_length_mm(board, pos), _routed_length_mm(board, neg)
            if lp > 0 and ln > 0 and abs(lp - ln) > 0.5:
                issues.append(
                    VerifierIssue(
                        id=f"R29:{base}",
                        severity="warning",
                        message=(
                            f"Differential pair {pos}/{neg} is length-mismatched by "
                            f"{abs(lp - ln):.2f} mm ({lp:.1f} vs {ln:.1f} mm) — match "
                            f"the two halves and route them as a coupled "
                            f"{_diff_pair_standard(base, pos, neg)[1]} Ω pair "
                            f"({_diff_pair_standard(base, pos, neg)[0]} signal integrity)."
                        ),
                        net=pos,
                        rule="R29_diff_pair_integrity",
                        source="ipc",
                    )
                )
        else:
            issues.append(
                VerifierIssue(
                    id=f"R29:{base}",
                    severity="info",
                    message=(
                        f"Differential pair {pos}/{neg} ({_diff_pair_standard(base, pos, neg)[0]}): "
                        f"route it as a coupled {_diff_pair_standard(base, pos, neg)[1]} Ω "
                        f"controlled-impedance pair with the two halves length-matched."
                    ),
                    net=pos,
                    rule="R29_diff_pair_integrity",
                    source="ipc",
                )
            )
    return issues


_DECOUPLE_ICS = {
    "mcu", "sensor", "regulator", "charger", "interface", "memory", "logic", "audio",
}


def R30_decoupling_per_pin(project: Project) -> list[VerifierIssue]:
    """Best practice is one decoupling cap per IC power pin, not one per rail.
    Promotes R01 (which flags a power pin with *zero* caps) to a count: an IC that
    IS decoupled but has fewer caps than power pins gets an advisory. Skips ICs
    R01 already covers (no caps at all). Advisory (info)."""
    issues: list[VerifierIssue] = []
    comps = {c.refdes: c for c in project.ir.components}
    cap_refdes = {
        c.refdes for c in project.ir.components
        if _cap_uf(library.get_part(c.mpn)) is not None
    }
    for comp in project.ir.components:
        part = library.get_part(comp.mpn)
        if not part or part.category not in _DECOUPLE_ICS:
            continue
        power_pins = [p for p in part.pins if p.type == "power_in"]
        if len(power_pins) < 2:
            continue  # a single-supply-pin part is R01's job
        supply_nets: set[str] = set()
        for pin in power_pins:
            n = _net_for_pin(project, comp.refdes, pin.number)
            if n and not _is_gnd_net(n):
                supply_nets.add(n)
        caps = {
            r
            for net in project.ir.nets
            if net.name in supply_nets
            for r, _ in net.pins
            if r in cap_refdes
        }
        # Only advise when the IC is decoupled but under-provisioned; zero caps is
        # R01's territory (a stronger warning), so don't double-report.
        if caps and len(caps) < len(power_pins):
            issues.append(
                VerifierIssue(
                    id=f"R30:{comp.refdes}",
                    severity="info",
                    message=(
                        f"{comp.refdes} has {len(power_pins)} power pins but only "
                        f"{len(caps)} decoupling cap(s) on its supply — best practice "
                        f"is one 100 nF per power pin, placed close to each pin."
                    ),
                    refdes=comp.refdes,
                    rule="R30_decoupling_per_pin",
                    source="ipc",
                )
            )
    return issues


# ---------------------------------------------------------------------------
# Engineering-rules Phase 5 — parametric SIZING rules (correctness, not just
# legality). These verify component *choices/values* against the physics using
# the curated electrical envelope (currentMaxMa / voltageMax / powerMaxW /
# defaultI2cAddress), so "trust the LLM's value" becomes "verify it". Same pure
# Project->issues shape as the rest; reuse the R27/R28 inference helpers.
# ---------------------------------------------------------------------------


def _regulator_output_nets(project: Project, comp: Component, part: LibraryPart) -> set[str]:
    out: set[str] = set()
    for pin in part.pins:
        if pin.type == "power_out":
            n = _net_for_pin(project, comp.refdes, pin.number)
            if n and not _is_gnd_net(n):
                out.add(n)
    return out


def _regulator_input_net(project: Project, comp: Component, part: LibraryPart) -> str | None:
    for pin in part.pins:
        if pin.type == "power_in":
            n = _net_for_pin(project, comp.refdes, pin.number)
            if n and not _is_gnd_net(n):
                return n
    return None


def R31_regulator_current_adequate(project: Project) -> list[VerifierIssue]:
    """A regulator must be rated for the current its rail actually draws. Sums
    the max draw of every consumer on the regulator's output rail (R27's
    `_net_current_ma`) and compares to the regulator's rated output current
    (`currentMaxMa`). Load > rating = the regulator browns out or overheats — a
    real functional defect the DRC never caught before. Only fires when both the
    rating and consumer current are known."""
    issues: list[VerifierIssue] = []
    comps = {c.refdes: c for c in project.ir.components}
    for comp in project.ir.components:
        part = library.get_part(comp.mpn)
        if not part or part.category != "regulator" or part.currentMaxMa is None:
            continue
        for net in _regulator_output_nets(project, comp, part):
            load = _net_current_ma(project, net, comps)
            if load is None or load <= 0:
                continue
            if load > part.currentMaxMa:
                issues.append(
                    VerifierIssue(
                        id=f"R31:{comp.refdes}:{net}",
                        severity="error",
                        message=(
                            f"{comp.refdes} ({comp.mpn}) is rated "
                            f"{part.currentMaxMa:.0f} mA but rail {net} draws "
                            f"~{load:.0f} mA — the regulator is undersized. Use a "
                            f"higher-current regulator or reduce the load."
                        ),
                        refdes=comp.refdes,
                        net=net,
                        rule="R31_regulator_current_adequate",
                        source="verifier",
                    )
                )
    return issues


def _is_linear_regulator(part: LibraryPart | None) -> bool:
    if not part or part.category != "regulator":
        return False
    d = (part.description or "").lower()
    if any(k in d for k in ("switching", "buck", "boost", "dc-dc", "dc/dc", "smps", "step-down", "step-up")):
        return False
    return "linear" in d or "ldo" in d or "low-dropout" in d or "low dropout" in d


def R32_linear_regulator_thermal(project: Project) -> list[VerifierIssue]:
    """A linear regulator burns the input↔output voltage difference as heat:
    P = (Vin − Vout)·Iout. Flags a linear reg whose dissipation exceeds its rated
    `powerMaxW` (error) or a high-dissipation design (warning) that needs a
    switching regulator or heatsinking. Switchers are exempt (they don't burn the
    difference). Only fires when Vin/Vout/Iout are inferable."""
    issues: list[VerifierIssue] = []
    comps = {c.refdes: c for c in project.ir.components}
    for comp in project.ir.components:
        part = library.get_part(comp.mpn)
        if not _is_linear_regulator(part):
            continue
        out_nets = _regulator_output_nets(project, comp, part)
        in_net = _regulator_input_net(project, comp, part)
        if not out_nets or not in_net:
            continue
        vin = _net_working_voltage(project, in_net, comps)
        if vin is None or vin <= 0:
            continue
        for net in out_nets:
            vout = _net_working_voltage(project, net, comps)
            iout = _net_current_ma(project, net, comps)
            if vout is None or iout is None or vin <= vout:
                continue
            power = (vin - vout) * (iout / 1000.0)  # W
            if part.powerMaxW is not None and power > part.powerMaxW:
                issues.append(
                    VerifierIssue(
                        id=f"R32:{comp.refdes}:{net}",
                        severity="error",
                        message=(
                            f"{comp.refdes} ({comp.mpn}) would dissipate "
                            f"~{power:.2f} W ({vin:.0f}→{vout:.0f} V @ {iout:.0f} mA) "
                            f"— above its {part.powerMaxW:.2f} W rating. Use a "
                            f"switching regulator or drop the voltage in stages."
                        ),
                        refdes=comp.refdes,
                        net=net,
                        rule="R32_linear_regulator_thermal",
                        source="verifier",
                    )
                )
            elif power > 1.0:
                issues.append(
                    VerifierIssue(
                        id=f"R32:{comp.refdes}:{net}",
                        severity="warning",
                        message=(
                            f"{comp.refdes} ({comp.mpn}) dissipates ~{power:.2f} W as "
                            f"heat ({vin:.0f}→{vout:.0f} V @ {iout:.0f} mA) — a linear "
                            f"regulator here runs hot. Consider a switching regulator "
                            f"or a heatsink-capable package."
                        ),
                        refdes=comp.refdes,
                        net=net,
                        rule="R32_linear_regulator_thermal",
                        source="verifier",
                    )
                )
    return issues


def _i2c_sda_net(project: Project, comp: Component, part: LibraryPart) -> str | None:
    for pin in part.pins:
        nm = pin.name.upper().replace("/", "")
        if nm in {"SDA", "SDIO", "SDASDIO", "DATA"}:
            return _net_for_pin(project, comp.refdes, pin.number)
    return None


def R34_i2c_address_collision(project: Project) -> list[VerifierIssue]:
    """Two I²C devices with the same address on one bus can't both be addressed.
    Groups I²C parts (interface tag + a `defaultI2cAddress`) by the SDA net they
    share and flags duplicate default addresses. A warning, not an error — many
    parts have an address-select pin, so it's resolvable by strapping; the message
    says so."""
    issues: list[VerifierIssue] = []
    buses: dict[str, list[tuple[str, str]]] = {}
    for comp in project.ir.components:
        part = library.get_part(comp.mpn)
        if not part or "I2C" not in (part.interfaces or []) or not part.defaultI2cAddress:
            continue
        sda = _i2c_sda_net(project, comp, part) or "_shared"
        buses.setdefault(sda, []).append((comp.refdes, part.defaultI2cAddress.strip().lower()))
    for sda, devs in buses.items():
        by_addr: dict[str, list[str]] = {}
        for refdes, addr in devs:
            by_addr.setdefault(addr, []).append(refdes)
        for addr, refdeses in by_addr.items():
            if len(refdeses) >= 2:
                where = f"bus {sda}" if sda != "_shared" else "the I²C bus"
                issues.append(
                    VerifierIssue(
                        id=f"R34:{sda}:{addr}",
                        severity="warning",
                        message=(
                            f"{', '.join(sorted(refdeses))} share I²C address "
                            f"{addr} on {where} — they can't be addressed "
                            f"independently. Set the address-select pin on one, or "
                            f"put them on separate buses / an I²C mux."
                        ),
                        net=sda if sda != "_shared" else None,
                        rule="R34_i2c_address_collision",
                        source="verifier",
                    )
                )
    return issues


# IC-class categories that always have more than a couple of pins — a part in one
# of these with <3 pins is almost certainly an under-populated datasheet-ingest
# (the 68 two-pin suspects in PIN_QUALITY_ANALYSIS §1). Connectors/passives are
# excluded because a 2-pin part is legitimate there.
_IC_CLASS_CATEGORIES = {
    "mcu", "regulator", "charger", "sensor", "actuator",
    "memory", "interface", "display", "audio",
}


def R35_pin_integrity(project: Project) -> list[VerifierIssue]:
    """Pin-data integrity (PIN_QUALITY_ANALYSIS Fix A). Nothing else in the stack
    flags a component whose pin list is empty or under-populated: R15 explicitly
    *skips* zero-pin parts, and every other rule iterates the pin list, so a part
    with missing pins produces zero findings and passes DRC clean — the single
    biggest source of the "missing pin" complaints. This rule turns that silent
    data gap into a visible, catchable issue for every user (fixing the defect
    class, not one project's data).

    Three deterministic, library-grounded checks:
      1. an electrical (non-mechanical) part with **zero** pins — it can't be
         wired or verified at all (error);
      2. an IC-class part with an implausibly low pin count (<3) — almost always
         an under-populated datasheet-ingest (warning);
      3. an instance with **fewer** pins than the library now defines — a stale
         add-time snapshot that a later library repair never propagated to
         (warning; re-adding the part fixes it).

    R15 (floating component) is orthogonal and still owns the "part is placed but
    unwired" case; this rule owns "the part's pin *data* is missing/incomplete".
    """
    issues: list[VerifierIssue] = []
    for comp in project.ir.components:
        part = library.get_part(comp.mpn)
        category = (part.category if part else "").lower()
        if category == "mechanical":
            continue  # mounting holes / heatsinks legitimately have no pins
        ir_count = len(comp.pins)
        lib_count = len(part.pins) if part else 0

        if ir_count == 0:
            issues.append(
                VerifierIssue(
                    id=f"R35:{comp.refdes}",
                    severity="error",
                    message=(
                        f"{comp.refdes} ({comp.mpn}) has no pins defined — it "
                        f"cannot be wired or verified. Its library entry is "
                        f"missing pin data (a datasheet-ingest gap); pick a "
                        f"fully-defined part or repair the library entry."
                    ),
                    refdes=comp.refdes,
                    rule="R35_pin_integrity",
                    source="verifier",
                )
            )
            continue
        if category in _IC_CLASS_CATEGORIES and ir_count < 3:
            issues.append(
                VerifierIssue(
                    id=f"R35:{comp.refdes}",
                    severity="warning",
                    message=(
                        f"{comp.refdes} ({comp.mpn}) defines only {ir_count} "
                        f"pin{'s' if ir_count != 1 else ''}, but a {category} "
                        f"normally has more — its library pin list looks "
                        f"under-populated (likely a datasheet-ingest gap)."
                    ),
                    refdes=comp.refdes,
                    rule="R35_pin_integrity",
                    source="verifier",
                )
            )
            continue
        if part is not None and ir_count < lib_count:
            issues.append(
                VerifierIssue(
                    id=f"R35:{comp.refdes}",
                    severity="warning",
                    message=(
                        f"{comp.refdes} ({comp.mpn}) has {ir_count} pins but the "
                        f"library now defines {lib_count} — this instance is a "
                        f"stale snapshot; re-add the part to pick up the "
                        f"corrected pins."
                    ),
                    refdes=comp.refdes,
                    rule="R35_pin_integrity",
                    source="verifier",
                )
            )
    return issues


def R36_gpio_5v_tolerance(project: Project) -> list[VerifierIssue]:
    """A GPIO of a 3.3 V-only MCU tied directly to a ≥5 V power rail (7.1).

    The pin-safety table knows which families are NOT 5 V-tolerant (Raspberry
    Pi, RP2040/Pico, ESP32 — all 3.3 V-only I/O; the AVR Arduino boards run
    5 V logic and are exempt). When one of their GPIOs sits on a power net
    whose working voltage is ≥4.5 V (a pull-up to 5 V, a strap to VBUS/VIN),
    the pin is over-volted the moment the rail comes up — a hard error, not a
    style question.

    Division of labour: R18 owns the *driven-output* case (a 5 V part whose
    output tracks its supply, e.g. HC-SR04 ECHO); the ERC matrix (R24) gives a
    generic logic-pin-on-rail warning. This rule owns the direct rail tie and
    escalates it to an error only when the family table proves the pin cannot
    take 5 V — a 3.3 V rail on an EN pin, or a 5 V rail on an Arduino D-pin,
    never fires.
    """
    issues: list[VerifierIssue] = []
    mcus = _mcu_or_host_components(project)
    if not mcus:
        return issues
    comps = {c.refdes: c for c in project.ir.components}
    name_maps = {ref: _pin_name_map(c) for ref, c in mcus.items()}
    resistors = _resistor_refdeses(project)
    net_by_name = {n.name: n for n in project.ir.nets}
    nets_of_pin: dict[tuple[str, str], str] = {
        (r, p): n.name for n in project.ir.nets for r, p in n.pins
    }

    def _report(r: str, p: str, rail: str, v: float, via: str | None) -> None:
        mpn = mcus[r].mpn
        tolerant = pin_safety.five_v_tolerant(mpn)
        if tolerant is None or tolerant:
            return  # unknown family, or 5 V-logic (AVR) — fine
        pin_name = name_maps[r].get(p, p)
        # Power/ground pins (5V, VSYS, VIN, GND...) don't parse as a GPIO
        # number, so powering the board from 5 V never false-positives.
        if pin_safety.gpio_number(pin_name, mpn) is None:
            return
        logic = pin_safety.logic_voltage(mpn) or 3.3
        how = (f"is pulled up to net {rail!r} (~{v:g} V) through {via}"
               if via else f"is tied to net {rail!r} (~{v:g} V)")
        issues.append(
            VerifierIssue(
                id=f"R36:{r}:{p}",
                severity="error",
                message=(
                    f"{r}.{pin_name} {how}, "
                    f"but {mpn} I/O is {logic:g} V-only and NOT 5 V-tolerant — "
                    f"the pin will be over-volted. Move it to a "
                    f"{logic:g} V rail or insert a level shifter "
                    f"(e.g. TXS0108EPWR / BSS138)."
                ),
                refdes=r,
                pinNumber=p,
                net=rail,
                rule="R36_gpio_5v_tolerance",
                source="verifier",
            )
        )

    hot_rails: dict[str, float] = {}
    for net in project.ir.nets:
        if _classify_net(net.name) != "power":
            continue
        v = _net_working_voltage(project, net.name, comps)
        if v is None or v < 4.5:
            continue
        hot_rails[net.name] = v
        for r, p in net.pins:
            if r in mcus:
                _report(r, p, net.name, v, None)

    # One hop through a resistor: a pull-up from a GPIO net to a 5 V rail
    # (review #23: four limit-switch inputs pulled up to VCC_5V on an ESP32 -
    # 5 V on the pin whenever the switch is open). Same fault, one resistor
    # away; the direct-tie check above cannot see it.
    seen = {(i.refdes, i.pinNumber) for i in issues}
    for rr in resistors:
        pins = [(x, p) for (x, p), _ in nets_of_pin.items() if x == rr]
        if len(pins) != 2:
            continue
        n_a, n_b = nets_of_pin[pins[0]], nets_of_pin[pins[1]]
        for rail, other in ((n_a, n_b), (n_b, n_a)):
            if rail not in hot_rails or _classify_net(other) != "signal":
                continue
            onet = net_by_name.get(other)
            if onet is None:
                continue
            for r, p in onet.pins:
                if r in mcus and (r, p) not in seen:
                    seen.add((r, p))
                    _report(r, p, rail, hot_rails[rail], f"{rr} (net {other})")
    return issues


# ---------------------------------------------------------------------------
# Week-5 robotics power-integrity rules (7.5) — extending the R22/R23 family.
# The failure classes a small robot actually dies from: a 5 V Arduino GPIO
# frying a 3.3 V sensor, motors sharing the logic rail, an unprotected coil
# killing its transistor, a battery lead melting a 1 A connector pin, and a
# stall transient browning out the MCU.
# ---------------------------------------------------------------------------

_INDUCTIVE_TOKENS = ("MOTOR", "RELAY", "SOLENOID", "COIL", "VIBRATION")
_SERVO_TOKENS = ("SERVO",)


def _is_bare_inductive_load(part: LibraryPart | None) -> bool:
    """A raw motor/relay/solenoid coil (no integrated driver). Servos are
    excluded — they integrate their own driver + protection."""
    if part is None or part.category != "actuator" or _is_motor_driver(part):
        return False
    blob = (part.mpn + " " + part.description).upper()
    if any(t in blob for t in _SERVO_TOKENS):
        return False
    return any(t in blob for t in _INDUCTIVE_TOKENS)


def R37_five_v_mcu_drives_3v3_part(project: Project) -> list[VerifierIssue]:
    """A 5 V-logic MCU GPIO wired straight into a ≤3.6 V part (7.5).

    R18 owns the opposite direction (a 5 V peripheral output into a 3.3 V
    MCU); this owns the classic Arduino mistake — an Uno/Nano/Mega (5 V
    logic), or an Arduino SHIELD driven by its 5 V host through the header
    (7.3), driving a 3.3 V-max sensor/radio/flash pin directly. Same net =
    direct copper, so a divider/shifter would put the victim on a different
    net; a level shifter ON the net exempts it. **Open-drain buses are safe**
    when their pull-ups go to a ≤3.6 V rail: on I²C the pull-up — not the 5 V
    host — sets the high level, so those are skipped (this also fixes a latent
    false positive for real 5 V MCUs on a 3.3 V-pulled-up I²C bus). Error:
    driving high over-volts the victim's I/O."""
    issues: list[VerifierIssue] = []
    comps = {c.refdes: c for c in project.ir.components}
    mcus = _mcu_or_host_components(project)
    if not mcus:
        return issues
    shifters = {
        c.refdes
        for c in project.ir.components
        if _is_level_shifter(library.get_part(c.mpn))
    }
    five_v_mcus: dict[str, tuple[Component, float]] = {}
    for r, c in mcus.items():
        logic = pin_safety.logic_voltage(c.mpn) or _mcu_logic_voltage(
            library.get_part(c.mpn)
        )
        if logic is not None and logic >= 4.5:
            five_v_mcus[r] = (c, logic)
    if not five_v_mcus:
        return issues
    name_maps = {r: _pin_name_map(c) for r, (c, _) in five_v_mcus.items()}

    _VICTIM_EXEMPT = {
        "passive", "connector", "mechanical", "switch", "led", "diode",
        "transistor", "crystal", "protection",
    }
    for net in project.ir.nets:
        if _classify_net(net.name) in {"power", "gnd"}:
            continue
        if any(r in shifters for r, _ in net.pins):
            continue
        driver = next(((r, p) for r, p in net.pins if r in five_v_mcus), None)
        if driver is None:
            continue
        # Open-drain bus pulled up to a ≤3.6 V rail: the pull-up, not the 5 V
        # host, sets the high level — safe for a 3.3 V part.
        if _net_is_open_drain(net, comps):
            pull_v = _net_pullup_rail_v(project, net, comps)
            if pull_v is not None and pull_v <= 3.6:
                continue
        r, p = driver
        mcu_comp, logic = five_v_mcus[r]
        pin_name = name_maps[r].get(p, p)
        if pin_safety.gpio_number(pin_name, mcu_comp.mpn) is None:
            continue  # not a GPIO (AREF, RESET, ...)
        for vr, vp in net.pins:
            if vr == r or vr not in comps:
                continue
            vpart = library.get_part(comps[vr].mpn)
            if (
                vpart is None
                or vpart.voltageMax is None
                or vpart.voltageMax >= 4.5
                or vpart.category in _VICTIM_EXEMPT
            ):
                continue
            if _pin_type(comps[vr], vp) not in {"input", "output", "bidirectional"}:
                continue
            issues.append(
                VerifierIssue(
                    id=f"R37:{r}:{p}:{vr}",
                    severity="error",
                    message=(
                        f"{r}.{pin_name} is {logic:g} V logic but drives {vr} "
                        f"({comps[vr].mpn}, max {vpart.voltageMax:g} V) directly on "
                        f"net {net.name!r} — a high level over-volts it. Insert a "
                        f"level shifter (TXS0108EPWR / BSS138) or use a "
                        f"{logic:g} V-tolerant part."
                    ),
                    refdes=vr,
                    pinNumber=vp,
                    net=net.name,
                    rule="R37_five_v_mcu_drives_3v3_part",
                    source="verifier",
                )
            )
            break  # one finding per net is enough
    return issues


def R38_motor_logic_rail_separation(project: Project) -> list[VerifierIssue]:
    """Motor supply and MCU logic supply on the SAME rail (7.5). PWM ripple
    and stall transients on a shared rail reset the MCU — the standard robot
    topology is battery → motor rail direct, battery → regulator → logic
    rail. Warning (a small vibration motor can share; R23's bulk cap helps),
    per the two-stage severity precedent."""
    issues: list[VerifierIssue] = []
    mcus = _mcu_components(project)
    if not mcus:
        return issues

    mcu_power_nets: dict[str, str] = {}
    for r, c in mcus.items():
        for pin in c.pins:
            if pin.type == "power_in":
                net = _net_for_pin(project, r, pin.number)
                if net:
                    mcu_power_nets[net] = r

    flagged: set[str] = set()
    for comp in project.ir.components:
        part = library.get_part(comp.mpn)
        if not _is_motor_driver(part):
            continue
        assert part is not None
        for pin in part.pins:
            if pin.type != "power_in" or not any(
                tok in pin.name.upper() for tok in _MOTOR_SUPPLY_TOKENS
            ):
                continue
            net = _net_for_pin(project, comp.refdes, pin.number)
            if not net or net in flagged or net not in mcu_power_nets:
                continue
            flagged.add(net)
            issues.append(
                VerifierIssue(
                    id=f"R38:{comp.refdes}:{net}",
                    severity="warning",
                    message=(
                        f"Motor supply {comp.refdes}.{pin.name} shares rail "
                        f"{net!r} with the MCU ({mcu_power_nets[net]}) — motor "
                        f"PWM/stall transients on a shared rail brown-out the "
                        f"logic. Feed the motor rail directly from the source and "
                        f"give the MCU its own regulated rail."
                    ),
                    refdes=comp.refdes,
                    pinNumber=pin.number,
                    net=net,
                    rule="R38_motor_logic_rail_separation",
                    source="verifier",
                )
            )
    return issues


def R39_flyback_protection(project: Project) -> list[VerifierIssue]:
    """A bare inductive load switched by a discrete transistor needs a flyback
    diode across the coil (7.5). Back-EMF at turn-off kills the transistor.
    Integrated H-bridge/stepper drivers have body-diode protection — a load on
    a driver's outputs is exempt; a load that nothing switches (direct to a
    connector) is skipped."""
    issues: list[VerifierIssue] = []
    comps = {c.refdes: c for c in project.ir.components}
    parts = {c.refdes: library.get_part(c.mpn) for c in project.ir.components}
    net_by_name = {n.name: n for n in project.ir.nets}

    driver_output_nets: set[str] = set()
    for comp in project.ir.components:
        part = parts[comp.refdes]
        if not _is_motor_driver(part):
            continue
        for pin in part.pins:  # type: ignore[union-attr]
            if pin.type == "output":
                net = _net_for_pin(project, comp.refdes, pin.number)
                if net:
                    driver_output_nets.add(net)

    def _nets_of(refdes: str) -> set[str]:
        comp = comps[refdes]
        out: set[str] = set()
        for pin in comp.pins:
            net = _net_for_pin(project, refdes, pin.number)
            if net:
                out.add(net)
        return out

    for comp in project.ir.components:
        part = parts[comp.refdes]
        if not _is_bare_inductive_load(part):
            continue
        load_nets = _nets_of(comp.refdes)
        if not load_nets:
            continue
        if load_nets & driver_output_nets:
            continue  # an integrated driver switches it — protected
        switched = any(
            parts.get(r) is not None and parts[r].category == "transistor"  # type: ignore[union-attr]
            for n in load_nets
            for r, _ in (net_by_name.get(n).pins if net_by_name.get(n) else [])
            if r != comp.refdes
        )
        if not switched:
            continue  # nothing switches it — no back-EMF edge to clamp
        protected = any(
            parts.get(r) is not None
            and parts[r].category == "diode"  # type: ignore[union-attr]
            and load_nets <= _nets_of(r)
            for r in comps
            if r != comp.refdes
        )
        if not protected:
            # A relay/solenoid coil on a discrete FET with no clamp is not a
            # "consider it" case: the first turn-off spike exceeds the FET's
            # Vds and it fails - and the design's whole function is that coil.
            # Error for coils (relay/solenoid); warning for motors (a brushed
            # motor on a bare transistor is often a deliberate hobby shortcut).
            blob = f"{part.mpn} {part.description}".lower()  # type: ignore[union-attr]
            is_coil = any(k in blob for k in ("relay", "solenoid", "coil", "contactor"))
            issues.append(
                VerifierIssue(
                    id=f"R39:{comp.refdes}",
                    severity="error" if is_coil else "warning",
                    message=(
                        f"{comp.refdes} ({comp.mpn}) is an inductive load switched "
                        f"by a transistor with no flyback diode across it — "
                        f"back-EMF at turn-off will destroy the switch. Add a "
                        f"diode (e.g. 1N4007 / SS34) across the coil, cathode to "
                        f"the supply side."
                    ),
                    refdes=comp.refdes,
                    rule="R39_flyback_protection",
                    source="verifier",
                )
            )
    return issues


# Per-pin current capacity by connector family (conservative datasheet-class
# ratings, amps per pin). Token-matched against MPN + package + description.
_CONNECTOR_PIN_AMPS: tuple[tuple[str, float], ...] = (
    ("JST_SH", 1.0), ("JST-SH", 1.0),
    ("JST_GH", 1.0), ("JST-GH", 1.0),
    ("QWIIC", 1.0),
    ("JST_PH", 2.0), ("JST-PH", 2.0),
    ("JST_XH", 3.0), ("JST-XH", 3.0),
    ("SCREW", 10.0), ("TERMINAL", 10.0),
    ("BARREL", 5.0), ("JACK", 5.0),
    ("HEADER", 3.0),
)


def _connector_pin_amps(part: LibraryPart | None) -> float | None:
    if part is None or part.category != "connector":
        return None
    blob = f"{part.mpn} {part.package} {part.description}".upper()
    for tok, amps in _CONNECTOR_PIN_AMPS:
        if tok in blob:
            return amps
    return None


def R40_header_pin_current(project: Project) -> list[VerifierIssue]:
    """A power rail's estimated current vs the connector pins carrying it
    (7.5). A battery or motor rail routinely exceeds a 1 A JST-SH or a 3 A
    0.1\" header pin; paralleled pins scale the capacity. Advisory — current
    estimates come from consumer ratings (R27's model), not measurement."""
    issues: list[VerifierIssue] = []
    comps = {c.refdes: c for c in project.ir.components}
    net_by_name = {n.name: n for n in project.ir.nets}

    for comp in project.ir.components:
        part = library.get_part(comp.mpn)
        amps = _connector_pin_amps(part)
        if amps is None:
            continue
        # Nets this connector carries, with how many of its pins carry each.
        carried: dict[str, int] = {}
        for pin in comp.pins:
            net = _net_for_pin(project, comp.refdes, pin.number)
            if net:
                carried[net] = carried.get(net, 0) + 1
        for net, n_pins in carried.items():
            if _classify_net(net) != "power":
                continue
            est_ma = _net_current_ma(project, net, comps)
            if est_ma is None:
                continue
            capacity_ma = amps * 1000.0 * n_pins
            if est_ma > capacity_ma:
                issues.append(
                    VerifierIssue(
                        id=f"R40:{comp.refdes}:{net}",
                        severity="warning",
                        message=(
                            f"Rail {net!r} is estimated at ~{est_ma / 1000.0:.1f} A "
                            f"but passes through {comp.refdes} ({comp.mpn}) on "
                            f"{n_pins} pin{'s' if n_pins != 1 else ''} rated "
                            f"~{amps:g} A each ({capacity_ma / 1000.0:g} A total) — "
                            f"the pin(s) will overheat. Use a higher-current "
                            f"connector (screw terminal / XT30) or parallel more "
                            f"pins."
                        ),
                        refdes=comp.refdes,
                        net=net,
                        rule="R40_header_pin_current",
                        source="verifier",
                    )
                )
    return issues


_STALL_FACTOR = 3.0  # stall current ≈ 3× a brushed motor's rated max (approximation)


def R41_motor_stall_brownout(project: Project) -> list[VerifierIssue]:
    """Motor-stall transient vs the supply's current capability (7.5). A
    stalled brushed motor draws roughly 3x its rated current;
    when the motor rail's source can't deliver that, the rail sags and the
    logic browns out (R23's bulk cap softens, it doesn't fix). Fires only
    when the library has both the motors' and the source's current ratings —
    same data-dependency rule as R31."""
    issues: list[VerifierIssue] = []
    comps = {c.refdes: c for c in project.ir.components}
    parts = {c.refdes: library.get_part(c.mpn) for c in project.ir.components}
    net_by_name = {n.name: n for n in project.ir.nets}

    for comp in project.ir.components:
        part = parts[comp.refdes]
        if not _is_motor_driver(part):
            continue
        assert part is not None

        # Stall estimate: motors hanging off this driver's outputs.
        stall_ma = 0.0
        motor_refs: list[str] = []
        for pin in part.pins:
            if pin.type != "output":
                continue
            net = _net_for_pin(project, comp.refdes, pin.number)
            net_obj = net_by_name.get(net) if net else None
            if not net_obj:
                continue
            for r, _ in net_obj.pins:
                mpart = parts.get(r)
                if (
                    mpart is not None
                    and mpart.category == "actuator"
                    and not _is_motor_driver(mpart)
                    and mpart.currentMaxMa is not None
                    and r not in motor_refs
                ):
                    stall_ma += mpart.currentMaxMa * _STALL_FACTOR
                    motor_refs.append(r)
        if not motor_refs:
            continue

        # The motor rail's source capability.
        for pin in part.pins:
            if pin.type != "power_in" or not any(
                tok in pin.name.upper() for tok in _MOTOR_SUPPLY_TOKENS
            ):
                continue
            net = _net_for_pin(project, comp.refdes, pin.number)
            net_obj = net_by_name.get(net) if net else None
            if not net_obj:
                continue
            source_ma: float | None = None
            source_ref: str | None = None
            for r, p in net_obj.pins:
                spart = parts.get(r)
                if spart is None or spart.currentMaxMa is None:
                    continue
                if _pin_type(comps[r], p) == "power_out":
                    source_ma = spart.currentMaxMa
                    source_ref = r
                    break
            if source_ma is None or stall_ma <= source_ma:
                continue
            issues.append(
                VerifierIssue(
                    id=f"R41:{comp.refdes}:{net}",
                    severity="warning",
                    message=(
                        f"Motor rail {net!r}: a stall on "
                        f"{'/'.join(motor_refs)} draws ~{stall_ma / 1000.0:.1f} A "
                        f"(≈{_STALL_FACTOR:g}× rated), but its source {source_ref} "
                        f"is rated {source_ma / 1000.0:.1f} A — the rail will sag "
                        f"and brown-out the logic. Feed motors from the battery "
                        f"directly, pick a higher-current source, and keep ≥100 µF "
                        f"bulk on the rail."
                    ),
                    refdes=comp.refdes,
                    net=net,
                    rule="R41_motor_stall_brownout",
                    source="verifier",
                )
            )
    return issues


_SUPPORTING_CATEGORIES = {
    "connector", "passive", "mechanical", "protection", "diode", "transistor",
    "crystal", "regulator",
}


def R43_requested_quantity(project: Project) -> list[VerifierIssue]:
    """The intent-vs-IR check (LED-matrix review, 2026-08-15). The agent records
    the user's countable asks via the `record_requirements` tool (structured
    data on the IR, never prose); this rule compares each requirement against
    what was actually built. A design containing 3 of the 100 requested LEDs is
    a hard ERROR — it feeds the agent's fix-up loop and blocks the
    manufacturability "ready" verdict, so a wrong board can never be presented
    as done. Silent when no requirements are recorded (older projects)."""
    from app.services import library as _library

    issues: list[VerifierIssue] = []
    reqs = getattr(project.ir, "requirements", None) or []
    if not reqs:
        return issues

    cat_by_mpn: dict[str, str] = {}

    def _category(mpn: str) -> str:
        if mpn not in cat_by_mpn:
            part = _library.get_part(mpn)
            cat_by_mpn[mpn] = part.category if part else ""
        return cat_by_mpn[mpn]

    # Requirements that resolve to the SAME parts must be judged as a POOL:
    # "1x battery connector (ScrewTerminal_2P)" + "4x ESC connector
    # (ScrewTerminal_2P)" is one ask for 5 terminals. Judged separately, the 4
    # built read as 4/1 over AND 4/4 met (review #19's "5/1 and 5/4"), and the
    # real shortfall - only 3 ESC outputs - was invisible. Pool by match key.
    def _match_key(r) -> tuple[str, str] | None:
        if r.mpn:
            return ("mpn", r.mpn)
        if r.category:
            return ("cat", r.category)
        return None

    pooled: dict[tuple[str, str], list] = {}
    for req in reqs:
        if getattr(req, "unavailable", False):
            continue
        k = _match_key(req)
        if k is not None:
            pooled.setdefault(k, []).append(req)
    pool_qty = {k: sum(r.quantity for r in rs) for k, rs in pooled.items()}
    pool_desc = {
        k: "in total (" + " + ".join(f"{r.quantity}x {r.description}" for r in rs) + ")"
        for k, rs in pooled.items()
    }
    pool_done: set[tuple[str, str]] = set()

    for req in reqs:
        if getattr(req, "unavailable", False):
            # The user asked for something the curated library cannot supply.
            # Whatever else got built, the design is INCOMPLETE - and it must
            # say so, not swap in a stand-in (review #8: a 3 mm LED as "SEG1").
            issues.append(
                VerifierIssue(
                    id=f"R43:{req.key}:unavailable",
                    severity="error",
                    message=(
                        f"User requested {req.quantity}x {req.description}, which is "
                        f"not in the curated parts library - the design is INCOMPLETE "
                        f"until that part is curated. Tell the user plainly; do not "
                        f"present a substitute as the requested part."
                    ),
                    rule="R43_requested_quantity",
                    source="verifier",
                )
            )
            continue
        key = _match_key(req)
        if key is None:
            continue
        if key in pool_done:
            continue
        pool_done.add(key)
        req_quantity = pool_qty[key]
        req_description = pool_desc[key] if len(pooled[key]) > 1 else req.description
        if req.mpn:
            accepted = {req.mpn}
            part = _library.get_part(req.mpn)
            if part:
                accepted.update(part.alternatives or [])
            count = sum(1 for c in project.ir.components if c.mpn in accepted)
        else:
            count = sum(
                1 for c in project.ir.components if _category(c.mpn) == req.category
            )
        if count == req_quantity:
            continue
        # Report against the pooled ask (same id/key contract as before).
        req = type(req)(**{**req.model_dump(), "quantity": req_quantity, "description": req_description})
        if count > req.quantity:
            # More than asked is not a build failure (an extra status LED can be
            # legitimate) but it IS worth a look — a duplicated array reads as
            # "20 LEDs" for a 10-LED ask. Supporting categories (connectors,
            # passives, protection, discretes) legitimately carry extras — a
            # battery connector next to two motor connectors — so a category
            # requirement there is only a floor.
            if req.category in _SUPPORTING_CATEGORIES:
                continue
            # By exact MPN the ask is unambiguous ("3x TCRT5000" -> 6 built is a
            # duplicated block, seen live) -> error so the fix-up loop removes
            # the extras; a category ask stays a warning.
            issues.append(
                VerifierIssue(
                    id=f"R43:{req.key}:over",
                    severity="error" if req.mpn else "warning",
                    message=(
                        f"User requested {req.quantity}x {req.description} but the "
                        f"design contains {count} — remove the extras (delete_component) "
                        f"or confirm the surplus is intended."
                    ),
                    rule="R43_requested_quantity",
                    source="verifier",
                )
            )
            continue
        issues.append(
            VerifierIssue(
                id=f"R43:{req.key}",
                severity="error",
                message=(
                    f"User requested {req.quantity}x {req.description} but the "
                    f"design contains {count}. Add the missing "
                    f"{req.quantity - count} (add_component_array for repeated "
                    f"parts) or, if the user changed their mind, re-record the "
                    f"requirements."
                ),
                rule="R43_requested_quantity",
                source="verifier",
            )
        )
    return issues


def R44_mcu_gpios_shorted(project: Project) -> list[VerifierIssue]:
    """Two or more GPIO pins of the SAME MCU (or shield/HAT host header) on one
    net. Seen live on the 10-LED chase board: a fix-up turn merged all ten LED
    signals into one net, so ten ESP32 outputs were shorted together and every
    LED lit at once — and nothing flagged it, because MCU IO pins are typed
    bidirectional (a bus is many bidirectionals on one net, so R24 rightly
    allows that across parts). Within ONE MCU there is no designed case for
    tying its own GPIOs together: it's a contention risk and the signals can't
    be driven independently. Error; feeds the fix-up loop via _FIX_HINTS.
    Power/ground-typed pins and nets are ignored (an MCU has many GND pins)."""
    issues: list[VerifierIssue] = []
    mcus = _mcu_or_host_components(project)
    if not mcus:
        return issues
    for net in project.ir.nets:
        if _classify_net(net.name) != "signal":
            continue
        by_mcu: dict[str, list[str]] = {}
        for r, p in net.pins:
            comp = mcus.get(r)
            if comp is None:
                continue
            pin = next((pp for pp in comp.pins if pp.number == p), None)
            if pin is None or str(pin.type) in {"power_in", "power_out", "ground", "passive", "no_connect"}:
                continue
            if pin_safety.gpio_number(pin.name, comp.mpn) is None:
                continue  # not a GPIO (EN, boot, RF, ...) — leave to other rules
            by_mcu.setdefault(r, []).append(pin.name)
        for refdes, names in by_mcu.items():
            if len(names) < 2:
                continue
            issues.append(
                VerifierIssue(
                    id=f"R44:{net.name}:{refdes}",
                    severity="error",
                    message=(
                        f"{len(names)} GPIOs of {refdes} ({', '.join(names[:6])}"
                        f"{'…' if len(names) > 6 else ''}) are wired to the same net "
                        f"{net.name} — the MCU's own outputs are shorted together and "
                        f"the signals can't be driven independently. Give each signal "
                        f"its own net and its own GPIO (allocate_pin)."
                    ),
                    refdes=refdes,
                    net=net.name,
                    rule="R44_mcu_gpios_shorted",
                    source="verifier",
                )
            )
    return issues


# Enable/sleep/reset pins whose "disabled" level is GND. Active-low control
# pins carry an n/N prefix or a B/N suffix (nSLEEP, NRST, RESETB, RESET_N, ENN);
# active-high enables are EN/ENA/ENB/EN1/ENABLE/CE/STBY (TB6612's STBY low =
# standby). Plain RESET/RST/SHDN are left alone — their polarity varies by part.
_DISABLED_AT_GND_RE = re.compile(
    r"^(N(RESET|RST|SLEEP|EN|CE|SHDN|STBY|PD)\d?"
    r"|(RESET|RST|EN|CE)(B|N|_N|_B)"
    r"|EN[AB]?\d?|ENABLE|CE\d?|STBY)$"
)


def R45_enable_pin_tied_low(project: Project) -> list[VerifierIssue]:
    """An enable / nSLEEP / active-low reset INPUT hard-wired to a ground net —
    the part is held disabled/asleep/in reset forever. Seen live: the agent
    "tied the DRV8833 nSLEEP pin to GND", i.e. a motor driver that can never
    drive; R24 only warns ("input shorted to ground") because many inputs are
    legitimately grounded (address selects). For pins whose NAME says enable /
    sleep / reset the polarity is known, so this is a hard error and feeds the
    fix-up loop (_FIX_HINTS['R45']): pull it up to the logic rail or drive it
    from a GPIO."""
    issues: list[VerifierIssue] = []
    comps = {c.refdes: c for c in project.ir.components}
    for net in project.ir.nets:
        if _classify_net(net.name) != "gnd":
            continue
        for r, p in net.pins:
            comp = comps.get(r)
            if comp is None:
                continue
            pin = next((pp for pp in comp.pins if pp.number == p), None)
            if pin is None or str(pin.type) != "input":
                continue
            if not _DISABLED_AT_GND_RE.match(pin.name.upper().strip()):
                continue
            issues.append(
                VerifierIssue(
                    id=f"R45:{r}:{p}",
                    severity="error",
                    message=(
                        f"{r}.{pin.name} is tied to {net.name} — this enable/sleep/"
                        f"reset input holds {r} permanently disabled. Pull it up to "
                        f"the logic rail (10 kΩ to VCC_3V3) or drive it from an MCU "
                        f"GPIO instead."
                    ),
                    refdes=r,
                    pinNumber=p,
                    net=net.name,
                    rule="R45_enable_pin_tied_low",
                    source="verifier",
                )
            )
    return issues


def _resistor_refdeses(project: Project) -> set[str]:
    out: set[str] = set()
    for c in project.ir.components:
        part = library.get_part(c.mpn)
        if part and part.category == "passive" and part.symbol.lower().endswith(":r"):
            out.add(c.refdes)
    return out


# Colour-channel pin names on a multi-channel LED (RGB). Each is an anode-side
# (or cathode-side) channel that needs its OWN series resistor.
_LED_CHANNEL_NAMES = {"R", "G", "B", "RED", "GREEN", "BLUE", "W", "WHITE",
                      "R1", "G1", "B1", "RIN", "GIN", "BIN"}
_LED_COMMON_NAMES = {"A", "ANODE", "K", "CATHODE", "COM", "COMMON", "VCC", "GND"}


def _led_channel_pins(comp: Component) -> list[Pin]:
    """Colour-channel pins of a multi-channel LED (an RGB 5050's R/G/B), i.e.
    every non-common pin. Empty for a plain 2-pin LED."""
    names = {p.name.upper().strip() for p in comp.pins}
    if not (names & _LED_CHANNEL_NAMES) or len(comp.pins) < 3:
        return []
    return [p for p in comp.pins if p.name.upper().strip() in _LED_CHANNEL_NAMES]


def _led_pins(comp: Component) -> tuple[Pin | None, Pin | None]:
    """(anode, cathode) pins of an LED-like part: names A/ANODE and K/CATHODE
    (discrete LEDs, and the IR-LED half of a reflective sensor). WS2812-class
    parts (VDD/DIN) have no A/K and are skipped."""
    a = k = None
    for p in comp.pins:
        n = p.name.upper().strip()
        if n in {"A", "ANODE", "A1"} and a is None:
            a = p
        elif n in {"K", "CATHODE", "K1"} and k is None:
            k = p
    return a, k


def R46_led_current_limit(project: Project) -> list[VerifierIssue]:
    """A/K parts wired straight across the supply, or an LED with no current
    limiting at all.

    - FORWARD across the supply (anode on a power rail, cathode on ground, no
      resistor on either net): an LED burns; a plain diode is a dead SHORT --
      the classic reverse-polarity-diode-in-parallel mistake (seen live: SS14
      anode VBAT / cathode GND, and a sensor's IR LED anode tied to VCC_3V3
      after its resistor was deleted). Error for both.
    - REVERSE across the supply: a TVS / zener / crowbar diode is normal --
      skipped; an LED that way round never lights -- warning.
    - LED with no resistor on either net and not across the rails: warning
      (may be a constant-current driver; usually a GPIO-direct LED)."""
    issues: list[VerifierIssue] = []
    resistors = _resistor_refdeses(project)
    net_of: dict[tuple[str, str], list] = {}
    for net in project.ir.nets:
        for r, p in net.pins:
            net_of.setdefault((r, p), []).append(net)
    for comp in project.ir.components:
        part = library.get_part(comp.mpn)
        if part is None:
            continue
        # Multi-channel LED (RGB): every colour channel needs its OWN series
        # resistor. Driving an RGB LED straight from three GPIOs is the single
        # most common way to cook one (beta review #7).
        channels = _led_channel_pins(comp) if part.category == "led" else []
        if channels:
            bare = []
            for ch in channels:
                nets = net_of.get((comp.refdes, ch.number), [])
                if not nets:
                    continue  # unwired -- R15/R16 own that
                if not any(r in resistors for n in nets for r, _ in n.pins):
                    bare.append(ch.name)
            if bare:
                issues.append(VerifierIssue(
                    id=f"R46:{comp.refdes}",
                    severity="error",
                    message=(
                        f"{comp.refdes} ({comp.mpn}): channel{'s' if len(bare) > 1 else ''} "
                        f"{', '.join(bare)} ha{'ve' if len(bare) > 1 else 's'} no series "
                        f"resistor — each colour of a multi-channel LED needs its own "
                        f"current-limiting resistor between the driving pin and the LED "
                        f"(sized for that colour's Vf; blue/green need a different value "
                        f"from red)."
                    ),
                    refdes=comp.refdes,
                    rule="R46_led_current_limit", source="verifier",
                ))
            continue
        a, k = _led_pins(comp)
        if a is None or k is None:
            continue
        is_led = part.category == "led" or (
            part.category == "sensor" and "led" in (part.description or "").lower()
            or part.category == "sensor" and "ir" in (part.description or "").lower()
        )
        is_diode = part.category == "diode"
        if not (is_led or is_diode):
            continue
        a_nets = net_of.get((comp.refdes, a.number), [])
        k_nets = net_of.get((comp.refdes, k.number), [])
        if not a_nets or not k_nets:
            continue  # unwired -- R15/R16 own floating pins
        a_cls = {_classify_net(n.name) for n in a_nets}
        k_cls = {_classify_net(n.name) for n in k_nets}
        # A resistor counts as SERIES only on a non-rail (intermediate) node:
        # pull-ups elsewhere on the 3.3 V rail are not in series with an LED
        # whose anode sits directly on that rail (missed live: IR LED anodes on
        # VCC_3V3 next to the sensors' own collector pull-ups).
        has_r = any(
            r in resistors
            for n in a_nets + k_nets if _classify_net(n.name) == "signal"
            for r, _ in n.pins
        )
        forward_across = "power" in a_cls and "gnd" in k_cls
        reverse_across = "power" in k_cls and "gnd" in a_cls
        if forward_across and not has_r:
            what = ("the LED sits straight across the supply and will burn"
                    if is_led else
                    "a forward-biased diode straight across the supply is a dead short; "
                    "a reverse-polarity diode goes in SERIES (BAT+ -> anode, cathode -> "
                    "the VBAT rail) or antiparallel (cathode -> rail, anode -> GND)")
            issues.append(VerifierIssue(
                id=f"R46:{comp.refdes}",
                severity="error",
                message=(
                    f"{comp.refdes} ({comp.mpn}): anode on {a_nets[0].name}, cathode on "
                    f"{k_nets[0].name}, no series resistor -- {what}."
                ),
                refdes=comp.refdes, net=a_nets[0].name,
                rule="R46_led_current_limit", source="verifier",
            ))
        elif is_led and reverse_across:
            issues.append(VerifierIssue(
                id=f"R46:{comp.refdes}",
                severity="warning",
                message=(
                    f"{comp.refdes} ({comp.mpn}): LED is reverse-biased (cathode on "
                    f"{k_nets[0].name}, anode on {a_nets[0].name}) -- it will never light."
                ),
                refdes=comp.refdes, net=a_nets[0].name,
                rule="R46_led_current_limit", source="verifier",
            ))
        elif is_led and not has_r and not reverse_across:
            # An LED between a rail and an MCU GPIO with no resistor is not a
            # "maybe a constant-current driver" case - a GPIO is a voltage
            # source, so the LED takes whatever the pin can give (review #20:
            # four status LEDs 3V3 -> GPIO, 0 errors). Error when one side is a
            # rail/ground and the other carries an MCU/host GPIO.
            mcus = _mcu_or_host_components(project)
            gpio_side = any(
                r in mcus
                for n in (a_nets[0], k_nets[0])
                for r, _ in n.pins
            )
            rail_side = (
                _classify_net(a_nets[0].name) != "signal"
                or _classify_net(k_nets[0].name) != "signal"
            )
            issues.append(VerifierIssue(
                id=f"R46:{comp.refdes}",
                severity="error" if (gpio_side and rail_side) else "warning",
                message=(
                    f"{comp.refdes} ({comp.mpn}): no series resistor on either LED "
                    f"net ({a_nets[0].name} / {k_nets[0].name}) -- "
                    + ("an MCU pin drives it straight from the rail: add a series "
                       "resistor (RC0603FR-07330RL) between the GPIO and the LED."
                       if (gpio_side and rail_side) else
                       "confirm a constant-current driver limits it, else add one.")
                ),
                refdes=comp.refdes, net=a_nets[0].name,
                rule="R46_led_current_limit", source="verifier",
            ))
    return issues


def R47_two_pin_part_shorted(project: Project) -> list[VerifierIssue]:
    """Both pins of a 2-pin part on the SAME net. A resistor/cap/inductor with
    both ends on one node does nothing (seen live: each USB-C CC resistor had
    R.1 and R.2 both on USB_CC1, so neither Rd was actually pulling down); a
    2-pin connector wired that way shorts whatever plugs in (motor, battery).
    Never intentional either way -- error."""
    issues: list[VerifierIssue] = []
    pins_by_ref: dict[str, dict[str, set[str]]] = {}
    for net in project.ir.nets:
        for r, p in net.pins:
            pins_by_ref.setdefault(r, {}).setdefault(net.name, set()).add(p)
    for comp in project.ir.components:
        part = library.get_part(comp.mpn)
        if part is None or len(comp.pins) != 2:
            continue
        if part.category not in {"connector", "passive", "led", "diode", "actuator", "protection", "crystal", "switch"}:
            continue
        for net_name, pins in pins_by_ref.get(comp.refdes, {}).items():
            if len(pins) < 2:
                continue
            if part.category == "connector":
                why = (f"the connector is shorted to itself; whatever plugs in (motor, "
                       f"battery) sees no voltage. Put each pin on its own net (e.g. "
                       f"driver OUT1 -> pin 1, OUT2 -> pin 2)")
            elif part.category == "switch":
                why = (f"a switch with both legs on one node switches nothing (review "
                       f"#20: four override buttons with both pins on GND). One leg to "
                       f"GND, the other to the MCU input with a pull-up")
            else:
                why = (f"both ends sit on one node, so the part has no effect at all "
                       f"(a shorted-out resistor/cap). Move one pin to the net it is "
                       f"meant to reach (e.g. a pull-down's other end to GND)")
            issues.append(VerifierIssue(
                id=f"R47:{comp.refdes}",
                severity="error",
                message=(
                    f"Both pins of {comp.refdes} ({comp.mpn}) are on net {net_name} -- {why}."
                ),
                refdes=comp.refdes, net=net_name,
                rule="R47_two_pin_part_shorted", source="verifier",
            ))
    return issues


# Safe continuous current per GPIO, mA, by pin_safety family. Conservative
# datasheet "recommended" values, not absolute maxima: AVR 20 mA (abs 40),
# ESP32 20 mA (abs 40 source / 28 sink), RP2040 12 mA at default drive,
# Raspberry Pi 16 mA per pin.
_GPIO_SAFE_MA = {"avr328": 20.0, "avr2560": 20.0, "esp32": 20.0,
                 "rp2040": 12.0, "rpi": 16.0}
_GPIO_SAFE_DEFAULT = 20.0

# Categories whose parts are LOADS when wired to a GPIO (they draw the current
# the pin must supply). Sensors/logic are inputs and don't count.
_GPIO_LOAD_CATEGORIES = {"actuator", "led"}


def R49_gpio_drive_current(project: Project) -> list[VerifierIssue]:
    """A load wired straight to an MCU GPIO that draws more than the pin can
    safely supply -- add a transistor. Fires only when the library knows the
    load's current (`currentMaxMa`/`currentTypMa`), the same data-dependency
    rule R31/R41 use, so an uncurated part stays silent rather than guessing.
    The reviewer's ask: 'verify the buzzer's rated current instead of assuming
    direct GPIO drive is safe'."""
    issues: list[VerifierIssue] = []
    mcus = _mcu_or_host_components(project)
    if not mcus:
        return issues
    comps = {c.refdes: c for c in project.ir.components}
    for net in project.ir.nets:
        if _classify_net(net.name) != "signal":
            continue
        gpio_owner: tuple[str, str] | None = None
        for r, pnum in net.pins:
            comp = mcus.get(r)
            if comp is None:
                continue
            pin = next((pp for pp in comp.pins if pp.number == pnum), None)
            if pin is None or pin_safety.gpio_number(pin.name, comp.mpn) is None:
                continue
            gpio_owner = (r, pin.name)
            break
        if gpio_owner is None:
            continue
        for r, pnum in net.pins:
            if r == gpio_owner[0]:
                continue
            comp = comps.get(r)
            part = library.get_part(comp.mpn) if comp else None
            if part is None or part.category not in _GPIO_LOAD_CATEGORIES:
                continue
            # The pin on THIS net must be the load itself (a buzzer's +, an LED
            # anode) — not a logic control input. A driver IC is an actuator
            # whose currentMaxMa is its OUTPUT rating; its AIN/BIN pins draw
            # microamps, so counting those flagged every correct motor board.
            load_pin = next((pp for pp in comp.pins if pp.number == pnum), None)
            if load_pin is None or str(load_pin.type) not in {"power_in", "passive"}:
                continue
            load_ma = part.currentMaxMa or part.currentTypMa
            if not load_ma:
                continue  # uncurated -- stay silent (data-dependency rule)
            mcu_mpn = mcus[gpio_owner[0]].mpn
            fam = pin_safety.family_key(mcu_mpn)
            safe = _GPIO_SAFE_MA.get(fam, _GPIO_SAFE_DEFAULT)
            if load_ma <= safe:
                continue
            issues.append(VerifierIssue(
                id=f"R49:{r}:{net.name}",
                severity="error",
                message=(
                    f"{r} ({part.mpn}) draws up to {load_ma:.0f} mA but sits directly on "
                    f"{gpio_owner[0]}.{gpio_owner[1]} -- a {mcu_mpn} GPIO should source at "
                    f"most ~{safe:.0f} mA. Drive it through a low-side switch instead "
                    f"(MMBT3904 + 1 kOhm base resistor, or an AO3400A logic-level FET): "
                    f"GPIO -> R -> transistor gate/base, load between the rail and the "
                    f"transistor's drain/collector, plus a flyback diode for a coil."
                ),
                refdes=r, net=net.name,
                rule="R49_gpio_drive_current", source="verifier",
            ))
    return issues


# Pin names that control a transistor: BJT base, FET gate.
_TRANSISTOR_CONTROL_NAMES = {"B", "BASE", "G", "GATE", "G1", "G2"}


def R52_transistor_control_floating(project: Project) -> list[VerifierIssue]:
    """A transistor whose base/gate reaches no driver. A low-side switch whose
    base sits on no net (or on a net with nothing but passives) can never turn
    on -- the load is dead and every other rule passes (seen live on the laser
    alarm: the buzzer's MMBT3904 base was unwired while its 1 k base resistor
    bridged two GPIOs). Error; the fix-up loop can wire it."""
    issues: list[VerifierIssue] = []
    comps = {c.refdes: c for c in project.ir.components}
    resistors_all = _resistor_refdeses(project)
    mcu_refs = set(_mcu_or_host_components(project))
    driver_refs = set(mcu_refs)
    for c in project.ir.components:
        part = library.get_part(c.mpn)
        if part is None:
            continue
        # Any IC with an OUTPUT-typed pin can drive a gate: a protection
        # controller's OD/OC (DW01A -> FS8205A), a gate driver, a comparator,
        # a logic gate - not only MCUs and other transistors.
        if part.category == "transistor" or any(p.type == "output" for p in part.pins):
            driver_refs.add(c.refdes)
    for comp in project.ir.components:
        part = library.get_part(comp.mpn)
        if part is None or part.category != "transistor":
            continue
        ctrl = next(
            (p for p in comp.pins if p.name.upper().strip() in _TRANSISTOR_CONTROL_NAMES),
            None,
        )
        if ctrl is None:
            continue
        # Walk one hop: the control pin's net, plus the far side of any 2-pin
        # passive on it (the usual base resistor), must reach a driver or a rail.
        seen_nets: set[str] = set()
        frontier = [n for n in project.ir.nets if (comp.refdes, ctrl.number) in
                    [(r, p) for r, p in n.pins]]
        reached = False
        for _hop in range(2):
            nxt = []
            for net in frontier:
                if net.name in seen_nets:
                    continue
                seen_nets.add(net.name)
                if _classify_net(net.name) != "signal":
                    reached = True  # tied to a rail/ground: a deliberate bias
                    break
                for r, _pin in net.pins:
                    if r in driver_refs and r != comp.refdes:
                        reached = True
                        break
                    other = comps.get(r)
                    if other is None or len(other.pins) != 2:
                        continue
                    opart = library.get_part(other.mpn)
                    if opart is None or opart.category != "passive":
                        continue
                    far = [nn for nn in project.ir.nets
                           if nn.name not in seen_nets
                           and any(rr == r for rr, _ in nn.pins)]
                    nxt.extend(far)
                if reached:
                    break
            if reached:
                break
            frontier = nxt
        if reached:
            # A BJT base wired STRAIGHT to a GPIO (no series resistor) draws
            # whatever the pin can deliver into the base-emitter junction and
            # can damage both. A FET gate is voltage-driven, so gate-direct is
            # fine and is not flagged.
            if ctrl.name.upper().strip() in {"B", "BASE"}:
                base_nets = [n for n in project.ir.nets
                             if (comp.refdes, ctrl.number) in [(r, p) for r, p in n.pins]]
                for bn in base_nets:
                    if _classify_net(bn.name) != "signal":
                        continue
                    if any(r in resistors_all for r, _ in bn.pins):
                        continue
                    on_gpio = any(
                        (r in mcu_refs and (pp := next(
                            (x for x in comps[r].pins if x.number == pnum), None)) is not None
                         and pin_safety.gpio_number(pp.name, comps[r].mpn) is not None)
                        for r, pnum in bn.pins
                    )
                    if not on_gpio:
                        continue
                    issues.append(VerifierIssue(
                        id=f"R52:{comp.refdes}:base_r",
                        severity="error",
                        message=(
                            f"{comp.refdes}.{ctrl.name} is wired straight to an MCU GPIO on "
                            f"{bn.name} with no series resistor — the base-emitter junction "
                            f"will pull far more than the pin can supply. Insert ~1 kOhm "
                            f"(RC0603FR-071KL) between the GPIO and the base."
                        ),
                        refdes=comp.refdes, pinNumber=ctrl.number, net=bn.name,
                        rule="R52_transistor_control_floating", source="verifier",
                    ))
            continue
        issues.append(VerifierIssue(
            id=f"R52:{comp.refdes}",
            severity="error",
            message=(
                f"{comp.refdes} ({comp.mpn}) {ctrl.name} pin reaches no driver — "
                f"the transistor can never switch, so its load is dead. Wire "
                f"{comp.refdes}.{ctrl.name} to an MCU GPIO (through a ~1 kOhm base "
                f"resistor for a BJT), or bias it deliberately to a rail."
            ),
            refdes=comp.refdes, pinNumber=ctrl.number,
            rule="R52_transistor_control_floating", source="verifier",
        ))
    return issues


def R51_five_v_output_into_3v3_gpio(project: Project) -> list[VerifierIssue]:
    """A peripheral running on 5 V driving a 3.3 V-only MCU input directly.
    The classic HC-SR04 ECHO -> ESP32 mistake (beta review #6): the module is
    powered at 5 V so its push-pull output swings to 5 V, which is over the
    absolute maximum of an ESP32/RP2040/Pi GPIO. Fires when the part's power
    pin sits on a >=4.5 V rail (or its curated voltageMin says 5 V) AND the
    net carries no divider/level-shifter resistor. Error; the fix is a divider
    or a level shifter.

    Division of labour: R36 owns a GPIO tied to a 5 V RAIL, R37 owns a 5 V-logic
    MCU driving a 3.3 V part; this is the third direction (5 V peripheral ->
    3.3 V MCU input)."""
    issues: list[VerifierIssue] = []
    mcus = _mcu_or_host_components(project)
    if not mcus:
        return issues
    resistors = _resistor_refdeses(project)
    net_names: dict[str, list[str]] = {}
    for net in project.ir.nets:
        for r, p in net.pins:
            net_names.setdefault(r, []).append(net.name)

    def _supply_is_5v(comp: Component, part) -> bool:
        if (part.voltageMin or 0) >= 4.5:
            return True
        for pin in comp.pins:
            if str(pin.type) != "power_in":
                continue
            for net in project.ir.nets:
                if (comp.refdes, pin.number) not in [(r, p) for r, p in net.pins]:
                    continue
                u = net.name.upper()
                if "5V" in u or u.startswith(("VBUS", "+5")):
                    return True
        return False

    for net in project.ir.nets:
        if _classify_net(net.name) != "signal":
            continue
        # the 3.3 V-only MCU input on this net
        victim = None
        for r, pnum in net.pins:
            comp = mcus.get(r)
            if comp is None:
                continue
            if pin_safety.five_v_tolerant(comp.mpn) is not False:
                continue
            pin = next((pp for pp in comp.pins if pp.number == pnum), None)
            if pin is None or pin_safety.gpio_number(pin.name, comp.mpn) is None:
                continue
            victim = (r, pin.name, comp.mpn)
            break
        if victim is None:
            continue
        if any(r in resistors for r, _ in net.pins):
            continue  # a divider / series resistor is present -- trust it
        for r, pnum in net.pins:
            if r == victim[0]:
                continue
            comp = next((c for c in project.ir.components if c.refdes == r), None)
            part = library.get_part(comp.mpn) if comp else None
            if part is None or comp is None:
                continue
            pin = next((pp for pp in comp.pins if pp.number == pnum), None)
            if pin is None or str(pin.type) not in {"output", "bidirectional"}:
                continue
            if not _supply_is_5v(comp, part):
                continue
            issues.append(VerifierIssue(
                id=f"R51:{r}:{net.name}",
                severity="error",
                message=(
                    f"{r} ({part.mpn}) runs at 5 V and drives {net.name} straight into "
                    f"{victim[0]}.{victim[1]} — {victim[2]} GPIOs are 3.3 V-only, so a "
                    f"5 V output exceeds their absolute maximum. Put a divider on this "
                    f"net (e.g. 10 kOhm from the 5 V output to the GPIO node and 20 kOhm "
                    f"from that node to GND) or a level shifter (TXS0108EPWR / BSS138)."
                ),
                refdes=r, net=net.name,
                rule="R51_five_v_output_into_3v3_gpio", source="verifier",
            ))
    return issues


def R50_redundant_power_stage(project: Project) -> list[VerifierIssue]:
    """A dev-board module that already carries its own USB input and regulator
    (Arduino Nano/Uno/Mega, ESP32 dev boards -- library flag `onboardPower`),
    plus a second USB connector / regulator on the same design. That is the
    "generic dev board" over-engineering every beta review complained about:
    the module is powered through its own USB or VIN pin, so the extra power
    tree is parts, board area and back-powering risk for nothing. Warning, not
    an error -- a board may deliberately regulate a separate rail."""
    issues: list[VerifierIssue] = []
    modules = [
        c for c in project.ir.components
        if (part := library.get_part(c.mpn)) and getattr(part, "onboardPower", False)
    ]
    if not modules:
        return issues
    extras: list[str] = []
    for c in project.ir.components:
        part = library.get_part(c.mpn)
        if part is None:
            continue
        if part.category == "regulator":
            extras.append(f"{c.refdes} ({part.mpn}, regulator)")
        elif part.category == "connector" and "usb" in (part.mpn + part.description).lower():
            extras.append(f"{c.refdes} ({part.mpn}, USB input)")
    if not extras:
        return issues
    mod = modules[0]
    issues.append(VerifierIssue(
        id=f"R50:{mod.refdes}",
        severity="warning",
        message=(
            f"{mod.refdes} ({mod.mpn}) is a module with its own USB input and "
            f"on-board regulator, but the design also has {', '.join(extras[:4])} — "
            f"power the board through the module (its USB or VIN pin) and drop the "
            f"extra power stage unless a separate rail is genuinely needed."
        ),
        refdes=mod.refdes,
        rule="R50_redundant_power_stage", source="verifier",
    ))
    return issues


# Bare MCU chips that cannot run without off-chip parts the agent tends to skip
# (water-level review: a bare RP2040 + LDO + USB-C, no flash, no crystal - it
# would never boot). mpn -> (needs_flash, needs_crystal).
_BARE_MCU_SUPPORT = {
    "RP2040": (True, True),
    "RP2350": (True, True),
    "ESP8266EX": (True, True),
    "ESP32-S2": (True, True),
}


def R53_bare_mcu_support(project: Project) -> list[VerifierIssue]:
    """A bare RP2040/RP2350/ESP8266EX/ESP32-S2 die has no program memory and no
    clock: without a QSPI flash and a crystal the board is unbootable, yet every
    net-level rule passes. Error per missing part; the fix hint points the agent
    at the module (Pico / WROOM) for hobby prompts."""
    issues: list[VerifierIssue] = []
    has_flash = False
    has_crystal = False
    for c in project.ir.components:
        part = library.get_part(c.mpn)
        if part is None:
            continue
        d = (part.description or "").lower()
        if part.category == "memory" and (
            "flash" in d or "qspi" in d
            or c.mpn.upper().startswith(("W25Q", "MX25", "GD25", "IS25"))
        ):
            has_flash = True
        if part.category == "crystal" and "32.768" not in c.mpn and "32.768" not in d:
            has_crystal = True
    for c in project.ir.components:
        need = _BARE_MCU_SUPPORT.get(c.mpn.upper())
        if not need:
            continue
        need_flash, need_xtal = need
        if need_flash and not has_flash:
            issues.append(VerifierIssue(
                id=f"R53:{c.refdes}:flash", severity="error", refdes=c.refdes,
                message=(f"{c.refdes} ({c.mpn}) is a bare chip with NO internal flash - without a "
                         f"QSPI flash (e.g. W25Q128JVSIQ on QSPI_SD0-3/SCLK/SS) it can never boot. "
                         f"Add the flash, or use the module (RaspberryPi_Pico / ESP32-WROOM-32) instead."),
                rule="R53_bare_mcu_support", source="verifier"))
        if need_xtal and not has_crystal:
            issues.append(VerifierIssue(
                id=f"R53:{c.refdes}:crystal", severity="error", refdes=c.refdes,
                message=(f"{c.refdes} ({c.mpn}) needs an external crystal (12 MHz for RP2040: "
                         f"Q_12MHz_3225 on XIN/XOUT with load caps) - none on the board."),
                rule="R53_bare_mcu_support", source="verifier"))
    return issues


def R54_connector_input_no_return(project: Project) -> list[VerifierIssue]:
    """An MCU input fed straight from an external connector that has no ground
    pin at all (soil-moisture review: J1 = {5 V, A0}; a 2-wire probe between
    5 V and an ADC pin has no return path and A0 floats). Error. Also warns when
    the connector does have GND but the input net carries nothing else and the
    pin is analog: a bare resistive probe still needs a fixed resistor (divider)
    or a sensor MODULE with its own output."""
    issues: list[VerifierIssue] = []
    comps = {c.refdes: c for c in project.ir.components}
    mcus = _mcu_or_host_components(project)
    cat: dict[str, str] = {}
    for c in project.ir.components:
        part = library.get_part(c.mpn)
        cat[c.refdes] = part.category if part else ""
    ground_refs: set[str] = set()
    for net in project.ir.nets:
        if _classify_net(net.name) == "gnd":
            ground_refs.update(r for r, _ in net.pins)
    seen: set[tuple[str, str]] = set()
    for net in project.ir.nets:
        if _classify_net(net.name) != "signal":
            continue
        refs = {r for r, _ in net.pins}
        conns = [r for r in refs if cat.get(r) == "connector"]
        mcu_pins = [(r, p) for r, p in net.pins if r in mcus]
        if not conns or not mcu_pins:
            continue
        # Nothing but the connector(s) and the MCU on this net.
        if any(r not in mcus and cat.get(r) != "connector" for r in refs):
            continue
        for jref in conns:
            j = comps.get(jref)
            if j is None or len(j.pins) > 6:
                continue  # big headers are breakouts, not a sensor cable
            for mref, mpin in mcu_pins:
                mc = comps[mref]
                pin_obj = next((pp for pp in mc.pins if pp.number == mpin), None)
                if pin_obj is None or pin_obj.type not in ("input", "bidirectional"):
                    continue
                key = (jref, mref)
                if key in seen:
                    continue
                seen.add(key)
                if jref not in ground_refs:
                    issues.append(VerifierIssue(
                        id=f"R54:{jref}:{net.name}", severity="error", refdes=jref, net=net.name,
                        message=(f"{jref} feeds {mref}.{pin_obj.name} on {net.name} but has NO ground pin - "
                                 f"whatever plugs in shares no ground reference with this board (a probe has "
                                 f"no return path and the input floats). A probe/sensor "
                                 f"cable needs GND on the connector; a bare resistive probe also needs a fixed "
                                 f"resistor from {net.name} to GND (divider), or use a sensor MODULE "
                                 f"(e.g. Soil_Moisture_Capacitive_v1.2 / Water_Level_Sensor_Module) whose "
                                 f"3-pin header carries VCC/GND/OUT."),
                        rule="R54_connector_input_no_return", source="verifier"))
                elif re.search(r"(^A\d+$|ADC|AIN|AOUT)", pin_obj.name.upper()):
                    issues.append(VerifierIssue(
                        id=f"R54:{jref}:{net.name}:divider", severity="warning", refdes=jref, net=net.name,
                        message=(f"{jref} drives analog input {mref}.{pin_obj.name} with nothing else on "
                                 f"{net.name}. Fine for a sensor MODULE with its own output; a bare "
                                 f"2-electrode probe needs a fixed resistor from {net.name} to GND to form "
                                 f"a divider, or it reads nothing."),
                        rule="R54_connector_input_no_return", source="verifier"))
    return issues


# Rails a battery / motor supply lives on — anything measured from one of these
# through a divider can be far above the MCU's analog input ceiling.
_HIGH_RAIL_PREFIXES = ("VBAT", "VBATT", "VIN", "VM", "VMOT", "VMOTOR", "VSYS", "VSUPPLY", "VS")
# Worst case assumed for a rail we cannot pin down: a 3S pack / 12 V robot
# supply. Stated in the message so the number is never silent.
_ASSUMED_RAIL_MAX_V = 12.0


def _adc_ceiling(mpn: str) -> float:
    """Highest voltage an MCU's analog input may see: its own supply maximum."""
    part = library.get_part(mpn)
    if part and part.voltageMax:
        return float(part.voltageMax)
    return 3.6


def _resistor_ohms(comp) -> float | None:
    part = library.get_part(comp.mpn)
    src = f"{comp.value or ''} {(part.description if part else '')} {comp.mpn}"
    m = re.search(r"(\d+(?:\.\d+)?)\s*([kKmM]?)\s*(?:R|Ohm|Ω|ohm)?", src)
    if not m:
        return None
    val = float(m.group(1))
    mult = {"k": 1e3, "K": 1e3, "m": 1e6, "M": 1e6, "": 1.0}[m.group(2)]
    return val * mult


def R55_adc_divider_overvoltage(project: Project) -> list[VerifierIssue]:
    """A battery-sense divider that does not attenuate enough. Review #13: a
    100k/100k pair (a 2:1 divider) took a motor battery rail straight into an
    ESP32 ADC pin — at anything above ~7 V that pin is over its absolute
    maximum, and every other rule passed. Structural: find the mid-node that
    carries an MCU analog pin plus two resistors, one reaching a battery/motor
    rail and one reaching ground, then check the worst case."""
    issues: list[VerifierIssue] = []
    comps = {c.refdes: c for c in project.ir.components}
    resistors = _resistor_refdeses(project)
    mcus = _mcu_or_host_components(project)

    def _nets_of(refdes: str, skip: str) -> list[str]:
        out = []
        for net in project.ir.nets:
            if net.name == skip:
                continue
            if any(r == refdes for r, _ in net.pins):
                out.append(net.name)
        return out

    for net in project.ir.nets:
        if _classify_net(net.name) != "signal":
            continue
        adc_pins = []
        for r, pnum in net.pins:
            comp = mcus.get(r)
            if comp is None:
                continue
            pin = next((pp for pp in comp.pins if pp.number == pnum), None)
            if pin and re.search(r"(^A\d+$|ADC|IO3[2-9]|IO2[5-7]|SENSOR_V)", pin.name.upper()):
                adc_pins.append((comp, pin))
        if not adc_pins:
            continue
        top = bottom = None
        for r, _ in net.pins:
            if r not in resistors:
                continue
            for other in _nets_of(r, net.name):
                cls = _classify_net(other)
                if cls == "gnd":
                    bottom = r
                elif any(other.upper().startswith(pfx) for pfx in _HIGH_RAIL_PREFIXES):
                    top = (r, other)
        if not top or not bottom:
            continue
        r_top = _resistor_ohms(comps[top[0]])
        r_bot = _resistor_ohms(comps[bottom])
        if not r_top or not r_bot:
            continue
        ratio = r_bot / (r_top + r_bot)
        for comp, pin in adc_pins:
            ceiling = _adc_ceiling(comp.mpn)
            worst = ratio * _ASSUMED_RAIL_MAX_V
            if worst <= ceiling:
                continue
            issues.append(
                VerifierIssue(
                    id=f"R55:{comp.refdes}:{pin.number}",
                    severity="error",
                    refdes=comp.refdes,
                    net=net.name,
                    pinNumber=pin.number,
                    message=(
                        f"Battery divider on {net.name} attenuates {top[1]} by only "
                        f"{ratio:.2f} — at a {_ASSUMED_RAIL_MAX_V:.0f} V worst case "
                        f"{comp.refdes}.{pin.name} would see {worst:.1f} V, over its "
                        f"{ceiling:.1f} V limit. Size the divider from the battery's "
                        f"MAXIMUM voltage (a 2S LiPo is 8.4 V, 3S is 12.6 V): raise the "
                        f"top resistor (e.g. 100k top / 22k bottom) and state the "
                        f"assumed pack voltage."
                    ),
                    rule="R55_adc_divider_overvoltage",
                    source="verifier",
                )
            )
    return issues




def R56_i2c_mode_straps(project: Project) -> list[VerifierIssue]:
    """An I2C sensor whose mode straps contradict the bus it is wired to.
    Review #14: the BME280's SDA/SCL were on the ESP32's I2C bus, but CSB was
    tied to GND - which selects SPI, so the sensor would never answer - and SDO
    (the address pin) was left floating. Both passed with 0 errors."""
    issues: list[VerifierIssue] = []
    i2c_refs: set[str] = set()
    mcus = _mcu_or_host_components(project)
    for net in project.ir.nets:
        names = set()
        has_mcu = any(r in mcus for r, _ in net.pins)
        for r, pnum in net.pins:
            comp = next((c for c in project.ir.components if c.refdes == r), None)
            if comp is None:
                continue
            pin = next((pp for pp in comp.pins if pp.number == pnum), None)
            if pin and re.search(r"\b(SDA|SCL)\b|SDI/SDA|SCK/SCL", pin.name.upper()):
                names.add(r)
        if names and (has_mcu or len(names) > 1):
            i2c_refs |= names

    for comp in project.ir.components:
        if comp.refdes not in i2c_refs:
            continue
        part = library.get_part(comp.mpn)
        if part is None or part.category not in {"sensor", "display", "memory", "interface"}:
            continue
        for pin in comp.pins:
            upper = pin.name.upper().strip()
            net = _net_for_pin(project, comp.refdes, pin.number)
            if upper in _MODE_SELECT_NAMES:
                if net is None:
                    issues.append(VerifierIssue(
                        id=f"R56:{comp.refdes}:{pin.number}", severity="error",
                        refdes=comp.refdes, pinNumber=pin.number,
                        message=(
                            f"{comp.refdes} ({comp.mpn}) is wired to an I2C bus but its "
                            f"{pin.name} mode pin is floating. Tie {pin.name} HIGH (to the "
                            f"part's supply rail) to select I2C."),
                        rule="R56_i2c_mode_straps", source="verifier"))
                elif _classify_net(net) == "gnd":
                    issues.append(VerifierIssue(
                        id=f"R56:{comp.refdes}:{pin.number}", severity="error",
                        refdes=comp.refdes, pinNumber=pin.number, net=net,
                        message=(
                            f"{comp.refdes} ({comp.mpn}) has SDA/SCL on an I2C bus but "
                            f"{pin.name} is tied to {net} — that selects SPI mode, so the "
                            f"part will never answer on I2C. Tie {pin.name} HIGH to the "
                            f"part's supply rail instead."),
                        rule="R56_i2c_mode_straps", source="verifier"))
            elif upper in _ADDRESS_SELECT_NAMES and net is None:
                issues.append(VerifierIssue(
                    id=f"R56:{comp.refdes}:{pin.number}", severity="warning",
                    refdes=comp.refdes, pinNumber=pin.number,
                    message=(
                        f"{comp.refdes} ({comp.mpn}) address pin {pin.name} is floating — "
                        f"the I2C address is undefined. Tie it to GND or the supply rail "
                        f"and state the resulting address in the summary."),
                    rule="R56_i2c_mode_straps", source="verifier"))
    return issues


def R57_form_factor(project: Project) -> list[VerifierIssue]:
    """The built board must match the user's recorded form-factor ask
    (`set_form_factor` — the physical sibling of R43). A watch that ships as a
    rectangle, or a requested 42 mm disc that had to grow to 55 mm to fit the
    parts, is REPORTED, never silent. Schematic-only projects (no board yet)
    are skipped — the rule speaks after auto-place."""
    spec = getattr(project.ir, "form_factor", None)
    board = project.board
    if spec is None or board is None or not board.components:
        return []
    issues: list[VerifierIssue] = []
    has_grid = any(a.grid for a in getattr(project.ir, "arrays", []))
    if board.shape != spec.shape:
        if has_grid and spec.shape == "round":
            issues.append(VerifierIssue(
                id="R57:shape", severity="warning",
                message=(
                    "The board is rectangular although a round board was "
                    "requested: a physical part grid (LED matrix / keypad) "
                    "drives the outline. Remove the grid constraint or accept "
                    "the rectangle."),
                rule="R57_form_factor", source="verifier"))
        else:
            issues.append(VerifierIssue(
                id="R57:shape", severity="error",
                message=(
                    f"The user asked for a {spec.shape} board but the built "
                    f"board is {board.shape} — re-run auto-place (the placer "
                    f"honours the recorded form factor)."),
                rule="R57_form_factor", source="verifier"))
        return issues

    def _over(built: float, asked: float | None) -> bool:
        return asked is not None and built > asked + 0.5

    def _sev(built: float, asked: float | None) -> str:
        # A modest overrun (parts slightly denser than hoped) is a warning; a
        # board 25 %+ past the ask is not the product that was requested — the
        # sweep's "30 mm ceiling puck" came out Ø55 (+83 %) as a mere warning
        # and the senior read called it "a hard mechanical violation, alone a
        # no-go". Error above 25 % so it blocks `ready` and reaches the chat.
        return "error" if (asked and built > asked * 1.25) else "warning"

    if spec.shape == "polygon":
        # A polygon is asked for by its SHAPE, so the shape is what must be
        # checked — a board that came back as a rectangle-with-points, or a
        # hexagon where a triangle was asked for, is not the product requested.
        built = [(pt.x, pt.y) for pt in (board.outline_points or [])]
        if len(built) < 3:
            issues.append(VerifierIssue(
                id="R57:outline", severity="error",
                message=(
                    "A polygon board was requested but the built board carries "
                    "no outline — re-run auto-place (the placer honours the "
                    "recorded form factor)."),
                rule="R57_form_factor", source="verifier"))
        else:
            if spec.sides and len(built) != spec.sides:
                issues.append(VerifierIssue(
                    id="R57:sides", severity="error",
                    message=(
                        f"Requested a {spec.sides}-sided board but the built "
                        f"outline has {len(built)} sides."),
                    rule="R57_form_factor", source="verifier"))
            span = max(board.width, board.height)
            if _over(span, spec.diameter_mm):
                issues.append(VerifierIssue(
                    id="R57:size", severity=_sev(span, spec.diameter_mm),
                    message=(
                        f"Requested a Ø{spec.diameter_mm:g} mm polygon board, but "
                        f"the parts physically need {span:.1f} mm across — drop "
                        f"or shrink parts to hit the requested size, or accept "
                        f"{span:.1f} mm."),
                    rule="R57_form_factor", source="verifier"))
    elif spec.shape == "round":
        if _over(board.width, spec.diameter_mm):
            issues.append(VerifierIssue(
                id="R57:size", severity=_sev(board.width, spec.diameter_mm),
                message=(
                    f"Requested a Ø{spec.diameter_mm:g} mm round board, but the "
                    f"parts physically need Ø{board.width:.1f} mm — drop or "
                    f"shrink parts to hit the requested size, or accept "
                    f"Ø{board.width:.1f} mm."),
                rule="R57_form_factor", source="verifier"))
    else:
        if _over(board.width, spec.width_mm) or _over(board.height, spec.height_mm):
            asked = (
                f"{spec.width_mm:g}x{spec.height_mm:g}"
                if spec.width_mm and spec.height_mm
                else f"{(spec.width_mm or spec.height_mm):g}"
            )
            worst = max(
                (board.width / spec.width_mm) if spec.width_mm else 1.0,
                (board.height / spec.height_mm) if spec.height_mm else 1.0,
            )
            issues.append(VerifierIssue(
                id="R57:size", severity="error" if worst > 1.25 else "warning",
                message=(
                    f"Requested a {asked} mm board, but the parts physically "
                    f"need {board.width:.1f}x{board.height:.1f} mm — drop or "
                    f"shrink parts to hit the requested size, or accept the "
                    f"larger board."),
                rule="R57_form_factor", source="verifier"))
    return issues


# ---------------------------------------------------------------------------
# Reviews #19-#23 (2026-08-18: drone PDB / WiFi relay board / solar charger /
# wearable HR / CNC stepper). Five boards shipped with 0-1 errors while being
# functionally dead: linear regulators fed from 16.8 V and from 4 V, an
# adjustable regulator with ADJ on ground, relay contacts and buttons that
# touched only GND, dividers wired to nothing, a protection IC with no FETs,
# a low-side FET whose source went nowhere. Each class below is deterministic
# and data-driven; each is silent when the data it needs is absent.
# ---------------------------------------------------------------------------

_LINEAR_DROPOUT_V = 2.0        # LM78xx/LM317-class headroom (LDOs carry theirs in voltageMin)
_LINEAR_HIGH_DROP_V = 8.0      # >= this drop from a battery/input rail => a buck is the topology
_LINEAR_WARN_DROP_V = 5.0


def _rail_source_voltage(
    project: Project, net_name: str, comps: dict[str, Component],
    parts: dict[str, LibraryPart | None],
) -> float | None:
    """What a rail actually CARRIES, for regulator-input judgements: the net
    name / declared input voltage; else the fixed output of a regulator whose
    power_out feeds it; else a single-cell charger/protection output (VSTOR,
    VBAT of a BQ25504/TP4056/DW01A => 4.2 V max); else a battery holder's cell.
    Never the consumers' ratings - `_net_working_voltage`'s fallback picks the
    largest voltageMax on the net, and an LM7805's own 20 V input rating made
    its 4 V VSTOR input read as 20 V (review #21)."""
    if _is_gnd_net(net_name):
        return 0.0
    up = net_name.upper()
    if up.startswith(_INPUT_RAIL_PREFIXES) and not _VOLT_RE.search(up):
        declared = _declared_input_voltage(project)
        if declared is not None:
            return declared
    named = _parse_rail_voltage(net_name)
    if named is not None and not up.startswith("VBAT"):
        return named
    net = next((n for n in project.ir.nets if n.name == net_name), None)
    if net is None:
        return named
    for r, pn in net.pins:
        part = parts.get(r)
        if part is None:
            continue
        pin = next((p for p in part.pins if p.number == pn), None)
        if pin is None or pin.type != "power_out":
            continue
        if part.category == "regulator":
            v = _regulator_out_voltage(part)
            if v is not None:
                return v
        if part.category == "charger" or _PROTECTION_IC_RE.search(part.mpn):
            return _LI_CELL_MAX_V
    for r, _pn in net.pins:
        part = parts.get(r)
        if part is None:
            continue
        blob = f"{part.mpn} {part.description}".upper()
        if "18650" in blob or "LIPO" in blob or "LI-ION" in blob or "LI-PO" in blob:
            return _LI_CELL_MAX_V
        if "CR2032" in blob or "CR2016" in blob or "CR1220" in blob or "COIN" in blob:
            return 3.0
    return named


def _regulator_out_voltage(part: LibraryPart | None) -> float | None:
    """Fixed output voltage from the MPN/description (AMS1117-3.3, LM7805,
    'LDO 3.3V', 'buck 5V'). None for adjustable/unknown."""
    if part is None:
        return None
    blob = f"{part.mpn} {part.description}"
    m = re.search(r"78(\d\d)\b", part.mpn.upper())
    if m:
        return float(int(m.group(1)))
    m = re.search(r"(?<![\d.])(\d{1,2}(?:\.\d)?)\s*V\b(?!\s*(?:in|input|max))", blob, re.IGNORECASE)
    if m:
        v = float(m.group(1))
        if 0.8 <= v <= 24:
            return v
    m = re.search(r"-(\d)[.V](\d)\b", part.mpn.upper())  # AMS1117-3.3 / -1.8 / MCP1700-3302
    if m:
        return float(f"{m.group(1)}.{m.group(2)}")
    return None


def _is_adjustable_regulator(part: LibraryPart | None) -> bool:
    if part is None or part.category != "regulator":
        return False
    names = {p.name.upper().strip() for p in part.pins}
    return bool(names & {"ADJ", "FB", "FEEDBACK", "VADJ", "ADJUST", "VFB"})


def up_is_input_rail(net_name: str) -> bool:
    """True for a raw INPUT rail (battery / USB / barrel jack), whose voltage
    is a range rather than a regulated point."""
    return net_name.upper().startswith(_INPUT_RAIL_PREFIXES)


def R58_regulator_input_range(project: Project) -> list[VerifierIssue]:
    """The regulator's INPUT rail vs what the part can regulate from.
    (a) Input BELOW output + dropout: an LM7805 on a 1S Li-ion (<=4.2 V) can
        never make 5 V - it needs a boost/buck-boost. Error.
    (b) A LINEAR regulator dropping >= 8 V from a battery/input rail (LM7805
        and LM317 on a 4S 16.8 V pack: ~6-12 W of heat at FC currents) - the
        topology is wrong regardless of exact current. Error; 5-8 V warning.
    Uses the declared input voltage (record_requirements '4S LiPo' / '12V
    input') and is silent when neither Vin nor Vout can be inferred."""
    issues: list[VerifierIssue] = []
    comps = {c.refdes: c for c in project.ir.components}
    parts = {c.refdes: library.get_part(c.mpn) for c in project.ir.components}
    for comp in project.ir.components:
        part = parts[comp.refdes]
        if part is None or part.category != "regulator":
            continue
        in_net = _regulator_input_net(project, comp, part)
        if not in_net:
            continue
        vin = _rail_source_voltage(project, in_net, comps, parts)
        if vin is None or vin <= 0:
            continue
        vout = _regulator_out_voltage(part)
        blob = (part.description or "").lower()
        is_boost = any(k in blob for k in ("boost", "step-up", "buck-boost", "buck/boost"))
        linear = _is_linear_regulator(part)
        # (a) input too low - any non-boost topology. The library's voltageMin
        # on a regulator IS its minimum input (LM7805 7.5, NCP1117 4.75,
        # MCP1700 2.3) - the datasheet number, not a generic dropout guess.
        if vout is not None and not is_boost:
            if part.voltageMin is not None and part.voltageMin > vout:
                need = part.voltageMin
            else:
                # A modern LDO (AP2112 / RT9013 / MCP1700 / TLV700) drops
                # 0.2-0.3 V at load - 3.3 V from a nominal 3.7 V LiPo is
                # standard practice. Legacy 1117-class parts carry their real
                # 4.5-4.75 V minimum in voltageMin and are caught above.
                is_ldo = "ldo" in blob or "low-dropout" in blob or "low dropout" in blob
                need = vout + (0.3 if (is_ldo or not linear) else _LINEAR_DROPOUT_V)
            # The rail's LOW end decides how much of the battery is actually
            # usable. Only meaningful when the part DOES regulate at full
            # charge — otherwise the (a) error below is the right message.
            vin_lo, vin_hi = _declared_input_range(project)
            works_at_full = vin + 0.05 >= need
            if (
                works_at_full
                and vin_lo is not None
                and vin_hi is not None
                and vin_hi > vin_lo
                and vin_lo + 0.05 < need
                and up_is_input_rail(in_net)
            ):
                # Fraction of the declared input range the design can still use.
                usable = max(0.0, (vin_hi - need) / (vin_hi - vin_lo))
                sev = "error" if usable < _DROPOUT_USABLE_ERROR else "warning"
                pct = int(round(usable * 100))
                issues.append(VerifierIssue(
                    id=f"R58:{comp.refdes}:dropout",
                    severity=sev,
                    message=(
                        f"{comp.refdes} ({comp.mpn}) makes {vout:g} V but needs at least "
                        f"{need:g} V in, and {in_net} runs {vin_lo:g}–{vin_hi:g} V: it drops out "
                        f"at {need:g} V, so only about {pct}% of the input range is usable and "
                        f"the board stops with charge left in the cell. Use a buck-boost "
                        f"(TPS63070) or a boost (TPS61023 / TPS61090) if the design must run to "
                        f"an empty cell."
                    ),
                    refdes=comp.refdes, net=in_net,
                    rule="R58_regulator_input_range", source="verifier",
                ))
            if vin + 0.05 < need:
                issues.append(VerifierIssue(
                    id=f"R58:{comp.refdes}:low",
                    severity="error",
                    message=(
                        f"{comp.refdes} ({comp.mpn}) makes {vout:g} V but its input "
                        f"rail {in_net} is only ~{vin:g} V — it needs ≥{need:g} V and "
                        f"cannot regulate. Use a boost converter (TPS61023 / TPS61090 / "
                        f"MT3608_MOD) or a buck-boost (TPS63070) from this source."
                    ),
                    refdes=comp.refdes, net=in_net,
                    rule="R58_regulator_input_range", source="verifier",
                ))
                continue
        # (b) linear from a high input rail: wrong topology
        if linear and vout is not None and vin - vout >= _LINEAR_WARN_DROP_V:
            drop = vin - vout
            sev = "error" if drop >= _LINEAR_HIGH_DROP_V else "warning"
            issues.append(VerifierIssue(
                id=f"R58:{comp.refdes}:drop",
                severity=sev,
                message=(
                    f"{comp.refdes} ({comp.mpn}) is a LINEAR regulator dropping "
                    f"{vin:g} V → {vout:g} V ({drop:g} V): every amp of load burns "
                    f"{drop:g} W as heat. Use a switching buck (MP1584EN / LM2596 / "
                    f"TPS54302 / TPS5430DDA) for this rail; keep an LDO only for a "
                    f"low-current final stage."
                ),
                refdes=comp.refdes, net=in_net,
                rule="R58_regulator_input_range", source="verifier",
            ))
    return issues


def R59_adjustable_regulator_feedback(project: Project) -> list[VerifierIssue]:
    """An adjustable regulator's ADJ/FB pin sets the output through a resistor
    divider from VOUT. ADJ tied straight to ground = the output is Vref
    (1.25 V), not the "9 V" the rail is named (review #19's LM317T); floating =
    undefined. Error: the pin must reach a resistor whose other end is on the
    regulator's output net, and a resistor to ground."""
    issues: list[VerifierIssue] = []
    resistors = _resistor_refdeses(project)
    net_by_name = {n.name: n for n in project.ir.nets}
    for comp in project.ir.components:
        part = library.get_part(comp.mpn)
        if not _is_adjustable_regulator(part):
            continue
        adj = next((p for p in part.pins if p.name.upper().strip() in
                    {"ADJ", "FB", "FEEDBACK", "VADJ", "ADJUST", "VFB"}), None)
        if adj is None:
            continue
        adj_net = _net_for_pin(project, comp.refdes, adj.number)
        outs = _regulator_output_nets(project, comp, part)
        in_net = _regulator_input_net(project, comp, part)
        # Some adjustable parts have an internal divider option (fixed
        # variants share the pin table); only judge parts whose output is not
        # a fixed value in the MPN.
        if _regulator_out_voltage(part) is not None and "adj" not in part.mpn.lower() and "317" not in part.mpn:
            continue
        problem: str | None = None
        if adj_net is None:
            problem = "is floating"
        elif _is_gnd_net(adj_net):
            problem = f"is tied straight to {adj_net}"
        else:
            net = net_by_name.get(adj_net)
            others = [(r, p) for r, p in (net.pins if net else []) if r != comp.refdes]
            r_to_out = False
            r_to_gnd = False
            for r, _p in others:
                if r not in resistors:
                    continue
                for n2 in project.ir.nets:
                    if n2.name == adj_net or not any(rr == r for rr, _ in n2.pins):
                        continue
                    if _is_gnd_net(n2.name):
                        r_to_gnd = True
                    elif n2.name in outs:
                        r_to_out = True
                    elif not outs and n2.name != in_net:
                        # A buck (MP1584EN / LM2596) has no power_out pin - its
                        # output is the SW node after the inductor. The top
                        # resistor's far end is the output by construction.
                        r_to_out = True
            if not (r_to_out and r_to_gnd):
                problem = (f"on {adj_net} has no feedback divider (needs one resistor "
                           f"to the output and one to ground)")
        if problem:
            issues.append(VerifierIssue(
                id=f"R59:{comp.refdes}",
                severity="error",
                message=(
                    f"{comp.refdes} ({comp.mpn}) is an ADJUSTABLE regulator but its "
                    f"{adj.name} pin {problem} — the output is undefined/1.25 V, not "
                    f"the rail voltage the design assumes. Add R1 (VOUT→{adj.name}) "
                    f"and R2 ({adj.name}→GND): Vout = 1.25·(1 + R2/R1)."
                ),
                refdes=comp.refdes, pinNumber=adj.number, net=adj_net,
                rule="R59_adjustable_regulator_feedback", source="verifier",
            ))
    return issues


def _wired_pins(project: Project) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for net in project.ir.nets:
        for r, p in net.pins:
            out.setdefault(r, set()).add(p)
    return out


_NC_NAME_RE = re.compile(r"^(N\.?C\.?|NC\d*|DNC|RESERVED|RSVD|EP|PAD|TAB|SHIELD|SH\d*|MP\d*|MH\d*|NU)$", re.IGNORECASE)


def R60_function_pins_unwired(project: Project) -> list[VerifierIssue]:
    """A part whose FUNCTION pins are all unwired does nothing: a relay with
    its coil on 5 V but COM/NO/NC in the air, a push button with one leg on
    GND and the other on nothing, a 3-pin output terminal touching only GND
    (review #20: four relays, four buttons, three terminals — 0 errors, since
    each had *some* pin on GND so R15 was satisfied). Function pins = every
    pin that is not power/ground/NC. Error when none of them is wired;
    warning when fewer than half are (a placed-but-decorative sensor)."""
    issues: list[VerifierIssue] = []
    wired = _wired_pins(project)
    for comp in project.ir.components:
        part = library.get_part(comp.mpn)
        # A regulator's function IS its power pins (a floating EN is R03's
        # finding, an unwired FB is R59's); a bare 2-pin passive is R15's.
        if part is None or part.category in {"mechanical", "passive", "regulator"}:
            continue
        pins = comp.pins or part.pins
        if len(pins) < 2:
            continue
        w = wired.get(comp.refdes, set())
        names = {p.name.upper().strip() for p in pins}
        # Relay / switch CONTACTS (COM/NO/NC) are the part's whole purpose. On
        # a relay "NC" is a contact, not a no-connect - judge contacts first.
        contact_re = re.compile(r"^(COM|NO|NC)\d*$", re.IGNORECASE)
        has_com = any(re.match(r"^COM\d*$", n) for n in names)
        has_no_nc = any(re.match(r"^(NO|NC)\d*$", n) for n in names)
        contacts = [p for p in pins if contact_re.match(p.name.strip())] if (has_com and has_no_nc) else []
        if contacts and not any(p.number in w for p in contacts) and w:
            issues.append(VerifierIssue(
                id=f"R60:{comp.refdes}",
                severity="error",
                message=(
                    f"{comp.refdes} ({comp.mpn}): its contact pins "
                    f"({'/'.join(p.name for p in contacts)}) are all unwired — the coil "
                    f"is driven but the relay switches NOTHING. Wire COM to the load "
                    f"supply and NO (or NC) to the load / output terminal."
                ),
                refdes=comp.refdes,
                rule="R60_function_pins_unwired", source="verifier",
            ))
            continue
        func = [p for p in pins
                if p.type not in {"power_in", "power_out", "ground"}
                and (contact_re.match(p.name.strip()) and contacts
                     or not _NC_NAME_RE.match(p.name.strip()))]
        if not func:
            continue
        n_wired = sum(1 for p in func if p.number in w)
        if n_wired == 0 and w:
            issues.append(VerifierIssue(
                id=f"R60:{comp.refdes}",
                severity="error",
                message=(
                    f"{comp.refdes} ({comp.mpn}) is wired only on power/ground — none "
                    f"of its {len(func)} function pins "
                    f"({', '.join(p.name for p in func[:6])}{'…' if len(func) > 6 else ''}) "
                    f"connect to anything, so the part does nothing. Wire them or "
                    f"delete the part."
                ),
                refdes=comp.refdes,
                rule="R60_function_pins_unwired", source="verifier",
            ))
        elif len(func) >= 3 and n_wired * 2 < len(func) and part.category in {
            "sensor", "interface", "actuator", "display", "audio", "charger", "memory"
        }:
            unw = [p.name for p in func if p.number not in w]
            issues.append(VerifierIssue(
                id=f"R60:{comp.refdes}",
                severity="warning",
                message=(
                    f"{comp.refdes} ({comp.mpn}): {len(unw)} of its {len(func)} function "
                    f"pins are unwired ({', '.join(unw[:6])}{'…' if len(unw) > 6 else ''}) "
                    f"— check the part actually does its job in this design."
                ),
                refdes=comp.refdes,
                rule="R60_function_pins_unwired", source="verifier",
            ))
    return issues


def _two_pin_passive_refs(project: Project) -> set[str]:
    out: set[str] = set()
    for c in project.ir.components:
        part = library.get_part(c.mpn)
        if part and part.category == "passive" and len(part.pins) == 2:
            out.add(c.refdes)
    return out


def R61_passive_island(project: Project) -> list[VerifierIssue]:
    """A cluster of 2-pin passives whose nets touch NOTHING but other passives
    in the cluster is dead copper: review #19's two telemetry dividers were
    R1-R3 and R2-R4 joined to each other and to no rail, connector or MCU
    (0 errors — every individual net had 2 pins). Walk the passive graph:
    an island with no non-passive endpoint anywhere is an error; a lone
    passive-only net (an RC snubber junction) is fine because that cluster
    reaches active parts through its other ends."""
    issues: list[VerifierIssue] = []
    passives = _two_pin_passive_refs(project)
    if not passives:
        return issues
    net_by_name = {n.name: n for n in project.ir.nets}
    nets_of: dict[str, set[str]] = {}
    for n in project.ir.nets:
        for r, _ in n.pins:
            nets_of.setdefault(r, set()).add(n.name)
    seen: set[str] = set()
    for start in sorted(passives):
        if start in seen or start not in nets_of:
            continue
        # BFS over passives sharing nets
        island = {start}
        frontier = [start]
        island_nets: set[str] = set()
        touches_active = False
        while frontier:
            r = frontier.pop()
            for nn in nets_of.get(r, ()):
                island_nets.add(nn)
                if _classify_net(nn) != "signal":
                    touches_active = True  # a rail/ground is a real endpoint
                for r2, _ in net_by_name[nn].pins:
                    if r2 == r:
                        continue
                    if r2 in passives:
                        if r2 not in island:
                            island.add(r2)
                            frontier.append(r2)
                    else:
                        touches_active = True
        seen |= island
        if not touches_active and len(island_nets) >= 1:
            refs = ", ".join(sorted(island))
            issues.append(VerifierIssue(
                id=f"R61:{sorted(island)[0]}",
                severity="error",
                message=(
                    f"{refs} form a passive island — their nets "
                    f"({', '.join(sorted(island_nets))}) reach no rail, ground, IC, "
                    f"connector or MCU pin, so the network does nothing. Wire its "
                    f"ends to the source and the load (e.g. divider: rail → R → "
                    f"SENSE → R → GND, SENSE to an ADC pin)."
                ),
                refdes=sorted(island)[0],
                rule="R61_passive_island", source="verifier",
            ))
    return issues


_PROTECTION_IC_RE = re.compile(r"\b(DW01|FS312|S-8261|BQ297|HY2120|XB811|AP9101)", re.IGNORECASE)


def R62_battery_protection_fets(project: Project) -> list[VerifierIssue]:
    """A 1S protection IC (DW01A-class) only *commands* — its OD/OC pins must
    drive the gates of the series MOSFET pair (FS8205A) that actually opens the
    battery path. OD/OC on nothing = the "protection" protects nothing (review
    #21: DW01A with VCC/GND wired and OD/OC/CS in the air, 0 errors)."""
    issues: list[VerifierIssue] = []
    parts = {c.refdes: library.get_part(c.mpn) for c in project.ir.components}
    for comp in project.ir.components:
        part = parts[comp.refdes]
        if part is None or not _PROTECTION_IC_RE.search(part.mpn):
            continue
        drive = [p for p in part.pins if p.name.upper().strip() in {"OD", "OC", "DO", "CO", "DOUT", "COUT"}]
        if not drive:
            continue
        missing: list[str] = []
        for p in drive:
            n = _net_for_pin(project, comp.refdes, p.number)
            ok = False
            if n:
                net = next((x for x in project.ir.nets if x.name == n), None)
                for r, pn in (net.pins if net else []):
                    if r == comp.refdes:
                        continue
                    tp = parts.get(r)
                    if tp is not None and tp.category == "transistor":
                        pin = next((pp for pp in tp.pins if pp.number == pn), None)
                        if pin and pin.name.upper().strip().startswith(("G", "B")):
                            ok = True
            if not ok:
                missing.append(p.name)
        if missing:
            issues.append(VerifierIssue(
                id=f"R62:{comp.refdes}",
                severity="error",
                message=(
                    f"{comp.refdes} ({comp.mpn}) is a battery-protection controller "
                    f"but its {'/'.join(missing)} gate-drive output(s) reach no MOSFET — "
                    f"nothing can open the battery path, so there is NO over-charge / "
                    f"over-discharge protection. Add the dual N-FET (FS8205A): OD→G1, "
                    f"OC→G2, sources common on the sense (CS) node, drains BAT- and "
                    f"PACK-."
                ),
                refdes=comp.refdes,
                rule="R62_battery_protection_fets", source="verifier",
            ))
    return issues


def _n_channel(part: LibraryPart | None) -> bool:
    if part is None or part.category != "transistor":
        return False
    blob = f"{part.mpn} {part.description}".upper()
    if "P-CH" in blob or "P CHANNEL" in blob or "PNP" in blob or "P-CHANNEL" in blob or "PMOS" in blob:
        return False
    return any(k in blob for k in ("N-CH", "N CHANNEL", "N-CHANNEL", "NPN", "NMOS", "MOSFET", "N-MOS"))


def R63_lowside_switch_topology(project: Project) -> list[VerifierIssue]:
    """An N-channel switch must have its source/emitter on ground (low-side)
    with the load between drain and the rail. Drain on the rail and source
    on the load = a source-follower whose "return" goes nowhere — the coil
    never conducts (review #20: four AO3400s, drain on 5 V, source to the
    relay coil, coil's other end also on 5 V; 0 errors)."""
    issues: list[VerifierIssue] = []
    parts = {c.refdes: library.get_part(c.mpn) for c in project.ir.components}
    for comp in project.ir.components:
        part = parts[comp.refdes]
        if not _n_channel(part):
            continue
        d = s = None
        for p in part.pins:
            nm = p.name.upper().strip()
            if nm in {"D", "DRAIN", "C", "COLLECTOR"}:
                d = p
            elif nm in {"S", "SOURCE", "E", "EMITTER"}:
                s = p
        if d is None or s is None:
            continue
        dn = _net_for_pin(project, comp.refdes, d.number)
        sn = _net_for_pin(project, comp.refdes, s.number)
        if dn is None or sn is None:
            continue
        # A rail by prefix (VCC/VBAT…) or by an explicit voltage in the name
        # (RELAY_SUPPLY_5V, MOTOR_12V — the classifier's prefix list misses those).
        drain_on_rail = _classify_net(dn) == "power" or _parse_rail_voltage(dn) is not None
        if drain_on_rail and not _is_gnd_net(sn) and _classify_net(sn) == "signal":
            issues.append(VerifierIssue(
                id=f"R63:{comp.refdes}",
                severity="error",
                message=(
                    f"{comp.refdes} ({comp.mpn}) N-channel switch: drain is on the "
                    f"rail {dn} and source on {sn} (not ground) — that is a "
                    f"source-follower with no return path; the load never gets a "
                    f"ground. Low-side wiring: load between {dn} and the DRAIN, "
                    f"SOURCE to GND, gate from the GPIO."
                ),
                refdes=comp.refdes, net=sn,
                rule="R63_lowside_switch_topology", source="verifier",
            ))
    return issues


def R64_series_diode_high_current(project: Project) -> list[VerifierIssue]:
    """A rectifier/Schottky in SERIES with a battery rail that feeds motor / ESC
    connectors or a motor driver: 0.3-0.7 V × the whole vehicle current is
    watts of heat and a real voltage drop (review #19: SS54 in the 4S ESC
    path). Warning naming the P-FET / ideal-diode alternative; error when the
    diode's rating is known and below the connector class current."""
    issues: list[VerifierIssue] = []
    parts = {c.refdes: library.get_part(c.mpn) for c in project.ir.components}
    net_by_name = {n.name: n for n in project.ir.nets}
    for comp in project.ir.components:
        part = parts[comp.refdes]
        if part is None or part.category != "diode":
            continue
        blob = f"{part.mpn} {part.description}".upper()
        if any(k in blob for k in ("TVS", "ZENER", "ESD", "SMAJ", "SMBJ", "MMSZ", "USBLC", "PESD")):
            continue
        a, k = _led_pins(comp)
        if a is None or k is None:
            continue
        an = _net_for_pin(project, comp.refdes, a.number)
        kn = _net_for_pin(project, comp.refdes, k.number)
        if an is None or kn is None or _is_gnd_net(kn) or _is_gnd_net(an):
            continue
        # series element between two power-class nets, one being an input rail
        if not (_classify_net(an) == "power" and _classify_net(kn) == "power"):
            continue
        # does the downstream rail feed motor/ESC/power connectors or a driver's VM?
        down = net_by_name.get(kn)
        heavy = 0
        for r, pn in (down.pins if down else []):
            tp = parts.get(r)
            if tp is None:
                continue
            if tp.category == "connector":
                heavy += 1
            elif _is_motor_driver(tp):
                heavy += 2
        if heavy < 2:
            continue
        rated = part.currentMaxMa
        sev = "warning"
        if rated is not None and rated < 5000:
            sev = "error"
        issues.append(VerifierIssue(
            id=f"R64:{comp.refdes}",
            severity=sev,
            message=(
                f"{comp.refdes} ({comp.mpn}) sits in SERIES with the {kn} rail that "
                f"feeds {heavy} high-current outputs"
                + (f" but is rated {rated/1000:g} A" if rated else "")
                + ". A series diode drops ~0.5 V and burns that × the load current "
                  "as heat. Use a P-channel MOSFET reverse-polarity stage "
                  "(AO4407A / IRF4905: source→rail, drain→battery, gate→GND) or an "
                  "ideal-diode controller instead."
            ),
            refdes=comp.refdes, net=kn,
            rule="R64_series_diode_high_current", source="verifier",
        ))
    return issues


_MAINS_NET_RE = re.compile(
    r"^(AC[_-]?L\d*|AC[_-]?N\d*|LINE\d*|L\d?_?AC|NEUTRAL\d*|MAINS?[_A-Z0-9]*|"
    r"AC[_-]?IN.*|AC[_-]?OUT.*|(110|115|120|220|230|240)V?AC.*|AC(110|115|120|220|230|240).*|"
    r"LIVE\d*|PHASE\d*|RELAY\d*_(COM|NO|NC)|LOAD\d*_(L|N|AC).*)$", re.IGNORECASE)
_ISOLATING_TOKENS = ("RELAY", "OPTOCOUPLER", "OPTO", "PC817", "6N137", "TRANSFORMER",
                     "HLK-PM", "AC-DC", "AC/DC", "SSR", "MOC30", "TRIAC", "ACS712",
                     "CT CLAMP", "SCT-013", "ISOLAT")


def is_mains_net(name: str) -> bool:
    return bool(_MAINS_NET_RE.match(name.strip()))


def R65_mains_isolation(project: Project) -> list[VerifierIssue]:
    """Any part bridging a MAINS net (AC_L / AC_N / LINE / NEUTRAL / RELAYn_COM…)
    and a low-voltage (SELV) net must be an isolating device (relay, opto,
    transformer, AC-DC module, SSR, isolated current sensor). A resistor,
    MCU pin, non-isolated sensor or plain regulator across that boundary is
    a shock/fire path — hard error. Silent when no net is named as mains."""
    issues: list[VerifierIssue] = []
    mains = {n.name for n in project.ir.nets if is_mains_net(n.name)}
    if not mains:
        return issues
    nets_of: dict[str, set[str]] = {}
    for n in project.ir.nets:
        for r, _ in n.pins:
            nets_of.setdefault(r, set()).add(n.name)
    for comp in project.ir.components:
        mine = nets_of.get(comp.refdes, set())
        on_mains = mine & mains
        on_selv = mine - mains
        if not on_mains or not on_selv:
            continue
        part = library.get_part(comp.mpn)
        blob = f"{comp.mpn} {(part.description if part else '')}".upper()
        if any(t in blob for t in _ISOLATING_TOKENS):
            continue
        if part is not None and part.category == "connector":
            # A terminal block carrying both L and a load wire is normal.
            continue
        issues.append(VerifierIssue(
            id=f"R65:{comp.refdes}",
            severity="error",
            message=(
                f"{comp.refdes} ({comp.mpn}) bridges MAINS ({', '.join(sorted(on_mains))}) "
                f"and the low-voltage side ({', '.join(sorted(on_selv)[:4])}) but is not "
                f"an isolating device. Only relays, optocouplers, transformers, "
                f"isolated AC-DC modules, SSRs or isolated current sensors may cross "
                f"the mains/SELV boundary."
            ),
            refdes=comp.refdes, net=sorted(on_mains)[0],
            rule="R65_mains_isolation", source="verifier",
        ))
    return issues


_SENSE_INPUT_RE = re.compile(r"^(V?IN[+-]|VIN[PN]|IN[PN]|RS[+-]|SHUNT[+-]?|SENSE[+-]?|VS[+-]|CS[+-]|I[+-])$", re.IGNORECASE)


def R66_sense_inputs_unwired(project: Project) -> list[VerifierIssue]:
    """A current/voltage SENSOR whose sense inputs (IN+/IN-, RS+/RS-, SHUNT) are
    unwired measures nothing — the requirement it was placed for is not met
    (review #20: four INA219s with IN+/IN- in the air, only I2C wired). R16
    warns per pin; this is the functional error, once per part. Also flags a
    shunt monitor whose IN+/IN- are wired but with NO resistor between them
    (nothing to sense across)."""
    issues: list[VerifierIssue] = []
    resistors = _resistor_refdeses(project)
    net_by_name = {n.name: n for n in project.ir.nets}
    for comp in project.ir.components:
        part = library.get_part(comp.mpn)
        if part is None or part.category not in {"sensor", "interface"}:
            continue
        sense = [p for p in part.pins if _SENSE_INPUT_RE.match(p.name.strip())]
        if len(sense) < 1:
            continue
        nets = [_net_for_pin(project, comp.refdes, p.number) for p in sense]
        if all(n is None for n in nets):
            issues.append(VerifierIssue(
                id=f"R66:{comp.refdes}",
                severity="error",
                message=(
                    f"{comp.refdes} ({comp.mpn}) sense input(s) "
                    f"{'/'.join(p.name for p in sense)} are unwired — it measures "
                    f"nothing. Put the sense pins across a shunt in the load path "
                    f"(IN+ on the supply side, IN- on the load side, R_shunt between)."
                ),
                refdes=comp.refdes,
                rule="R66_sense_inputs_unwired", source="verifier",
            ))
            continue
        plus = [n for p, n in zip(sense, nets) if n and p.name.strip().endswith("+")]
        minus = [n for p, n in zip(sense, nets) if n and p.name.strip().endswith("-")]
        if plus and minus and plus[0] != minus[0]:
            bridged = False
            for r in resistors:
                rn = {n.name for n in project.ir.nets if any(rr == r for rr, _ in n.pins)}
                if plus[0] in rn and minus[0] in rn:
                    bridged = True
                    break
            if not bridged:
                issues.append(VerifierIssue(
                    id=f"R66:{comp.refdes}:shunt",
                    severity="error",
                    message=(
                        f"{comp.refdes} ({comp.mpn}) has its sense inputs on {plus[0]} "
                        f"and {minus[0]} but no shunt resistor between them — nothing "
                        f"to measure. Add a low-value shunt (0.1 Ω / 2512) between the "
                        f"two nets in the load path."
                    ),
                    refdes=comp.refdes,
                    rule="R66_sense_inputs_unwired", source="verifier",
                ))
    return issues


def R67_rail_name_vs_regulator(project: Project) -> list[VerifierIssue]:
    """A fixed-output regulator feeding a rail whose NAME says a different
    voltage: LD1117-33 (3.3 V) on `VCC_5V` (review #23) — every part and
    pull-up on that rail was chosen for 5 V. Names are how the agent, the
    other rules and the user reason about a rail; a lie there propagates.
    Error when the two differ by more than 0.35 V."""
    issues: list[VerifierIssue] = []
    for comp in project.ir.components:
        part = library.get_part(comp.mpn)
        if part is None or part.category != "regulator" or _is_adjustable_regulator(part):
            continue
        vout = _regulator_out_voltage(part)
        if vout is None:
            continue
        for net in _regulator_output_nets(project, comp, part):
            named = _parse_rail_voltage(net)
            if named is None or net.upper().startswith(("VBAT", "VBUS", "VSYS")):
                continue
            if abs(named - vout) > 0.35:
                issues.append(VerifierIssue(
                    id=f"R67:{comp.refdes}:{net}",
                    severity="error",
                    message=(
                        f"{comp.refdes} ({comp.mpn}) outputs {vout:g} V but its output "
                        f"rail is named {net!r} ({named:g} V) — parts on that rail were "
                        f"picked for {named:g} V. Rename the net or use a {named:g} V "
                        f"regulator."
                    ),
                    refdes=comp.refdes, net=net,
                    rule="R67_rail_name_vs_regulator", source="verifier",
                ))
    return issues


_SW_NODE_NAMES = {"SW", "LX", "PH", "SWITCH", "SW1", "SW2", "PH1", "PH2", "OUTPUT", "OUT_SW", "LX1", "LX2"}


def R68_switcher_inductor(project: Project) -> list[VerifierIssue]:
    """A switching regulator's SW/LX/PH node must reach an inductor — that IS
    the converter. Seen in the prompt sweep: an MP1584EN with SW on a stray
    cap, COMP/FREQ tied to the output, no inductor anywhere; R59 passed (a
    divider existed) and R26 could only say "VCC_3V3 has no source". Error
    when the SW-node net carries no inductor-category passive (name/desc says
    inductor / µH / uH); silent for linear regulators and parts with no SW pin.
    LM2596's SW pin is literally named 'Output' — matched by name set."""
    issues: list[VerifierIssue] = []
    parts = {c.refdes: library.get_part(c.mpn) for c in project.ir.components}
    net_by_name = {n.name: n for n in project.ir.nets}

    def _is_inductor(p: LibraryPart | None) -> bool:
        if p is None or p.category != "passive":
            return False
        blob = f"{p.mpn} {p.description} {p.symbol}".upper()
        return "INDUCTOR" in blob or "µH" in blob or "UH " in blob or blob.endswith("UH") or ":L" in p.symbol.upper()

    for comp in project.ir.components:
        part = parts[comp.refdes]
        if part is None or part.category != "regulator" or _is_linear_regulator(part):
            continue
        blob = (part.description or "").lower()
        if not any(k in blob for k in ("buck", "boost", "switching", "step-down", "step-up", "dc-dc", "dc/dc", "smps", "buck-boost")):
            continue
        sw = next((p for p in part.pins if p.name.upper().strip() in _SW_NODE_NAMES), None)
        if sw is None:
            continue
        net = _net_for_pin(project, comp.refdes, sw.number)
        if net is None:
            issues.append(VerifierIssue(
                id=f"R68:{comp.refdes}", severity="error", refdes=comp.refdes, pinNumber=sw.number,
                message=(f"{comp.refdes} ({comp.mpn}) is a switching regulator but its {sw.name} "
                         f"(switch node) pin is unwired — there is no inductor, so there is no converter. "
                         f"Wire {sw.name} → inductor → the output rail (and the BS/BOOT cap {sw.name}↔BS)."),
                rule="R68_switcher_inductor", source="verifier"))
            continue
        n = net_by_name.get(net)
        has_l = any(_is_inductor(parts.get(r)) for r, _ in (n.pins if n else []) if r != comp.refdes)
        if not has_l:
            issues.append(VerifierIssue(
                id=f"R68:{comp.refdes}", severity="error", refdes=comp.refdes, pinNumber=sw.number, net=net,
                message=(f"{comp.refdes} ({comp.mpn}) switch node {sw.name} is on net {net} with NO inductor — "
                         f"a buck/boost without its inductor is not a converter. Put a power inductor from "
                         f"{sw.name} to the output rail (RLB0914-100KL 10 µH / RLB0914-330KL 33 µH), the "
                         f"catch diode or BS cap the datasheet shows, and the output cap on the far side; "
                         f"or apply_template('buck_lm2596_5v' / '_3v3' / '_12v')."),
                rule="R68_switcher_inductor", source="verifier"))
    return issues


# --------------------------------------------------------------------------- #
# Review round 2026-08-20: three of five boards were the WRONG SIZE, and the
# worst offender shipped with 0 errors. R57 only speaks when a form factor was
# recorded; these two rules need no such cooperation.
# --------------------------------------------------------------------------- #

# Tolerance on a published standard outline: fabs cut to ±0.2 mm, and our own
# placement rounds to 0.1 — 2 mm is generous and still catches a 94 x 100 mm
# "Uno shield" (spec 68.58 x 53.34).
_STANDARD_OUTLINE_TOL_MM = 2.0


def R69_standard_form_factor_outline(project: Project) -> list[VerifierIssue]:
    """A board carrying a STANDARD host header must match that standard's
    published outline.

    An Arduino shield or a Raspberry Pi HAT is defined by its mechanics: the
    header mates only if the board is the specified size and the header sits at
    the specified place. Review round 2026-08-20: a shield built at
    94.5 x 100 mm (spec 68.58 x 53.34) — it cannot plug into an Uno, and
    nothing said so, because the design never recorded a form factor.

    Detection needs no new data: the anchor MPN of every entry in
    `form_factors.FORM_FACTORS` identifies the standard; the entry carries the
    spec outline. The fix is named in the message (`apply_template`), which
    also locks the header at its spec position."""
    from app.services import form_factors

    board = project.board
    if board is None or not board.components:
        return []
    issues: list[VerifierIssue] = []
    for ff in form_factors.FORM_FACTORS.values():
        placed = next((pc for pc in board.components if pc.mpn == ff.anchor_mpn), None)
        if placed is None:
            continue
        dw = abs(board.width - ff.board_w)
        dh = abs(board.height - ff.board_h)
        if dw > _STANDARD_OUTLINE_TOL_MM or dh > _STANDARD_OUTLINE_TOL_MM:
            issues.append(VerifierIssue(
                id=f"R69:{ff.name}",
                severity="error",
                message=(
                    f"This board carries the {ff.title} header ({placed.refdes}) but is "
                    f"{board.width:.1f} x {board.height:.1f} mm — the standard outline is "
                    f"{ff.board_w} x {ff.board_h} mm, so it will not fit the host. "
                    f"Apply the form factor (apply_template \"{ff.name}\"), which sizes the "
                    f"board and locks the header at its spec position."),
                refdes=placed.refdes,
                rule="R69_standard_form_factor_outline",
                source="verifier",
            ))
        elif not placed.locked:
            issues.append(VerifierIssue(
                id=f"R69:{ff.name}:anchor",
                severity="warning",
                message=(
                    f"{placed.refdes} is the {ff.title} host header but is not locked, so "
                    f"auto-place can move it off the mating position. Apply the form factor "
                    f"(apply_template \"{ff.name}\") to pin it."),
                refdes=placed.refdes,
                rule="R69_standard_form_factor_outline",
                source="verifier",
            ))
    return issues


# Fill factor uses the part BODIES, never the courtyards: courtyards overlap
# each other and include keep-outs (the ESP32-WROOM's is 48 x 41 mm because of
# its antenna), which made a tightly-packed stub board read as "106 % full".
# Calibrated over all 33 local builds: real boards run 15-62 % body fill, the
# sparsest legitimate one is 15.3 %; a 9-part IMU breakout on 62.5 x 44 mm
# (the 2026-08-20 review) is ~4 %.
_MIN_FILL_FACTOR = 0.08
_FILL_MAX_PARTS = 25          # only judge small designs; a dense board is its own proof
_FILL_MIN_AREA_MM2 = 1200.0   # ignore genuinely tiny boards (a 30x30 breakout)


def R70_board_oversized_for_content(project: Project) -> list[VerifierIssue]:
    """The board is far larger than its parts need.

    "More precision, product-shaped results" was the expert's verdict and it
    keeps coming back: a 9-part IMU breakout on 62.5 x 44 mm, an ESP32-S3 dev
    board at 4.8x its requested area. Fill factor = the placed parts' courtyard
    area over the board area; below 10 % on a small design the board is padding,
    not layout. A warning normally (the user may want the room), an ERROR when
    the user recorded a size — then it is R57's finding restated with the
    reason."""
    board = project.board
    if board is None or not board.components:
        return []
    if len(board.components) > _FILL_MAX_PARTS:
        return []
    area = board.width * board.height
    if area < _FILL_MIN_AREA_MM2:
        return []
    parts_area = 0.0
    for pc in board.components:
        fp = pc.footprint
        parts_area += float(fp.width or 0.0) * float(fp.height or 0.0)
    if parts_area <= 0:
        return []
    fill = parts_area / area
    if fill >= _MIN_FILL_FACTOR:
        return []
    asked = getattr(project.ir, "form_factor", None) is not None
    return [VerifierIssue(
        id="R70:fill",
        severity="error" if asked else "warning",
        message=(
            f"The board is {board.width:.1f} x {board.height:.1f} mm but its parts occupy only "
            f"{fill * 100:.0f}% of it — mostly empty copper. Re-run auto-place, or state the size "
            f"you want (set_form_factor) and it will be built to that."),
        rule="R70_board_oversized_for_content",
        source="verifier",
    )]


# The Raspberry Pi HAT specification: a board that plugs onto the 40-pin bank
# MUST carry an ID EEPROM on the dedicated ID_SD/ID_SC pins (GPIO0/GPIO1), and
# nothing else may use those pins. Review round 2026-08-20: the HAT build put
# the 24LC256 on the design without anything checking it landed there.
_ID_EEPROM_MPN_RE = re.compile(r"^(24|AT24|CAT24|M24)[A-Z]*\d+", re.IGNORECASE)
_HAT_HEADER_MPNS = {"RPi_HAT_Header_40P"}


def _is_id_eeprom(part) -> bool:
    if part is None:
        return False
    if _ID_EEPROM_MPN_RE.match(part.mpn or ""):
        return True
    return (part.category or "").lower() == "memory"


def R71_hat_id_eeprom(project: Project) -> list[VerifierIssue]:
    """A Raspberry Pi HAT needs its ID EEPROM on ID_SD/ID_SC — and only that.

    The Pi reads the HAT's identity over the two dedicated pins at boot; a HAT
    without one is not a HAT (the OS applies no overlay), and hanging the
    EEPROM off the normal I2C bus (GPIO2/3) does not work. `pin_safety` already
    marks GPIO0/1 reserved; this checks the EEPROM actually reaches them and
    that nothing else squats there."""
    hat = next(
        (c for c in project.ir.components if c.mpn in _HAT_HEADER_MPNS), None
    )
    if hat is None:
        return []
    part = library.get_part(hat.mpn)
    if part is None:
        return []
    id_pins = [
        pin for pin in part.pins
        if "ID_SD" in pin.name.upper() or "ID_SC" in pin.name.upper()
    ]
    if not id_pins:
        return []
    issues: list[VerifierIssue] = []
    eeproms = [
        c for c in project.ir.components if _is_id_eeprom(library.get_part(c.mpn))
    ]
    # The header's pins are named ID_SD_EEPROM / ID_SC_EEPROM — key on the
    # token, not the full name.
    id_nets: dict[str, str | None] = {}
    for pin in id_pins:
        token = "ID_SD" if "ID_SD" in pin.name.upper() else "ID_SC"
        id_nets[token] = _net_for_pin(project, hat.refdes, pin.number)
    if not eeproms:
        issues.append(VerifierIssue(
            id="R71:missing",
            severity="error",
            message=(
                f"This is a Raspberry Pi HAT ({hat.refdes}) but it has no ID EEPROM. "
                f"The Pi reads the HAT's identity over ID_SD/ID_SC at boot and applies "
                f"no device-tree overlay without it — add a 24LC256-I/SN (or 24C32) and "
                f"wire its SDA/SCL to {hat.refdes}'s ID_SD/ID_SC pins."),
            refdes=hat.refdes,
            rule="R71_hat_id_eeprom",
            source="verifier",
        ))
        return issues

    # The EEPROM must sit on BOTH ID pins.
    wired_ok = False
    for eep in eeproms:
        epart = library.get_part(eep.mpn)
        if epart is None:
            continue
        sda = next((pin for pin in epart.pins if pin.name.upper() in {"SDA", "SDA/IO"}), None)
        scl = next((pin for pin in epart.pins if pin.name.upper() in {"SCL", "SCK"}), None)
        if sda is None or scl is None:
            continue
        n_sda = _net_for_pin(project, eep.refdes, sda.number)
        n_scl = _net_for_pin(project, eep.refdes, scl.number)
        if n_sda and n_scl and n_sda == id_nets.get("ID_SD") and n_scl == id_nets.get("ID_SC"):
            wired_ok = True
            # Nothing else may share the ID bus.
            for label, net_name in (("ID_SD", n_sda), ("ID_SC", n_scl)):
                net = next((n for n in project.ir.nets if n.name == net_name), None)
                if net is None:
                    continue
                strangers = sorted(
                    {r for r, _ in net.pins if r not in (hat.refdes, eep.refdes)}
                )
                if strangers:
                    issues.append(VerifierIssue(
                        id=f"R71:{label}:shared",
                        severity="error",
                        message=(
                            f"{label} ({net_name}) is reserved for the HAT ID EEPROM but also "
                            f"carries {', '.join(strangers)}. GPIO0/GPIO1 are read at boot — "
                            f"move those parts to the normal I2C bus (GPIO2/GPIO3)."),
                        net=net_name,
                        rule="R71_hat_id_eeprom",
                        source="verifier",
                    ))
            # WP left floating is a write-protect coin flip.
            wp = next((pin for pin in epart.pins if pin.name.upper().startswith("WP")), None)
            if wp is not None and _net_for_pin(project, eep.refdes, wp.number) is None:
                issues.append(VerifierIssue(
                    id=f"R71:{eep.refdes}:wp",
                    severity="warning",
                    message=(
                        f"{eep.refdes}'s write-protect pin {wp.name} is floating — tie it to "
                        f"GND to allow programming (or to VCC to lock the EEPROM)."),
                    refdes=eep.refdes,
                    pinNumber=wp.number,
                    rule="R71_hat_id_eeprom",
                    source="verifier",
                ))
            break
    if not wired_ok:
        refs = ", ".join(e.refdes for e in eeproms)
        issues.append(VerifierIssue(
            id="R71:miswired",
            severity="error",
            message=(
                f"The ID EEPROM ({refs}) is not on the HAT's ID_SD/ID_SC pins. The Pi reads "
                f"HAT identity over those two dedicated pins only — wire the EEPROM's SDA to "
                f"{hat.refdes} ID_SD and SCL to {hat.refdes} ID_SC (not the normal I2C bus)."),
            refdes=eeproms[0].refdes,
            rule="R71_hat_id_eeprom",
            source="verifier",
        ))
    return issues


# A module whose footprint is a bare pin header has NO BODY: its courtyard is
# the 3.6 mm-wide pin strip, so overlap / off-board / fit / hole-clearance /
# board-sizing all under-measure it and the 3D view shows a header where a
# 43 x 43 mm brick will sit. The 2026-08-20 curation gave the 12 most-used
# modules real outlines; this reports the rest instead of letting them pass
# silently.
_HEADER_FP_RE = re.compile(r"PinHeader|PinSocket|Socket_Strip", re.IGNORECASE)
# Categories where a wrong body is dangerous rather than untidy: these parts are
# big, and something mounts on or beside them.
_BODY_CRITICAL = {"actuator", "display", "relay", "power"}


def R72_module_without_body(project: Project) -> list[VerifierIssue]:
    """A plug-in MODULE whose footprint is a bare pin header.

    The pads are right — the module does solder onto that header — but the
    part has no outline, so every geometric check measures a 3.6 mm strip
    instead of the real board. Warning normally; error for the categories where
    the missing volume actually collides with something."""
    issues: list[VerifierIssue] = []
    seen: set[str] = set()
    for comp in project.ir.components:
        part = library.get_part(comp.mpn)
        if part is None or comp.mpn in seen:
            continue
        package = (getattr(part, "package", "") or "").upper()
        fp = part.footprint or ""
        if not package.startswith("MODULE") or not _HEADER_FP_RE.search(fp):
            continue
        if getattr(part, "offBoard", False):
            # A servo, a stepper, a membrane keypad, a probe on a cable: the
            # board carries their CONNECTOR and nothing else, so the header is
            # the correct footprint and there is no body to miss. Flagging
            # these was a false ERROR the agent could not have fixed.
            continue
        seen.add(comp.mpn)
        category = (part.category or "").lower()
        issues.append(VerifierIssue(
            id=f"R72:{comp.mpn}",
            severity="error" if category in _BODY_CRITICAL else "warning",
            message=(
                f"{comp.refdes} ({comp.mpn}) is a plug-in module but its footprint is a bare "
                f"pin header ({fp}), so Synqit measures it as the pin strip, not the module: "
                f"overlap, board size, enclosure fit and the 3D view are all wrong for it. "
                f"Leave room for the real module by hand, or ask for a curated footprint."),
            refdes=comp.refdes,
            rule="R72_module_without_body",
            source="verifier",
        ))
    return issues


# --------------------------------------------------------------------------- #
# R73 — the design must contain what the user explicitly asked for.
#
# The most-repeated complaint of the 2026-08-20 batch, from three different
# reviewers: "the tool's own explanation text correctly said 150 Ohm but it
# still went ahead and used the wrong part", "asked for a through-hole resistor
# but it gave a surface-mount version", "asked for a 5mm LED, but it used a
# 3mm one". R43 counts parts; this checks their VALUES and PACKAGES.
# --------------------------------------------------------------------------- #
_MOUNT_THT_TOKENS = ("THT", "DIP", "RADIAL", "AXIAL", "TO-92", "TO-220", "D3.0", "D5.0", "THROUGH")


def _part_mount(part) -> str | None:
    """"tht" / "smd" for a library part, or None when it cannot be told."""
    if part is None:
        return None
    package = (getattr(part, "package", "") or "").upper()
    fp = (part.footprint or "").upper()
    blob = f"{package} {fp} {(part.description or '').upper()}"
    if any(tok in blob for tok in _MOUNT_THT_TOKENS):
        return "tht"
    if re.search(r"\b(0201|0402|0603|0805|1206|1210|2010|2512)\b", package):
        return "smd"
    if any(tok in package for tok in ("SOT", "SOIC", "QFN", "QFP", "DFN", "SON", "TSSOP", "SMD", "LGA", "BGA")):
        return "smd"
    if "SMD" in fp or "_SMD" in fp:
        return "smd"
    return None


def _part_resistor_ohms(part) -> float | None:
    """Resistance from a LIBRARY PART's curated description ("150Ω resistor,
    0603, 1%"). Distinct from `_resistor_ohms`, which reads an IR component's
    value — shadowing that one silently disabled R55."""
    if part is None:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*([kKmM])?\s*(?:ohm|Ω|Ω)", part.description or "", re.IGNORECASE)
    if not m:
        return None
    v = float(m.group(1))
    mult = (m.group(2) or "").lower()
    return v * (1e3 if mult == "k" else 1e6 if mult == "m" else 1.0)


def _part_capacitor_farads(part) -> float | None:
    if part is None:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*([pnuµμ]?)F\b", part.description or "", re.IGNORECASE)
    if not m:
        return None
    mult = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "": 1.0}
    return float(m.group(1)) * mult.get(m.group(2).lower(), 1.0)


def _target_matches(part, target: str) -> bool:
    if part is None:
        return False
    cat = (part.category or "").lower()
    desc = (part.description or "").lower()
    if target == "resistor":
        return _is_resistor(part)
    if target == "capacitor":
        return cat == "passive" and ("cap" in (part.symbol or "").lower() or "capacitor" in desc)
    if target == "inductor":
        return cat == "passive" and ("ind" in (part.symbol or "").lower() or "inductor" in desc)
    if target == "led":
        return cat == "led"
    if target in ("header", "connector"):
        return cat == "connector"
    return True  # "any"


def R73_explicit_part_constraint(project: Project) -> list[VerifierIssue]:
    """A value / package / size the user stated explicitly must be what the
    design contains. Silent when the prompt stated nothing concrete."""
    cons = getattr(project.ir, "partConstraints", None) or []
    if not cons or not project.ir.components:
        return []
    from app.services import constraints_text as _ct

    parts = {c.refdes: library.get_part(c.mpn) for c in project.ir.components}
    issues: list[VerifierIssue] = []
    for con in cons:
        matching = [
            (c, parts[c.refdes]) for c in project.ir.components
            if _target_matches(parts[c.refdes], con.target)
        ]
        if not matching:
            continue  # nothing of that class in the design — R43's business
        described = _ct.describe(_ct.PartConstraint(
            kind=con.kind, target=con.target, value=con.value, text=con.text,
            tolerance=con.tolerance,
            meta=({"ohms": float(con.value)} if con.kind == "value" and con.target == "resistor"
                  else {"farads": float(con.value)} if con.kind == "value" and con.target == "capacitor"
                  else {"henries": float(con.value)} if con.kind == "value" and con.target == "inductor"
                  else {}),
        ))
        ok = False
        found: list[str] = []
        for comp, part in matching:
            if con.kind == "value":
                actual = (
                    _part_resistor_ohms(part) if con.target == "resistor"
                    else _part_capacitor_farads(part) if con.target == "capacitor"
                    else None
                )
                if actual is None:
                    continue
                want = float(con.value)
                tol = max(con.tolerance, 0.01) * want
                found.append(f"{comp.refdes}={actual:g}")
                if abs(actual - want) <= tol:
                    ok = True
                    break
            elif con.kind == "mount":
                mount = _part_mount(part)
                if mount is None:
                    continue
                found.append(f"{comp.refdes}={mount}")
                if mount == con.value:
                    ok = True
                    break
            elif con.kind == "size":
                package = (getattr(part, "package", "") or "").upper()
                blob = f"{package} {(part.description or '').upper()}"
                found.append(f"{comp.refdes}={package or '?'}")
                token = con.value.upper().replace("MM", "MM")
                if token in blob or token.replace("MM", "") + "MM" in blob.replace(" ", ""):
                    ok = True
                    break
        if ok or not found:
            continue
        issues.append(VerifierIssue(
            id=f"R73:{con.kind}:{con.target}:{con.value}",
            severity="error",
            message=(
                f"You asked for {described} (\"{con.text}\") but the design uses "
                f"{', '.join(found[:4])}. Swap the part for one that matches what you asked "
                f"for, or say explicitly that the substitute is acceptable."
            ),
            refdes=matching[0][0].refdes,
            rule="R73_explicit_part_constraint",
            source="verifier",
        ))
    return issues



# --- R74: duplicate hardware nobody asked for --------------------------------
# Two of the five 2026-08-20 reports found the same shape of defect: a board
# carrying TWO USB-C receptacles, and a board carrying a microSD socket the
# prompt never mentioned. The first is an electrical hazard (two VBUS sources
# back-feeding each other through one rail, and a user who plugs in both
# shorts one host's 5 V to the other's); the second is cost and assembly the
# user is paying for without knowing. R43 could not see either: it only judges
# what the user DID ask for, so a part nobody requested is invisible to it.
#
# Deliberately narrow. It fires on a duplicated PORT of the same kind, which is
# almost never intentional on a hobby board and is checkable from the library
# alone. Judging "unrequested" in general needs intent, which belongs to the
# prompt (rule 11b) and the critic, not to a deterministic rule.
_PORT_KINDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("USB receptacle", ("usb",)),
    ("microSD/SD socket", ("microsd", "micro sd", "sd_card", "sd card", "sdcard")),
    ("barrel jack", ("barrel", "dc jack", "dc_jack")),
    ("Ethernet jack", ("rj45", "ethernet jack")),
)


def _port_kind(part) -> str | None:
    """Which duplicated-port family this connector belongs to, if any."""
    if part is None or (part.category or "").lower() != "connector":
        return None
    blob = f"{part.mpn} {part.description or ''} {part.footprint or ''}".lower()
    for label, tokens in _PORT_KINDS:
        if any(tok in blob for tok in tokens):
            return label
    return None


def _requested_count(project: Project, part) -> int | None:
    """How many of this part the user actually asked for, if they said."""
    total = 0
    stated = False
    for req in project.ir.requirements:
        req_mpn = (req.mpn or "").strip()
        if req_mpn and req_mpn == part.mpn:
            stated = True
            total += max(1, int(req.quantity or 1))
            continue
        if not req_mpn and (req.category or "").lower() == "connector":
            # A category-level ask ("two USB ports") can't be pinned to this
            # exact MPN — treat it as permission and stay silent.
            return None
    return total if stated else None


def R74_duplicate_unrequested_port(project: Project) -> list[VerifierIssue]:
    """Two connectors of the same PORT kind when the user asked for one (or
    said nothing). Error for a power-carrying port, warning otherwise."""
    issues: list[VerifierIssue] = []
    by_kind: dict[str, list] = {}
    for comp in project.ir.components:
        part = library.get_part(comp.mpn)
        kind = _port_kind(part)
        if kind is None:
            continue
        by_kind.setdefault(kind, []).append((comp, part))

    for kind, members in by_kind.items():
        if len(members) < 2:
            continue
        asked = _requested_count(project, members[0][1])
        if asked is None or asked >= len(members):
            continue  # they asked for this many (or asked by category)
        refs = ", ".join(c.refdes for c, _ in members)
        powered = kind in ("USB receptacle", "barrel jack")
        if powered:
            msg = (
                f"This board has {len(members)} {kind}s ({refs}) but only one was asked for. "
                f"Two power inputs on one rail back-feed each other, and plugging in both "
                f"connects one host's supply to the other's. Delete the extra one."
            )
        else:
            msg = (
                f"This board has {len(members)} {kind}s ({refs}) and only one was asked for. "
                f"The extra one is cost and assembly nobody requested — delete it, or say "
                f"explicitly that you want more than one."
            )
        issues.append(VerifierIssue(
            id=f"R74:{kind}",
            severity="error" if powered else "warning",
            message=msg,
            refdes=members[-1][0].refdes,
            rule="R74_duplicate_unrequested_port",
            source="verifier",
        ))
    return issues



# --- R75: state the current a limiting resistor actually sets ----------------
# A reviewer on 2026-08-20: the tool "didn't specify a forward current, making
# its own resistor value impossible to independently verify". Telling the model
# to show its arithmetic helps, but a sentence is only as trustworthy as the
# model writing it. This DERIVES the number from the parts that are actually in
# the design — rail voltage, the LED's curated forward voltage, the resistor's
# curated resistance — so the answer is checkable whatever the prose says, and
# it is wrong loudly if the value is wrong.
def _led_forward_voltage(part) -> float | None:
    """The LED's forward voltage, from its curated data."""
    if part is None:
        return None
    # voltageMin/Max bracket Vf for an LED (1.8-2.2 V for a red 3 mm).
    lo, hi = getattr(part, "voltageMin", None), getattr(part, "voltageMax", None)
    if lo is not None and hi is not None and 0 < lo <= hi <= 5.0:
        return (lo + hi) / 2.0
    m = re.search(r"Vf\s*=?\s*(\d+(?:\.\d+)?)\s*V", part.description or "", re.IGNORECASE)
    return float(m.group(1)) if m else None


def R75_led_current_stated(project: Project) -> list[VerifierIssue]:
    """For every LED driven through a series resistor, report the current that
    resistor actually sets — and flag it when it exceeds the LED's rating."""
    issues: list[VerifierIssue] = []
    for comp in project.ir.components:
        part = library.get_part(comp.mpn)
        if part is None or (part.category or "").lower() != "led":
            continue
        vf = _led_forward_voltage(part)
        if vf is None:
            continue

        # The series resistor: a 2-pin resistor sharing a net with this LED.
        led_nets = {n.name for n in project.ir.nets
                    if any(r == comp.refdes for r, _ in n.pins)}
        series = None
        for other in project.ir.components:
            opart = library.get_part(other.mpn)
            if opart is None or not _is_resistor(opart):
                continue
            onets = {n.name for n in project.ir.nets
                     if any(r == other.refdes for r, _ in n.pins)}
            if led_nets & onets:
                ohms = _part_resistor_ohms(opart)
                if ohms:
                    series = (other.refdes, ohms)
                    break
        if series is None:
            continue  # R46 owns the missing-resistor case

        # The supply: the highest rail voltage this LED/resistor pair reaches.
        rail_v = None
        for name in led_nets | {n.name for n in project.ir.nets
                                if any(r == series[0] for r, _ in n.pins)}:
            lo, hi = rail_voltage_range(project, name)
            v = hi if hi is not None else lo
            if v is not None and (rail_v is None or v > rail_v):
                rail_v = v
        if rail_v is None or rail_v <= vf:
            continue  # nothing to compute from, or a boost/charge-pump case

        ref, ohms = series
        current_ma = (rail_v - vf) / ohms * 1000.0
        rated = getattr(part, "currentMaxMa", None)

        if rated and current_ma > rated * 1.05:
            issues.append(VerifierIssue(
                id=f"R75:{comp.refdes}",
                severity="error",
                message=(
                    f"{ref} ({_fmt_r(ohms)}) puts {current_ma:.0f} mA through {comp.refdes}, "
                    f"past its {rated:.0f} mA rating: ({rail_v:g} V − {vf:g} V) / {_fmt_r(ohms)}. "
                    f"Use at least {_fmt_r((rail_v - vf) / (rated / 1000.0))}."
                ),
                refdes=comp.refdes,
                rule="R75_led_current_stated",
                source="verifier",
            ))
        elif current_ma < 1.0:
            # Arithmetically fine, physically dark. Reporting this as a healthy
            # "0 mA, within rating" would be the same kind of vacuous pass the
            # 2026-08-20 round was about.
            issues.append(VerifierIssue(
                id=f"R75:{comp.refdes}",
                severity="warning",
                message=(
                    f"{ref} ({_fmt_r(ohms)}) leaves only {current_ma:.2f} mA through "
                    f"{comp.refdes} — ({rail_v:g} V − {vf:g} V) / {_fmt_r(ohms)} — which is "
                    f"too little to light it. A visible LED wants 2-20 mA; "
                    f"{_fmt_r((rail_v - vf) / 0.010)} would give 10 mA."
                ),
                refdes=comp.refdes,
                rule="R75_led_current_stated",
                source="verifier",
            ))
        else:
            issues.append(VerifierIssue(
                id=f"R75:{comp.refdes}",
                severity="info",
                message=(
                    f"{comp.refdes} runs at about {current_ma:.0f} mA: "
                    f"({rail_v:g} V − {vf:g} V forward drop) / {_fmt_r(ohms)} at {ref}"
                    + (f", within its {rated:.0f} mA rating." if rated else ".")
                ),
                refdes=comp.refdes,
                rule="R75_led_current_stated",
                source="verifier",
            ))
    return issues


def _fmt_r(ohms: float) -> str:
    if ohms >= 1e6:
        return f"{ohms / 1e6:g} MΩ"
    if ohms >= 1e3:
        return f"{ohms / 1e3:g} kΩ"
    return f"{ohms:g} Ω"


# 2-pin resistive sensors that only mean something as one leg of a divider.
_RESISTIVE_SENSOR_RE = re.compile(
    r"LDR|PHOTORESIST|NTC|PTC_THERM|THERMISTOR|GL55", re.IGNORECASE)


def _is_resistive_2pin(comp, part) -> bool:
    """A plain resistor, or a 2-pin resistive sensor (LDR/NTC class)."""
    if part is None or len(part.pins) != 2:
        return False
    if _part_resistor_ohms(part) is not None:
        return True
    hay = f"{part.mpn} {part.description or ''}"
    return part.category == "sensor" and bool(_RESISTIVE_SENSOR_RE.search(hay))


def R76_sense_divider_unread(project: Project) -> list[VerifierIssue]:
    """A divider that measures nothing, and a sensor that nothing measures.

    Review #36 (dual-axis solar tracker) shipped with 0 errors while: two of
    the four LDR divider midpoints reached no MCU pin at all, both battery-
    sense midpoints (R5/R7, R6/R8) dangled unread, and one LDR sat straight
    across VBAT→GND with no divider — a light-dependent battery drain that
    nothing measures. Every existing rule was satisfied: the nets have two
    pins (not R12), the chains reach rails through their far ends (not R61),
    and resistors are passives (exempt from R60). The class is precisely
    "the sense TOPOLOGY exists but its OUTPUT goes nowhere".

    (a) A net whose every endpoint is a pin of a 2-pin resistor or resistive
        sensor, whose far pins land on a power rail on one side and ground on
        the other (a divider), and which reaches no input/bidirectional pin —
        error: the divider divides for nobody.
    (b) A 2-pin resistive SENSOR with one pin on a power rail and the other on
        ground — error: it loads the supply and nothing reads it.
    """
    issues: list[VerifierIssue] = []
    comps = {c.refdes: c for c in project.ir.components}
    parts = {c.refdes: library.get_part(c.mpn) for c in project.ir.components}
    net_class = {n.name: _classify_net(n.name) for n in project.ir.nets}
    nets_of: dict[str, list[str]] = {}
    for n in project.ir.nets:
        for r, p in n.pins:
            nets_of.setdefault(r, []).append(n.name)

    def far_net(refdes: str, this_net: str) -> str | None:
        others = [nn for nn in nets_of.get(refdes, []) if nn != this_net]
        return others[0] if len(others) == 1 else None

    # (a) divider midpoints nobody reads. Deliberately NOT limited to
    # signal-named nets: the tracker's dead battery dividers were named
    # VBAT_SENSE1/2, which _classify_net reads as power — the divider SHAPE
    # (only resistive 2-pin legs, far ends on rail and ground) is the
    # evidence, not the name.
    for net_name, endpoints in _typed_net_endpoints(project):
        if len(endpoints) < 2:
            continue
        if any(t in ("input", "bidirectional") for _, _, t in endpoints):
            continue  # something reads the node
        legs = [(r, p) for r, p, _ in endpoints]
        if not all(_is_resistive_2pin(comps.get(r), parts.get(r)) for r, _ in legs):
            continue  # a cap (RC snubber), IC or connector on the node — not ours
        far = {far_net(r, net_name) for r, _ in legs}
        far_classes = {net_class.get(f) for f in far if f}
        if not {"power", "gnd"} <= far_classes:
            continue  # not a rail-to-ground divider
        members = ", ".join(sorted({r for r, _ in legs}))
        issues.append(VerifierIssue(
            id=f"R76:{net_name}",
            severity="error",
            message=(
                f"The divider at {net_name} ({members}) measures nothing — its "
                f"midpoint reaches no MCU/ADC input. Connect {net_name} to an "
                f"analog-capable pin (allocate_pin role \"adc\") or remove the "
                f"divider."
            ),
            net=net_name,
            rule="R76_sense_divider_unread",
            source="verifier",
        ))

    # (b) a resistive sensor straight across the supply
    for c in project.ir.components:
        part = parts.get(c.refdes)
        if part is None or part.category != "sensor":
            continue
        if not _is_resistive_2pin(c, part):
            continue
        my_nets = nets_of.get(c.refdes, [])
        if len(my_nets) != 2:
            continue
        classes = sorted(net_class.get(nn, "signal") for nn in my_nets)
        if classes == ["gnd", "power"]:
            issues.append(VerifierIssue(
                id=f"R76:{c.refdes}",
                severity="error",
                message=(
                    f"{c.refdes} ({c.mpn}) sits straight across the supply "
                    f"with no sense node — it loads the rail and nothing can "
                    f"read it. Put it in a divider with a fixed resistor and "
                    f"take the midpoint to an ADC input."
                ),
                refdes=c.refdes,
                rule="R76_sense_divider_unread",
                source="verifier",
            ))
    return issues


# ---- review #39 (Lucky Singh audit + phrasing-sensitivity sheet, 2026-08-26) ----

_STRAP_PIN_RE = re.compile(
    r"^(PS[0-9](/\w+)?|MODE[0-9]?|SEL[0-9]?|CLKSEL[0-9]?|PROTOCOL|CONFIG|CFG[0-9]?|"
    r"SA0(/\w+)?|AD0|AD1|ADDR[0-9]?|A[0-2]|IM[0-9]|I2C_ADDR|SDO/ADDR|SDO/SA0)$"
)


def R77_floating_strap_pin(project: Project) -> list[VerifierIssue]:
    """A mode-select or address strap left floating is an undefined part.

    Review #39 (STM32 flight controller): the BNO085's PS1 (pin 5) and
    PS0/WAKE (pin 6) were left on no net. The datasheet says both must be tied
    low for I2C; floating, the sensor does not know which protocol to speak and
    never enumerates. The checker reported zero errors — R16 does see the pins,
    but as a "you still need to wire this" warning among many, and R03 owns
    only EN/CE/RESET/BOOT. A strap pin is a different class: the part is not
    *unfinished*, it is *misconfigured*, and the board is dead on arrival.

    Fires as an ERROR on any placed-and-partially-wired IC whose input pin
    matches a strap-pin name (PSx, MODE, SEL, CLKSEL, SA0/AD0/ADDR, ...) and
    sits on no net. Connectors and passives are out of scope, and so are
    microstep-select pins (M0/M1/MS1-3): the DRV8825/A4988 pull those down
    internally, so floating is a documented full-step configuration.
    """
    issues: list[VerifierIssue] = []
    connected: dict[str, set[str]] = {}
    for net in project.ir.nets:
        for r, p in net.pins:
            connected.setdefault(r, set()).add(p)
    for comp in project.ir.components:
        part = library.get_part(comp.mpn)
        if not part or part.category in {"connector", "passive", "led", "mechanical"}:
            continue
        # An MCU's A0/A1/A2 are ANALOG pins, not address straps (35 false
        # positives on Arduino Nano boards in the sweep). Host headers likewise.
        if part.category == "mcu" or pin_safety.is_mcu_host_header(comp.mpn):
            continue
        wired = connected.get(comp.refdes, set())
        if not wired:
            continue  # R15's job
        for pin in part.pins:
            if pin.type not in {"input", "bidirectional"} or pin.number in wired:
                continue
            if not _STRAP_PIN_RE.match(pin.name.strip().upper()):
                continue
            issues.append(VerifierIssue(
                id=f"R77:{comp.refdes}:{pin.number}",
                severity="error",
                message=(
                    f"{comp.refdes} ({comp.mpn}) strap pin {pin.name} (pin {pin.number}) is "
                    f"floating — a mode/address select pin must be tied high or low per the "
                    f"datasheet, or the part powers up in an undefined configuration."
                ),
                refdes=comp.refdes,
                pinNumber=pin.number,
                rule="R77_floating_strap_pin",
            ))
    return issues


_SECONDARY_BUS_PREFIXES = ("ENV_", "AUX_", "SEC_", "S_SCL", "S_SDA", "EXT_", "MAG_")
_HOST_BUS_PREFIXES = ("H_",)


def R78_secondary_bus_as_host(project: Project) -> list[VerifierIssue]:
    """The MCU is on the sensor's *auxiliary* bus, not its host bus.

    Review #39 (STM32 flight controller): the BNO085's host was wired to pins
    15/16 (ENV_SCL/ENV_SDA — the bus the IMU uses to drive an external
    environmental sensor) while the real host pins 19/20 (H_SCL/H_SDA) stayed
    open. The phrasing test then reproduced it on demand: "use pins 15 and 16
    as the primary interface" was followed in one run. Electrically nothing
    is shorted, so no existing rule saw it; functionally the MCU is talking to
    a port that will never answer.

    Fires as an ERROR when an MCU / host-header pin shares a net with a
    peripheral pin whose name carries a secondary-bus prefix (ENV_, AUX_,
    SEC_, EXT_, MAG_) while that peripheral has an unconnected H_-prefixed
    host pin. Names come from the curated pin table, so a part without the
    H_/ENV_ convention is simply out of scope — never a false positive.
    """
    issues: list[VerifierIssue] = []
    hosts = _mcu_or_host_components(project)
    if not hosts:
        return issues
    parts = {c.refdes: library.get_part(c.mpn) for c in project.ir.components}
    connected: dict[str, set[str]] = {}
    for net in project.ir.nets:
        for r, p in net.pins:
            connected.setdefault(r, set()).add(p)
    for net in project.ir.nets:
        if not any(r in hosts for r, _ in net.pins):
            continue
        for r, pnum in net.pins:
            if r in hosts:
                continue
            part = parts.get(r)
            if not part:
                continue
            pin = next((x for x in part.pins if x.number == pnum), None)
            if not pin or not pin.name.upper().startswith(_SECONDARY_BUS_PREFIXES):
                continue
            host_pins = [x for x in part.pins if x.name.upper().startswith(_HOST_BUS_PREFIXES)]
            open_host = [x for x in host_pins if x.number not in connected.get(r, set())]
            if not host_pins or not open_host:
                continue
            issues.append(VerifierIssue(
                id=f"R78:{r}:{pnum}",
                severity="error",
                message=(
                    f"{r} ({part.mpn}) pin {pin.name} (pin {pnum}) is on net {net.name} with the "
                    f"MCU, but it is the part's auxiliary/secondary bus — the host interface is "
                    + ", ".join(f"{x.name} (pin {x.number})" for x in open_host)
                    + ", which is unconnected. Move the MCU bus to the host pins and leave the "
                    "secondary bus for its own external sensor."
                ),
                refdes=r,
                pinNumber=pnum,
                rule="R78_secondary_bus_as_host",
            ))
    return issues


def R79_regulator_output_cap(project: Project) -> list[VerifierIssue]:
    """A regulator output with no capacitor is an oscillator.

    Phrasing test (2026-08-26), 3.3 V from USB: the AP2112K stage was wired
    right and the thermal maths was right, but the output capacitor was called
    "optional". The Diodes datasheet requires 1 uF minimum on VOUT for
    stability. R01 only looks at power_in pins, so a bare VOUT was invisible.

    Fires as an ERROR on a regulator/charger power_out pin whose net carries
    no capacitor at all, as a WARNING when the only capacitance is under
    1 uF (a 100 nF alone does not stabilise an LDO).
    """
    issues: list[VerifierIssue] = []
    parts = {c.refdes: library.get_part(c.mpn) for c in project.ir.components}
    for comp in project.ir.components:
        part = parts.get(comp.refdes)
        if not part or part.category not in {"regulator", "charger"}:
            continue
        # A plug-in module (TP4056_MOD, MT3608_MOD) carries its own output
        # capacitors on the module PCB — 2 false positives in the sweep.
        if (part.package or "").upper().startswith("MODULE") or comp.mpn.upper().endswith("_MOD"):
            continue
        for pin in part.pins:
            if pin.type != "power_out":
                continue
            # A gate-drive / driver output (CN3791 VG) is not a rail.
            if re.match(r"^(VG|GATE|DRV|OD|OC)(?![A-Z0-9])", pin.name.upper()):
                continue
            net = _net_for_pin(project, comp.refdes, pin.number)
            if not net:
                continue
            caps = [
                uf for n in project.ir.nets if n.name == net
                for r, _ in n.pins if r != comp.refdes
                if (uf := _cap_uf(parts.get(r))) is not None
            ]
            if caps and max(caps) >= 1.0:
                continue
            issues.append(VerifierIssue(
                id=f"R79:{comp.refdes}:{pin.number}",
                severity="error" if not caps else "warning",
                message=(
                    f"{comp.refdes} ({comp.mpn}) output {pin.name} (pin {pin.number}) on net "
                    f"{net} has "
                    + ("no output capacitor" if not caps
                       else f"only {max(caps):g} uF of output capacitance")
                    + " — an LDO/charger output needs at least 1 uF to GND for stability; "
                    "it is not optional."
                ),
                refdes=comp.refdes,
                pinNumber=pin.number,
                rule="R79_regulator_output_cap",
            ))
    return issues


def R80_internal_rail_pin(project: Project) -> list[VerifierIssue]:
    """An internal-regulator pin takes its capacitor and nothing else.

    Framework audit (2026-08-28): the STM32's VCAP — fed by the chip's own
    core regulator — was reported as an unsourced rail (a false alarm, now
    fixed in R26), and the agent then tied the pin to 3.3 V to make the
    warning go away. The datasheet allows exactly one external part on a
    VCAP-class pin: a capacitor to ground. An external supply fights the
    internal regulator and can destroy the chip; ground shorts it outright.

    Error when a VCAP-class pin (VCAP*, VDDCORE, VCORE, DCOUPL) shares a net
    with ground, a named power rail, or another part's power pin; warning
    when the pin is wired but its required capacitor is missing.
    """
    issues: list[VerifierIssue] = []
    parts = {c.refdes: library.get_part(c.mpn) for c in project.ir.components}
    net_pins = {n.name: n.pins for n in project.ir.nets}
    for comp in project.ir.components:
        part = parts.get(comp.refdes)
        if part is None or part.category == "connector":
            continue
        for pin in part.pins:
            if not _INTERNAL_RAIL_RE.match(pin.name.strip()):
                continue
            net = _net_for_pin(project, comp.refdes, pin.number)
            if not net:
                continue  # unwired required pins are R07/R16's finding
            others: list[tuple[str, str | None, LibraryPart | None]] = []
            for r, p in net_pins.get(net, []):
                if r == comp.refdes:
                    continue  # a second VCAP of the same chip is not a fault
                lp = parts.get(r)
                pn = next((pp for pp in (lp.pins if lp else []) if pp.number == p), None)
                others.append((r, pn.type if pn else None, lp))
            problem: str | None = None
            if _is_gnd_net(net):
                problem = ("is tied to ground — that short-circuits the internal "
                           "regulator. Remove the ground tie")
            elif _is_power_net(net) or any(t in ("power_out", "power_in") for _, t, _ in others):
                problem = ("is tied to a supply rail — this pin is fed by the "
                           "chip's OWN on-chip regulator; an external supply "
                           "fights it and can damage the part. Disconnect the rail")
            if problem is not None:
                issues.append(VerifierIssue(
                    id=f"R80:{comp.refdes}:{pin.number}",
                    severity="error",
                    message=(
                        f"{comp.refdes} ({comp.mpn}) {pin.name} (pin {pin.number}) {problem}; "
                        f"connect ONLY its datasheet capacitor to GND on net {net}."
                    ),
                    refdes=comp.refdes,
                    pinNumber=pin.number,
                    net=net,
                    rule="R80_internal_rail_pin",
                ))
                continue
            if not any(_cap_uf(lp) is not None for _, _, lp in others):
                issues.append(VerifierIssue(
                    id=f"R80:{comp.refdes}:{pin.number}",
                    severity="warning",
                    message=(
                        f"{comp.refdes} ({comp.mpn}) {pin.name} (pin {pin.number}) on net {net} "
                        f"has no capacitor — the datasheet requires this pin's decoupling "
                        f"capacitor to GND (check the part's datasheet for the value, "
                        f"typically 1-4.7 uF)."
                    ),
                    refdes=comp.refdes,
                    pinNumber=pin.number,
                    net=net,
                    rule="R80_internal_rail_pin",
                ))
    return issues


def R81_duplicate_controller(project: Project) -> list[VerifierIssue]:
    """Two (or more) controllers on one board when nothing asked for two. Seen
    on the Prompt-to-Product loop check (2026-09-06): "rain alert using ESP32 +
    RGB LED + moisture sensor" shipped with an ESP32-C3-MINI-1 AND an
    ESP32-WROOM-32, 0 errors — twice the power tree, twice the firmware, one
    of them wired to nothing useful. The generic-dev-board over-engineering
    class (R50) for compute. Warning: a co-processor is a legitimate design,
    so the rule speaks only when no recorded requirement asks for more than
    one controller, and it names both so the fix is a one-line delete."""
    mcus = [
        c for c in project.ir.components
        if (p := library.get_part(c.mpn)) and p.category == "mcu"
        and not pin_safety.is_mcu_host_header(c.mpn)
    ]
    if len(mcus) < 2:
        return []
    asked = 0
    mpns = {c.mpn for c in mcus}
    for r in getattr(project.ir, "requirements", None) or []:
        if getattr(r, "category", None) == "mcu" or getattr(r, "mpn", None) in mpns:
            asked += int(getattr(r, "quantity", 1) or 1)
    if asked >= 2:
        return []
    named = ", ".join(f"{c.refdes} ({c.mpn})" for c in mcus)
    return [VerifierIssue(
        id=f"R81:{mcus[0].refdes}",
        severity="warning",
        message=(
            f"Two controllers on one board — {named} — but the request asked for "
            f"one. Each needs its own power tree, programming path and firmware; "
            f"keep the one the peripherals are wired to and delete the other, or "
            f"record why a co-processor is needed."
        ),
        refdes=mcus[0].refdes,
        rule="R81_duplicate_controller", source="verifier",
    )]


ALL_RULES = [
    R01_decoupling_cap,
    R02_i2c_pullups,
    R03_no_floating_en,
    R04_voltage_rail_compat,
    R05_crystal_load_caps,
    R06_usb_dp_dn_routing,
    R07_required_pins,
    R08_gnd_net_present,
    R09_unique_refdes,
    R10_polarized_caps,
    R11_rail_conflict,
    R12_orphan_net,
    R13_signal_terminates_at_mcu,
    R14_excess_regulators,
    R15_floating_component,
    R16_unconnected_signal_input,
    R17_mcu_pin_safety,
    R18_voltage_domain,
    R19_motor_driver_completeness,
    # R20_one_source_per_rail — RETIRED (engineering-rules Phase 1 §6.6): the ERC
    # matrix cell power_out↔power_out (R24) subsumes it and evaluates every net,
    # not just name-classified power rails. The function is kept for reference /
    # external callers but is no longer in the active rule set to avoid
    # double-reporting the same short. R16/R21/R11 are orthogonal (they check a
    # pin across *multiple* nets / on *no* net) so they stay.
    R21_signal_pin_conflict,
    R22_usb_c_cc_termination,
    R23_motor_supply_bulk_cap,
    R24_pin_type_matrix,
    R25_driver_presence,
    R26_power_pin_driven,
    R27_ipc_trace_width,
    R28_ipc_clearance,
    R29_diff_pair_integrity,
    R30_decoupling_per_pin,
    R31_regulator_current_adequate,
    R32_linear_regulator_thermal,
    R34_i2c_address_collision,
    R35_pin_integrity,
    R36_gpio_5v_tolerance,
    R37_five_v_mcu_drives_3v3_part,
    R38_motor_logic_rail_separation,
    R39_flyback_protection,
    R40_header_pin_current,
    R41_motor_stall_brownout,
    R42_ground_return_path,
    R43_requested_quantity,
    R44_mcu_gpios_shorted,
    R45_enable_pin_tied_low,
    R46_led_current_limit,
    R47_two_pin_part_shorted,
    R49_gpio_drive_current,
    R50_redundant_power_stage,
    R51_five_v_output_into_3v3_gpio,
    R52_transistor_control_floating,
    R53_bare_mcu_support,
    R54_connector_input_no_return,
    R55_adc_divider_overvoltage,
    R56_i2c_mode_straps,
    R57_form_factor,
    R58_regulator_input_range,
    R59_adjustable_regulator_feedback,
    R60_function_pins_unwired,
    R61_passive_island,
    R62_battery_protection_fets,
    R63_lowside_switch_topology,
    R64_series_diode_high_current,
    R65_mains_isolation,
    R66_sense_inputs_unwired,
    R67_rail_name_vs_regulator,
    R68_switcher_inductor,
    R69_standard_form_factor_outline,
    R70_board_oversized_for_content,
    R71_hat_id_eeprom,
    R72_module_without_body,
    R73_explicit_part_constraint,
    R74_duplicate_unrequested_port,
    R75_led_current_stated,
    R76_sense_divider_unread,
    R77_floating_strap_pin,
    R78_secondary_bus_as_host,
    R79_regulator_output_cap,
    R80_internal_rail_pin,
    R81_duplicate_controller,
]


def run_all(project: Project) -> list[VerifierIssue]:
    issues: list[VerifierIssue] = []
    for rule in ALL_RULES:
        issues.extend(rule(project))
    return apply_acknowledgements(project, issues)


def apply_acknowledgements(
    project: Project, issues: list[VerifierIssue]
) -> list[VerifierIssue]:
    """An acknowledged warning stays VISIBLE, with the reason attached, but
    stops counting as an open finding.

    "This I2C bus has no pull-ups because the Pi supplies them on its header"
    is a design decision, not a defect — before this, two correct boards in the
    2026-08-20 review round carried "resolve the N warnings" as a blocking
    checklist item forever. Errors can never be acknowledged (enforced in
    `tools.acknowledge_finding`), so this only ever touches warnings/info."""
    acks = {a.issue_id: a for a in (getattr(project.ir, "acknowledgements", None) or [])}
    if not acks:
        return issues
    for iss in issues:
        ack = acks.get(iss.id)
        if ack is not None and iss.severity != "error":
            iss.severity = "info"
            iss.message = f"{iss.message} — acknowledged: {ack.reason}"
    return issues
