"""Prompt sweep — an autonomous reviewer that tries MANY product prompts against
the real Synqit agent, verifies every build, and writes the insight document
the team then works from.

The 23 beta reviews each cost a human a day; every one turned into a rule, a
part or an archetype. This script does the same loop without the human in the
first seat: it (1) generates prompts across a taxonomy of product archetypes x
power sources x constraints (plus optional LLM-invented prompts), (2) runs each
through `agent.run` -> auto_place -> auto_route, (3) verifies with the 77
deterministic checks AND the datasheet-cited LLM critic AND a "senior engineer"
LLM read of the finished design, (4) aggregates everything into defect
classes with a repro (the exact prompt + saved project JSON), and (5) writes
`docs/sweeps/<stamp>.md` + `.json`.

Everything the LLM says is labelled as opinion; the deterministic layer is the
truth. Costs tokens (~1-3 min per prompt). Interruptible: each run is saved as
it finishes and `--resume` skips prompts already in the run dir.

    python -m scripts.prompt_sweep --list                 # show the prompt matrix
    python -m scripts.prompt_sweep --limit 12             # a first taste
    python -m scripts.prompt_sweep --all --invent 20      # matrix + 20 LLM-invented prompts
    python -m scripts.prompt_sweep --resume --out docs/sweeps/run1
    python -m scripts.prompt_sweep --report-only --out docs/sweeps/run1
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_BACKEND_ROOT / ".env", override=True)

from app.models import Project  # noqa: E402
from app.services import (  # noqa: E402
    agent, library, llm_critic, mechanical_tools, mechanical_verifier,
    pcb_verifier, placement, routing, verifier,
)

# --------------------------------------------------------------------------- #
# Prompt taxonomy — archetypes x variants. Every cell is a prompt a real user
# might type; the variants force the decisions the reviews said matter (input
# voltage, mains, isolation, board shape, module vs bare IC).
# --------------------------------------------------------------------------- #

ARCHETYPES: list[tuple[str, str, list[str]]] = [
    # (key, base prompt, variant suffixes)
    ("iot-sensor", "Design an ESP32 environmental sensor node with temperature, humidity and CO2 that reports over WiFi",
     ["", ", powered by a single 18650 cell with USB-C charging", ", powered from 12 V DC with a buck converter", ", on a 30 mm round board for a ceiling puck"]),
    ("motor-robot", "Design a two-wheel differential-drive robot controller with an ESP32, two DC motors, an ultrasonic sensor and a battery input",
     ["", " on a 3S LiPo with reverse-polarity protection", " using an Arduino Nano and a TB6612", " with encoders on both motors"]),
    ("stepper", "Design a 3-axis stepper controller for a small CNC with limit switches, 24 V input and a 5 V logic rail",
     ["", " using DRV8825 modules", " with an SD card for G-code files"]),
    ("mains", "Design a smart plug that switches a 230 V appliance with a relay, measures its power, and reports over WiFi",
     ["", " using an isolated AC-DC module for the logic supply", " with a triac dimmer instead of a relay"]),
    ("solar", "Design a solar-powered weather station with an 18650, MPPT charging, BME280 and LoRa reporting",
     ["", " that must run from a 6 V panel", " with a 5 V boost for a small display"]),
    ("wearable", "Design a round fitness tracker under 40 mm with a heart-rate sensor, an OLED and BLE, charged over USB-C",
     ["", " using a MAX30102 module and an ESP32-C3", " with a vibration motor for alerts"]),
    ("audio", "Design a Bluetooth speaker board with an ESP32, an I2S DAC and a 10 W class-D amplifier, powered from a 2S LiPo",
     ["", " with a MEMS microphone for voice", " with a 3.5 mm aux input"]),
    ("display", "Design a desk clock with a 4-digit 7-segment display, an RTC with battery backup, and two buttons, powered by USB",
     ["", " using an ESP32 for NTP sync", " with a 16x2 LCD instead of 7-segment"]),
    ("industrial", "Design an isolated RS-485 Modbus sensor node with a 24 V input, two 4-20 mA analog inputs and an STM32",
     ["", " with an optocoupled digital input", " with a relay output"]),
    ("battery-tool", "Design a 4S battery management and protection board with a fuel gauge, balancing and a USB-C PD charger",
     ["", " for a drone pack", " with a coulomb counter"]),
    ("hobby-simple", "Make an Arduino Nano board with a buzzer, a button and three LEDs",
     ["", " and a potentiometer for the buzzer volume", " on a 50x30 mm board"]),
    ("keyboard", "Design a 4x4 macro keypad with a Raspberry Pi Pico, an OLED and a rotary encoder",
     ["", " with per-key RGB LEDs", " on a 100x100 mm board"]),
    ("power-supply", "Design a bench power supply board: 12 V in, adjustable 1.25-10 V out at 2 A, with a current-sense readout",
     ["", " using a linear regulator", " using a buck converter with an INA226 monitor"]),
    ("thermal", "Design a 3D-printer hotend controller with a 100k thermistor, a MOSFET-driven 12 V heater and a fan output, on an ESP32",
     ["", " with a bed heater channel too", " with a thermocouple instead of a thermistor"]),
    ("gps-tracker", "Design a GPS tracker with a SIM7080G cellular module, a NEO-M9N GPS and a LiPo, in a 50x30 mm board",
     ["", " with an accelerometer for wake-on-motion", ""]),
]


def prompt_matrix() -> list[dict]:
    out = []
    for key, base, variants in ARCHETYPES:
        seen = set()
        for i, v in enumerate(variants):
            text = (base + v).strip()
            if text in seen:
                continue
            seen.add(text)
            out.append({"id": f"{key}-{i}", "archetype": key, "prompt": text})
    return out


def invent_prompts(n: int, seed_archetypes: list[str]) -> list[dict]:
    """Ask the model for N realistic user prompts a hardware hobbyist / small
    product team would type — different from the matrix, spread across the
    archetypes, each forcing at least one real engineering decision."""
    if not os.environ.get("OPENAI_API_KEY") or n <= 0:
        return []
    client = agent._make_openai_client()  # noqa: SLF001
    model = os.environ.get("OPENAI_MODEL", "gpt-4o")
    sys_msg = (
        "You generate realistic one-sentence requests that a hobbyist or small hardware team "
        "would type into an AI PCB-design tool. Each must name the product, its power source and "
        "at least one constraint that forces a real engineering decision (voltage, current, mains, "
        "isolation, size/shape, battery chemistry, module vs bare IC). Vary the domains: " +
        ", ".join(seed_archetypes) + ". Return strict JSON: {\"prompts\": [\"...\", ...]}."
    )
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": sys_msg},
                      {"role": "user", "content": f"Give me {n} distinct prompts."}],
            response_format={"type": "json_object"},
        )
        data = json.loads(r.choices[0].message.content or "{}")
        ps = [str(x).strip() for x in data.get("prompts", []) if str(x).strip()]
    except Exception:  # noqa: BLE001
        return []
    return [{"id": f"invent-{i}", "archetype": "invented", "prompt": p} for i, p in enumerate(ps[:n])]


# --------------------------------------------------------------------------- #
# Senior-engineer read (LLM, opinion) — separate from the datasheet-cited critic
# --------------------------------------------------------------------------- #

def senior_engineer_read(project: Project, prompt: str) -> dict:
    """Ask the model to review the finished design as the expert reviewer would:
    does it match the ask, what would a senior engineer reject, what part is
    missing. Structured JSON; labelled opinion downstream."""
    if not os.environ.get("OPENAI_API_KEY"):
        return {}
    client = agent._make_openai_client()  # noqa: SLF001
    model = os.environ.get("OPENAI_MODEL", "gpt-4o")
    parts = [f"{c.refdes}={c.mpn}" for c in project.ir.components]
    nets = [f"{n.name}: " + ", ".join(f"{r}.{p}" for r, p in n.pins[:8]) + (" …" if len(n.pins) > 8 else "")
            for n in project.ir.nets]
    reqs = [f"{r.quantity}x {r.description} ({'UNAVAILABLE' if r.unavailable else (r.mpn or r.category)})"
            for r in (project.ir.requirements or [])]
    board = project.board
    bdesc = (f"{board.shape} {board.width:.0f}x{board.height:.0f} mm, {len(board.components)} placed, "
             f"{len(board.rats)} unrouted") if board else "no board"
    errs = [f"{i.rule}: {i.message}" for i in (project.issues or []) if i.severity == "error"][:20]
    sys_msg = (
        "You are a senior hardware engineer reviewing a PCB design produced by an AI tool. Be blunt "
        "and specific, like a design review at a good company. Judge: (1) does the design deliver "
        "what the prompt asked, (2) the top electrical/architectural problems a senior engineer would "
        "reject (name the part and net), (3) parts that SHOULD be there but are missing from the "
        "design or from the tool's library, (4) is the board size/shape appropriate for the product, "
        "(5) a 1-10 'would I let this go to fab' score. Return strict JSON: "
        "{\"delivers_ask\": bool, \"score\": int, \"problems\": [\"...\"], \"missing_parts\": [\"...\"], "
        "\"size_verdict\": \"...\", \"one_line\": \"...\"}."
    )
    user = json.dumps({
        "prompt": prompt, "requirements_recorded": reqs, "form_factor": (project.ir.form_factor.model_dump() if project.ir.form_factor else None),
        "parts": parts, "nets": nets[:60], "board": bdesc, "verifier_errors": errs,
    }, ensure_ascii=False)
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": sys_msg}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
        )
        return json.loads(r.choices[0].message.content or "{}")
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:200]}


# --------------------------------------------------------------------------- #
# One run
# --------------------------------------------------------------------------- #

def run_prompt(spec: dict, out_dir: Path, critic: bool = True) -> dict:
    pid = f"sweep-{spec['id']}"
    project = Project(id=pid, name=spec["id"], path=f"/{pid}")
    rec: dict = {"id": spec["id"], "archetype": spec["archetype"], "prompt": spec["prompt"], "started": time.time()}
    t0 = time.time()
    try:
        for _ in agent.run(project, spec["prompt"]):
            pass
    except Exception as exc:  # noqa: BLE001
        rec["agent_error"] = f"{exc!s}"[:200]
        traceback.print_exc()
    rec["t_agent"] = round(time.time() - t0, 1)
    rec["parts"] = len(project.ir.components)
    rec["mpns"] = sorted({c.mpn for c in project.ir.components})
    rec["requirements"] = [
        {"q": r.quantity, "d": r.description, "match": r.mpn or r.category, "unavailable": bool(r.unavailable)}
        for r in (project.ir.requirements or [])]
    rec["form_factor"] = project.ir.form_factor.model_dump() if project.ir.form_factor else None
    rec["copilot_last"] = next((m.text for m in reversed(project.chat) if m.role == "assistant" and m.text), "")[:600]

    board = None
    if project.ir.components and "agent_error" not in rec:
        try:
            t0 = time.time()
            board = placement.auto_place(project); project.board = board
            rec["t_place"] = round(time.time() - t0, 1)
            if not (project.mechanical and project.mechanical.mountingHoles):
                mechanical_tools.derive_mounting(project)
            t0 = time.time()
            board = routing.auto_route(project); project.board = board
            rec["t_route"] = round(time.time() - t0, 1)
        except Exception as exc:  # noqa: BLE001
            rec["board_error"] = f"{exc!s}"[:200]
    if board:
        rec["board"] = {"shape": board.shape, "w": round(board.width, 1), "h": round(board.height, 1),
                        "rats": len(board.rats), "traces": len(board.traces)}
        pcb_verifier.merge_into_project(project)
        mechanical_verifier.merge_into_project(project)

    det = verifier.run_all(project)
    project.issues = [i for i in (project.issues or []) if not (i.rule or "").startswith("R")] + det
    rec["errors"] = [{"rule": i.rule, "msg": i.message[:180], "refdes": i.refdes} for i in project.issues if i.severity == "error"]
    rec["warnings"] = [{"rule": i.rule, "msg": i.message[:180]} for i in project.issues if i.severity == "warning"]
    if critic and project.ir.components:
        try:
            crit = llm_critic.critique(project)
            rec["critic"] = [{"sev": i.severity, "msg": i.message[:200], "basis": getattr(i, "citation", None)} for i in crit]
        except Exception as exc:  # noqa: BLE001
            rec["critic_error"] = str(exc)[:200]
        rec["senior"] = senior_engineer_read(project, spec["prompt"])
    rec["t_total"] = round(time.time() - rec["started"], 1)
    (out_dir / f"{spec['id']}.json").write_text(project.model_dump_json(), encoding="utf-8")
    (out_dir / f"{spec['id']}.rec.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
    return rec


# --------------------------------------------------------------------------- #
# Aggregation + report
# --------------------------------------------------------------------------- #

def aggregate(recs: list[dict]) -> dict:
    n = len(recs)
    rule_hits: Counter = Counter()
    rule_examples: dict[str, list] = defaultdict(list)
    unavailable: Counter = Counter()
    unav_examples: dict[str, list] = defaultdict(list)
    missing_parts: Counter = Counter()
    problems: list = []
    sizes = []
    for r in recs:
        for e in r.get("errors", []):
            rule_hits[e["rule"]] += 1
            if len(rule_examples[e["rule"]]) < 3:
                rule_examples[e["rule"]].append((r["id"], e["msg"]))
        for q in r.get("requirements", []):
            if q["unavailable"]:
                unavailable[q["d"].lower()] += 1
                unav_examples[q["d"].lower()].append(r["id"])
        for mp in (r.get("senior") or {}).get("missing_parts", []) or []:
            missing_parts[str(mp).strip()[:60]] += 1
        for pr in (r.get("senior") or {}).get("problems", []) or []:
            problems.append((r["id"], str(pr)[:200]))
        if r.get("board"):
            sizes.append((r["id"], r["board"]["w"], r["board"]["h"], r["board"]["shape"], r["board"]["rats"], r["parts"]))
    clean = sum(1 for r in recs if r.get("board") and not r["errors"] and r["board"]["rats"] == 0)
    delivered = sum(1 for r in recs if (r.get("senior") or {}).get("delivers_ask") is True)
    scores = [s for s in ((r.get("senior") or {}).get("score") for r in recs) if isinstance(s, (int, float))]
    return {
        "n": n, "clean_builds": clean, "delivered_by_senior_read": delivered,
        "avg_senior_score": round(sum(scores) / len(scores), 1) if scores else None,
        "agent_errors": [r["id"] for r in recs if r.get("agent_error")],
        "rule_hits": rule_hits.most_common(), "rule_examples": rule_examples,
        "unavailable": unavailable.most_common(), "unav_examples": unav_examples,
        "missing_parts": missing_parts.most_common(30), "problems": problems,
        "sizes": sizes,
        "timings": {"agent": [r.get("t_agent") for r in recs], "route": [r.get("t_route") for r in recs if r.get("t_route") is not None]},
    }


def write_report(recs: list[dict], agg: dict, out_dir: Path, stamp: str) -> Path:
    L: list[str] = []
    A = L.append
    A(f"# Synqit prompt sweep — {stamp}\n")
    A(f"{agg['n']} prompts through the live agent → place → route → 77 deterministic checks → datasheet-cited critic → senior-engineer LLM read. "
      "Deterministic findings are facts; the two LLM layers are labelled opinion. Every row has a repro: the prompt, and the saved project JSON in this folder.\n")
    A("## Headline numbers\n")
    A(f"- Clean builds (0 errors, 0 unrouted): **{agg['clean_builds']} / {agg['n']}**")
    A(f"- Senior-read says the design delivers the ask: **{agg['delivered_by_senior_read']} / {agg['n']}** (opinion)")
    if agg["avg_senior_score"] is not None:
        A(f"- Average 'would I let this go to fab' score: **{agg['avg_senior_score']} / 10** (opinion)")
    if agg["agent_errors"]:
        A(f"- Agent crashes: {', '.join(agg['agent_errors'])}")
    ta = [t for t in agg["timings"]["agent"] if t]
    tr = agg["timings"]["route"]
    if ta:
        A(f"- Agent time: median {sorted(ta)[len(ta)//2]:.0f} s, max {max(ta):.0f} s; route: median {sorted(tr)[len(tr)//2] if tr else 0:.0f} s, max {max(tr) if tr else 0:.0f} s")
    A("")
    A("## What the deterministic checks caught (fact)\n")
    A("| Rule | Builds | Example |")
    A("|---|---|---|")
    for rule, cnt in agg["rule_hits"]:
        ex = agg["rule_examples"][rule][0]
        A(f"| `{rule}` | {cnt} | {ex[0]}: {ex[1][:110]} |")
    A("")
    A("## Library gaps — requirements the copilot marked UNAVAILABLE (fact)\n")
    if agg["unavailable"]:
        for d, c in agg["unavailable"]:
            A(f"- **{d}** — {c}× ({', '.join(agg['unav_examples'][d][:4])})")
    else:
        A("- none recorded")
    A("")
    A("## Parts the senior read says are missing (opinion, needs a human check)\n")
    for mp, c in agg["missing_parts"][:25]:
        A(f"- {mp} — {c}×")
    A("")
    A("## Board size / shape (fact)\n")
    A("| Run | Parts | Board | Unrouted |")
    A("|---|---|---|---|")
    for pid, w, h, shape, rats, parts in sorted(agg["sizes"], key=lambda s: -(s[1] * s[2])):
        A(f"| {pid} | {parts} | {shape} {w:g}×{h:g} mm | {rats} |")
    A("")
    A("## Per-prompt ledger\n")
    for r in recs:
        b = r.get("board") or {}
        sen = r.get("senior") or {}
        A(f"### {r['id']} · {r['archetype']}")
        A(f"> {r['prompt']}\n")
        A(f"- parts {r['parts']} · board {b.get('shape','—')} {b.get('w','—')}×{b.get('h','—')} · unrouted {b.get('rats','—')} · "
          f"errors {len(r.get('errors', []))} · warnings {len(r.get('warnings', []))} · agent {r.get('t_agent','—')} s · route {r.get('t_route','—')} s")
        if r.get("agent_error"):
            A(f"- **agent error:** {r['agent_error']}")
        if r.get("requirements"):
            A("- requirements: " + "; ".join(f"{q['q']}× {q['d']}" + (" **[UNAVAILABLE]**" if q["unavailable"] else "") for q in r["requirements"]))
        if r.get("form_factor"):
            A(f"- form factor: {r['form_factor']}")
        for e in r.get("errors", [])[:8]:
            A(f"- ✗ `{e['rule']}` {e['msg']}")
        if len(r.get("errors", [])) > 8:
            A(f"- … +{len(r['errors']) - 8} more errors")
        for c in (r.get("critic") or [])[:4]:
            A(f"- critic ({c['sev']}): {c['msg']}")
        if sen:
            A(f"- senior read (opinion): score {sen.get('score','?')}/10 · delivers ask: {sen.get('delivers_ask')} · {sen.get('one_line','')}")
            for p in (sen.get("problems") or [])[:4]:
                A(f"  - {p}")
            if sen.get("size_verdict"):
                A(f"  - size: {sen['size_verdict']}")
        if r.get("copilot_last"):
            A(f"- copilot said: _{r['copilot_last'][:240].replace(chr(10), ' ')}_")
        A("")
    A("## Suggested actions (derived)\n")
    A("Ordered by how many builds each would fix. A rule that fires often is doing its job — the action is on the *agent/library* side unless the finding is a false positive.\n")
    for rule, cnt in agg["rule_hits"][:12]:
        A(f"- `{rule}` fired in {cnt} builds → check whether the system prompt / archetype needs to prevent it, or whether it is a false positive (then fix the rule).")
    for d, c in agg["unavailable"][:12]:
        A(f"- curate a part for **{d}** ({c} builds blocked)")
    for mp, c in agg["missing_parts"][:8]:
        A(f"- consider curating: {mp} (senior read, {c}×)")
    path = out_dir / f"sweep-{stamp}.md"
    path.write_text("\n".join(L), encoding="utf-8")
    (out_dir / f"sweep-{stamp}.json").write_text(json.dumps({"aggregate": agg, "runs": recs}, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--all", action="store_true", help="every matrix prompt (default: first --limit)")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--only", action="append", default=[], help="archetype key or prompt id substring")
    ap.add_argument("--invent", type=int, default=0, help="also ask the model for N new prompts")
    ap.add_argument("--no-critic", action="store_true")
    ap.add_argument("--out", default=None, help="run directory (default docs/sweeps/<stamp>)")
    ap.add_argument("--resume", action="store_true", help="skip prompts already recorded in --out")
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()

    prompts = prompt_matrix()
    if args.only:
        prompts = [p for p in prompts if any(o in p["id"] or o == p["archetype"] for o in args.only)]
    elif not args.all:
        prompts = prompts[: args.limit]
    if args.list:
        for p in prompts:
            print(f"{p['id']:16} {p['prompt']}")
        print(f"{len(prompts)} prompts")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = Path(args.out) if args.out else (_BACKEND_ROOT.parent / "docs" / "sweeps" / stamp)
    out_dir.mkdir(parents=True, exist_ok=True)

    recs: list[dict] = []
    if args.report_only or args.resume:
        for f in sorted(out_dir.glob("*.rec.json")):
            recs.append(json.loads(f.read_text(encoding="utf-8")))
    if not args.report_only:
        if not os.environ.get("OPENAI_API_KEY"):
            print("No live key — the sweep exercises the real agent.", file=sys.stderr)
            return 2
        done = {r["id"] for r in recs}
        if args.invent:
            prompts += invent_prompts(args.invent, [a[0] for a in ARCHETYPES])
        todo = [p for p in prompts if p["id"] not in done]
        print(f"Sweeping {len(todo)} prompts → {out_dir}  ({len(done)} already done)")
        for i, spec in enumerate(todo, 1):
            print(f"[{i}/{len(todo)}] {spec['id']}: {spec['prompt'][:90]}")
            rec = run_prompt(spec, out_dir, critic=not args.no_critic)
            recs.append(rec)
            b = rec.get("board") or {}
            print(f"      parts {rec['parts']} · {b.get('shape','—')} {b.get('w','—')}x{b.get('h','—')} · rats {b.get('rats','—')} · "
                  f"errors {len(rec['errors'])} · senior {((rec.get('senior') or {}).get('score', '?'))}/10 · {rec['t_total']} s")
    if not recs:
        print("nothing to report")
        return 1
    recs.sort(key=lambda r: r["id"])
    agg = aggregate(recs)
    path = write_report(recs, agg, out_dir, stamp)
    print(f"\nReport: {path}\n  clean {agg['clean_builds']}/{agg['n']} · senior avg {agg['avg_senior_score']} · top rules {agg['rule_hits'][:5]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
