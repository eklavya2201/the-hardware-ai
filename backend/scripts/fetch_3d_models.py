"""Fetch + convert KiCad 3D package models to GLB for the 3D board view.

Phase 1 of docs/REAL_3D_MODELS_PLAN.md. Pulls STEP models straight from the
KiCad packages3D library (permissive licence, already aligned to the footprints
we use), converts each to a web GLB, and computes the scale / up-axis / seating
transform so it renders correctly in our Y-up, millimetre Three.js scene.

KiCad STEP models are in metres and Z-up, so every model gets scale=1000 and a
Z-up->Y-up rotation; the seating offset drops the part's base onto the board.

Run from backend/:  python -m scripts.fetch_3d_models
Outputs GLBs to frontend/public/models/ and prints ready-to-paste registry
entries for frontend/src/lib/models3d.ts.

Requires: cascadio, trimesh, numpy  (pip install cascadio trimesh).
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

import cascadio
import trimesh

# Auto-pack every converted GLB (meshopt, no-op if gltfpack is absent). Must
# run AFTER compute_transform — trimesh can't read a packed file.
try:
    from scripts.compress_glbs import pack_glb
except ImportError:  # run as a plain script from scripts/
    from compress_glbs import pack_glb

RAW = "https://gitlab.com/kicad/libraries/kicad-packages3D/-/raw/master"
_FRONTEND = Path(__file__).resolve().parent.parent.parent / "frontend"
OUT_DIR = _FRONTEND / "public" / "models"
# Phase 2: auto-resolved models live in a subfolder, manifest in src.
AUTO_DIR = OUT_DIR / "auto"
MANIFEST = _FRONTEND / "src" / "lib" / "models3d.manifest.json"

# Bound repo growth in the auto sweep.
MAX_AUTO_MODELS = 220
MAX_AUTO_BYTES = 1200 * 1024

# Registry key -> candidate KiCad STEP paths (first that exists wins). Candidate
# lists tolerate the messy QFN/SOIC filename variants without a 404 stopping us.
PACKAGES: dict[str, list[str]] = {
    "0402": ["Resistor_SMD.3dshapes/R_0402_1005Metric.step"],
    "0603": ["Resistor_SMD.3dshapes/R_0603_1608Metric.step"],
    "0805": ["Resistor_SMD.3dshapes/R_0805_2012Metric.step"],
    "1206": ["Resistor_SMD.3dshapes/R_1206_3216Metric.step"],
    "SOT-23": ["Package_TO_SOT_SMD.3dshapes/SOT-23.step"],
    "SOT-23-5": ["Package_TO_SOT_SMD.3dshapes/SOT-23-5.step"],
    "SOT-223": [
        "Package_TO_SOT_SMD.3dshapes/SOT-223-3_TabPin2.step",
        "Package_TO_SOT_SMD.3dshapes/SOT-223.step",
    ],
    "SOIC-8": ["Package_SO.3dshapes/SOIC-8_3.9x4.9mm_P1.27mm.step"],
    "SOIC-14": ["Package_SO.3dshapes/SOIC-14_3.9x8.7mm_P1.27mm.step"],
    "TSSOP": [
        "Package_SO.3dshapes/TSSOP-8_4.4x3mm_P0.65mm.step",
        "Package_SO.3dshapes/TSSOP-14_4.4x5mm_P0.65mm.step",
    ],
    "QFN": [
        "Package_DFN_QFN.3dshapes/QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm.step",
        "Package_DFN_QFN.3dshapes/QFN-32_5x5mm_P0.5mm.step",
        "Package_DFN_QFN.3dshapes/QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm.step",
    ],
    "LQFP": [
        "Package_QFP.3dshapes/LQFP-48_7x7mm_P0.5mm.step",
        "Package_QFP.3dshapes/LQFP-64_10x10mm_P0.5mm.step",
    ],
    "CRYSTAL": [
        "Crystal.3dshapes/Crystal_SMD_3225-4Pin_3.2x2.5mm.step",
    ],
    # ---- Phase 3 hero parts (distinctive silhouettes) ----
    "SOT-23-6": ["Package_TO_SOT_SMD.3dshapes/SOT-23-6.step"],
    "USB_C": [
        "Connector_USB.3dshapes/USB_C_Receptacle_GCT_USB4085.step",
        "Connector_USB.3dshapes/USB_C_Receptacle_HRO_TYPE-C-31-M-12.step",
        "Connector_USB.3dshapes/USB_C_Receptacle_Amphenol_12401548E4-2A.step",
    ],
    "LGA-8": [
        "Package_LGA.3dshapes/Bosch_LGA-8_2.5x2.5mm_P0.65mm_ClockwisePinNumbering.step",
        "Package_LGA.3dshapes/Bosch_LGA-8_2.5x2.5mm_P0.65mm.step",
    ],
    "JST_PH": [
        "Connector_JST.3dshapes/JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical.step",
        "Connector_JST.3dshapes/JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal.step",
    ],
    "CP_ELEC": [
        "Capacitor_SMD.3dshapes/CP_Elec_6.3x7.7.step",
        "Capacitor_SMD.3dshapes/CP_Elec_8x10.5.step",
    ],
    # ---- recoverable package models (push coverage toward the KiCad ceiling) ----
    "TO-92": ["Package_TO_SOT_THT.3dshapes/TO-92_Inline.step",
              "Package_TO_SOT_THT.3dshapes/TO-92.step"],
    "TO-220": ["Package_TO_SOT_THT.3dshapes/TO-220-3_Vertical.step"],
    "TO-252": ["Package_TO_SOT_SMD.3dshapes/TO-252-2.step",
               "Package_TO_SOT_SMD.3dshapes/TO-252-3_TabPin2.step"],
    "SOIC-16": ["Package_SO.3dshapes/SOIC-16_3.9x9.9mm_P1.27mm.step"],
    "SOP-16": ["Package_SO.3dshapes/SOP-16_3.9x9.9mm_P1.27mm.step",
               "Package_SO.3dshapes/SOIC-16_3.9x9.9mm_P1.27mm.step"],
    "MSOP-10": ["Package_SO.3dshapes/MSOP-10_3x3mm_P0.5mm.step"],
    "SSOP-28": ["Package_SO.3dshapes/SSOP-28_5.3x10.2mm_P0.65mm.step"],
    "LGA-14": ["Package_LGA.3dshapes/Bosch_LGA-14_3x2.5mm_P0.5mm.step",
               "Package_LGA.3dshapes/ST_LGA-14_2.5x3.0mm_P0.5mm.step"],
    "LGA-16": ["Package_LGA.3dshapes/LGA-16_3x3mm_P0.5mm.step",
               "Package_LGA.3dshapes/InvenSense_LGA-16_3x3mm_P0.5mm.step"],
    "LGA-10": ["Package_LGA.3dshapes/LGA-10_2x2mm_P0.5mm.step",
               "Package_LGA.3dshapes/ST_LGA-10L_2.5x2.5mm_P0.6mm_LayoutBorder3x2y.step"],
    "DIP-8": ["Package_DIP.3dshapes/DIP-8_W7.62mm.step"],
    "USB_MICRO": ["Connector_USB.3dshapes/USB_Micro-B_Molex-105017-0001.step",
                  "Connector_USB.3dshapes/USB_Micro-B_Amphenol_10118194-0001LF_Horizontal.step"],
    # MPN-keyed override (wired into MPN_MODELS, not PACKAGE_MODELS).
    "ESP32-WROOM-32": [
        "RF_Module.3dshapes/ESP32-WROOM-32.step",
        "RF_Module.3dshapes/ESP32-WROOM-32E.step",
    ],
}


def _url_ok(url: str) -> bool:
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status == 200
    except Exception:
        return False


def _download(url: str, dest: Path) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            if r.status != 200:
                return False
            dest.write_bytes(r.read())
        return dest.stat().st_size > 0
    except Exception:
        return False


def _as_single_mesh(scene_or_mesh) -> trimesh.Trimesh:
    if isinstance(scene_or_mesh, trimesh.Scene):
        return scene_or_mesh.to_geometry() if hasattr(scene_or_mesh, "to_geometry") \
            else trimesh.util.concatenate(tuple(scene_or_mesh.geometry.values()))
    return scene_or_mesh


# GLB filename tokens of Y-asymmetric connector bodies confirmed mirrored by the
# blanket -90X transform (fab-outline audit, 2026-07-23) — see compute_transform.
_YAW_FIX = (
    "USB_C_Receptacle_GCT_USB4085",
    "JST_PH_S2B-PH-K",
    "HDMI_Micro-D_Molex_46765-1xxx",
    # Modules are Y-asymmetric too: the ESP32-WROOM-32 can occupies the pad end
    # and the antenna the other, with no pads under it. Measured from the
    # committed GLB (2026-08-23): body y spans -9.755..+15.745 mm, centre
    # +2.995 — so the blanket -90X mirror rendered the can ~6 mm from where its
    # own pads are, with the antenna pointing inboard.
    "RF_Module__ESP32-WROOM-32",
)


def compute_transform(glb_path: Path) -> dict:
    """Return {scale, rotation:[x,y,z]deg, offset:[x,y,z]mm} so the model renders
    seated and upright in our Y-up, mm scene.

    KiCad STEP models are a uniform convention: metres, Z-up, with z=0 at the
    BOARD SURFACE — THT pins/pegs deliberately extend into -z (through the
    board). So the transform is the SAME for every one: scale 1000 (m->mm) and a
    -90 deg X rotation maps +Z(up) -> +Y(up).

    Seating rule: NEVER lift a model above its native origin. min_z < 0 is a
    THT pin/peg that belongs below the board — lifting by -min_z (the old
    behaviour) floated every THT connector by its pin length. Only a model whose
    whole geometry hovers ABOVE z=0 (min_z > 0, a stray base offset) is sunk
    back down onto the board.

    Pin-anchored footprints (Week-5 7.3 finding): the -90 deg X rotation maps
    model +Y to scene -Z, i.e. it MIRRORS the footprint-frame Y the pad cache
    uses. Centred models don't care; the PinHeader/PinSocket families anchor at
    pin 1, so their bodies rendered on the wrong side of the origin (a 2x20
    header hung 48 mm off the board). Their bodies are symmetric pin arrays, so
    a translation is exact: offset_z = 2 * (raw in-plane Y centre) puts the
    body back over the pads.

    Y-asymmetric connectors (2026-07-23 fab-outline audit): for a body that is
    NOT Y-symmetric, a translation can't fix the mirror — the mouth would still
    face backward. Those get an extra 180 deg yaw instead (rotation
    [-90, 0, 180]; exact because the shells ARE X-symmetric), which also negates
    model X, compensated by offset_x = 2 * (raw in-plane X centre). The set is
    explicit (_YAW_FIX) — mirroring is undetectable from the GLB alone; it was
    established by comparing measured body centres against the official KiCad
    footprints' F.Fab outlines."""
    mesh = _as_single_mesh(trimesh.load(glb_path, force="mesh"))
    min_z_mm = float(mesh.vertices[:, 2].min()) * 1000.0
    off_y = -min_z_mm if min_z_mm > 0 else 0.0
    if off_y:
        print(f"   note: {glb_path.name} floats {min_z_mm:.2f}mm above z=0 — sinking it")
    off_x = off_z = yaw = 0.0
    if any(tok in glb_path.name for tok in _YAW_FIX):
        yaw = 180.0
        cx_mm = float((mesh.bounds[0][0] + mesh.bounds[1][0]) / 2.0) * 1000.0
        if abs(cx_mm) >= 0.05:
            off_x = round(2.0 * cx_mm, 4)
        print(f"   note: {glb_path.name} is Y-asymmetric — yaw 180, offset_x {off_x}mm")
    elif re.search(r"PinHeader|PinSocket|Socket_Strip", glb_path.name):
        cy_mm = float((mesh.bounds[0][1] + mesh.bounds[1][1]) / 2.0) * 1000.0
        if abs(cy_mm) >= 0.05:
            off_z = round(2.0 * cy_mm, 4)
            print(f"   note: {glb_path.name} is pin-anchored — offset_z {off_z}mm onto its pads")
    return {
        "scale": 1000.0,
        "rotation": [-90.0, 0.0, yaw],   # Z-up -> Y-up (+ de-mirror yaw)
        "offset": [off_x, round(off_y, 4), off_z],
    }


