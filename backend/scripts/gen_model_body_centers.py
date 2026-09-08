"""Measure each connector's 3D-model body centre — the cutout anchor (6.1).

The enclosure cutout must centre on the connector's VISIBLE BODY. Neither the
placement origin nor the pad-field centre reliably tracks it: measured examples —
the GCT USB-C's body centre sits on its pad centre (2.98 vs 2.95 mm) while the
BarrelJack's body centre sits on the origin (-0.15 mm) with its pads 2.35 mm off
axis. The models are the ground truth, so we MEASURE them: load each
connector-category part's GLB (the same one the 3D view renders), take the body
bounding-box centre (vertices above the board plane — pins below are excluded),
and store it in `app/library/model_body_centers.json` in IR-local mm.

Frame mapping (matches BoardView3D's PartMesh transform): scene x = model x,
scene z (= IR y) = -model y. So IR-local centre = (cx_model, -cy_model), scaled
by the manifest's per-model scale (KiCad GLBs are metres -> x1000).

Tooling-only (needs trimesh; not in requirements.txt) — the JSON is committed and
read at runtime by `mechanical_derive`, so prod needs no trimesh. Connectors only:
they're the only parts the cutout derivation anchors on.

Usage:  python -m scripts.gen_model_body_centers
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

_FRONTEND_PUBLIC = _BACKEND_ROOT.parent / "frontend" / "public"
_MANIFEST = _BACKEND_ROOT.parent / "frontend" / "src" / "lib" / "models3d.manifest.json"
_OUT = _BACKEND_ROOT / "app" / "library" / "model_body_centers.json"

# Vertices above this z (model mm) count as "body"; below are THT pins/pegs.
_BODY_Z_MIN = 0.05

# Package/class fallback for connectors with no exact-footprint manifest entry —
# mirrors the frontend resolution (models3d.ts PACKAGE_MODELS + classModel) so
# the anchor is measured from the SAME GLB the 3D view actually renders (keep in
# sync with models3d.ts, like rotXY mirrors geometry.py). Token → spec.
_CONNECTOR_FALLBACK: list[tuple[str, dict]] = [
    ("USB", {"url": "/models/usb_c.glb", "scale": 1000.0}),
    ("JST", {"url": "/models/jst_ph.glb", "scale": 1000.0}),
]


def _resolve_spec(part, manifest: dict) -> dict | None:
    """Frontend-order model resolution for a connector part: per-MPN manifest
    entry → exact footprint → package/class fallback."""
    entry = manifest.get(f"MPN:{part.mpn}") or manifest.get(part.footprint)
    if entry and "url" in entry:
        return entry
    hay = f"{part.footprint} {part.mpn}".upper()
    for token, spec in _CONNECTOR_FALLBACK:
        if token in hay:
            return spec
    return None


def main() -> int:
    import trimesh  # tooling dep

    from app.services import library

    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    # `--only MPN[,MPN]` re-measures just those parts and MERGES into the
    # committed file. A blanket run re-reads every GLB, and the committed ones
    # are meshopt-packed — which trimesh cannot read — so a full run would drop
    # the very measurements it is supposed to preserve. Measure a part right
    # after generating its model, before `compress_glbs` packs it.
    only = None
    for i, arg in enumerate(sys.argv):
        if arg == "--only" and i + 1 < len(sys.argv):
            only = {s for s in sys.argv[i + 1].split(",") if s}
    out: dict[str, dict] = {}
    if only:
        try:
            out = json.loads(_OUT.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            out = {}
    missing: list[str] = []

    for part in library.all_parts():
        if part.category != "connector":
            continue
        if only is not None and part.mpn not in only:
            continue
        entry = _resolve_spec(part, manifest)
        if not entry:
            missing.append(f"{part.mpn} ({part.footprint}: no manifest model)")
            continue
        glb = _FRONTEND_PUBLIC / entry["url"].lstrip("/")
        if not glb.exists():
            missing.append(f"{part.mpn} ({entry['url']}: file missing)")
            continue
        try:
            mesh = trimesh.load(str(glb), force="mesh")
            scale = float(entry.get("scale", 1000.0))
            v = mesh.vertices * scale  # -> mm
            body = v[v[:, 2] > _BODY_Z_MIN]
            if len(body) == 0:
                body = v
            cx_model = float((body[:, 0].min() + body[:, 0].max()) / 2.0)
            cy_model = float((body[:, 1].min() + body[:, 1].max()) / 2.0)
            # Manifest rotation is a three.js XYZ Euler — its Z component spins
            # the model in-plane FIRST (before the shared Z-up→Y-up -90°X).
            # Oriented stand-ins (the USB variants) rely on it.
            import math
            rz = math.radians(float((entry.get("rotation") or [-90, 0, 0])[2]))
            if rz:
                c, s = math.cos(rz), math.sin(rz)
                cx_model, cy_model = cx_model * c - cy_model * s, cx_model * s + cy_model * c
            # IR-local: scene z (IR y) = -model y — PLUS the manifest's offset,
            # which PartMesh applies after the rotation (the 7.3 pin-header
            # Y-mirror fixes live there; ignoring it regenerates stale anchors).
            off = entry.get("offset") or [0.0, 0.0, 0.0]
            cx = cx_model + float(off[0])
            cy = -cy_model + float(off[2])
            out[part.mpn] = {
                "footprint": part.footprint,
                "cx": round(cx, 3),
                "cy": round(cy, 3),
            }
            print(f"  {part.mpn:26} body centre IR-local ({cx:+.2f}, {cy:+.2f}) mm")
        except Exception as exc:  # noqa: BLE001
            missing.append(f"{part.mpn}: {exc}")

    _OUT.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nwrote {len(out)} body centres -> {_OUT.name}")
    if missing:
        print(f"no model measured for {len(missing)} connector(s) (anchor falls back):")
        for m in missing:
            print(f"  - {m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
