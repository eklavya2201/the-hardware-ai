"""Regression — user-driven board form factor (set_form_factor / R57 / round pipeline).

Guards the form-factor generalization (2026-08-18, the "make Synqit smart about
shape, not just a smartwatch keyword" review):
  1. `set_form_factor` records shape + dims with validation (the R43 pattern).
  2. `placement.auto_place` honours the spec: round boards at the requested
     diameter with the display dead centre and EVERY body corner inside the
     disc; rect boards at the requested size (grown only when a part can't fit).
  3. The legacy smartwatch heuristic still implies a round board with no spec.
  4. `R57_form_factor` reports shape mismatch (error) and size overrun (warning).
  5. The router never leaves the disc on a round board.
  6. `derive_mounting` writes a CIRCLE outline + bolt-circle holes that pass
     the mechanical DRC on a round board.
  7. PCB_R02 catches a part parked in the bounding-box corner of a round board.

Offline: no LLM, no network, no DB (in-memory Project only).
    python -m scripts.regression_form_factor_spec
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.models import FormFactorSpec, Project, SchematicIR  # noqa: E402
from app.services import mechanical_tools, mechanical_verifier, placement, routing, tools  # noqa: E402
from app.services.pcb_verifier import PCB_R02_off_board  # noqa: E402
from app.services.verifier import R57_form_factor  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def fresh(pid: str = "ff-test") -> Project:
    return Project(id=pid, name=pid, path=f"/tmp/{pid}", ir=SchematicIR())


def _watch_project() -> Project:
    """A small wearable-ish design: round display + HR sensor + MCU + passives."""
    p = fresh("watch")
    for mpn in ("GC9A01-1.28-Round", "MAX30102", "ESP32-WROOM-32"):
        tools.add_component(p, mpn=mpn)
    for _ in range(3):
        tools.add_component(p, mpn="CC0603KRX7R9BB104")
    refs = [c.refdes for c in p.ir.components]
    disp = next(c.refdes for c in p.ir.components if c.mpn == "GC9A01-1.28-Round")
    mcu = next(c.refdes for c in p.ir.components if c.mpn == "ESP32-WROOM-32")
    hr = next(c.refdes for c in p.ir.components if c.mpn == "MAX30102")
    # A couple of real nets so routing has work to do.
    tools.connect(p, net_name="GND", pins=[[r, "GND"] for r in (disp, mcu)])
    tools.connect(p, net_name="VCC_3V3", pins=[[disp, "VCC"], [mcu, "3V3"]])
    tools.connect(p, net_name="SPI_SCK", pins=[[disp, "SCL"], [mcu, "IO18"]])
    tools.connect(p, net_name="I2C_SDA", pins=[[hr, "SDA"], [mcu, "IO21"]])
    return p


def _inside_circle(board, slack: float = 0.25) -> bool:
    """Every body-bbox corner of every part inside the disc (PCB_R02's test)."""
    from app.services.pcb_verifier import _body_bbox  # noqa: SLF001

    r_lim = board.width / 2.0 + slack
    cx = cy = board.width / 2.0
    for pc in board.components:
        x0, y0, x1, y1 = _body_bbox(pc)
        for px, py in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
            if math.hypot(px - cx, py - cy) > r_lim:
                return False
    return True


def main() -> int:
    # --- set_form_factor validation ----------------------------------------
    p = fresh()
    result, diff = tools.set_form_factor(p, shape="round", diameter_mm=42)
    check(
        "set_form_factor records a round spec",
        p.ir.form_factor is not None
        and p.ir.form_factor.shape == "round"
        and p.ir.form_factor.diameter_mm == 42
        and "42" in diff.summary,
    )
    tools.set_form_factor(p, shape="rect", width_mm=50, height_mm=20)
    check(
        "set_form_factor replaces the previous spec",
        p.ir.form_factor.shape == "rect" and p.ir.form_factor.width_mm == 50,
    )
    for kwargs, why in [
        (dict(shape="hexagon"), "unknown shape"),
        (dict(shape="round", width_mm=50), "round with width"),
        (dict(shape="rect", diameter_mm=42), "rect with diameter"),
        (dict(shape="round", diameter_mm=5), "diameter below sane range"),
        (dict(shape="rect", width_mm=9999), "width above sane range"),
    ]:
        try:
            tools.set_form_factor(fresh(), **kwargs)
            check(f"set_form_factor rejects {why}", False)
        except tools.ToolError:
            check(f"set_form_factor rejects {why}", True)

    # --- round placement from an explicit spec ------------------------------
    p = _watch_project()
    tools.set_form_factor(p, shape="round", diameter_mm=55, note="smartwatch")
    board = placement.auto_place(p)
    p.board = board
    ctr = board.width / 2.0
    check("explicit round spec builds a round board", board.shape == "round")
    check(
        "round board honours a feasible requested diameter",
        abs(board.width - 55.0) < 0.01 and abs(board.height - 55.0) < 0.01,
        f"built Ø{board.width}",
    )
    disp_pc = next(pc for pc in board.components if pc.mpn == "GC9A01-1.28-Round")
    # The anchor puts the display's BODY centre at the disc centre; the stored
    # x/y is the footprint anchor, so measure the pad-field centre.
    from app.services.pcb_verifier import _body_bbox  # noqa: SLF001
    x0, y0, x1, y1 = _body_bbox(disp_pc)
    dcx, dcy = (x0 + x1) / 2, (y0 + y1) / 2
    check(
        "display is anchored at the disc centre",
        math.hypot(dcx - ctr, dcy - ctr) < 1.5,
        f"display centre at ({dcx:.1f}, {dcy:.1f}) vs {ctr:.1f}",
    )
    check("every part is fully inside the disc", _inside_circle(board))
    check("R57 silent when the board matches the ask", R57_form_factor(p) == [])
    check("PCB_R02 clean on the honest round layout", PCB_R02_off_board(board) == [])

    # --- routing stays inside the disc --------------------------------------
    routed = routing.auto_route(p)
    r_lim = board.width / 2.0 - 0.05
    ok = True
    for tr in routed.traces:
        for seg in tr.segments:
            for px, py in ((seg.x1, seg.y1), (seg.x2, seg.y2)):
                if math.hypot(px - ctr, py - ctr) > r_lim:
                    ok = False
    check("router keeps every trace point inside the disc", ok)

    # --- diameter grows only when parts demand it, and R57 says so ----------
    p2 = _watch_project()
    tools.set_form_factor(p2, shape="round", diameter_mm=30)  # ESP32 won't fit Ø30
    board2 = placement.auto_place(p2)
    p2.board = board2
    check("undersized diameter grows to fit the parts", board2.width > 30.0)
    issues = R57_form_factor(p2)
    check(
        "R57 ERRORS when the requested diameter had to grow by more than 25 % (Ø30 asked)",
        len(issues) == 1 and issues[0].severity == "error" and "Ø30" in issues[0].message,
        f"got {[(i.id, i.severity, i.message) for i in issues]}",
    )
    # A modest overrun (< 25 %) stays a warning: ask for 5 % under what it built.
    p2.ir.form_factor = FormFactorSpec(shape="round", diameter_mm=round(board2.width / 1.05, 1))
    issues = R57_form_factor(p2)
    check(
        "R57 only WARNS on a small (< 25 %) diameter overrun",
        len(issues) == 1 and issues[0].severity == "warning",
        f"got {[(i.id, i.severity) for i in issues]}",
    )

    # --- rect spec honoured --------------------------------------------------
    p3 = _watch_project()
    tools.set_form_factor(p3, shape="rect", width_mm=90, height_mm=60)
    board3 = placement.auto_place(p3)
    p3.board = board3
    check(
        "rect spec builds the requested size",
        board3.shape == "rect"
        and abs(board3.width - 90.0) < 0.01
        and abs(board3.height - 60.0) < 0.01,
        f"built {board3.width}x{board3.height}",
    )
    check("R57 silent on the honoured rect spec", R57_form_factor(p3) == [])

    # --- R57 shape mismatch is an error --------------------------------------
    p3.ir.form_factor = FormFactorSpec(shape="round", diameter_mm=42)
    issues = R57_form_factor(p3)  # board is still the 90x60 rect
    check(
        "R57 errors on a shape mismatch",
        len(issues) == 1 and issues[0].severity == "error" and issues[0].id == "R57:shape",
        f"got {[(i.id, i.severity) for i in issues]}",
    )

    # --- legacy smartwatch heuristic still implies round ---------------------
    p4 = _watch_project()  # GC9A01 + MAX30102, no explicit spec
    check("smartwatch heuristic implies a round spec", placement.requested_form(p4) is not None)
    board4 = placement.auto_place(p4)
    check("implied wearable still builds a round board", board4.shape == "round")

    # --- mechanical: circle outline + bolt-circle holes ----------------------
    result, _ = mechanical_tools.derive_mounting(p)
    check(
        "derive_mounting writes a circle outline for a round board",
        p.mechanical is not None and p.mechanical.outline.shape == "circle"
        and abs(p.mechanical.outline.width - board.width) < 0.01,
    )
    holes = p.mechanical.mountingHoles
    ok = len(holes) == 4
    for h in holes:
        if math.hypot(h.x - ctr, h.y - ctr) > ctr - h.diameter / 2.0:
            ok = False
    check("bolt-circle holes sit fully inside the disc", ok,
          f"holes at {[(h.x, h.y) for h in holes]}")
    check(
        "wearable-scale round board gets M2 hardware",
        all(h.standoff == "M2" for h in holes),
    )
    mech_issues = [i for i in mechanical_verifier.run(p) if i.severity == "error"]
    check("mechanical DRC 0 errors on the derived round board", mech_issues == [],
          f"got {[(i.id, i.message) for i in mech_issues]}")

    # --- PCB_R02 catches a corner part on a round board ----------------------
    corner = next(pc for pc in board.components if pc.mpn == "CC0603KRX7R9BB104")
    old_xy = (corner.x, corner.y)
    corner.x, corner.y = 3.0, 3.0  # inside the bounding box, outside the disc
    flagged = PCB_R02_off_board(board)
    check(
        "PCB_R02 flags a part in the round board's corner air",
        any(i.refdes == corner.refdes for i in flagged),
        f"got {[(i.id, i.refdes) for i in flagged]}",
    )
    corner.x, corner.y = old_xy

    # --- 4-layer stack-up ------------------------------------------------------
    from app.services import gerber
    from app.services.pcb_verifier import PCB_R03_unrouted_nets

    p4 = _watch_project()
    try:
        tools.set_form_factor(p4, shape="round", diameter_mm=44, layers=3)
        check("set_form_factor rejects layers=3", False)
    except tools.ToolError:
        check("set_form_factor rejects layers=3", True)
    tools.set_form_factor(p4, shape="round", diameter_mm=44, layers=4)
    check("form factor records layers=4", p4.ir.form_factor.layers == 4)
    b4 = placement.auto_place(p4)
    check("4-layer request builds a 4-layer board", b4.layer_count == 4, f"got {b4.layer_count}")
    plane_nets = {pl.net for pl in b4.planes}
    check("4-layer board carries a GND plane on In1",
          any(pl.layer == "In1" and pl.net == "GND" for pl in b4.planes),
          f"planes={[(pl.layer, pl.net) for pl in b4.planes]}")
    check("4-layer board carries the logic rail on In2",
          any(pl.layer == "In2" and pl.net == "VCC_3V3" for pl in b4.planes),
          f"planes={[(pl.layer, pl.net) for pl in b4.planes]}")
    p4.board = b4
    r4 = routing.auto_route(p4)
    # A plane net's only copper on the outer layers is the pad -> via fan-out
    # stub (the via drops to the plane); a real routed trace would be long.
    def _tlen(t):
        return sum(math.hypot(s.x2 - s.x1, s.y2 - s.y1) for s in t.segments)
    long_plane = [t.net for t in r4.traces if t.net in plane_nets and _tlen(t) > 3.0]
    check("plane nets carry only short fan-out stubs, no routed traces",
          not long_plane, f"{long_plane}")
    check("power-plane SMD pads got their via",
          any(v.net == "VCC_3V3" for v in r4.vias))
    check("plane nets carry no rats",
          not any(r.net in plane_nets for r in r4.rats))
    check("PCB_R03 is silent on plane nets",
          not any(i.net in plane_nets for i in PCB_R03_unrouted_nets(r4)))
    p4.board = r4
    import io
    import zipfile
    zf = zipfile.ZipFile(io.BytesIO(gerber.fab_zip(p4)))
    names = zf.namelist()
    check("fab zip carries G2L + G3L inner layers",
          any(n.endswith(".G2L") for n in names) and any(n.endswith(".G3L") for n in names),
          f"{names}")
    check("fab README says 4-layer",
          "4-layer" in zf.read("README.txt").decode())
    inner = zf.read(next(n for n in names if n.endswith(".G2L"))).decode()
    check("inner plane Gerber is a valid RS-274X file",
          inner.startswith("%") and "M02*" in inner and "%LPC*%" in inner,
          inner[:80])
    p2 = _watch_project()
    tools.set_form_factor(p2, shape="round", diameter_mm=44, layers=2)
    b2 = placement.auto_place(p2)
    check("layers=2 forces a 2-layer board", b2.layer_count == 2 and not b2.planes)
    pd = _watch_project()
    bd = placement.auto_place(pd)
    check("small design defaults to 2-layer", bd.layer_count == 2 and not bd.planes,
          f"got {bd.layer_count}")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURES: {FAILURES}")
        return 1
    print("All form-factor regression checks green.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
