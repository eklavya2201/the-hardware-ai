"""SPICE netlist generation + ngspice subprocess runner — PRD §7.6.

Strategy:
- The library doesn't ship SPICE models for ICs (ESP32, sensors). For those,
  we emit a constant-current sink on each power_in pin to model "MCU draws X mA
  from this rail". That's enough to validate the analog/power-path side of the
  design — power-on transient, decoupling effectiveness, brownout under load.
- Resistors/caps/inductors map to native SPICE R/C/L primitives.
- Diodes get a generic 1N4148-ish model.
- USB-C / micro-USB get a 5V VBUS source.
- LiPo connectors (JST-PH) get a 3.7V VBAT source.
- Regulators get a behavioral voltage source (output at fixed Vout, current-limited).
- Connectors/headers/test points are open (no electrical contribution).

Limitations: digital signals don't simulate. Anything that depends on MCU
behavior (clock, GPIO toggle, etc.) needs a different tool (digital sim or
firmware-in-the-loop). For now: power-path analysis only.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.models import Net, Project
from app.services import library


# Currents for "IC draws X mA on its power rail" model. Conservative estimates;
# real designs would override per-MPN.
_DEFAULT_IC_CURRENT_MA = {
    "mcu": 80.0,         # ESP32 active ~70-160mA, RP2040 ~30-50mA
    "sensor": 5.0,       # BME280, etc.
    "charger": 1.0,
    "memory": 8.0,       # SPI flash active
    "interface": 20.0,
    "display": 30.0,     # OLED/TFT backlight + driver
    "actuator": 100.0,   # vibration motor
    "audio": 50.0,       # class-D amp
    "logic": 5.0,
}

# Default voltage at the output of a regulator if we recognize it.
_REGULATOR_VOUT = {
    "AP2112K-3.3TRG1": 3.3,
    "AMS1117-3.3": 3.3,
    "AMS1117-5.0": 5.0,
    "AMS1117-1.8": 1.8,
    "MCP1700-3302E/TO": 3.3,
    "RT9013-33": 3.3,
    "LD1117-33": 3.3,
    "NCP1117-33": 3.3,
    "AP7361-33": 3.3,
    "MIC5219-3.3": 3.3,
    "TPS61023": 3.3,
    "TPS62840": 3.3,
}


def _sanitize_net(name: str) -> str:
    """SPICE accepts most identifiers but it's safer to strip oddballs."""
    s = re.sub(r"[^A-Za-z0-9_]+", "_", name)
    return s.strip("_") or "N"


def _value_to_spice(value: str | None, default: float = 0.0) -> str:
    """Convert a human-readable value like '10k', '100nF', '4.7uH' to a SPICE
    numeric string (e.g. '10k', '100n', '4.7u')."""
    if not value:
        return str(default)
    s = value.strip().replace("Ω", "").replace("Ohm", "").replace("ohm", "")
    s = s.replace("F", "").replace("H", "").replace(" ", "")
    # SPICE uses 'meg' for mega; everything else is single-letter (k, m, u, n, p, f).
    s = s.replace("M", "meg").replace("MEG", "meg")
    return s


def _classify(part) -> str:
    return getattr(part, "category", "passive") if part else "passive"


def _is_input_connector(part) -> bool:
    if part is None:
        return False
    name = (part.mpn or "").upper()
    if "USB4085" in name or "USB_C" in name:
        return True
    if "10118194" in name or "USB_MICRO" in name:
        return True
    return False


def _is_battery_connector(part) -> bool:
    if part is None:
        return False
    name = (part.mpn or "").upper()
    return name.startswith("S2B-PH") or "JST_PH" in name or "JST-PH" in name


def _net_for_pin(project: Project, refdes: str, pin_number: str) -> str | None:
    for net in project.ir.nets:
        for r, p in net.pins:
            if r == refdes and p == pin_number:
                return net.name
    return None


# ---- Netlist generation ---------------------------------------------------


@dataclass
class NetlistContext:
    """Captures decisions made during netlist generation so the caller can
    surface them ("modeled U1 as 80mA current sink", etc.)."""
    voltage_sources: list[str]
    current_sinks: list[str]
    skipped: list[str]
    modeled: list[str]


