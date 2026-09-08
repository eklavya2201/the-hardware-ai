"""Board-edit scope regression — the drag-snaps-back bug.

The component-property endpoint edits two different structures: target="ir"
moves the schematic symbol, target="board" moves the PLACED component on the
PCB (what the canvas drag/rotate mean). Writing a board move into the IR was
the beta bug "parts are not movable in pcb" — the placement never changed, so
the part snapped back on the next render.

Guards: board x/y/rotation land on board.components and leave the IR symbol
untouched; ir stays the default; bad values reject; unplaced refdes rejects.

Exit codes: 0 pass · 1 an assertion failed.

Usage: python -m scripts.regression_board_edit
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from fastapi import HTTPException

from app.api.projects import _set_board_position, _set_board_rotation
from app.models import (
    Board,
    Component,
    Footprint,
    PlacedComponent,
    Project,
    SchematicIR,
)

_failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
    if not ok:
        _failures.append(label)


def _project() -> Project:
    return Project(
        id="p",
        name="p",
        path="/p",
        ir=SchematicIR(
            components=[Component(refdes="R1", mpn="RC0603FR-074K7L", x=250, y=350)]
        ),
        board=Board(
            width=30,
            height=20,
            components=[
                PlacedComponent(
                    refdes="R1",
                    mpn="RC0603FR-074K7L",
                    x=5.0,
                    y=5.0,
                    rotation=0,
                    layer="top",
                    footprint=Footprint(
                        name="Resistor_SMD:R_0603_1608Metric", width=1.6, height=0.8
                    ),
                )
            ],
        ),
    )


def main() -> int:
    print("regression_board_edit")

    p = _project()
    _set_board_position(p, "R1", "x", "12.5")
    _set_board_position(p, "R1", "y", "7.25")
    pc = p.board.components[0]
    check("board x lands on the placed component", pc.x == 12.5)
    check("board y lands on the placed component", pc.y == 7.25)
    ir = p.ir.components[0]
    check("schematic symbol untouched by board move", ir.x == 250 and ir.y == 350)

    _set_board_rotation(p, "R1", "270")
    check("board rotation lands on the placed component", pc.rotation == 270)

    try:
        _set_board_position(p, "R1", "x", "abc")
        check("non-numeric x rejects", False)
    except HTTPException:
        check("non-numeric x rejects", True)

    try:
        _set_board_rotation(p, "R1", "45")
        check("off-grid rotation rejects", False)
    except HTTPException:
        check("off-grid rotation rejects", True)

    try:
        _set_board_position(p, "C9", "x", "1")
        check("unplaced refdes rejects", False)
    except HTTPException:
        check("unplaced refdes rejects", True)

    if _failures:
        print(f"{len(_failures)} FAILURE(S)")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