def _kicad_step_candidates(footprint: str) -> list[str]:
    """Map a 'Lib:Name' footprint string to candidate KiCad STEP paths.

    KiCad packages3D mirrors footprint libs: 'Capacitor_SMD:C_0603_1608Metric'
    -> 'Capacitor_SMD.3dshapes/C_0603_1608Metric.step'. A couple of libs were
    consolidated (QFN lives under Package_DFN_QFN), so we try those too."""
    if ":" not in footprint:
        return []
    lib, name = footprint.split(":", 1)
    libs = [lib]
    if lib == "Package_QFN":
        libs.append("Package_DFN_QFN")
    elif lib == "Package_DFN_QFN":
        libs.append("Package_QFN")
    return [f"{ln}.3dshapes/{name}.step" for ln in libs]


def sweep_library() -> None:
    """Phase 2 — auto-resolve every library footprint to a KiCad model.

    Enumerates the unique footprint strings across the curated library, resolves
    each to a KiCad STEP by name (no hand-curation), converts to GLB, and writes
    a manifest the frontend consumes by footprint name. Bounded by size/count so
    repo growth stays sane; the rest fall back to the procedural box."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.services import library  # noqa: E402

    AUTO_DIR.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict] = (
        json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    )

    seen: set[str] = set()
    footprints: list[str] = []
    for p in library.all_parts():
        if p.footprint and ":" in p.footprint and p.footprint not in seen:
            seen.add(p.footprint)
            footprints.append(p.footprint)

    tmp = Path(tempfile.mkdtemp())
    added = resolved = 0
    for fp in sorted(footprints):
        if added >= MAX_AUTO_MODELS:
            print(f"... hit cap of {MAX_AUTO_MODELS} new models; stopping")
            break
        if fp in manifest:
            resolved += 1
            continue
        chosen = next(
            (c for c in _kicad_step_candidates(fp) if _url_ok(f"{RAW}/{c}")), None
        )
        if chosen is None:
            continue
        safe = fp.replace(":", "__").replace("/", "_")
        step = tmp / f"{safe}.step"
        if not _download(f"{RAW}/{chosen}", step):
            continue
        glb = AUTO_DIR / f"{safe}.glb"
        try:
            cascadio.step_to_glb(str(step), str(glb), tol_linear=0.01, tol_angular=0.1)
            if glb.stat().st_size > MAX_AUTO_BYTES:
                glb.unlink()
                print(f"[big]  {fp} ({glb.name}) over size budget — skipped")
                continue
            tf = compute_transform(glb)
            pack_glb(glb)
        except Exception as exc:  # noqa: BLE001
            print(f"[fail] {fp}: {exc}")
            continue
        manifest[fp] = {"url": f"/models/auto/{safe}.glb", **tf}
        resolved += 1
        added += 1
        print(f"[ok]   {fp:54} {glb.stat().st_size/1024:6.1f} KB")

    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nResolved {resolved}/{len(footprints)} footprints "
          f"({100*resolved//max(len(footprints),1)}%); {added} new this run. "
          f"Manifest: {MANIFEST}")


import glob as _glob
import os as _os


def _local_3dmodel_bases() -> list[str]:
    """Directories holding KiCad's stock `*.3dshapes` STEP models (local, no
    download). Env override first, then the standard install locations."""
    bases = []
    for k, v in _os.environ.items():
        if k.startswith("KICAD") and k.endswith("3DMODEL_DIR") and v:
            bases.append(v)
    bases += _glob.glob(r"C:\Program Files\KiCad\*\share\kicad\3dmodels")
    bases += ["/usr/share/kicad/3dmodels", "/usr/local/share/kicad/3dmodels"]
    return [b for b in dict.fromkeys(bases) if _os.path.isdir(b)]


def _resolve_local_model(raw: str, bases: list[str]) -> Path | None:
    """Resolve an embedded `${..._3DMODEL_DIR}/Lib.3dshapes/Name.step` reference
    to a local file. Handles .step/.stp/.STEP/.wrl→.step and env-var prefixes.
    Returns None for 3rd-party paths we don't have locally."""
    rel = raw.split("}", 1)[1] if "}" in raw else raw
    rel = rel.lstrip("/\\").replace("\\", "/")
    stems = [rel]
    low = rel.lower()
    if low.endswith(".wrl"):
        stems.append(rel[:-4] + ".step")
    for base in bases:
        for cand in stems:
            p = Path(base) / cand
            if p.exists():
                return p
            # case-insensitive .step/.STEP fallback
            alt = p.with_suffix(".step") if p.suffix.lower() != ".step" else p.with_suffix(".STEP")
            if alt.exists():
                return alt
    return None


