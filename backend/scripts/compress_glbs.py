"""Compress the web GLB model library with gltfpack (meshopt).

Rewrites every GLB under frontend/public/models/ in place with
meshopt-compressed buffers (EXT_meshopt_compression). The frontend needs no
loader change: drei's useGLTF wires the MeshoptDecoder into GLTFLoader
unconditionally. Flags:

  -cc  aggressive compression (the proven 206-model 66.6 MB -> 9.8 MB run)
  -kn  keep node names — the 3D view clones scenes and reasons about nodes

Two sharp edges, learned the hard way:
  * gltfpack decides the container by OUTPUT extension — the temp file must
    end in .glb or it silently writes glTF-JSON.
  * trimesh cannot decode EXT_meshopt_compression, so anything that MEASURES
    a GLB (fetch_3d_models.compute_transform, gen_model_body_centers.py) must
    run on the uncompressed file first; fetch_3d_models packs only after its
    transform is computed. Re-measuring an already-packed library requires
    regenerating the GLBs from STEP.

Usage (from backend/):  python scripts/compress_glbs.py [--dry-run]
Idempotent: packing an already-packed file yields no further meaningful drop.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parents[2] / "frontend" / "public" / "models"


def find_gltfpack() -> str | None:
    return shutil.which("gltfpack")


def pack_glb(glb: Path, tool: str | None = None) -> tuple[int, int]:
    """Compress one GLB in place; returns (bytes_before, bytes_after).

    Keeps the original untouched if gltfpack fails or doesn't shrink it.
    """
    tool = tool or find_gltfpack()
    before = glb.stat().st_size
    if not tool:
        return before, before
    tmp = glb.with_name(glb.stem + ".pack.glb")  # MUST end in .glb (see docstring)
    try:
        run = subprocess.run(
            [tool, "-i", str(glb), "-o", str(tmp), "-cc", "-kn"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if run.returncode != 0 or not tmp.exists():
            print(f"[fail] {glb.name}: {run.stderr.strip() or run.stdout.strip()}")
            return before, before
        after = tmp.stat().st_size
        if after >= before:
            tmp.unlink()
            return before, before
        tmp.replace(glb)
        return before, after
    finally:
        if tmp.exists() and tmp != glb:
            tmp.unlink(missing_ok=True)


def main() -> None:
    tool = find_gltfpack()
    if not tool:
        sys.exit("gltfpack not found on PATH — npm install -g gltfpack")
    dry = "--dry-run" in sys.argv
    glbs = sorted(MODELS_DIR.rglob("*.glb"))
    # `--only SUBSTR[,SUBSTR]` packs just the matching files. Re-packing the
    # whole committed set after generating ONE new model rewrites ~200 already
    # packed GLBs for no size gain and buries the real change in the diff.
    only = None
    for i, arg in enumerate(sys.argv):
        if arg == "--only" and i + 1 < len(sys.argv):
            only = [s for s in sys.argv[i + 1].split(",") if s]
    if only:
        glbs = [g for g in glbs if any(s in g.name for s in only)]
    if not glbs:
        sys.exit(f"no GLBs under {MODELS_DIR}" + (f" matching {only}" if only else ""))
    total_before = total_after = 0
    for glb in glbs:
        if dry:
            total_before += glb.stat().st_size
            continue
        before, after = pack_glb(glb, tool)
        total_before += before
        total_after += after
        if after < before:
            print(f"[ok]   {glb.relative_to(MODELS_DIR)!s:56} "
                  f"{before / 1024:8.1f} KB -> {after / 1024:7.1f} KB")
    if dry:
        print(f"{len(glbs)} GLBs, {total_before / 1e6:.1f} MB (dry run)")
    else:
        print(f"\n{len(glbs)} GLBs: {total_before / 1e6:.1f} MB -> "
              f"{total_after / 1e6:.1f} MB "
              f"({100 * (1 - total_after / total_before):.0f}% smaller)")


if __name__ == "__main__":
    main()
