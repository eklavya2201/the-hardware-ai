"""Regression — array/matrix build tools, the requested-quantity gate (R43), and physical-grid placement.

Guards the LED-matrix beta-review fixes (2026-08-15):
  1. `add_component_array` places N copies in a row-major grid with contiguous
     refdeses and returns the shared pin list.
  2. `connect_matrix` wires a rows x cols block into ROW<r>/COL<c> nets, all
     pins canonicalized, all-or-nothing on a bad refdes.
  3. `record_requirements` stores the user's countable asks on the IR (with
     MPN/category validation) and R43 turns any shortfall into a hard error.
  4. `export.manufacturability_report` is not "ready" while R43 fails — an
     incomplete design can never reach the Order panel as ready:true.

Offline: no LLM, no network, no DB (in-memory Project only).
    python -m scripts.regression_matrix_tools
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.models import Project, SchematicIR  # noqa: E402
from app.services import export, tools  # noqa: E402
from app.services.verifier import R43_requested_quantity  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def fresh() -> Project:
    return Project(id="matrix-test", name="matrix-test", path="/tmp/matrix-test", ir=SchematicIR())


def main() -> int:
    p = fresh()

    # --- record_requirements ------------------------------------------------
    result, _ = tools.record_requirements(
        p,
        items=[
            {"description": "3mm through-hole LED", "quantity": 100, "category": "led"},
            {"description": "ESP32 module", "quantity": 1, "mpn": "ESP32-WROOM-32"},
        ],
    )
    check("record_requirements stores both items", len(p.ir.requirements) == 2 and result["recorded"] == 2)

    issues = R43_requested_quantity(p)
    check(
        "R43 errors on the empty design (one per unmet requirement)",
        len(issues) == 2 and all(i.severity == "error" for i in issues),
        f"got {[(i.id, i.severity) for i in issues]}",
    )

    report = export.manufacturability_report(p)
    check(
        "manufacturability not ready while requirements unmet",
        report["ready"] is False
        and any(b.get("kind") == "completeness" for b in report["blockers"]),
    )

    for bad, why in [
        ([{"description": "x", "quantity": 1, "mpn": "NOT_A_REAL_MPN"}], "unknown MPN"),
        ([{"description": "x", "quantity": 1, "category": "nonsense"}], "unknown category"),
        ([{"description": "x", "quantity": 1}], "no mpn or category"),
        ([{"description": "x", "quantity": 0, "category": "led"}], "quantity 0"),
    ]:
        try:
            tools.record_requirements(fresh(), items=bad)
            check(f"record_requirements rejects {why}", False)
        except tools.ToolError:
            check(f"record_requirements rejects {why}", True)

    # --- add_component_array ------------------------------------------------
    result, diff = tools.add_component_array(
        p, mpn="LED_3MM_R", count=100, rows=10, cols=10,
        start_x=0, start_y=0, pitch_x=150, pitch_y=150,
    )
    leds = [c for c in p.ir.components if c.mpn == "LED_3MM_R"]
    check("array places 100 LEDs", len(leds) == 100)
    check(
        "array refdeses are contiguous row-major D1..D100",
        result["refdeses_first"] == "D1" and result["refdeses_last"] == "D100"
        and {c.refdes for c in leds} == {f"D{i}" for i in range(1, 101)},
    )
    d12 = next(c for c in leds if c.refdes == "D12")  # index 11 -> row 1, col 1
    check("array grid positions honour pitch", (d12.x, d12.y) == (150.0, 150.0), f"got {(d12.x, d12.y)}")
    check("array returns the shared pin list", result["pin_count"] == 2 and len(result["pins"]) == 2)

    try:
        tools.add_component_array(fresh(), mpn="LED_3MM_R", count=1000)
        check("array rejects count over ceiling", False)
    except tools.ToolError:
        check("array rejects count over ceiling", True)
    try:
        tools.add_component_array(fresh(), mpn="LED_3MM_R", count=1)
        check("array rejects count 1", False)
    except tools.ToolError:
        check("array rejects count 1", True)

    # --- R43 satisfied ------------------------------------------------------
    tools.add_component(p, mpn="ESP32-WROOM-32", refdes="U1")
    issues = R43_requested_quantity(p)
    check("R43 silent once quantities are met", issues == [], f"got {[i.message for i in issues]}")

    # --- connect_matrix -----------------------------------------------------
    result, _ = tools.connect_matrix(
        p, refdes_prefix="D", rows=10, cols=10, row_pin="A", col_pin="K"
    )
    row0 = next(n for n in p.ir.nets if n.name == "ROW0")
    col9 = next(n for n in p.ir.nets if n.name == "COL9")
    check("connect_matrix wires 100 parts", result["wired"] == 100)
    check(
        "10 row + 10 col nets, 10 pins each",
        len([n for n in p.ir.nets if n.name.startswith("ROW")]) == 10
        and len([n for n in p.ir.nets if n.name.startswith("COL")]) == 10
        and len(row0.pins) == 10 and len(col9.pins) == 10,
    )
    # Pin names canonicalize to numbers: A=2 (anode), K=1 (cathode).
    check(
        "matrix pins canonicalized (A->2 on rows, K->1 on cols)",
        all(pin == "2" for _, pin in row0.pins) and all(pin == "1" for _, pin in col9.pins),
        f"row0={row0.pins[:3]} col9={col9.pins[:3]}",
    )
    check(
        "D1 on ROW0+COL0, D100 on ROW9+COL9",
        ("D1", "2") in row0.pins and ("D100", "1") in col9.pins,
    )

    p2 = fresh()
    tools.add_component_array(p2, mpn="LED_3MM_R", count=4, rows=2, cols=2)
    try:
        tools.connect_matrix(p2, refdes_prefix="D", rows=3, cols=3, row_pin="A", col_pin="K")
        check("connect_matrix rejects a missing refdes (all-or-nothing)", False)
    except tools.ToolError:
        check(
            "connect_matrix rejects a missing refdes (all-or-nothing)",
            not any(n.name.startswith(("ROW", "COL")) for n in p2.ir.nets),
        )

    # --- ready gate end-to-end ---------------------------------------------
    report = export.manufacturability_report(p)
    check(
        "manufacturability ready once the matrix is complete",
        report["ready"] is True,
        f"blockers={report['blockers']}",
    )
    for i in range(61, 101):  # delete 40 LEDs -> 60 remain
        tools.delete_component(p, refdes=f"D{i}")
    report = export.manufacturability_report(p)
    completeness = [b for b in report["blockers"] if b.get("kind") == "completeness"]
    check(
        "deleting 40 LEDs flips ready back to False with the shortfall named",
        report["ready"] is False and completeness and "40" in completeness[0]["reason"],
        f"blockers={report['blockers']}",
    )

    # --- BOM aggregates the array -------------------------------------------
    tools.recompute_bom(p)
    led_line = next(l for l in p.bom if l.mpn == "LED_3MM_R")
    check("BOM aggregates the LED array into one line", led_line.qty == 60)

    # --- Physical grid placement (phase 3) ---------------------------------
    from app.services import mechanical_tools, mechanical_verifier, placement, pcb_verifier

    g = fresh()
    res, _ = tools.add_component_array(g, mpn="LED_3MM_W", count=100, rows=10, cols=10, pitch_mm=12.0)
    check("rows/cols+pitch array is recorded as a physical grid",
          len(g.ir.arrays) == 1 and g.ir.arrays[0].grid and g.ir.arrays[0].pitch_mm == 12.0
          and res["pcb_grid"]["extent_mm"] == [108.0, 108.0])
    tools.add_component_array(g, mpn="CC0603KRX7R9BB104", count=4)
    check("bare-count array is a free batch (not a grid)", g.ir.arrays[1].grid is False and g.ir.arrays[1].pitch_mm is None)
    try:
        tools.add_component_array(fresh(), mpn="LED_3MM_W", count=4, rows=2, cols=2, pitch_mm=2.0)
        check("array rejects a pitch below the footprint minimum", False)
    except tools.ToolError:
        check("array rejects a pitch below the footprint minimum", True)
    tools.connect_matrix(g, refdes_prefix="D", rows=10, cols=10, row_pin="A", col_pin="K")
    tools.add_component(g, mpn="ESP32-WROOM-32", refdes="U1")
    tools.add_component(g, mpn="ULN2003ADR", refdes="U2")
    tools.add_component(g, mpn="USB_C_16Pin_Power_Only", refdes="J1")
    tools.connect(g, net_name="GND", pins=[["U1", "1"], ["U2", "8"], ["J1", "A1"]])

    board = placement.auto_place(g)
    leds = [c for c in board.components if c.mpn == "LED_3MM_W"]
    xs = sorted({round(c.x, 3) for c in leds})
    ys = sorted({round(c.y, 3) for c in leds})
    check("grid: 100 LEDs on exactly 10 columns x 10 rows", len(leds) == 100 and len(xs) == 10 and len(ys) == 10,
          f"x={len(xs)} y={len(ys)}")
    check("grid: exact 12 mm pitch both axes",
          all(abs((xs[i + 1] - xs[i]) - 12.0) < 1e-6 for i in range(9))
          and all(abs((ys[i + 1] - ys[i]) - 12.0) < 1e-6 for i in range(9)))
    d1 = next(c for c in leds if c.refdes == "D1"); d10 = next(c for c in leds if c.refdes == "D10")
    d91 = next(c for c in leds if c.refdes == "D91")
    check("grid: row-major order (D1 top-left, D10 same row, D91 same column)",
          abs(d1.y - d10.y) < 1e-6 and abs(d1.x - d91.x) < 1e-6 and d10.x > d1.x and d91.y > d1.y)
    grid_right = max(xs) + 3.0
    free = [c for c in board.components if c.mpn != "LED_3MM_W"]
    check("free parts sit in the control strip beside the grid, never inside it",
          all(c.x > grid_right for c in free), f"min free x={min(c.x for c in free):.1f} grid_right={grid_right:.1f}")
    check("board sized from grid extent (height ≈ 108 + margins + body)",
          abs(board.height - (2 * placement.GRID_MARGIN + 108.0 + 4.42)) < 0.5, f"h={board.height:.2f}")
    check("grid members carry a grid placementReason", "grid D1-D100" in (d1.placementReason or ""))
    geo = [i for i in pcb_verifier.run_all(board) if i.rule.startswith(("PCB_R01", "PCB_R02"))]
    check("no overlap / off-board PCB issues on the grid board (body-centre R01/R02)", not geo,
          f"{[(i.rule, i.message[:50]) for i in geo]}")

    g.board = board
    mechanical_tools.derive_mounting(g)
    mechanical_verifier.merge_into_project(g)
    mech_err = [i for i in g.issues if (i.rule or "").startswith("MECH") and i.severity == "error"]
    check("derive_mounting: 4 corner holes, 0 mechanical errors",
          len(g.mechanical.mountingHoles) == 4 and not mech_err, f"errs={[i.message[:60] for i in mech_err]}")

    # Re-place with a changed pitch: grid follows, board resizes.
    tools.set_array_pitch(g, array_key="D1-D100", pitch_mm=10.0)
    board2 = placement.auto_place(g)
    xs2 = sorted({round(c.x, 3) for c in board2.components if c.mpn == "LED_3MM_W"})
    check("set_array_pitch: re-place honours the new 10 mm pitch and shrinks the board",
          len(xs2) == 10 and abs((xs2[1] - xs2[0]) - 10.0) < 1e-6 and board2.height < board.height)

    # Deleting a member leaves a gap, not a re-flow.
    tools.delete_component(g, refdes="D55")
    board3 = placement.auto_place(g)
    d56 = next(c for c in board3.components if c.refdes == "D56")
    d54 = next(c for c in board3.components if c.refdes == "D54")
    check("deleted grid member leaves its slot empty (neighbours keep 2-pitch gap)",
          abs((d56.x - d54.x) - 20.0) < 1e-6, f"gap={d56.x - d54.x:.2f}")

    # --- R44: an MCU's own GPIOs shorted on one net (chase-light review) ----
    from app.services.verifier import R44_mcu_gpios_shorted
    r44p = fresh()
    tools.add_component(r44p, mpn="ESP32-WROOM-32", refdes="U1")
    tools.add_component_array(r44p, mpn="LED_R_0603", count=3, rows=1, cols=3, pitch_mm=8)
    pins, _ = tools.allocate_pin(r44p, role="gpio", count=3)
    nums = [pp["number"] for pp in pins["pins"]]
    for i, n in enumerate(nums, 1):
        tools.connect(r44p, net_name=f"LED{i}", pins=[["U1", n], [f"D{i}", "A"]])
    check("R44 silent when each LED has its own GPIO net", not R44_mcu_gpios_shorted(r44p))
    # `connect` now REFUSES a pin that is already on another net (the sweep's
    # #1 defect, R21) — so a second GPIO net can only arise from a hand edit /
    # import. Assert the guard, then build the shorted state on the IR directly
    # so R44 is still exercised.
    try:
        tools.connect(r44p, net_name="LED_CHAIN", pins=[["U1", n] for n in nums])
        check("connect refuses a pin already on another net", False)
    except tools.ToolError:
        check("connect refuses a pin already on another net", True)
    from app.models import Net as _Net
    for net in r44p.ir.nets:
        net.pins = [(r, p) for r, p in net.pins if not (r == "U1" and p in nums)]
    r44p.ir.nets.append(_Net(name="LED_CHAIN", pins=[("U1", n) for n in nums]))
    r44 = R44_mcu_gpios_shorted(r44p)
    check("R44 errors when 3 GPIOs of one MCU share a net", len(r44) == 1 and r44[0].severity == "error"
          and r44[0].net == "LED_CHAIN", f"{[i.message[:60] for i in r44]}")
    tools.connect(r44p, net_name="GND", pins=[["U1", "1"], ["U1", "15"], ["U1", "38"]])
    check("R44 ignores the MCU's many GND pins", len(R44_mcu_gpios_shorted(r44p)) == 1)

    # --- R43 over-count is a warning, not a blocker ---------------------------
    over = fresh()
    tools.record_requirements(over, items=[{"description": "LEDs", "quantity": 2, "category": "led"}])
    tools.add_component_array(over, mpn="LED_R_0603", count=4)
    ov = R43_requested_quantity(over)
    check("R43 over-count by category -> warning", len(ov) == 1 and ov[0].severity == "warning")
    over_mpn = fresh()
    tools.record_requirements(over_mpn, items=[{"description": "IR sensors", "quantity": 3, "mpn": "TCRT5000"}])
    tools.add_component_array(over_mpn, mpn="TCRT5000", count=6)
    om = R43_requested_quantity(over_mpn)
    check("R43 over-count by exact MPN -> error (duplicated block)", len(om) == 1 and om[0].severity == "error")
    check("R43 over-count does not block export readiness",
          not [b for b in export.manufacturability_report(over)["blockers"] if b.get("kind") == "completeness"])

    # --- Board sizing: chase board is compact, strip goes BELOW a short row --
    ch = fresh()
    tools.add_component_array(ch, mpn="LED_3MM_R", count=10, rows=1, cols=10, pitch_mm=8.0)
    tools.add_component_array(ch, mpn="RC0603FR-07330RL", count=10, rows=1, cols=10, pitch_mm=8.0)
    tools.add_component(ch, mpn="ESP32-WROOM-32", refdes="U1")
    tools.add_component(ch, mpn="USB_C_16Pin_Power_Only", refdes="J1")
    cb = placement.auto_place(ch)
    d = {c.refdes: c for c in cb.components}
    check("chase board is compact (< 100 x 80 mm) and holds the ESP32 on-board",
          cb.width < 100 and cb.height < 80 and not [i for i in pcb_verifier.run_all(cb) if i.rule.startswith("PCB_R02")],
          f"{cb.width:.1f}x{cb.height:.1f}")
    check("resistor row sits directly under the LED row (aligned x, below)",
          abs((d["R2"].x - d["R1"].x) - 8.0) < 1e-6 and d["R1"].y > d["D1"].y and d["U1"].y > d["R1"].y,
          f"D1={d['D1'].x:.1f},{d['D1'].y:.1f} R1={d['R1'].x:.1f},{d['R1'].y:.1f} U1y={d['U1'].y:.1f}")

    # --- ir_line_sensor template + arrange_grid + R45 (line-follower review) --
    from app.services import templates
    from app.services.verifier import R45_enable_pin_tied_low
    bot = fresh()
    tools.add_component(bot, mpn="ESP32-WROOM-32", refdes="U1")
    sensors = []
    for outn in ("IR_LEFT", "IR_CENTER", "IR_RIGHT"):
        res_t, _ = templates.apply(bot, template_name="ir_line_sensor",
                                   net_map={"VCC": "VCC_3V3", "GND": "GND", "OUT": outn})
        sensors.append(res_t["created"]["sensor"])
    led_nets = [n for n in bot.ir.nets if n.name.startswith("LED_A_")]
    check("ir_line_sensor: 3 applications -> 3 distinct internal LED nets", len(led_nets) == 3
          and all(len(n.pins) == 2 for n in led_nets), f"{[(n.name, len(n.pins)) for n in led_nets]}")
    out_net = next(n for n in bot.ir.nets if n.name == "IR_LEFT")
    check("ir_line_sensor: OUT is the collector (pin 3) + pull-up, not the emitter",
          (sensors[0], "3") in out_net.pins and not any(pn == "4" for _, pn in out_net.pins))
    tools.arrange_grid(bot, refdeses=sensors, pitch_mm=15)
    tools.add_component(bot, mpn="DRV8833PWPR", refdes="U9")
    for ref in ("M1", "M2", "J1"):
        tools.add_component(bot, mpn="S2B-PH-K-S(LF)(SN)", refdes=ref)
    bb = placement.auto_place(bot)
    sy = {round(c.y, 3) for c in bb.components if c.refdes in sensors}
    sx = sorted(c.x for c in bb.components if c.refdes in sensors)
    check("arrange_grid: sensors form one straight 15 mm row at the front (top) edge",
          len(sy) == 1 and abs((sx[1] - sx[0]) - 15.0) < 1e-6 and min(sy) < bb.height / 3,
          f"y={sy} x={sx} h={bb.height:.1f}")
    check("robot board is compact (< 90 x 90 mm)", bb.width < 90 and bb.height < 90, f"{bb.width:.1f}x{bb.height:.1f}")
    check("R45 silent on the clean robot board", not R45_enable_pin_tied_low(bot))
    tools.connect(bot, net_name="GND", pins=[["U9", "nSLEEP"]])
    r45 = R45_enable_pin_tied_low(bot)
    check("R45 errors when DRV8833 nSLEEP is tied to GND", len(r45) == 1 and r45[0].severity == "error"
          and r45[0].refdes == "U9", f"{[i.message[:60] for i in r45]}")
    tools.disconnect(bot, net_name="GND", pins=[["U9", "11"]])
    tools.connect(bot, net_name="VCC_3V3", pins=[["U9", "nSLEEP"]])
    check("R45 silent when nSLEEP is pulled to the logic rail", not R45_enable_pin_tied_low(bot))
    # R43 over-count: connectors are a supporting category -> no false alarm
    tools.record_requirements(bot, items=[{"description": "motor connectors", "quantity": 2, "category": "connector"}])
    check("R43 over-count skipped for supporting categories (3 connectors vs 2 asked)",
          not R43_requested_quantity(bot))
    try:
        tools.arrange_grid(fresh(), refdeses=["X1", "X2"])
        check("arrange_grid rejects unknown refdes", False)
    except tools.ToolError:
        check("arrange_grid rejects unknown refdes", True)

    # --- R46 / R47 / R21 (line-follower review, run 5) ------------------------
    from app.services.verifier import R46_led_current_limit, R47_two_pin_part_shorted, R21_signal_pin_conflict
    e = fresh()
    tools.add_component(e, mpn="LED_R_0603", refdes="D1")
    tools.add_component(e, mpn="SS14", refdes="D2")
    tools.add_component(e, mpn="RC0603FR-07330RL", refdes="R1")
    tools.add_component(e, mpn="RC0603FR-0710KL", refdes="R2")  # a pull-up elsewhere on the rail
    tools.add_component(e, mpn="ESP32-WROOM-32", refdes="U1")
    tools.connect(e, net_name="VCC_3V3", pins=[["D1", "A"], ["R2", "1"], ["U1", "2"]])
    tools.connect(e, net_name="GND", pins=[["D1", "K"], ["U1", "1"]])
    r46 = R46_led_current_limit(e)
    check("R46: LED straight across VCC/GND is an error even with a pull-up elsewhere on the rail",
          len(r46) == 1 and r46[0].severity == "error" and r46[0].refdes == "D1", f"{[(i.severity, i.message[:50]) for i in r46]}")
    tools.disconnect(e, net_name="VCC_3V3", pins=[["D1", "2"]])
    tools.connect(e, net_name="LED1", pins=[["D1", "A"], ["R1", "2"]])
    tools.connect(e, net_name="VCC_3V3", pins=[["R1", "1"]])
    check("R46 silent with a series resistor on the signal side", not R46_led_current_limit(e))
    tools.connect(e, net_name="VBAT", pins=[["D2", "A"]])
    tools.connect(e, net_name="GND", pins=[["D2", "K"]])
    r46 = R46_led_current_limit(e)
    check("R46: forward-biased Schottky across VBAT/GND is an error (dead short)",
          len(r46) == 1 and r46[0].severity == "error" and r46[0].refdes == "D2" and "short" in r46[0].message)
    tools.disconnect(e, net_name="VBAT", pins=[["D2", "2"]]); tools.disconnect(e, net_name="GND", pins=[["D2", "1"]])
    tools.connect(e, net_name="VBAT", pins=[["D2", "K"]]); tools.connect(e, net_name="GND", pins=[["D2", "A"]])
    check("R46 silent on a reverse-biased (crowbar/TVS-style) diode", not R46_led_current_limit(e))
    tools.add_component(e, mpn="S2B-PH-K-S(LF)(SN)", refdes="J1")
    tools.connect(e, net_name="MOTOR_L", pins=[["J1", "1"], ["J1", "2"]])
    r47 = R47_two_pin_part_shorted(e)
    check("R47: both pins of a 2-pin connector on one net is an error", len(r47) == 1 and r47[0].severity == "error")
    tools.disconnect(e, net_name="MOTOR_L", pins=[["J1", "2"]])
    tools.connect(e, net_name="MOTOR_L_B", pins=[["J1", "2"]])
    check("R47 silent when the pins are on separate nets", not R47_two_pin_part_shorted(e))
    for net in list(e.ir.nets):  # free R1 first — connect refuses a pin on two nets
        net.pins = [(r, p) for r, p in net.pins if r != "R1"]
    tools.connect(e, net_name="MOTOR_L", pins=[["R1", "1"], ["R1", "2"]])
    r47r = [i for i in R47_two_pin_part_shorted(e) if i.refdes == "R1"]
    check("R47 also flags a shorted-out 2-pin passive (both resistor ends on one net)",
          len(r47r) == 1 and r47r[0].severity == "error" and "no effect" in r47r[0].message)

    # --- R49 GPIO drive current / R50 redundant power stage (buzzer review) ---
    from app.services.verifier import R49_gpio_drive_current, R50_redundant_power_stage
    bz = fresh()
    tools.add_component(bz, mpn="Arduino_Nano_V3", refdes="U1")
    tools.add_component(bz, mpn="Buzzer_Active_9mm", refdes="BZ1")
    tools.connect(bz, net_name="BUZZ", pins=[["U1", "D9_PWM"], ["BZ1", "1"]])
    tools.connect(bz, net_name="GND", pins=[["U1", "GND1"], ["BZ1", "2"]])
    r49 = R49_gpio_drive_current(bz)
    check("R49: 30 mA buzzer straight on an Arduino GPIO is an error",
          len(r49) == 1 and r49[0].severity == "error" and "MMBT3904" in r49[0].message,
          f"{[i.message[:70] for i in r49]}")
    check("R50 silent on a Nano board with no extra power stage", not R50_redundant_power_stage(bz))
    tools.add_component(bz, mpn="AP2112K-3.3TRG1", refdes="U2")
    r50 = R50_redundant_power_stage(bz)
    check("R50 warns when a Nano carrier also has its own regulator",
          len(r50) == 1 and r50[0].severity == "warning" and r50[0].refdes == "U1")
    # A bare module (no onboard power) must NOT trip R50.
    bare = fresh()
    tools.add_component(bare, mpn="ESP32-WROOM-32", refdes="U1")
    tools.add_component(bare, mpn="AP2112K-3.3TRG1", refdes="U2")
    check("R50 silent for a bare ESP32-WROOM-32 + LDO (that design needs one)",
          not R50_redundant_power_stage(bare))
    # A logic input on a driver IC must not be read as its output current.
    drv = fresh()
    tools.add_component(drv, mpn="ESP32-WROOM-32", refdes="U1")
    tools.add_component(drv, mpn="DRV8833PWPR", refdes="U2")
    tools.connect(drv, net_name="AIN1", pins=[["U1", "IO13"], ["U2", "AIN1"]])
    check("R49 ignores a driver IC's logic input (its 1.5 A rating is the output)",
          not R49_gpio_drive_current(drv))
    # connect now refuses this at the source; build the two-net state directly
    # (a hand edit / import) so R21 itself is still exercised.
    from app.models import Net as _NetR21
    e.ir.nets.append(_NetR21(name="SENSE", pins=[("U1", "2")]))  # U1.3V3 already on VCC_3V3 -> also on a signal net
    r21 = [i for i in R21_signal_pin_conflict(e) if i.refdes == "U1"]
    check("R21 now flags a pin on a power rail AND a signal net", len(r21) == 1 and r21[0].severity == "error",
          f"{[i.message[:60] for i in r21]}")

    # --- small design: a big module must stay on the board (buzzer review) ----
    tiny = fresh()
    tools.add_component(tiny, mpn="Arduino_Nano_V3", refdes="U1")
    tools.add_component(tiny, mpn="Buzzer_Active_9mm", refdes="BZ1")
    tools.add_component(tiny, mpn="MMBT3904", refdes="Q1")
    tools.add_component(tiny, mpn="RC0603FR-071KL", refdes="R1")
    tools.connect(tiny, net_name="GND", pins=[["U1", "GND1"], ["Q1", "2"]])
    tb = placement.auto_place(tiny)
    off = [i for i in pcb_verifier.run_all(tb) if i.rule.startswith("PCB_R02")]
    check("<10-part board keeps a 46 mm module inside the outline",
          not off and tb.width < 60 and tb.height < 80,
          f"{tb.width:.1f}x{tb.height:.1f} {[i.message[:40] for i in off]}")

    # --- R46 multi-channel LED / R51 5V->3V3 / R52 transistor / PCB sync -----
    from app.services import pcb_verifier as _pcbv
    from app.services.verifier import (R51_five_v_output_into_3v3_gpio,
                                       R52_transistor_control_floating,
                                       R19_motor_driver_completeness)
    rgb = fresh()
    tools.add_component(rgb, mpn="ESP32-WROOM-32", refdes="U1")
    tools.add_component(rgb, mpn="LED_RGB_5050", refdes="D1")
    tools.connect(rgb, net_name="GND", pins=[["U1", "1"], ["D1", "K"]])
    for pin, gpio in (("R", "IO16"), ("G", "IO17"), ("B", "IO18")):
        tools.connect(rgb, net_name=f"LED_{pin}", pins=[["U1", gpio], ["D1", pin]])
    r46 = R46_led_current_limit(rgb)
    check("R46: RGB channels straight on GPIOs is an error naming all three",
          len(r46) == 1 and r46[0].severity == "error"
          and all(ch in r46[0].message for ch in ("R", "G", "B")),
          f"{[i.message[:70] for i in r46]}")
    for i, (pin, gpio) in enumerate((("R", "IO16"), ("G", "IO17"), ("B", "IO18")), start=1):
        tools.disconnect(rgb, net_name=f"LED_{pin}", pins=[["U1", gpio]])
        tools.add_component(rgb, mpn="RC0603FR-07330RL", refdes=f"R{i}")
        tools.connect(rgb, net_name=f"LED_{pin}_DRV", pins=[["U1", gpio], [f"R{i}", "1"]])
        tools.connect(rgb, net_name=f"LED_{pin}", pins=[[f"R{i}", "2"]])
    check("R46 silent once every RGB channel has its own resistor", not R46_led_current_limit(rgb))

    us = fresh()
    tools.add_component(us, mpn="ESP32-WROOM-32", refdes="U1")
    tools.add_component(us, mpn="HC-SR04", refdes="J5")
    tools.connect(us, net_name="+5V", pins=[["J5", "VCC"]])
    tools.connect(us, net_name="GND", pins=[["U1", "1"], ["J5", "GND"]])
    tools.connect(us, net_name="TRIG", pins=[["U1", "IO16"], ["J5", "TRIG"]])
    tools.connect(us, net_name="ECHO", pins=[["U1", "IO17"], ["J5", "ECHO"]])
    r51 = R51_five_v_output_into_3v3_gpio(us)
    check("R51: 5 V HC-SR04 ECHO straight into an ESP32 GPIO is an error",
          len(r51) == 1 and r51[0].severity == "error" and r51[0].net == "ECHO",
          f"{[i.message[:70] for i in r51]}")
    check("R51 does not flag TRIG (an MCU output into the module)",
          not any(i.net == "TRIG" for i in r51))
    tools.disconnect(us, net_name="ECHO", pins=[["U1", "IO17"]])
    tools.add_component(us, mpn="RC0603FR-0710KL", refdes="R1")
    tools.connect(us, net_name="ECHO", pins=[["R1", "1"]])
    tools.connect(us, net_name="ECHO_3V3", pins=[["R1", "2"], ["U1", "IO17"]])
    check("R51 silent once a divider resistor is in the path", not R51_five_v_output_into_3v3_gpio(us))

    tr = fresh()
    tools.add_component(tr, mpn="Arduino_Nano_V3", refdes="U1")
    tools.add_component(tr, mpn="MMBT3904", refdes="Q1")
    tools.add_component(tr, mpn="Buzzer_Active_9mm", refdes="BZ1")
    tools.add_component(tr, mpn="RC0603FR-071KL", refdes="R1")
    tools.connect(tr, net_name="GND", pins=[["U1", "GND1"], ["Q1", "E"]])
    tools.connect(tr, net_name="5V", pins=[["U1", "5V"], ["BZ1", "1"]])
    tools.connect(tr, net_name="BUZZ_DRV", pins=[["BZ1", "2"], ["Q1", "C"]])
    # the live bug: the base resistor bridges two MCU pins, base left floating
    tools.connect(tr, net_name="CTRL", pins=[["U1", "D9_PWM"], ["R1", "1"]])
    tools.connect(tr, net_name="SENSE", pins=[["U1", "A0"], ["R1", "2"]])
    r52 = R52_transistor_control_floating(tr)
    check("R52: a floating transistor base is an error (the live buzzer bug)",
          len(r52) == 1 and r52[0].severity == "error" and r52[0].refdes == "Q1",
          f"{[i.message[:70] for i in r52]}")
    tools.disconnect(tr, net_name="SENSE", pins=[["R1", "2"]])
    tools.connect(tr, net_name="BASE", pins=[["R1", "2"], ["Q1", "B"]])
    check("R52 silent once the base reaches a GPIO through its resistor",
          not R52_transistor_control_floating(tr))
    tools.add_component(tr, mpn="MMBT3904", refdes="Q2")
    tools.connect(tr, net_name="GND", pins=[["Q2", "E"]])
    tools.connect(tr, net_name="DIRECT_BASE", pins=[["U1", "D8"], ["Q2", "B"]])
    r52b = [i for i in R52_transistor_control_floating(tr) if i.refdes == "Q2"]
    check("R52: a BJT base straight on a GPIO (no series resistor) is an error",
          len(r52b) == 1 and "1 kOhm" in r52b[0].message, f"{[i.message[:60] for i in r52b]}")
    fet = fresh()
    tools.add_component(fet, mpn="ESP32-WROOM-32", refdes="U1")
    tools.add_component(fet, mpn="AO3400A", refdes="Q1")
    tools.connect(fet, net_name="GND", pins=[["U1", "1"], ["Q1", "S"]])
    tools.connect(fet, net_name="GATE", pins=[["U1", "IO16"], ["Q1", "G"]])
    check("R52 does not demand a gate resistor on a FET (voltage-driven)",
          not R52_transistor_control_floating(fet))

    # R19 accepts a motor rail named VM (net-class fix)
    mot = fresh()
    tools.add_component(mot, mpn="ESP32-WROOM-32", refdes="U1")
    tools.add_component(mot, mpn="TB6612FNG", refdes="U2")
    tools.add_component(mot, mpn="JST_XH_2P", refdes="J1")
    tools.add_component(mot, mpn="JST_XH_2P", refdes="J2")
    tools.connect(mot, net_name="VM", pins=[["U2", "VM"]])
    tools.connect(mot, net_name="GND", pins=[["U1", "1"], ["U2", "GND"]])
    tools.connect(mot, net_name="MOT_A1", pins=[["U2", "AO1"], ["J1", "1"]])
    tools.connect(mot, net_name="MOT_A2", pins=[["U2", "AO1B"], ["J1", "2"]])
    tools.connect(mot, net_name="MOT_B1", pins=[["U2", "BO1"], ["J2", "1"]])
    tools.connect(mot, net_name="MOT_B2", pins=[["U2", "BO2"], ["J2", "2"]])
    r19_unwired = [i for i in R19_motor_driver_completeness(mot) if i.id.startswith("R19:unwired")]
    check("R19 silent on a driver whose outputs are wired to connectors", not r19_unwired)
    bare = fresh()
    tools.add_component(bare, mpn="ESP32-WROOM-32", refdes="U1")
    tools.add_component(bare, mpn="TB6612FNG", refdes="U2")
    tools.add_component(bare, mpn="JST_XH_2P", refdes="J1")
    tools.connect(bare, net_name="VM", pins=[["U2", "VM"]])
    tools.connect(bare, net_name="VCC_5V", pins=[["J1", "1"]])
    tools.connect(bare, net_name="VM", pins=[["J1", "2"]])
    r19_bare = [i for i in R19_motor_driver_completeness(bare) if i.id.startswith("R19:unwired")]
    check("R19: a driver with NO output wired is an error (the live rover bug)",
          len(r19_bare) == 1 and r19_bare[0].severity == "error", f"{[i.message[:60] for i in r19_bare]}")
    uln = fresh()
    tools.add_component(uln, mpn="ESP32-WROOM-32", refdes="U1")
    tools.add_component(uln, mpn="ULN2003ADR", refdes="U3")
    tools.add_component(uln, mpn="LED_R_0603", refdes="D1")
    tools.connect(uln, net_name="COL0", pins=[["U3", "16"], ["D1", "K"]])
    check("R19 tolerates unused channels on a multi-channel array",
          not [i for i in R19_motor_driver_completeness(uln) if i.id.startswith("R19:unwired")])
    check("a rail named VM counts as a power rail (R19 no longer mis-flags it)",
          not [i for i in R19_motor_driver_completeness(mot) if "motor supply" in i.message],
          f"{[i.message[:60] for i in R19_motor_driver_completeness(mot)]}")

    # PCB_R08: a board that predates a schematic edit
    sync = fresh()
    tools.add_component(sync, mpn="ESP32-WROOM-32", refdes="U1")
    tools.add_component(sync, mpn="CC0603KRX7R9BB104", refdes="C1")
    sync.board = placement.auto_place(sync)
    check("PCB sync clean right after auto-place", not _pcbv.board_sync_issues(sync))
    tools.add_component(sync, mpn="LED_R_0603", refdes="D1")
    sync_issues = _pcbv.board_sync_issues(sync)
    check("PCB_R08: a part added after auto-place is an error naming the fix",
          len(sync_issues) == 1 and sync_issues[0].severity == "error"
          and "D1" in sync_issues[0].message and "auto-place" in sync_issues[0].message,
          f"{[i.message[:80] for i in sync_issues]}")
    sync.board = placement.auto_place(sync)
    check("PCB_R08 clears after re-running auto-place", not _pcbv.board_sync_issues(sync))

    # --- honesty guards: chat status + stale-board export (risk sweep) --------
    from app.models import ChatMessage
    from app.services import agent as _agent
    hon = fresh()
    tools.add_component(hon, mpn="ESP32-WROOM-32", refdes="U1")
    tools.connect(hon, net_name="SIG", pins=[["U1", "IO16"]])  # orphan net -> error
    tools.run_drc(hon)
    hon.chat.append(ChatMessage(id="m1", role="assistant",
                                text="All blocking DRC errors are resolved; proceed to layout."))
    _agent._append_drc_status(hon)
    check("chat cannot claim 'DRC resolved' while errors remain (review #6 pt 15)",
          "NOT clean" in hon.chat[-1].text and "blocking DRC error" in hon.chat[-1].text,
          hon.chat[-1].text[-80:])
    _agent._append_drc_status(hon)
    check("the correction is not appended twice on a re-run",
          hon.chat[-1].text.count("NOT clean") == 1)
    clean_p = fresh()
    for _ in _agent._run_stub(clean_p, "esp32"):
        pass
    clean_p.chat.append(ChatMessage(id="m2", role="assistant", text="Done."))
    _agent._append_drc_status(clean_p)
    check("a clean design gets no status footer", clean_p.chat[-1].text == "Done.")

    stale = fresh()
    tools.add_component(stale, mpn="ESP32-WROOM-32", refdes="U1")
    tools.add_component(stale, mpn="CC0603KRX7R9BB104", refdes="C1")
    stale.board = placement.auto_place(stale)
    check("export is ready when the board matches the schematic",
          export.manufacturability_report(stale)["ready"] is True)
    tools.add_component(stale, mpn="LED_R_0603", refdes="D1")
    rep = export.manufacturability_report(stale)
    check("a stale board blocks export (cannot ship the old layout's Gerbers)",
          rep["ready"] is False and any(b.get("kind") == "sync" for b in rep["blockers"]))

    # --- malformed tool calls must never kill the turn (live crash) ----------
    mal = fresh()
    tools.add_component(mal, mpn="ESP32-WROOM-32", refdes="U1")
    tools.add_component(mal, mpn="TB6612FNG", refdes="U2")
    tools.add_component(mal, mpn="JST_XH_2P", refdes="J1")
    for bad, label in (
        ([["U2", "AO1", "J1"]], "3-element pin entry"),
        ([["U2"]], "1-element pin entry"),
        (["U2"], "bare string instead of a pair"),
    ):
        try:
            tools.dispatch(mal, "connect", {"net_name": "MOT", "pins": bad})
            check(f"connect rejects a {label}", False)
        except tools.ToolError:
            check(f"connect rejects a {label} as a ToolError", True)
        except Exception as exc:  # noqa: BLE001
            check(f"connect rejects a {label} as a ToolError", False, f"escaped as {type(exc).__name__}")
    res_ok, _ = tools.dispatch(mal, "connect", {"net_name": "MOT", "pins": [["U2", "AO1"], ["J1", "1"]]})
    check("well-formed pairs still connect", res_ok["added"] == ["U2.1", "J1.1"])
    try:
        tools.dispatch(mal, "connect", {"parameters": {"net_name": "X"}})
        check("dispatch rejects an unknown keyword as a ToolError", False)
    except tools.ToolError:
        check("dispatch rejects an unknown keyword as a ToolError", True)

    # --- reviews #8-#12: requirement gaming, bare-MCU support, probe return, router carve
    from app.services import library as _lib
    from app.services import routing as _routing
    from app.services.verifier import R53_bare_mcu_support, R54_connector_input_no_return

    for m in ("5161AS", "5161BS", "TM1637_4Digit_Module", "TTP223_Module", "TouchPad_D12mm",
              "MAX9814_Module", "KY-038", "Water_Level_Sensor_Module", "Soil_Moisture_Capacitive_v1.2"):
        check(f"library has {m}", _lib.get_part(m) is not None)
    rp = _lib.get_part("RP2040")
    rp_num = {pn.name: pn.number for pn in rp.pins}
    check("RP2040 pin table is the real QFN-56 map (USB_DM=46, RUN=26, VREG_IN=44, 30 GPIOs)",
          rp_num.get("USB_DM") == "46" and rp_num.get("RUN") == "26" and rp_num.get("VREG_IN") == "44"
          and sum(1 for pn in rp.pins if pn.name.startswith("GPIO")) == 30)

    # record_requirements: substitution language is rejected; unavailable=true is an R43 error
    g = fresh()
    try:
        tools.record_requirements(g, items=[{"description": "7-segment display replaced by single LED", "quantity": 1, "mpn": "LED_3MM_R"}])
        check("record_requirements rejects a re-worded substitute requirement", False)
    except tools.ToolError:
        check("record_requirements rejects a re-worded substitute requirement", True)
    tools.record_requirements(g, items=[{"description": "single-digit 7-segment display", "quantity": 1, "unavailable": True}])
    r43u = R43_requested_quantity(g)
    check("unavailable requirement is a hard R43 error (design incomplete)",
          len(r43u) == 1 and r43u[0].severity == "error" and "not in the curated" in r43u[0].message)
    rep = export.manufacturability_report(g)
    check("unavailable requirement blocks ready:true", not rep.get("ready", True))

    # R53: bare RP2040 with no flash / crystal is unbootable -> 2 errors; Pico module is silent
    bare = fresh()
    tools.add_component(bare, mpn="RP2040", refdes="U1")
    r53 = R53_bare_mcu_support(bare)
    check("R53: bare RP2040 without QSPI flash + crystal = 2 errors",
          len(r53) == 2 and all(i.severity == "error" for i in r53), f"{[i.message[:60] for i in r53]}")
    tools.add_component(bare, mpn="W25Q128JVSIQ", refdes="U2")
    tools.add_component(bare, mpn="Q_12MHz_3225", refdes="Y1")
    check("R53 silent once flash + 12 MHz crystal are on the board", not R53_bare_mcu_support(bare))
    pico = fresh()
    tools.add_component(pico, mpn="RaspberryPi_Pico", refdes="U1")
    check("R53 silent on the Pico module", not R53_bare_mcu_support(pico))

    # R54: soil review - Nano + 2-pin header {5V, A0}, no GND -> error; 3-pin sensor header w/ GND -> quiet
    soil = fresh()
    tools.add_component(soil, mpn="Arduino_Nano_V3", refdes="U1")
    tools.add_component(soil, mpn="PinHeader_1x2_P2.54mm", refdes="J1")
    tools.connect(soil, net_name="VCC_5V", pins=[["U1", "5V"], ["J1", "2"]])
    tools.connect(soil, net_name="SOIL_SIG", pins=[["J1", "1"], ["U1", "A0"]])
    r54 = R54_connector_input_no_return(soil)
    check("R54: probe header with no GND feeding A0 is an error", len(r54) == 1 and r54[0].severity == "error")
    okc = fresh()
    tools.add_component(okc, mpn="Arduino_Nano_V3", refdes="U1")
    tools.add_component(okc, mpn="Soil_Moisture_Capacitive_v1.2", refdes="U2")
    tools.connect(okc, net_name="VCC_5V", pins=[["U1", "5V"], ["U2", "VCC"]])
    tools.connect(okc, net_name="GND", pins=[["U1", "GND1"], ["U2", "GND"]])
    tools.connect(okc, net_name="SOIL", pins=[["U2", "AOUT"], ["U1", "A0"]])
    check("R54 silent for a real sensor MODULE on the ADC", not R54_connector_input_no_return(okc))
    dht = fresh()
    tools.add_component(dht, mpn="Arduino_Nano_V3", refdes="U1")
    tools.add_component(dht, mpn="PinHeader_1x3_P2.54mm", refdes="J1")
    tools.connect(dht, net_name="VCC_5V", pins=[["U1", "5V"], ["J1", "1"]])
    tools.connect(dht, net_name="GND", pins=[["U1", "GND1"], ["J1", "3"]])
    tools.connect(dht, net_name="DATA", pins=[["J1", "2"], ["U1", "D2"]])
    check("R54 silent for a 3-pin sensor header (VCC/DATA/GND) on a digital pin", not R54_connector_input_no_return(dht))

    # Router: an 0603 LED row + shared VCC net used to leave rats (pads sealed by the body keepout)
    row = fresh()
    tools.add_component(row, mpn="RaspberryPi_Pico", refdes="U1")
    tools.add_component_array(row, mpn="LED_Y_0603", count=8, rows=1, cols=8, pitch_mm=10.0)
    tools.add_component_array(row, mpn="RC0603FR-07330RL", count=8, rows=1, cols=8, pitch_mm=10.0)
    for k in range(1, 9):
        tools.connect(row, net_name=f"LED{k}", pins=[["U1", f"GP{k}"], [f"R{k}", "1"]])
        tools.connect(row, net_name=f"LEDA{k}", pins=[[f"R{k}", "2"], [f"D{k}", "2"]])
        tools.connect(row, net_name="GND", pins=[[f"D{k}", "1"]])
    tools.connect(row, net_name="GND", pins=[["U1", "GND1"] if any(pn.name == "GND1" for pn in row.ir.components[0].pins) else ["U1", "3"]])
    row.board = placement.auto_place(row)
    rb = _routing.auto_route(row)
    check("router: LED row + resistor row routes with zero rats (pad approach carve)", len(rb.rats) == 0, f"{len(rb.rats)} rats")

    # --- reviews #13-#16: rail sourcing, ADC divider, I2C straps, relay --------
    from app.services.verifier import (
        R03_no_floating_en, R26_power_pin_driven, R55_adc_divider_overvoltage,
        R56_i2c_mode_straps,
    )
    from app.services import templates as _tpl
    from app.services import verifier

    for m in ("SRD-05VDC-SL-C", "Relay_Module_1CH_5V"):
        check(f"library has {m}", _lib.get_part(m) is not None)
    check("relay_lock_driver template exists", _tpl.get_template("relay_lock_driver") is not None)

    # R26: a battery connector on the rail IS the source; without one it is an error.
    src = fresh()
    tools.add_component(src, mpn="ESP32-WROOM-32", refdes="U1")
    tools.add_component(src, mpn="AP2112K-3.3TRG1", refdes="U2")
    tools.connect(src, net_name="VBAT", pins=[["U2", "1"]])
    r26 = [i for i in R26_power_pin_driven(src) if i.severity == "error"]
    check("R26: a rail feeding a regulator with no source at all is an error", len(r26) == 1)
    tools.add_component(src, mpn="ScrewTerminal_2P_5mm", refdes="J1")
    tools.connect(src, net_name="VBAT", pins=[["J1", "1"]])
    check("R26 silent once a power connector sources the rail", not R26_power_pin_driven(src))

    # R03: a floating regulator EN is an error; a module's RESET stays a warning.
    en = fresh()
    tools.add_component(en, mpn="AP2112K-3.3TRG1", refdes="U1")
    r03 = [i for i in R03_no_floating_en(en) if i.refdes == "U1" and "EN" in i.message]
    check("R03: a regulator's floating EN is an error",
          len(r03) == 1 and r03[0].severity == "error")
    nano = fresh()
    tools.add_component(nano, mpn="Arduino_Nano_V3", refdes="U1")
    r03n = [i for i in R03_no_floating_en(nano) if i.severity == "error"]
    check("R03: a module's floating RESET stays a warning", not r03n)

    # R55: a 1:1 battery divider into an ADC pin is an error; a sized one is not.
    for top, bot, want in (("RC0603FR-07100KL", "RC0603FR-07100KL", True),
                           ("RC0603FR-07100KL", "RC0603FR-0727KL", False)):
        dv = fresh()
        tools.add_component(dv, mpn="ESP32-WROOM-32", refdes="U1")
        tools.add_component(dv, mpn=top, refdes="R1")
        tools.add_component(dv, mpn=bot, refdes="R2")
        tools.connect(dv, net_name="VBAT", pins=[["R1", "1"]])
        tools.connect(dv, net_name="BATT_SENSE", pins=[["R1", "2"], ["R2", "1"], ["U1", "IO34"]])
        tools.connect(dv, net_name="GND", pins=[["R2", "2"], ["U1", "1"]])
        got = bool([i for i in R55_adc_divider_overvoltage(dv) if i.severity == "error"])
        check(f"R55: {'1:1 divider errors' if want else 'a sized divider is silent'}", got == want)

    # R56: CSB on GND selects SPI (error); CSB high + SDO tied is silent.
    for csb_net, want in (("GND", True), ("VCC_3V3", False)):
        w = fresh()
        tools.add_component(w, mpn="ESP32-WROOM-32", refdes="U1")
        tools.add_component(w, mpn="BME280", refdes="U2")
        tools.connect(w, net_name="VCC_3V3", pins=[["U1", "2"], ["U2", "6"], ["U2", "8"]])
        tools.connect(w, net_name="GND", pins=[["U1", "1"], ["U2", "5"], ["U2", "7"], ["U2", "4"]])
        tools.connect(w, net_name=csb_net, pins=[["U2", "1"]])
        tools.connect(w, net_name="I2C_SDA", pins=[["U1", "33"], ["U2", "2"]])
        tools.connect(w, net_name="I2C_SCL", pins=[["U1", "36"], ["U2", "3"]])
        got = bool([i for i in R56_i2c_mode_straps(w) if i.severity == "error"])
        check(f"R56: CSB on {csb_net} {'errors' if want else 'is accepted'}", got == want)
        if not want:
            check("R24 accepts SDO strapped to GND as a deliberate address strap",
                  not [i for i in verifier.R24_pin_type_matrix(w)
                       if i.severity == "error" and "U2" in (i.message or "")])

    # The reference stub design must itself be clean under the new rules.
    stub = fresh()
    for _ in _agent._run_stub(stub, "esp32"):
        pass
    check("the built-in stub design has 0 blocking errors",
          not [i for i in verifier.run_all(stub) if i.severity == "error"],
          f"{[(i.rule, i.message[:60]) for i in verifier.run_all(stub) if i.severity == 'error']}")

    # ESP32 module body = the real 18x25.5 mm can, not the antenna keep-out.
    from app.services import footprints as _fp
    esp = _fp.for_part(_lib.get_part("ESP32-WROOM-32"))
    check("ESP32-WROOM-32 body is the module can (keep-out stays in the courtyard)",
          esp.width < 20 and esp.height < 30 and (esp.courtyard_w or 0) > 40,
          f"{esp.width}x{esp.height} courtyard {esp.courtyard_w}x{esp.courtyard_h}")

    # --- review #17: real MCU pin tables, allocator guard, empty-turn guard ---
    from app.services import pin_safety as _ps

    for mpn, want in (("STM32F411CEU6", 40), ("STM32F103C8T6", 40), ("STM32G031G8U6", 20)):
        part = _lib.get_part(mpn)
        check(f"{mpn} has a real pin table (>= {want} pins)",
              part is not None and len(part.pins) >= want,
              f"{len(part.pins) if part else 0} pins")
    check("pin_safety knows the STM32 family", _ps.is_supported("STM32F411CEU6"))

    # allocate_pin returns the datasheet bus pins, never a reserved one.
    al = fresh()
    tools.add_component(al, mpn="STM32F411CEU6", refdes="U1")
    got = {}
    for role in ("i2c_sda", "i2c_scl", "spi_sck", "spi_mosi", "spi_miso", "spi_cs", "adc"):
        res, _ = tools.allocate_pin(al, role=role)
        pin = res["pins"][0]
        got[role] = pin["name"] if isinstance(pin, dict) else str(pin)
        tools.connect(al, net_name=f"N_{role}", pins=[["U1", pin["number"] if isinstance(pin, dict) else pin]])
    check("allocate_pin gives STM32 the datasheet bus pins (I2C PB7/PB6, SPI PA5-7)",
          got["i2c_sda"] == "PB7" and got["i2c_scl"] == "PB6"
          and got["spi_sck"] == "PA5" and got["spi_mosi"] == "PA7" and got["spi_miso"] == "PA6",
          str(got))
    # Exhaust it: reserved pins must never appear, and it must refuse cleanly.
    names = list(got.values())
    for i in range(60):
        try:
            res, _ = tools.allocate_pin(al, role="gpio")
        except tools.ToolError:
            break
        pin = res["pins"][0]
        names.append(pin["name"] if isinstance(pin, dict) else str(pin))
        tools.connect(al, net_name=f"G{i}", pins=[["U1", pin["number"] if isinstance(pin, dict) else pin]])
    forbidden = [n for n in names if n.upper() in {"PA13", "PA14", "PH0", "PH1", "NRST"}
                 or n.upper().startswith(("VDD", "VSS", "VBAT", "VCAP"))]
    check("allocate_pin never hands out a reset / debug / oscillator / supply pin",
          not forbidden, f"handed out {forbidden}")

    # A stub-like part with only supply/reset pins must refuse, not improvise.
    from app.models import Component as _Comp, Pin as _Pin
    stub = fresh()
    stub.ir.components.append(_Comp(refdes="U1", mpn="STM32F411CEU6", footprint="x", pins=[
        _Pin(number="7", name="NRST", type="input"),
        _Pin(number="24", name="VDD", type="power_in"),
        _Pin(number="23", name="VSS", type="ground")]))
    try:
        tools.allocate_pin(stub, role="i2c_sda")
        check("allocate_pin refuses when a part breaks out no usable I/O", False)
    except tools.ToolError:
        check("allocate_pin refuses when a part breaks out no usable I/O", True)

    # Empty-turn guard.
    from app.models import ChatMessage as _Chat
    et = fresh()
    et.chat.append(_Chat(id="u", role="user", text="I want to build a rain alert with an ESP32."))
    et.chat.append(_Chat(id="a", role="assistant", text="You could build this with... shall I generate it?"))
    _agent._append_empty_turn_notice(et, et.chat[0].text)
    check("a build turn that produced nothing says so", "Nothing was built this turn" in et.chat[-1].text)
    _agent._append_empty_turn_notice(et, et.chat[0].text)
    check("the empty-turn notice is not appended twice",
          et.chat[-1].text.count("Nothing was built this turn") == 1)
    q = fresh()
    q.chat.append(_Chat(id="u", role="user", text="What does R43 check?"))
    q.chat.append(_Chat(id="a", role="assistant", text="It compares requirements against the design."))
    _agent._append_empty_turn_notice(q, q.chat[0].text)
    check("a question is not treated as an empty build turn",
          "Nothing was built this turn" not in q.chat[-1].text)

    # Non-grid design is untouched by the grid path.
    plain = fresh()
    for i in range(12):
        tools.add_component(plain, mpn="CC0603KRX7R9BB104")
    tools.add_component(plain, mpn="ESP32-WROOM-32", refdes="U1")
    pb = placement.auto_place(plain)
    check("plain design (no arrays) still uses the classic heuristic path", pb.width > 0 and not placement.has_physical_grid(plain))

    print()
    if FAILURES:
        print(f"MATRIX-TOOLS REGRESSION — {len(FAILURES)} failure(s): {FAILURES}")
        return 1
    print("MATRIX-TOOLS REGRESSION — all checks green.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
