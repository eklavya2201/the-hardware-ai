"""FreeCAD bridge (Prompt to Product, 2026-09): the product as an EDITABLE
parametric document, not a frozen STEP.

`freecadcmd` (FreeCAD's headless binary, its own Python) runs
`freecad_scripts/build_product.py` on the `mechanical_bridge.product_scene`
payload and writes a `.FCStd` in which every feature is named — `Board`,
`Hole_1`, `Part_U1`, `Case`, `Standoff_3`, `Cutout_J1` — so a person opens it
in FreeCAD and changes the wall thickness or a cutout instead of re-drawing
the case. Same never-raises, degrade-when-absent contract as kicad_bridge.

Why FreeCAD when CadQuery already has the OpenCASCADE kernel: the kernel is
the same; the DOCUMENT is what FreeCAD adds — labelled, parametric, and the
format a mechanical engineer actually edits.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from app.models import Project
from app.services import mechanical_bridge

SCRIPT = Path(__file__).resolve().parent / "freecad_scripts" / "build_product.py"
DEFAULT_TIMEOUT = 180


@dataclass
class FreecadResult:
    success: bool
    data: dict = field(default_factory=dict)
    error: str | None = None
    raw_log: str = ""


def find_freecadcmd() -> str | None:
    env = os.environ.get("FREECAD_CMD")
    if env and Path(env).exists():
        return env
    for name in ("freecadcmd", "FreeCADCmd", "freecadcmd.exe", "FreeCADCmd.exe"):
        found = shutil.which(name)
        if found:
            return found
    for base in (Path(r"C:\Program Files"), Path(r"C:\Program Files (x86)")):
        for cand in sorted(base.glob("FreeCAD*/bin/FreeCADCmd.exe"), reverse=True):
            return str(cand)
    return None


def is_available() -> bool:
    return find_freecadcmd() is not None


def status() -> str:
    return "available" if is_available() else "absent"


def build_fcstd(project: Project, include_enclosure: bool = True, timeout: int = DEFAULT_TIMEOUT) -> FreecadResult:
    """Return the .FCStd bytes in `data["fcstd"]` (plus `data["step"]` when
    FreeCAD's own STEP writer succeeded)."""
    exe = find_freecadcmd()
    if exe is None:
        return FreecadResult(False, error=(
            "FreeCAD not found. Install FreeCAD 1.0 (`apt install freecad` on Debian; "
            "the Windows installer) or set FREECAD_CMD to its freecadcmd binary."))
    scene = mechanical_bridge.product_scene(project, include_enclosure)
    if scene is None:
        return FreecadResult(False, error="No board outline — place the board first.")
    with tempfile.TemporaryDirectory(prefix="synqit_freecad_") as tmp:
        tmp_path = Path(tmp)
        in_path, out_path = tmp_path / "in.json", tmp_path / "out.json"
        in_path.write_text(json.dumps(scene), encoding="utf-8")
        env = dict(os.environ, SYNQIT_IN=str(in_path), SYNQIT_OUT=str(out_path),
                   SYNQIT_FCSTD=str(tmp_path / "product.FCStd"), SYNQIT_STEP=str(tmp_path / "product.step"))
        try:
            proc = subprocess.run(
                [exe, "-c", f"import runpy; runpy.run_path({str(SCRIPT)!r})"],
                cwd=tmp, capture_output=True, text=True, timeout=timeout, env=env,
            )
        except subprocess.TimeoutExpired:
            return FreecadResult(False, error=f"FreeCAD timed out ({timeout}s).")
        log = (proc.stdout or "") + "\n" + (proc.stderr or "")
        if not out_path.exists():
            return FreecadResult(False, error=f"FreeCAD produced no result (exit {proc.returncode}).", raw_log=log[-2000:])
        try:
            result = json.loads(out_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            return FreecadResult(False, error=f"FreeCAD returned bad JSON: {exc}", raw_log=log[-2000:])
        if not result.get("ok"):
            return FreecadResult(False, error=result.get("error") or "FreeCAD script failed.", raw_log=log[-2000:])
        data = dict(result.get("data") or {})
        fc = tmp_path / "product.FCStd"
        if not fc.exists():
            return FreecadResult(False, error="FreeCAD wrote no .FCStd.", raw_log=log[-2000:])
        data["fcstd"] = fc.read_bytes()
        st = tmp_path / "product.step"
        if st.exists():
            data["step"] = st.read_bytes()
        return FreecadResult(True, data=data, raw_log=log[-2000:])
