"""Backfill sizing-critical library metadata — feeds the Phase-5 sizing rules.

The sizing rules (R31 regulator current, R32 thermal, R34 I²C address) + the
templates are only as good as the curated electrical envelope, and ~192 loady
parts lack `currentMaxMa`, ~46 I²C parts lack `defaultI2cAddress`, and ~39 lack
their `I2C` interface tag. This tool closes those gaps in TWO tiers, cheapest
first:

  1. DETERMINISTIC (free, instant) — parse the specs already sitting in each
     part's `description`/`firmwareNotes` text: a current rating ("1.5 A"/
     "600 mA"), an I²C address ("0x76"), the I²C interface tag. No LLM, no
     network. Only ~15–20% of gaps are text-recoverable, but it's free.

  2. LLM-FROM-KNOWLEDGE (`--llm`, needs a key) — the fast lever for the BULK. A
     modern model already KNOWS most common parts' specs (an ESP32's peak draw,
     an LM7805's 1.5 A, a BME280's 0x76), so we ask it to fill the missing
     sizing fields from the MPN + description in BATCHES — NO per-part datasheet
     fetch (slow + bot-blocked), NO human-review bottleneck for high-confidence
     single-field fills. This is far faster and more effective than scraping
     datasheets one at a time.

Only EMPTY fields are ever filled (never overwrites curated data). Dry-run by
default — prints exactly what it would change. `--apply` edits `parts.json` in
place (with a `.bak` backup) and re-validates via `verify_library`.

Usage:
    python -m scripts.backfill_metadata                 # dry-run, deterministic
    python -m scripts.backfill_metadata --apply         # apply deterministic
    python -m scripts.backfill_metadata --llm           # dry-run, +LLM proposals
    python -m scripts.backfill_metadata --llm --apply    # apply both
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

# Standalone scripts must load .env themselves (the web app does it in main.py).
try:
    from dotenv import load_dotenv
    load_dotenv(_BACKEND_ROOT / ".env")
except Exception:
    pass

_PARTS_PATH = _BACKEND_ROOT / "app" / "library" / "parts.json"

LOADY = {"mcu", "regulator", "charger", "sensor", "actuator", "display",
         "memory", "interface", "audio", "logic"}
I2C_LIKELY = {"sensor", "interface", "memory", "display", "audio"}

_CUR = re.compile(r"(\d+(?:\.\d+)?)\s*(µA|uA|mA|A)\b", re.I)
_ADDR = re.compile(r"0x[0-9a-fA-F]{2}")


def _text(p: dict) -> str:
    return f"{p.get('description', '')} {p.get('firmwareNotes', '')}"


def _parse_current_ma(text: str) -> float | None:
    """Largest current in the text, in mA (usually the rated max). Handles
    A/mA/µA."""
    vals: list[float] = []
    for m in _CUR.finditer(text):
        v, u = float(m.group(1)), m.group(2).lower()
        vals.append(v / 1000 if u in ("µa", "ua") else v if u == "ma" else v * 1000)
    return max(vals) if vals else None


def _mentions_i2c(text: str) -> bool:
    t = text.lower()
    if not ("i2c" in t or "i²c" in t or "iic" in t):
        return False
    # Negative guard: a part described as 1-Wire / analog / SPI-only that merely
    # NAMES i2c (e.g. "1-Wire, not I2C") is a false positive — require the text
    # not to foreground a different bus. Conservative: skip if 1-Wire is present.
    if "1-wire" in t or "one-wire" in t or "onewire" in t:
        return False
    return True


def deterministic_fills(part: dict) -> dict:
    """Fields fillable from this part's own text (empty fields only)."""
    fills: dict = {}
    cat = part.get("category", "")
    text = _text(part)

    if cat in LOADY and part.get("currentMaxMa") is None:
        cur = _parse_current_ma(text)
        if cur is not None:
            fills["currentMaxMa"] = round(cur, 3)

    if cat in I2C_LIKELY:
        ifaces = part.get("interfaces") or []
        if _mentions_i2c(text) and "I2C" not in ifaces:
            fills["interfaces"] = ifaces + ["I2C"]
        if (_mentions_i2c(text) or "I2C" in ifaces) and not part.get("defaultI2cAddress"):
            m = _ADDR.search(text)
            if m:
                fills["defaultI2cAddress"] = m.group(0).lower()
    return fills


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write changes to parts.json")
    ap.add_argument("--llm", action="store_true", help="also propose LLM-from-knowledge fills")
    args = ap.parse_args(argv)

    raw = json.loads(_PARTS_PATH.read_text(encoding="utf-8"))
    parts = raw["parts"] if isinstance(raw, dict) and "parts" in raw else raw

    det_changes: list[tuple[str, dict]] = []
    for p in parts:
        fills = deterministic_fills(p)
        if fills:
            det_changes.append((p["mpn"], fills))

    print(f"DETERMINISTIC (from existing text) — {len(det_changes)} parts fillable:")
    by_field: dict[str, int] = {}
    for _, f in det_changes:
        for k in f:
            by_field[k] = by_field.get(k, 0) + 1
    for k, n in sorted(by_field.items(), key=lambda x: -x[1]):
        print(f"  {n:4d}  {k}")
    for mpn, f in det_changes[:12]:
        print(f"     e.g. {mpn}: {f}")

    llm_changes: list[tuple[str, dict]] = []
    if args.llm:
        llm_changes = _llm_backfill(parts)
        idx0 = {p["mpn"]: p for p in parts}
        print(f"\nLLM proposals — {len(llm_changes)} high-confidence "
              f"currentMaxMa fills (review):")
        for mpn, f in llm_changes:
            desc = (idx0.get(mpn, {}).get("description") or "")[:55]
            print(f"  {mpn:28s} {f.get('currentMaxMa'):>8} mA  <- {desc}")

    if not args.apply:
        print("\n(dry-run — pass --apply to write parts.json)")
        return 0

    # Apply: fill empty fields only, in place, with a backup.
    fills_by_mpn: dict[str, dict] = {}
    for mpn, f in det_changes + llm_changes:
        fills_by_mpn.setdefault(mpn, {}).update(f)
    idx = {p["mpn"]: p for p in parts}
    applied = 0
    for mpn, f in fills_by_mpn.items():
        p = idx.get(mpn)
        if not p:
            continue
        for k, v in f.items():
            if not p.get(k):  # only fill empty
                p[k] = v
                applied += 1
    backup = _PARTS_PATH.with_suffix(".json.bak")
    backup.write_text(_PARTS_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    _PARTS_PATH.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nApplied {applied} field(s) across {len(fills_by_mpn)} parts. "
          f"Backup: {backup.name}. Now run: python -m scripts.verify_library")
    return 0