def build_netlist(
    project: Project,
    *,
    analysis: str = "tran",
    tran_step: str = "100u",
    tran_stop: str = "10m",
) -> tuple[str, NetlistContext, list[str]]:
    """Generate a ngspice netlist string for the given project.

    `analysis` ∈ {"op", "tran"}. For "op" we output a DC operating point;
    for "tran" we ramp VBUS from 0 to its rated voltage with a 1ms rise time
    so the simulation captures power-on behaviour.
    """
    ctx = NetlistContext(voltage_sources=[], current_sinks=[], skipped=[], modeled=[])

    lines: list[str] = [
        f"* Synqit-generated SPICE netlist for project {project.id}",
        f"* {len(project.ir.components)} components, {len(project.ir.nets)} nets",
        "",
    ]

    # Map every IR net name to a sanitized SPICE node name.
    net_name_map: dict[str, str] = {n.name: _sanitize_net(n.name) for n in project.ir.nets}
    # Ensure GND is canonical (node 0).
    for k, v in list(net_name_map.items()):
        if k.upper() in {"GND", "VSS", "AGND", "DGND"}:
            net_name_map[k] = "0"

    def node(net_name: str | None) -> str:
        if not net_name:
            return "0"
        return net_name_map.get(net_name, _sanitize_net(net_name))

    # ---- Voltage sources for input rails ----
    has_vbus = False
    has_vbat = False

    for c in project.ir.components:
        part = library.get_part(c.mpn)
        if _is_input_connector(part):
            # Wire VBUS pin to a 5V source (transient: ramp 0->5 over 1ms).
            vbus_net = None
            for net in project.ir.nets:
                if any(r == c.refdes and "VBUS" in p.upper() for r, p in net.pins):
                    vbus_net = net.name
                    break
                if any(
                    r == c.refdes and (p == "A4" or p == "B12" or p == "1") for r, p in net.pins
                ) and "VBUS" in net.name.upper():
                    vbus_net = net.name
                    break
            if vbus_net and not has_vbus:
                if analysis == "tran":
                    lines.append(
                        f"V{c.refdes}_USB {node(vbus_net)} 0 PULSE(0 5 100u 500u 1m 10 100)"
                    )
                else:
                    lines.append(f"V{c.refdes}_USB {node(vbus_net)} 0 DC 5")
                ctx.voltage_sources.append(f"V{c.refdes}_USB → {vbus_net} (5V from {c.mpn})")
                has_vbus = True
        elif _is_battery_connector(part):
            vbat_net = None
            for net in project.ir.nets:
                if any(r == c.refdes for r, _ in net.pins) and (
                    "VBAT" in net.name.upper() or "BAT" in net.name.upper()
                ):
                    vbat_net = net.name
                    break
            if vbat_net and not has_vbat:
                lines.append(f"V{c.refdes}_BAT {node(vbat_net)} 0 DC 3.7")
                ctx.voltage_sources.append(f"V{c.refdes}_BAT → {vbat_net} (3.7V LiPo)")
                has_vbat = True

    lines.append("")

    # ---- Components ----
    for c in project.ir.components:
        part = library.get_part(c.mpn)
        category = _classify(part)
        mpn_upper = c.mpn.upper()

        # Find the canonical pair of nets for 2-terminal parts.
        pin_nets = {
            pin: _net_for_pin(project, c.refdes, pin)
            for pin in {p.number for p in (part.pins if part else [])}
        }

        # --- Resistors ---
        if category == "passive" and (
            mpn_upper.startswith("RC") or mpn_upper.startswith("R_")
        ):
            n1 = node(_net_for_pin(project, c.refdes, "1"))
            n2 = node(_net_for_pin(project, c.refdes, "2"))
            value = _value_to_spice(c.value, default=10000)
            lines.append(f"R{c.refdes} {n1} {n2} {value}")
            continue

        # --- Capacitors ---
        if category == "passive" and (
            mpn_upper.startswith("CC") or mpn_upper.startswith("CL") or mpn_upper.startswith("C_")
        ):
            n1 = node(_net_for_pin(project, c.refdes, "1"))
            n2 = node(_net_for_pin(project, c.refdes, "2"))
            value = _value_to_spice(c.value, default=1e-7)
            lines.append(f"C{c.refdes} {n1} {n2} {value}")
            continue

        # --- Inductors ---
        if category == "passive" and (mpn_upper.startswith("L_") or "INDUCTOR" in mpn_upper):
            n1 = node(_net_for_pin(project, c.refdes, "1"))
            n2 = node(_net_for_pin(project, c.refdes, "2"))
            value = _value_to_spice(c.value, default=1e-5)
            lines.append(f"L{c.refdes} {n1} {n2} {value}")
            continue

        # --- LEDs (modeled as a 2V drop diode + series resistor implicitly handled by surrounding R) ---
        if category == "led":
            n_a = node(_net_for_pin(project, c.refdes, "2"))  # anode
            n_k = node(_net_for_pin(project, c.refdes, "1"))  # cathode
            lines.append(f"D{c.refdes} {n_a} {n_k} DLED")
            continue

        # --- Diodes (Schottky / TVS / generic) ---
        if category == "diode":
            n_k = node(_net_for_pin(project, c.refdes, "1"))
            n_a = node(_net_for_pin(project, c.refdes, "2"))
            lines.append(f"D{c.refdes} {n_a} {n_k} DGEN")
            continue

        # --- Regulator: behavioral voltage source on output pin ---
        if category == "regulator":
            vout = _REGULATOR_VOUT.get(c.mpn, 3.3)
            # Heuristic: pick the pin named VOUT or pin 5/2 depending on package.
            out_net = None
            for pn in (part.pins if part else []):
                if pn.name.upper() in {"VOUT", "VO", "OUT"}:
                    out_net = _net_for_pin(project, c.refdes, pn.number)
                    break
            if out_net is None:
                ctx.skipped.append(f"{c.refdes} ({c.mpn}): no output pin found")
                continue
            lines.append(
                f"V{c.refdes}_OUT {node(out_net)} 0 DC {vout}"
            )
            ctx.modeled.append(f"{c.refdes}: ideal {vout}V source on {out_net}")
            continue

        # --- ICs (MCU/sensor/etc.): current sink on each power_in pin ---
        if category in {"mcu", "sensor", "memory", "interface", "display", "audio", "logic", "charger"}:
            ma = _DEFAULT_IC_CURRENT_MA.get(category, 5.0)
            placed_any = False
            for pn in (part.pins if part else []):
                if pn.type != "power_in":
                    continue
                rail = _net_for_pin(project, c.refdes, pn.number)
                if not rail:
                    continue
                lines.append(
                    f"I{c.refdes}_{pn.number} {node(rail)} 0 DC {ma}m"
                )
                placed_any = True
            if placed_any:
                ctx.current_sinks.append(f"{c.refdes} ({c.mpn}): modeled as {ma}mA on its power rails")
            else:
                ctx.skipped.append(f"{c.refdes} ({c.mpn}): no power pin connected")
            continue

        # --- Actuators (vibration, buzzer): treat as a small load ---
        if category == "actuator":
            placed = False
            for pn in (part.pins if part else []):
                if pn.type == "power_in":
                    rail = _net_for_pin(project, c.refdes, pn.number)
                    if rail:
                        lines.append(f"I{c.refdes} {node(rail)} 0 DC 100m")
                        placed = True
                        break
            if placed:
                ctx.current_sinks.append(f"{c.refdes}: 100mA load")
            else:
                ctx.skipped.append(f"{c.refdes}: actuator with no power pin")
            continue

        # --- MOSFETs: skip for now (need topology context) ---
        # --- Transistors, crystals, switches, connectors: open (no contribution) ---
        ctx.skipped.append(f"{c.refdes} ({c.mpn}): {category} not modeled")

    lines.append("")

    # ---- Models ----
    lines.extend([
        "* SPICE models",
        ".MODEL DGEN D(IS=1e-14 N=1.5)",
        ".MODEL DLED D(IS=1e-13 N=1.7)",
        "",
    ])

    # ---- Analysis ----
    lines.append("* Analysis")
    if analysis == "op":
        lines.append(".OP")
    else:  # tran
        lines.append(f".TRAN {tran_step} {tran_stop} UIC")

    # Capture all interesting nets (everything except GND), preserving order so
    # the output parser can pair columns correctly.
    captured_nets = [
        _sanitize_net(n.name)
        for n in project.ir.nets
        if n.name.upper() not in {"GND", "VSS"}
    ][:20]
    print_targets = " ".join(f"v({n})" for n in captured_nets)

    lines.append("")
    lines.append(".CONTROL")
    if analysis == "op":
        lines.append("op")
        lines.append(f"print {print_targets}")
    else:
        lines.append(f"tran {tran_step} {tran_stop}")
        lines.append(f"wrdata sim_out.txt {print_targets}")
    lines.append("quit")
    lines.append(".ENDC")
    lines.append("")
    lines.append(".END")

    return "\n".join(lines), ctx, captured_nets


