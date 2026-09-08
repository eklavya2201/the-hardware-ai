"""Give authored/downloaded footprints (no embedded 3D model) an approximate
package-family solid, so the 3D view shows a real-ish chip instead of a box.

The frontend's class fallback keys on the footprint *name*, which for authored
parts is "Synqit:..." (no package token), so it can't resolve them. Here the
backend knows each part's real package, so we add a manifest alias
`Synqit:...` -> a hero package GLB (qfn/lga/soic/...). Non-chip parts (buzzers,
motors, LEDs, gas cans) have no good package solid and stay procedural.
Run after `fetch_3d_models.py --local`.
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))
from dotenv import load_dotenv; load_dotenv()
from app.services import library, footprints as F

MANIFEST = _BACKEND.parent / "frontend" / "src" / "lib" / "models3d.manifest.json"

# Hero GLBs that exist in frontend/public/models/. All KiCad-derived: mm, Z-up.
HERO = lambda u: {"url": f"/models/{u}", "scale": 1000, "rotation": [-90, 0, 0]}


def family_glb(pkg: str, fp: str) -> dict | None:
    """Map a part's package/footprint to the closest hero package GLB."""
    h = f"{pkg} {fp}".upper()
    if re.search(r"QFP", h): return HERO("lqfp.glb")
    if re.search(r"QFN|DFN|WSON|VSON|SON|TDFN|UDFN|LFCSP", h): return HERO("qfn.glb")
    if re.search(r"BGA|WLP|WLCSP|\bCSP", h): return HERO("qfn.glb")
    if re.search(r"LCC", h) and not re.search(r"PLCC", h): return HERO("qfn.glb")
    if re.search(r"LGA-16|LGA16", h): return HERO("lga-16.glb")
    if re.search(r"LGA-14|LGA14", h): return HERO("lga-14.glb")
    if re.search(r"LGA|OLGA|OPLGA|HLGA|OESIP", h): return HERO("lga-8.glb")
    # TSSOP/HTSSOP have their own hero — keep them off the fatter SOIC solid
    # (and matching the frontend's own "TSSOP" package default, so adding a
    # manifest alias here never downgrades what the resolver already picked).
    if re.search(r"HTSSOP|TSSOP", h): return HERO("tssop.glb")
    if re.search(r"SOIC|SSOP|MSOP|HSOP|\bSOP-|WSOF", h): return HERO("soic-8.glb")
    if re.search(r"SOT-?23-6", h): return HERO("sot-23-6.glb")
    # SC-70-6 / SOT-363 is the miniaturised 6-lead SOT — same silhouette as the
    # SOT-23-6 hero, one size down (2.0x1.25 mm body vs 2.9x1.6).
    if re.search(r"SC-?70-?6|SOT-?363", h): return HERO("sot-23-6.glb")
    if re.search(r"SOT-?23", h): return HERO("sot-23.glb")
    if re.search(r"CHIPLED|OESIP|OPTO", h): return HERO("lga-8.glb")     # small optical chips
    if re.search(r"TO-?39|TO-?5\b|TO-?46|TO-?18", h): return HERO("to-92.glb")  # metal cans
    if re.search(r"ESP-?01|WROOM|ESP32-C3-MINI|ESP-MODULE", h): return HERO("esp32-wroom.glb")
    return None  # boards, gas cans, buzzers, LEDs, encoders, FFC → procedural box


def main():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    # footprint string -> part (for package lookup)
    by_fp = {}
    for p in library.all_parts():
        if p.footprint:
            by_fp.setdefault(p.footprint, p)

    added = skipped = 0
    for fp, part in by_fp.items():
        if ":" not in fp or (fp in manifest and manifest[fp].get("url")):
            continue
        spec = family_glb(getattr(part, "package", "") or "", fp)
        if spec is None:
            skipped += 1
            continue
        manifest[fp] = spec
        added += 1
        print(f"  {fp:42s} -> {spec['url']}")

    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nauthored fallback: +{added} package-family aliases, {skipped} left procedural")


if __name__ == "__main__":
    main()
