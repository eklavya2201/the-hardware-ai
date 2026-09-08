"""IR-derived design summary — Phase 1 of the Suyash-feedback roadmap.

Produces a scannable, structured review of a project computed entirely from the
source-of-truth IR (components, nets, pins) and the verifier issues — never from
LLM prose. Because every connection row is derived from ``project.ir.nets``, the
summary can never assert a connection that does not exist on a net. That is the
anti-mismatch guarantee (feedback item #2): the same stance that already stops
the agent inventing parts, applied to explanations.

Public API:
  build(project) -> DesignSummary
  net_contains(project, [(refdes, pin), ...]) -> bool   # the claim primitive
"""

from __future__ import annotations

from app.models import (
    Component,
    DesignSummary,
    NetDetail,
    NetEndpoint,
    PinMapRow,
    PinRisk,
    PowerRailRow,
    Project,
    SummaryComponent,
    VerifierIssue,
)
from app.services import library, pin_safety, verifier

# ---------------------------------------------------------------------------
# Net classification
# ---------------------------------------------------------------------------

# Named rails that aren't caught by the generic prefix test below (VBAT etc.).
_EXTRA_POWER_RAILS = {
    "VBAT", "VIN", "VM", "VMOT", "VMOTOR", "VSYS", "VPP", "VDDA", "VREF",
}


def _is_power_net(name: str) -> bool:
    upper = name.upper()
    if upper in _EXTRA_POWER_RAILS or upper in {"VCC", "VDD"}:
        return True
    return any(
        upper.startswith(p)
        for p in ("VCC", "VDD", "+3V", "+5V", "3V3", "5V", "VBUS", "VBAT")
    )


def _is_gnd_net(name: str) -> bool:
    return name.upper() in {"GND", "VSS", "AGND", "DGND", "0V"}


# ---------------------------------------------------------------------------
# Component / pin helpers (operate on the IR, not the library, so the summary
# reflects exactly what was placed)
# ---------------------------------------------------------------------------

_ROLE_BY_CATEGORY = {
    "mcu": "MCU",
    "regulator": "Voltage regulator",
    "charger": "Battery charger",
    "sensor": "Sensor",
    "passive": "Passive",
    "led": "LED",
    "switch": "Switch",
    "transistor": "Transistor",
    "diode": "Diode",
    "connector": "Connector",
    "crystal": "Crystal / oscillator",
    "protection": "Protection",
    "mechanical": "Mechanical",
    "display": "Display",
    "actuator": "Actuator / driver",
    "memory": "Memory",
    "interface": "Interface",
    "audio": "Audio",
    "logic": "Logic",
}


def _components_by_refdes(project: Project) -> dict[str, Component]:
    return {c.refdes: c for c in project.ir.components}


def _category(mpn: str) -> str | None:
    part = library.get_part(mpn)
    return part.category if part else None


def _mcu_refdeses(project: Project) -> set[str]:
    return {
        c.refdes for c in project.ir.components if _category(c.mpn) == "mcu"
    }


def _mcu_or_host_refdeses(project: Project) -> set[str]:
    """MCU refdeses PLUS shield/HAT host-header connectors (7.3). On a
    form-factor board the compute host is off-board through the header, so the
    pin map treats the header as the MCU surface — a peripheral's signal
    'reaches the MCU' by reaching the header pin wired to the host."""
    out = _mcu_refdeses(project)
    out |= {
        c.refdes
        for c in project.ir.components
        if pin_safety.is_mcu_host_header(c.mpn)
    }
    return out


def _pin_name(comp: Component | None, number: str) -> str:
    """Display name for a stored pin number, e.g. '19' -> 'IO19'. Falls back to
    the number itself when the name is missing or just repeats the number."""
    if comp is not None:
        for pin in comp.pins:
            if pin.number == number:
                return pin.name or number
    return number


def _pin_type(comp: Component | None, number: str) -> str | None:
    if comp is not None:
        for pin in comp.pins:
            if pin.number == number:
                return pin.type
    return None