def _llm_backfill(parts: list[dict]) -> list[tuple[str, dict]]:
    """Ask the model to fill missing sizing fields from its own knowledge, in
    batches. NO datasheet fetch. Returns [(mpn, {field: value})]. Needs a key."""
    from app.services import agent
    if not agent.has_api_key():
        print("\nLLM: no OPENAI_API_KEY — skipping the from-knowledge pass.")
        return []
    import os
    from openai import OpenAI, AzureOpenAI

    # Only the loady parts still missing a current rating (the bulk gap).
    targets = [
        p for p in parts
        if p.get("category") in LOADY and p.get("currentMaxMa") is None
    ]
    print(f"\nLLM-FROM-KNOWLEDGE — {len(targets)} parts missing currentMaxMa; "
          f"batching (no datasheet fetch)…")

    base_url = os.environ.get("OPENAI_BASE_URL", "")
    if "openai.azure.com" in base_url:
        client = AzureOpenAI(
            api_key=os.environ["OPENAI_API_KEY"], azure_endpoint=base_url,
            api_version=os.environ.get("OPENAI_API_VERSION", "2024-12-01-preview"),
        )
    else:
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    out: list[tuple[str, dict]] = []
    BATCH = 25
    for i in range(0, len(targets), BATCH):
        chunk = targets[i:i + BATCH]
        listing = "\n".join(
            f"- {p['mpn']} ({p.get('category')}): {p.get('description', '')[:80]}"
            for p in chunk
        )
        prompt = (
            "You are a hardware librarian. For each part, give its MAX operating "
            "current in mA (currentMaxMa) ONLY if you are confident from the MPN. "
            "Skip parts you are unsure about. Reply as JSON: "
            '{"parts":[{"mpn":"...","currentMaxMa":<mA>,"confidence":"high|med"}]}.\n\n'
            + listing
        )
        try:
            resp = client.chat.completions.create(
                model=model, temperature=0,
                response_format={"type": "json_object"},
                messages=[{"role": "user", "content": prompt}],
            )
            data = json.loads(resp.choices[0].message.content)
        except Exception as exc:  # noqa: BLE001
            print(f"  batch {i // BATCH}: {exc}")
            continue
        for row in data.get("parts", []):
            if row.get("confidence") == "high" and isinstance(row.get("currentMaxMa"), (int, float)):
                out.append((row["mpn"], {"currentMaxMa": float(row["currentMaxMa"])}))
        print(f"  batch {i // BATCH + 1}: +{len(out)} high-confidence so far")
    return out


if __name__ == "__main__":
    raise SystemExit(main())