# Skip individual GLBs bigger than this (huge modules/connectors) — they fall
# back to procedural. Raised vs the network sweep since local convert is free.
MAX_LOCAL_BYTES = 2_000 * 1024


def sweep_local() -> None:
    """Task 3.3 — full-coverage 3D sweep from LOCAL KiCad STEP models.

    For every unique footprint across the library, ask the bridge for the
    footprint's embedded 3D-model reference (authoritative), resolve it to a
    local `.step`, convert to GLB, and write the manifest keyed by footprint
    string. No downloads, no name-guessing — the footprint tells us its model."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.services import library, kicad_bridge  # noqa: E402

    bases = _local_3dmodel_bases()
    if not bases:
        print("No local KiCad 3dmodels dir found; is KiCad installed?")
        return
    print(f"local 3D model bases: {bases}")
    AUTO_DIR.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict] = (
        json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    )

    footprints = sorted({p.footprint for p in library.all_parts()
                         if p.footprint and ":" in p.footprint})
    added = resolved = no_model = missing = big = failed = 0
    for fp in footprints:
        if fp in manifest and manifest[fp].get("url"):
            resolved += 1
            continue
        res = kicad_bridge.run_script("dump_footprint.py", {"footprint": fp})
        models = res.data.get("models", []) if res.success else []
        if not models:
            no_model += 1
            continue
        step = next((_resolve_local_model(m, bases) for m in models
                     if _resolve_local_model(m, bases)), None)
        if step is None:
            # Not installed locally — try KiCad's packages3D repo online. After
            # stripping the ${..._DIR}/ prefix, the path IS the RAW repo tail:
            # 'Connector_FFC-FPC.3dshapes/Amphenol_....step'.
            for m in models:
                rel = (m.split("}", 1)[1] if "}" in m else m).lstrip("/\\").replace("\\", "/")
                if ".3dshapes/" not in rel:
                    continue  # 3rd-party (Espressif etc.) — not in packages3D
                dl = Path(tempfile.mkdtemp()) / "m.step"
                if _download(f"{RAW}/{rel}", dl) or _download(f"{RAW}/{rel[:-4]}step", dl):
                    step = dl
                    break
            if step is None:
                missing += 1
                continue
        safe = fp.replace(":", "__").replace("/", "_")
        glb = AUTO_DIR / f"{safe}.glb"
        try:
            cascadio.step_to_glb(str(step), str(glb), tol_linear=0.01, tol_angular=0.1)
            if glb.stat().st_size > MAX_LOCAL_BYTES:
                print(f"[big]  {fp} ({glb.stat().st_size/1024:.0f} KB) — skipped")
                glb.unlink(); big += 1; continue
            tf = compute_transform(glb)
            pack_glb(glb)
        except Exception as exc:  # noqa: BLE001
            print(f"[fail] {fp}: {exc}"); failed += 1; continue
        manifest[fp] = {"url": f"/models/auto/{safe}.glb", **tf}
        resolved += 1; added += 1
        if added % 25 == 0:
            print(f"  ...{added} converted")

    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    total = len(footprints)
    print(f"\nsweep_local: {resolved}/{total} footprints have a real model "
          f"({100*resolved//max(total,1)}%). +{added} new. "
          f"no_model={no_model} missing_local={missing} oversize={big} failed={failed}")
    print(f"Manifest: {MANIFEST}")


# Drop-in directory for per-MPN vendor models: <MPN>.step / <MPN>.stp / <MPN>.glb.
# A vendor model is exactly that part's shape, so it must beat every package
# stand-in — the frontend resolves manifest key "MPN:<mpn>" ahead of the
# footprint/package tiers (docs/PHOTOREALISTIC_3D_PLAN.md Layer 1 item 2).
# NOTE licensing: many manufacturer STEP files may not be redistributable —
# only drop in models we're allowed to commit (see REAL_3D_MODELS_PLAN.md §4.5).
VENDOR_DIR = Path(__file__).resolve().parent.parent / "app" / "library" / "vendor_models"


def sweep_mpn() -> None:
    """Per-MPN vendor sweep — convert backend/app/library/vendor_models/<MPN>.*
    to GLB and write manifest entries keyed "MPN:<mpn>"."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.services import library  # noqa: E402

    if not VENDOR_DIR.is_dir():
        print(f"No vendor models dir ({VENDOR_DIR}) — nothing to do.")
        return
    known = {p.mpn for p in library.all_parts()}
    AUTO_DIR.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict] = (
        json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    )

    sources = sorted(
        f for f in VENDOR_DIR.iterdir()
        if f.suffix.lower() in (".step", ".stp", ".glb")
    )
    if not sources:
        print(f"Vendor models dir is empty ({VENDOR_DIR}) — nothing to do.")
        return
    added = failed = 0
    for src in sources:
        mpn = src.stem
        if mpn not in known:
            print(f"[warn] {src.name}: MPN {mpn!r} not in the library — skipped")
            continue
        safe = "mpn_" + re.sub(r"[^A-Za-z0-9_.-]", "_", mpn)
        glb = AUTO_DIR / f"{safe}.glb"
        try:
            if src.suffix.lower() == ".glb":
                glb.write_bytes(src.read_bytes())
            else:
                cascadio.step_to_glb(str(src), str(glb), tol_linear=0.01, tol_angular=0.1)
            tf = compute_transform(glb)
            pack_glb(glb)
        except Exception as exc:  # noqa: BLE001
            print(f"[fail] {mpn}: {exc}"); failed += 1; continue
        manifest[f"MPN:{mpn}"] = {"url": f"/models/auto/{safe}.glb", **tf}
        added += 1
        print(f"[ok]   MPN:{mpn} <- {src.name}")

    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nsweep_mpn: +{added} per-MPN models, {failed} failed. Manifest: {MANIFEST}")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    registry: dict[str, dict] = {}
    tmp = Path(tempfile.mkdtemp())

    for key, candidates in PACKAGES.items():
        chosen = next((c for c in candidates if _url_ok(f"{RAW}/{c}")), None)
        if chosen is None:
            print(f"[skip] {key}: no candidate STEP found")
            continue
        step = tmp / f"{key}.step"
        if not _download(f"{RAW}/{chosen}", step):
            print(f"[skip] {key}: download failed ({chosen})")
            continue

        out_name = f"{key.lower()}.glb"
        glb = OUT_DIR / out_name
        try:
            cascadio.step_to_glb(str(step), str(glb), tol_linear=0.01, tol_angular=0.1)
            tf = compute_transform(glb)
            pack_glb(glb)
        except Exception as exc:  # noqa: BLE001
            print(f"[fail] {key}: convert/analyze error: {exc}")
            continue

        registry[key] = {"url": f"/models/{out_name}", **tf}
        size_kb = glb.stat().st_size / 1024
        print(f"[ok]   {key:9} <- {chosen.split('/')[-1]:42} {size_kb:6.1f} KB  "
              f"rot={tf['rotation'][0]:+.0f}  offY={tf['offset'][1]:.3f}")

    print("\n// ---- paste into frontend/src/lib/models3d.ts (PACKAGE_MODELS) ----")
    for key, spec in registry.items():
        print(f'  "{key}": {json.dumps(spec)},')
    print(f"\nWrote {len(registry)} GLBs to {OUT_DIR}")


if __name__ == "__main__":
    if "--local" in sys.argv:
        sweep_local()          # task 3.3 — full-coverage sweep from local KiCad STEP
    elif "--mpn" in sys.argv:
        sweep_mpn()            # per-MPN vendor models -> "MPN:<mpn>" manifest keys
    elif "--from-library" in sys.argv:
        sweep_library()        # network sweep from KiCad packages3D (legacy)
    else:
        main()