def _unique(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


# ---------------------------------------------------------------------------
# The claim primitive
# ---------------------------------------------------------------------------


def _same_pin(comp: Component | None, stored: str, token: str) -> bool:
    """True if a query token (number OR name) refers to the pin stored on a net
    (always a canonical number). Tolerant so callers can pass either form."""
    if stored == token:
        return True
    if comp is None:
        return False
    for pin in comp.pins:
        if pin.number == stored or pin.name == stored:
            return token in (pin.number, pin.name)
    return False


def net_contains(project: Project, pairs: list[tuple[str, str]]) -> bool:
    """True iff some single net contains every ``(refdes, pin)`` pair.

    The claim primitive for grounded explanations: the UI/summary may only state
    a connection like ``BIN2 -> GPIO23`` when this returns True for that pin pair.
    ``pin`` may be a pin number or a pin name; both are resolved against the IR.
    """
    if not pairs:
        return False
    comps = _components_by_refdes(project)
    for net in project.ir.nets:
        if all(
            any(
                r == refdes and _same_pin(comps.get(r), p, pin)
                for (r, p) in net.pins
            )
            for (refdes, pin) in pairs
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def _components(project: Project) -> list[SummaryComponent]:
    out: list[SummaryComponent] = []
    for c in project.ir.components:
        cat = _category(c.mpn)
        role = _ROLE_BY_CATEGORY.get(cat or "", (cat or "Component").title())
        out.append(
            SummaryComponent(
                refdes=c.refdes, mpn=c.mpn, role=role, description=c.description
            )
        )
    out.sort(key=lambda s: _refdes_sort_key(s.refdes))
    return out


def _refdes_sort_key(refdes: str) -> tuple[str, int, str]:
    """Sort U1, U2, U10 naturally (prefix letters, then numeric suffix)."""
    prefix = "".join(ch for ch in refdes if ch.isalpha())
    digits = "".join(ch for ch in refdes if ch.isdigit())
    return (prefix, int(digits) if digits else 0, refdes)


def _pin_mapping(project: Project) -> list[PinMapRow]:
    """Walk signal nets and pair each peripheral pin with the MCU pin it reaches.

    Only nets that touch exactly the MCU on one side and a non-passive peripheral
    on the other are considered — power/ground rails and pull-up resistors are
    excluded so the table reads like the builder's mental model.
    """
    comps = _components_by_refdes(project)
    mcus = _mcu_or_host_refdeses(project)
    rows: list[PinMapRow] = []
    for net in project.ir.nets:
        if _is_power_net(net.name) or _is_gnd_net(net.name):
            continue
        mcu_pins = [(r, p) for (r, p) in net.pins if r in mcus]
        if not mcu_pins:
            continue
        periph_pins = [
            (r, p)
            for (r, p) in net.pins
            if r not in mcus and _category(comps[r].mpn if r in comps else "") != "passive"
        ]
        if not periph_pins:
            continue
        # One MCU pin per signal net is the norm; pair peripherals to the first.
        mr, mp = mcu_pins[0]
        mcu_comp = comps.get(mr)
        mcu_pin_label = _pin_name(mcu_comp, mp)
        # Authoritative boot/strap/flash/input-only risk for this MCU pin.
        risk = None
        if mcu_comp is not None:
            verdict = pin_safety.risk(mcu_comp.mpn, mcu_pin_label)
            if verdict:
                risk = PinRisk(level=verdict["level"], note=verdict["note"])  # type: ignore[arg-type]
        for (pr, pp) in periph_pins:
            pcomp = comps.get(pr)
            pin_disp = _pin_name(pcomp, pp)
            rows.append(
                PinMapRow(
                    device=f"{pr} {pin_disp}".strip(),
                    source=f"{pr}.{pp}",
                    mcuPin=mcu_pin_label,
                    mcuRefdes=mr,
                    net=net.name,
                    risk=risk,
                )
            )
    return rows


# Tokens that mark a *raw input* rail (vs a regulated on-board rail).
_INPUT_RAIL_TOKENS = (
    "VBUS", "VBAT", "VIN", "VM", "VMOT", "VSYS", "+5V", "5V", "USB",
)
_RAIL_KIND_ORDER = {"input": 0, "regulated": 1, "rail": 2}


def _rail_upstream(
    project: Project, comps: dict[str, Component], sources: list[str]
) -> list[str]:
    """For each source (regulator/charger) on this rail, the name(s) of the rail
    feeding its own power_in pins — the link that makes the supply chain."""
    out: list[str] = []
    for r in sources:
        comp = comps.get(r)
        if not comp:
            continue
        for pin in comp.pins:
            if _pin_type(comp, pin.number) != "power_in":
                continue
            for net in project.ir.nets:
                if net.name == r:  # defensive; nets aren't named after refdes
                    continue
                if any(rr == r and pp == pin.number for rr, pp in net.pins):
                    if _is_power_net(net.name) and net.name not in out:
                        out.append(net.name)
    return out


# Rules whose findings are power-integrity facts about a specific rail —
# rolled up per power-tree row (Week-5 7.6) so rail problems surface in the
# Power architecture card, not only in the flat issue lists.
_POWER_RULE_PREFIXES = (
    "R11", "R14", "R20", "R23", "R26", "R31", "R32", "R36", "R38", "R40", "R41",
)


def _motor_supply_nets(project: Project) -> set[str]:
    """Nets feeding a motor driver's VM-class (high-current motor supply) pin.
    Mirrors the verifier's R19/R23/R38 detection so the tree and the rules
    always agree on what a 'motor rail' is."""
    from app.services.verifier import _MOTOR_SUPPLY_TOKENS, _is_motor_driver

    out: set[str] = set()
    for comp in project.ir.components:
        part = library.get_part(comp.mpn)
        if not _is_motor_driver(part):
            continue
        for pin in comp.pins:
            if pin.type != "power_in" or not any(
                tok in pin.name.upper() for tok in _MOTOR_SUPPLY_TOKENS
            ):
                continue
            for net in project.ir.nets:
                if any(r == comp.refdes and p == pin.number for r, p in net.pins):
                    out.add(net.name)
    return out


def _power_architecture(project: Project) -> list[PowerRailRow]:
    from app.services.verifier import _net_current_ma

    comps = _components_by_refdes(project)
    mcus = _mcu_refdeses(project)
    motor_nets = _motor_supply_nets(project)
    rows: list[PowerRailRow] = []
    for net in project.ir.nets:
        if _is_gnd_net(net.name) or not _is_power_net(net.name):
            continue
        sources: list[str] = []
        loads: list[str] = []
        source_regulated = False
        source_max_ma: float | None = None
        mcu_on_rail = False
        for (r, p) in net.pins:
            comp = comps.get(r)
            ptype = _pin_type(comp, p)
            if ptype == "power_out":
                sources.append(r)
                if comp and _category(comp.mpn) in {"regulator", "charger"}:
                    source_regulated = True
                part = library.get_part(comp.mpn) if comp else None
                if part and part.currentMaxMa is not None:
                    source_max_ma = max(source_max_ma or 0.0, part.currentMaxMa)
            elif ptype == "power_in":
                loads.append(r)
                if r in mcus:
                    mcu_on_rail = True

        upper = net.name.upper()
        if source_regulated:
            kind = "regulated"
        elif any(tok in upper for tok in _INPUT_RAIL_TOKENS):
            kind = "input"
        else:
            kind = "rail"

        # Robotics role (7.6): a rail feeding a motor driver's VM pin is a
        # motor rail; one that ALSO powers the MCU is the R38 shared-rail
        # hazard, badged "mixed" right in the tree.
        if net.name in motor_nets:
            role = "mixed" if mcu_on_rail else "motor"
        else:
            role = "logic"

        rail_issues = [
            i
            for i in project.issues
            if i.net == net.name and (i.rule or "").startswith(_POWER_RULE_PREFIXES)
        ]
        worst = None
        if any(i.severity == "error" for i in rail_issues):
            worst = "error"
        elif any(i.severity == "warning" for i in rail_issues):
            worst = "warning"
        elif rail_issues:
            worst = "info"

        v_lo, v_hi = verifier.rail_voltage_range(project, net.name, comps)
        rows.append(
            PowerRailRow(
                rail=net.name,
                sources=_unique(sources),
                loads=_unique(loads),
                kind=kind,
                upstream=_rail_upstream(project, comps, _unique(sources)),
                role=role,
                estCurrentMa=_net_current_ma(project, net.name, comps),
                sourceMaxMa=source_max_ma,
                issueCount=len(rail_issues),
                worstSeverity=worst,
                voltageMin=v_lo,
                voltageMax=v_hi,
            )
        )
    # Order by place in the supply chain: inputs first, then regulated rails,
    # then anything we couldn't classify — so the table reads top-down.
    rows.sort(key=lambda r: (_RAIL_KIND_ORDER.get(r.kind, 3), r.rail))
    return rows


# Issues that describe something *absent* (a missing connection/part) rather
# than a risk on something present. Drives the Warnings vs Missing-Items split.
_MISSING_RULES = {
    "R03_no_floating_en",
    "R07_required_pins",
    "R08_gnd_net_present",
    "R12_orphan_net",
    "R13_signal_terminates_at_mcu",
    "R15_floating_component",
    "R16_unconnected_signal_input",
}
_MISSING_KEYWORDS = (
    "missing", "not connected", "no connection", "unconnected", "floating",
    "orphan", "does not reach", "doesn't reach", "absent", "undefined",
    "must be connected", "no gnd", "no ground",
)


def _is_missing(issue: VerifierIssue) -> bool:
    if issue.rule in _MISSING_RULES:
        return True
    msg = (issue.message or "").lower()
    return any(k in msg for k in _MISSING_KEYWORDS)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def net_detail(project: Project, net_name: str) -> NetDetail | None:
    """All pins on a net + routed status (Phase 4 / feedback #8).

    Server-side counterpart to the client's NetDetailPanel, using the
    authoritative library MCU category instead of the most-pins heuristic.
    Returns None when the net doesn't exist.
    """
    net = next((n for n in project.ir.nets if n.name == net_name), None)
    if net is None:
        return None
    comps = _components_by_refdes(project)
    mcus = _mcu_or_host_refdeses(project)

    endpoints: list[NetEndpoint] = []
    mcu_refdes: str | None = None
    mcu_pin: str | None = None
    for (r, p) in net.pins:
        comp = comps.get(r)
        is_mcu = r in mcus
        endpoints.append(
            NetEndpoint(
                refdes=r,
                pin=p,
                name=_pin_name(comp, p),
                isMcu=is_mcu,
                pinType=_pin_type(comp, p),
            )
        )
        if is_mcu and mcu_refdes is None:
            mcu_refdes = r
            mcu_pin = _pin_name(comp, p)

    if _is_gnd_net(net_name):
        role = "gnd"
    elif _is_power_net(net_name):
        role = "power"
    else:
        role = "signal"

    board = project.board
    routed: bool | None = None
    trace_count = vias = rats = 0
    if board is not None:
        trace_count = sum(1 for t in board.traces if t.net == net_name)
        vias = sum(1 for v in board.vias if v.net == net_name)
        rats = sum(1 for r in board.rats if r.net == net_name)
        # A GND net is carried by the copper pour when present — treat as routed.
        if role == "gnd" and board.has_ground_pour:
            routed = True
        else:
            routed = trace_count > 0 and rats == 0

    return NetDetail(
        net=net_name,
        endpoints=endpoints,
        mcuRefdes=mcu_refdes,
        mcuPin=mcu_pin,
        role=role,
        routed=routed,
        traceCount=trace_count,
        vias=vias,
        ratsnest=rats,
        issues=[i for i in project.issues if i.net == net_name],
    )


def build(project: Project) -> DesignSummary:
    """Compute the full IR-derived summary for a project."""
    issues = project.issues
    return DesignSummary(
        components=_components(project),
        pinMapping=_pin_mapping(project),
        powerArchitecture=_power_architecture(project),
        warnings=[i for i in issues if not _is_missing(i)],
        missingItems=[i for i in issues if _is_missing(i)],
    )