# ---- ngspice subprocess runner --------------------------------------------


@dataclass
class SimulationResult:
    success: bool
    analysis: str
    netlist: str
    waveforms: dict[str, list[tuple[float, float]]]   # net_name -> [(t, v)]
    op_values: dict[str, float]                       # net_name -> V (DC op only)
    raw_log: str
    error: str | None
    notes: list[str]


def find_ngspice() -> str | None:
    """Locate the ngspice executable. Returns the full path, or None if not
    installed. Searches the OS PATH plus a few common Windows install dirs."""
    # Allow override via env.
    explicit = os.environ.get("NGSPICE_BIN")
    if explicit and Path(explicit).exists():
        return explicit

    found = shutil.which("ngspice")
    if found:
        return found

    # Common Windows install locations.
    for candidate in (
        r"C:\Program Files\ngspice\bin\ngspice.exe",
        r"C:\Program Files (x86)\ngspice\bin\ngspice.exe",
        r"C:\ngspice\bin\ngspice.exe",
    ):
        if Path(candidate).exists():
            return candidate
    return None


def run_simulation(
    project: Project,
    analysis: str = "tran",
    tran_step: str = "100u",
    tran_stop: str = "10m",
) -> SimulationResult:
    """Generate netlist and run ngspice. Always returns a SimulationResult
    (success=False if ngspice missing or simulation failed)."""
    netlist, ctx, captured_nets = build_netlist(
        project, analysis=analysis, tran_step=tran_step, tran_stop=tran_stop
    )

    notes = []
    notes.extend(ctx.voltage_sources)
    notes.extend(ctx.current_sinks)
    notes.extend(f"modeled: {m}" for m in ctx.modeled)
    notes.extend(f"skipped: {s}" for s in ctx.skipped)

    ngspice = find_ngspice()
    if ngspice is None:
        return SimulationResult(
            success=False,
            analysis=analysis,
            netlist=netlist,
            waveforms={},
            op_values={},
            raw_log="",
            error=(
                "ngspice not found. Install it (https://ngspice.sourceforge.io/) "
                "and ensure it's on PATH, or set NGSPICE_BIN to its full path."
            ),
            notes=notes,
        )

    with tempfile.TemporaryDirectory(prefix="voltron_spice_") as tmp:
        tmp_path = Path(tmp)
        cir_path = tmp_path / "circuit.cir"
        out_path = tmp_path / "sim_out.txt"
        cir_path.write_text(netlist, encoding="utf-8")

        try:
            proc = subprocess.run(
                [ngspice, "-b", str(cir_path)],
                cwd=str(tmp_path),
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return SimulationResult(
                success=False, analysis=analysis, netlist=netlist,
                waveforms={}, op_values={}, raw_log="",
                error="Simulation timed out (30s).", notes=notes,
            )

        log = (proc.stdout or "") + "\n" + (proc.stderr or "")

        # ngspice writes errors to stdout/stderr; success is best determined
        # by whether the output file exists and parseable.
        if analysis == "op":
            op_values = _parse_op(log)
            return SimulationResult(
                success=bool(op_values),
                analysis="op",
                netlist=netlist,
                waveforms={},
                op_values=op_values,
                raw_log=log,
                error=None if op_values else "Could not parse OP output. See raw log.",
                notes=notes,
            )

        # transient — wrdata may write to "sim_out.txt" or "sim_out.txt.data"
        # depending on the ngspice version; check both.
        candidates = [out_path, tmp_path / "sim_out.txt.data", tmp_path / "sim_out.data"]
        chosen = next((p for p in candidates if p.exists() and p.stat().st_size > 0), None)
        if chosen is None:
            return SimulationResult(
                success=False, analysis="tran", netlist=netlist,
                waveforms={}, op_values={}, raw_log=log,
                error="ngspice produced no transient output. See raw log.",
                notes=notes,
            )

        waveforms = _parse_wrdata(
            chosen.read_text(encoding="utf-8", errors="replace"),
            expected_nets=captured_nets,
        )
        return SimulationResult(
            success=bool(waveforms) and any(waveforms.values()),
            analysis="tran",
            netlist=netlist,
            waveforms=waveforms,
            op_values={},
            raw_log=log,
            error=None if waveforms and any(waveforms.values()) else "Empty waveform output. Raw log may have details.",
            notes=notes,
        )


def _parse_op(log: str) -> dict[str, float]:
    """Parse `print` output from a `.op` analysis. Lines look like:
    `v(vcc_3v3) = 3.300000e+00`."""
    values: dict[str, float] = {}
    for line in log.splitlines():
        match = re.match(r"\s*v\(([^)]+)\)\s*=\s*([-+0-9.eE]+)", line)
        if match:
            values[match.group(1)] = float(match.group(2))
    return values


def _parse_wrdata(
    text: str, expected_nets: list[str]
) -> dict[str, list[tuple[float, float]]]:
    """Parse ngspice `wrdata` output.

    Format (most common): no header, just numeric rows. Each signal contributes
    a pair of columns — `time, value, time, value, ...`. We need the caller to
    pass the list of nets in the order they appeared in the `wrdata` command.

    Some ngspice builds emit a header line; we skip lines that contain any
    alphabetic character so the parser handles either layout.
    """
    waveforms: dict[str, list[tuple[float, float]]] = {n: [] for n in expected_nets}
    if not expected_nets:
        return waveforms

    expected_cols = 2 * len(expected_nets)

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Skip header / comment / status lines (anything with letters).
        # Numbers can have 'e', 'E', '+', '-', '.', so check for OTHER alpha.
        if re.search(r"[A-DFGHIJ-Zb-df-z]", line):
            continue
        parts = line.split()
        if len(parts) < expected_cols:
            continue
        for i, name in enumerate(expected_nets):
            try:
                t = float(parts[2 * i])
                v = float(parts[2 * i + 1])
                waveforms[name].append((t, v))
            except (ValueError, IndexError):
                pass
    return waveforms
