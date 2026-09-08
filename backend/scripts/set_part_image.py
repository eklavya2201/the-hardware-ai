"""Attach a real product image to a library part (the `imageUrl` field).

The Library card renders imageUrl > Wokwi pictorial > category glyph, so this
is how a part graduates to a real photo. Two modes:

  # copy a local photo into the frontend's static assets and point at it
  python -m scripts.set_part_image --mpn HC-SR04 --file ~/Downloads/hcsr04.jpg

  # or point at an already-hosted image URL directly
  python -m scripts.set_part_image --mpn HC-SR04 --url https://…/hcsr04.jpg

  python -m scripts.set_part_image --list          # parts still without a photo

Local files are copied to frontend/public/parts/<slug>.<ext> (same pattern as
the GLB models in public/models/) and imageUrl is set to "/parts/<slug>.<ext>",
served by the frontend origin — no backend/static config needed.

Only use images you have the right to ship (own photos, vendor press assets,
openly-licensed) — same no-fabrication bar as the rest of the library.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
LIBRARY = BACKEND / "app" / "library"
PARTS_DIR = BACKEND.parent / "frontend" / "public" / "parts"
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".svg"}


def _slug(mpn: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", mpn.lower()).strip("-")


def _load(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, parts: list[dict]) -> None:
    path.write_text(json.dumps(parts, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _find(mpn: str) -> tuple[Path, list[dict], dict] | None:
    """Locate the part and the JSON file it lives in (user layer shadows base)."""
    for fname in ("user_parts.json", "parts.json"):
        path = LIBRARY / fname
        if not path.exists():
            continue
        parts = _load(path)
        for p in parts:
            if p.get("mpn", "").upper() == mpn.upper():
                return path, parts, p
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--mpn", help="Part to attach the image to")
    group.add_argument("--list", action="store_true", help="List parts without imageUrl")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--file", help="Local image file to copy into frontend/public/parts/")
    src.add_argument("--url", help="Remote image URL to store as-is")
    ap.add_argument("--remove", action="store_true", help="Clear the part's imageUrl")
    args = ap.parse_args()

    if args.list:
        for fname in ("parts.json", "user_parts.json"):
            path = LIBRARY / fname
            if not path.exists():
                continue
            missing = [p["mpn"] for p in _load(path) if not p.get("imageUrl")]
            print(f"{fname}: {len(missing)} without a photo")
            for m in missing:
                print(f"  {m}")
        return 0

    hit = _find(args.mpn)
    if hit is None:
        print(f"MPN {args.mpn!r} not found in the library.", file=sys.stderr)
        return 1
    path, parts, part = hit

    if args.remove:
        part.pop("imageUrl", None)
        _save(path, parts)
        print(f"[CLEARED] {part['mpn']} imageUrl  ({path.name})")
        return 0

    if args.url:
        image_url = args.url
    elif args.file:
        f = Path(args.file).expanduser()
        if not f.exists():
            print(f"File not found: {f}", file=sys.stderr)
            return 1
        ext = f.suffix.lower()
        if ext not in ALLOWED_EXT:
            print(f"Unsupported extension {ext}; use one of {sorted(ALLOWED_EXT)}", file=sys.stderr)
            return 1
        PARTS_DIR.mkdir(parents=True, exist_ok=True)
        dest = PARTS_DIR / f"{_slug(part['mpn'])}{ext}"
        shutil.copyfile(f, dest)
        image_url = f"/parts/{dest.name}"
        print(f"[COPIED] {f} -> {dest}")
    else:
        print("Provide --file or --url (or --remove).", file=sys.stderr)
        return 1

    part["imageUrl"] = image_url
    _save(path, parts)
    print(f"[SET] {part['mpn']} imageUrl = {image_url}  ({path.name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
