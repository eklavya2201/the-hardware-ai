"""Agent A — the demand miner: what the library is actually MISSING.

The library grew to 23,941 parts by catalogue sweep, which is why it holds
thousands of capacitor variants and still missed the relay module a user asked
for. This script answers the other question: what did people want that we could
not give them? It reads only evidence, never opinion.

Four sources, strongest first:

  1. `unavailable=true` requirements on stored projects — the agent recorded, in
     the user's own words, an ask the library could not satisfy. Proof.
  2. Zero-result `search_parts` calls (services/demand_log.py) — the agent went
     looking and found nothing.
  3. Thin-result searches (1-2 hits) — we technically had something, but no
     choice; the second-source question a real BOM asks.
  4. R43 shortfall errors — the build did not contain what was requested.

Output is a ranked list of NEEDS with their evidence, written to
`app/library/demand_report.json` and printed. It names categories and asks, not
part numbers: choosing the MPN is the curator's job (Agent B), and checking it
is the verifier's (Agent C).

    python -m scripts.demand_report                      # local DB + demand log
    python -m scripts.demand_report --db /data/voltron.db --json out.json
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
LIBRARY = BACKEND / "app" / "library"

# Words that describe a PART CLASS. A need is only actionable if we can say what
# kind of thing is missing; "variable voltage sources representing x, y, alpha"
# is a simulation concept, not a part, and must not reach the curator.
_CLASS_WORDS = {
    "relay", "speaker", "buzzer", "microphone", "mic", "display", "lcd", "oled",
    "led", "sensor", "imu", "accelerometer", "gyro", "magnetometer", "barometer",
    "thermistor", "thermocouple", "encoder", "motor", "servo", "stepper",
    "driver", "mosfet", "transistor", "regulator", "buck", "boost", "ldo",
    "charger", "battery", "holder", "cell", "connector", "header", "socket",
    "usb", "jack", "terminal", "switch", "button", "potentiometer", "trimmer",
    "crystal", "oscillator", "resonator", "eeprom", "flash", "memory", "sd",
    "mcu", "microcontroller", "fpga", "transceiver", "radio", "lora", "rf",
    "antenna", "bluetooth", "wifi", "gps", "gnss", "modem", "sim", "ethernet",
    "phy", "opamp", "comparator", "adc", "dac", "shunt", "current", "isolator",
    "optocoupler", "gate", "bldc", "esc", "solenoid", "valve", "pump", "fan",
    "heater", "camera", "rtc", "clock", "fuse", "tvs", "diode", "inductor",
    "transformer", "amplifier", "codec", "dcdc", "converter", "level",
}
_STOP = {"the", "a", "an", "for", "with", "and", "or", "of", "to", "in", "on",
         "no", "matching", "part", "available", "library", "curated", "requested",
         "not", "module", "board", "chip", "ic", "using", "that", "this", "is"}


def _phrase(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())[:110]


def _classes(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9+.\-]+", (text or "").lower())
    return sorted({w for w in words if w in _CLASS_WORDS})


def _actionable(text: str) -> bool:
    return bool(_classes(text))


def mine_projects(db_path: Path) -> tuple[list[dict], dict]:
    """Unavailable requirements + R43 shortfalls from every stored project."""
    needs: list[dict] = []
    stats = {"projects": 0, "requirements": 0, "unavailable": 0, "r43": 0}
    if not db_path.exists():
        return needs, stats
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute("select id, ir, issues from projects").fetchall()
    except sqlite3.Error:
        return needs, stats
    for row in rows:
        stats["projects"] += 1
        try:
            ir = json.loads(row["ir"] or "{}")
            issues = json.loads(row["issues"] or "[]")
        except ValueError:
            continue
        for req in ir.get("requirements") or []:
            stats["requirements"] += 1
            if not req.get("unavailable"):
                continue
            stats["unavailable"] += 1
            text = _phrase(req.get("description"))
            needs.append({
                "need": text, "source": "unavailable_requirement", "weight": 10,
                "project": row["id"], "classes": _classes(text),
                "actionable": _actionable(text),
            })
        for issue in issues:
            if str(issue.get("rule") or "").startswith("R43") and issue.get("severity") == "error":
                stats["r43"] += 1
                text = _phrase(issue.get("message"))
                needs.append({
                    "need": text, "source": "r43_shortfall", "weight": 4,
                    "project": row["id"], "classes": _classes(text),
                    "actionable": _actionable(text),
                })
    db.close()
    return needs, stats


def mine_searches(log_rows: list[dict]) -> tuple[list[dict], dict]:
    """Zero-result and thin-result searches."""
    needs: list[dict] = []
    stats = {"searches": len(log_rows), "zero": 0, "thin": 0}
    for row in log_rows:
        count = int(row.get("count") or 0)
        query = _phrase(row.get("query"))
        if not query:
            continue
        if count == 0:
            stats["zero"] += 1
            needs.append({"need": query, "source": "search_no_results", "weight": 8,
                          "project": row.get("project"), "classes": _classes(query),
                          "actionable": _actionable(query)})
        elif count <= 2:
            stats["thin"] += 1
            needs.append({"need": query, "source": "search_thin", "weight": 2,
                          "project": row.get("project"), "classes": _classes(query),
                          "actionable": _actionable(query)})
    return needs, stats


def already_have(classes: list[str], evidence: list[str]) -> list[dict]:
    """What the CURRENT library offers for this need. A need the library can
    already answer is a DISCOVERY problem (the agent searched badly, or the
    search ranked badly), not a catalogue problem — and curating another part
    would not fix it. Prod asked for a relay module in August; three had been
    curated that same month."""
    from app.services import library as lib

    seen: dict[str, dict] = {}
    for query in [" ".join(classes)] + evidence[:2]:
        for part in lib.search(query=query)[:6]:
            seen.setdefault(part.mpn, {
                "mpn": part.mpn, "category": part.category,
                "description": (part.description or "")[:70],
            })
    return list(seen.values())[:6]


def rank(needs: list[dict]) -> list[dict]:
    """Group by the part classes named, so five ways of asking for a relay are
    one need with five pieces of evidence."""
    groups: dict[str, dict] = {}
    for n in needs:
        key = "+".join(n["classes"]) or "(unclassified)"
        g = groups.setdefault(key, {
            "classes": n["classes"], "score": 0, "evidence": [],
            "sources": collections.Counter(), "actionable": n["actionable"],
        })
        g["score"] += n["weight"]
        g["sources"][n["source"]] += 1
        if len(g["evidence"]) < 6 and n["need"] not in g["evidence"]:
            g["evidence"].append(n["need"])
    out = []
    for key, g in groups.items():
        out.append({
            "need": key, "classes": g["classes"], "score": g["score"],
            "actionable": g["actionable"], "evidence": g["evidence"],
            "sources": dict(g["sources"]),
        })
    return sorted(out, key=lambda r: (-r["score"], r["need"]))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=str(BACKEND / "voltron.db"),
                    help="project database to mine (read-only)")
    ap.add_argument("--json", default=str(LIBRARY / "demand_report.json"))
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args(argv)

    from app.services import demand_log, library as lib

    project_needs, pstats = mine_projects(Path(args.db))
    search_needs, sstats = mine_searches(demand_log.read())
    ranked = rank(project_needs + search_needs)
    for r in ranked:
        r["library_has"] = already_have(r["classes"], r["evidence"])
        # A need with plausible existing parts goes to the DISCOVERY bucket:
        # the curator must not add a duplicate to fix a search problem.
        r["verdict"] = "discovery" if r["library_has"] else "curate"
    actionable = [r for r in ranked if r["actionable"]]
    skipped = [r for r in ranked if not r["actionable"]]

    print("EVIDENCE")
    print(f"  projects mined        {pstats['projects']}")
    print(f"  requirements recorded {pstats['requirements']}")
    print(f"  unavailable asks      {pstats['unavailable']}   <- proven gaps")
    print(f"  R43 shortfalls        {pstats['r43']}")
    print(f"  searches logged       {sstats['searches']}  (zero-result {sstats['zero']}, thin {sstats['thin']})")
    print(f"  library today         {len(lib.all_parts())} parts\n")

    to_curate = [r for r in actionable if r["verdict"] == "curate"]
    to_find = [r for r in actionable if r["verdict"] == "discovery"]

    print(f"CURATE — the library genuinely has nothing ({len(to_curate)})")
    for r in to_curate[: args.top]:
        srcs = ", ".join(f"{k}x{v}" for k, v in r["sources"].items())
        print(f"  [{r['score']:>3}] {r['need']:<26} {srcs}")
        for e in r["evidence"][:2]:
            print(f"          · {e[:96]}")

    print()
    print(f"DISCOVERY — we HAVE candidates; the agent did not find them ({len(to_find)})")
    for r in to_find[: args.top]:
        have = ", ".join(h["mpn"] for h in r["library_has"][:4])
        print(f"  [{r['score']:>3}] {r['need']:<26} have: {have}")
        print(f"          · {r['evidence'][0][:96]}")
    if skipped:
        print("\nNOT ACTIONABLE (no part class named — a curator cannot act on these)")
        for r in skipped[:5]:
            print(f"  [{r['score']:>3}] {r['evidence'][0][:90]}")

    payload = {"evidence": {**pstats, **sstats},
               "curate": to_curate, "discovery": to_find, "skipped": skipped}
    Path(args.json).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nwrote {args.json}")
    print("next: python -m scripts.curate_bot run --from-demand   (Agent B)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
