"""Blender bridge (Prompt to Product, 2026-09): a product render from our own
geometry — no image model guessing what the board might look like.

`blender -b --python blender_scripts/render_product.py -- in.json out.png`
imports the STL meshes `mechanical_bridge.export_meshes` writes (board, part
envelopes, case), assigns materials, frames a three-quarter camera on the
bounding box and renders with Cycles on the CPU (no GPU on the server; the
Debian build has no OpenImageDenoise, so denoising stays off). Never raises;
absent Blender => (None, reason), same as the other bridges.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.models import Project
from app.services import mechanical_bridge

SCRIPT = Path(__file__).resolve().parent / "blender_scripts" / "render_product.py"
DEFAULT_TIMEOUT = 240


def find_blender() -> str | None:
    env = os.environ.get("BLENDER_BIN")
    if env and Path(env).exists():
        return env
    found = shutil.which("blender") or shutil.which("blender.exe")
    if found:
        return found
    for base in (Path(r"C:\Program Files\Blender Foundation"),):
        for cand in sorted(base.glob("Blender*/blender.exe"), reverse=True):
            return str(cand)
    return None


def is_available() -> bool:
    return find_blender() is not None


def status() -> str:
    return "available" if is_available() else "absent"


def render(project: Project, *, width: int = 1280, height: int = 800, samples: int = 48,
           include_enclosure: bool = True, timeout: int = DEFAULT_TIMEOUT) -> tuple[bytes | None, str | None]:
    """PNG bytes of the product, or (None, why)."""
    exe = find_blender()
    if exe is None:
        return None, ("Blender not found. Install Blender 4.x (`apt install blender` on Debian) "
                      "or set BLENDER_BIN to its binary.")
    with tempfile.TemporaryDirectory(prefix="synqit_blender_") as tmp:
        meshes, err = mechanical_bridge.export_meshes(project, tmp, include_enclosure)
        if meshes is None:
            return None, err
        scene = mechanical_bridge.product_scene(project, include_enclosure) or {}
        payload = {
            "meshes": meshes,
            "width": width, "height": height, "samples": samples,
            "name": project.name,
            "board_mm": [scene.get("outline", {}).get("width", 50), scene.get("outline", {}).get("height", 40)],
        }
        in_path, out_path = Path(tmp) / "in.json", Path(tmp) / "render.png"
        in_path.write_text(json.dumps(payload), encoding="utf-8")
        try:
            proc = subprocess.run(
                [exe, "-b", "--python", str(SCRIPT), "--", str(in_path), str(out_path)],
                cwd=tmp, capture_output=True, text=True, timeout=timeout,
                env=dict(os.environ, BLENDER_USER_CONFIG=tmp),
            )
        except subprocess.TimeoutExpired:
            return None, f"Blender timed out ({timeout}s)."
        if not out_path.exists():
            tail = ((proc.stdout or "") + (proc.stderr or ""))[-1200:]
            return None, f"Blender produced no image (exit {proc.returncode}). {tail}"
        return out_path.read_bytes(), None
