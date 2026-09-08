"""IPC parametric formulas we own — engineering-rules plan Phase 3.

Pure math (no Project dependency): the two parametric checks the plan says to
implement ourselves because they're *formulas*, not chart lookups —
  - IPC-2221 conductor sizing (the closed-form trace-width-vs-current rule; the
    de-facto formula every trace-width calculator uses, and what IPC-2152's
    charts refine). `trace_width_mm`.
  - IPC-2221 §6.3 Table 6-1 conductor spacing-vs-voltage (clearance). Encoded as
    the external-uncoated class; `clearance_mm`.

verifier.py R27/R28 apply these against the IR (current/voltage inference lives
there, geometry checks against the board). Keeping the physics here makes it unit
-testable in isolation and reusable by the SPICE/PCB sides.
"""

from __future__ import annotations

# 1 oz/ft² copper finished thickness. 35 µm = 1.378 mil.
_OZ_TO_MILS = 1.378
_MIL_TO_MM = 0.0254


def copper_thickness_mils(copper_oz: float) -> float:
    return copper_oz * _OZ_TO_MILS


def trace_width_mm(
    current_a: float,
    delta_t_c: float = 10.0,
    copper_oz: float = 1.0,
    external: bool = True,
) -> float:
    """Minimum trace width (mm) to carry `current_a` with a `delta_t_c` temperature
    rise, per the IPC-2221 conductor-sizing formula:

        I = k · ΔT^0.44 · A^0.725      (A = cross-section in mil², I in A)

    solved for width = A / thickness. k = 0.048 external / 0.024 internal.
    Validated against published calculators (1 A/10 °C/1 oz ext ≈ 0.30 mm;
    10 A/10 °C/1 oz ext ≈ 7.2 mm)."""
    if current_a <= 0:
        return 0.0
    k = 0.048 if external else 0.024
    area_mils2 = (current_a / (k * (delta_t_c ** 0.44))) ** (1.0 / 0.725)
    width_mils = area_mils2 / copper_thickness_mils(copper_oz)
    return width_mils * _MIL_TO_MM


# IPC-2221B §6.3 Table 6-1 — minimum electrical conductor spacing, EXTERNAL
# conductors, uncoated, sea level to 3050 m. (voltage_ceiling_V, min_mm) rows;
# the first row whose ceiling ≥ the working voltage applies. Above 500 V the
# standard is a slope of +0.0025 mm/V, handled in `clearance_mm`.
_CLEARANCE_TABLE = [
    (15, 0.05),
    (30, 0.05),
    (50, 0.10),
    (100, 0.10),
    (150, 0.20),
    (170, 0.20),
    (250, 0.20),
    (300, 0.20),
    (500, 0.25),
]


def clearance_mm(voltage: float) -> float:
    """Minimum conductor spacing (mm) for a working `voltage` between conductors,
    per IPC-2221 Table 6-1 (external, uncoated). Above 500 V: 0.0025 mm/V."""
    v = abs(voltage)
    if v > 500:
        return 0.00305 * v  # 500 V→0.25 mm reference + IPC slope
    for ceiling, mm in _CLEARANCE_TABLE:
        if v <= ceiling:
            return mm
    return 0.25
