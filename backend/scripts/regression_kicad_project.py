"""Regression guard for the full KiCad project export (offline, no KiCad).

Asserts the handoff is real and honest:
1. `.kicad_sch` is well-formed S-expression, one lib_symbol per unique MPN,
   one symbol instance per component, correct header/version.
2. Electrical completeness — every (refdes, pin) in the IR's nets gets a wire
   stub AND a global label carrying that net name, so the schematic is
   connected by label rather than silently missing nets.
3. Grid discipline — every pin/wire coordinate lands on KiCad's 1.27 mm grid
   (off-grid pins break wire attachment and trip ERC).
4. Determinism — exporting twice produces byte-identical output.
5. The zip contains the project/schematic/netlist/BOM/README and degrades
   gracefully (README note) when the board can't be included.

Run: python -m scripts.regression_kicad_project
"""

from __future__ import annotations

import io
import os
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.pop("OPENAI_API_KEY", None)  # force the deterministic stub board

from app.models import Project
from app.services import agent, export, export_sch


def check(name: str, ok: bool) -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    return ok


def balanced(text: str) -> bool:
    """Parens balance outside of quoted strings."""
    depth = 0
    in_str = False
    esc = False
    for ch in text:
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not in_str


def main() -> int:
    results: list[bool] = []

    project = Project(id="regression", name="Regression Board", path="/regression")
    for _ in agent.run(project, "low-power ESP32 sensor board"):
        pass
    sch = export_sch.kicad_sch(project)

    # --- 1. Structure ------------------------------------------------------
    results.append(check("schematic parens balanced", balanced(sch)))
    results.append(check("kicad_sch header + generator", sch.startswith("(kicad_sch (version")
                         and 'generator "synqit"' in sch))
    mpns = {c.mpn for c in project.ir.components}
    lib_syms = set(re.findall(r'\(symbol "Synqit:([^"]+)"', sch))
    results.append(check(f"one lib_symbol per unique MPN ({len(mpns)})",
                         len(lib_syms) == len(mpns)))
    instances = len(re.findall(r'\(symbol \(lib_id ', sch))
    results.append(check(f"one instance per component ({len(project.ir.components)})",
                         instances == len(project.ir.components)))

    # --- 2. Electrical completeness ---------------------------------------
    labels = re.findall(r'\(global_label "([^"]+)"', sch)
    connections = sum(len(n.pins) for n in project.ir.nets)
    results.append(check(f"a global label per connected pin ({connections})",
                         len(labels) == connections))
    net_names = {n.name for n in project.ir.nets}
    results.append(check("labels only carry real net names", set(labels) <= net_names))
    results.append(check("every net appears at least once",
                         net_names <= set(labels)))
    wires = len(re.findall(r"\(wire \(pts ", sch))
    results.append(check(f"a wire stub per connected pin ({connections})", wires == connections))

    # --- 3. Grid discipline ------------------------------------------------
    coords = [float(v) for v in re.findall(r"\(xy (-?\d+\.?\d*) ", sch)]
    coords += [float(v) for v in re.findall(r"\(xy -?\d+\.?\d* (-?\d+\.?\d*)\)", sch)]
    off = [c for c in coords if abs(round(c / 1.27) * 1.27 - c) > 1e-6]
    results.append(check(f"all {len(coords)} wire coords on the 1.27 mm grid", not off))

    # --- 4. Determinism ----------------------------------------------------
    results.append(check("export is deterministic", export_sch.kicad_sch(project) == sch))

    # --- 5. Project zip ----------------------------------------------------
    blob, notes = export.kicad_project(project)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = zf.namelist()
        exts = {Path(n).suffix for n in names}
        results.append(check("zip has schematic, project, netlist, BOM, README",
                             {".kicad_sch", ".kicad_pro", ".net", ".csv", ".txt"} <= exts))
        readme = next(zf.read(n).decode() for n in names if n.endswith("README.txt"))
        results.append(check("README states the export is one-way",
                             "ONE-WAY" in readme))
        results.append(check("board absence is disclosed, not silent",
                             bool(project.board) or ("Board not included" in readme and notes)))
        sch_in_zip = next(zf.read(n).decode() for n in names if n.endswith(".kicad_sch"))
        results.append(check("zipped schematic matches the generator", sch_in_zip == sch))

    if all(results):
        print("\nAll KiCad-project-export regressions pass.")
        return 0
    print("\nFAILURES present.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
