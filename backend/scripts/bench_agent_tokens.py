"""Benchmark: do the QUERY tools cut tokens and improve the output?

Runs the REAL schematic agent (agent.run, the server's OPENAI_* env — Azure
gpt-5.1 on the founder's box) on a fixed prompt set in two modes, chosen by
SYNQIT_AGENT_QUERIES (agent.QUERIES_ENV):

  0  baseline — today's verbs only, query tools hidden, rule block stripped
  1  queries  — query tools + the "ask, don't guess" / no-unverified-claims rule

Prompt set: the three loop-check prompts (rain alert, 7-segment counter, round
smartwatch) plus two edit turns on the rain-alert result. 5 turns x 2 modes =
10 runs, sequential, in-process (no server). Per run: prompt / completion /
cached tokens summed over the turn (agent.USAGE, from the client's `usage`),
LLM calls, tool calls, queries asked, tool-output characters, wall-clock, and
OUTPUT QUALITY from the IR: verifier errors, warnings, R43 shortfalls,
unconnected pins, and for the edit turns whether the asked change is present.

Writes bench/results/<timestamp>.json + .md (a markdown table with totals and
% change). Reports what it measured — do not tune the prompt to this.

Run from backend/:  python -m scripts.bench_agent_tokens [--reps 1] [--modes 0,1]
Costs real tokens (~a few dollars per repetition on gpt-5.1).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from dotenv import load_dotenv  # noqa: E402

# The key lives in backend/.env of the main checkout (git-ignored); a worktree
# has none. Never printed.
for _env in (_BACKEND_ROOT / ".env", _BACKEND_ROOT.parent.parent / "Cursor-for-hardware" / "backend" / ".env"):
    if _env.exists():
        load_dotenv(_env)
        break

# (slug, prompt, base slug whose project the turn continues — None = fresh project)
SCENARIOS: list[tuple[str, str, str | None]] = [
    ("rain-alert", "I want to build rain alert using ESP32 + a single RGB LED and moisture sensor.", None),
    ("rain-edit-button", "connect the button to a GPIO with a pull-up", "rain-alert"),
    ("rain-edit-led", "add a second LED on another pin", "rain-alert"),
    ("counter-7seg", "Make a push-button counter that shows the count on a 7-segment display.", None),
    ("smartwatch", "Design a round smartwatch board, 42 mm, with a GC9A01 display, heart-rate sensor, "
                   "haptic motor, LiPo charging and USB-C.", None),
]

COLUMNS = ["mode", "prompt", "prompt_tok", "cached_tok", "completion_tok", "llm_calls", "tool_calls",
           "queries", "state_guesses", "tool_errors", "tool_out_chars", "secs", "errors", "warnings", "r43", "unconnected", "change_ok"]


# --------------------------------------------------------------------------- #
# quality — deterministic reads of the IR after the turn
# --------------------------------------------------------------------------- #
def _category(mpn: str) -> str:
    from app.services import library
    part = library.get_part(mpn)
    return (part.category if part else "") or ""


def _mcu_refdeses(project) -> set[str]:
    from app.services import pin_safety
    return {c.refdes for c in project.ir.components
            if _category(c.mpn) == "mcu" or pin_safety.is_mcu_host_header(c.mpn)}


def _nets_of(project, refdes: str) -> list:
    return [n for n in project.ir.nets if any(r == refdes for r, _ in n.pins)]


def _mcu_pins_reached(project, refdes: str) -> set[tuple[str, str]]:
    """MCU pins the part reaches directly or through ONE two-pin passive."""
    mcus = _mcu_refdeses(project)
    out: set[tuple[str, str]] = set()
    comps = {c.refdes: c for c in project.ir.components}
    for net in _nets_of(project, refdes):
        for r, p in net.pins:
            if r in mcus:
                out.add((r, p))
            elif r != refdes and r in comps and _category(comps[r].mpn) == "passive" and len(comps[r].pins) == 2:
                for net2 in _nets_of(project, r):
                    if net2 is net:
                        continue
                    out.update((r2, p2) for r2, p2 in net2.pins if r2 in mcus)
    return out


def _button_on_gpio_with_pullup(project) -> dict:
    from app.services import tools
    switches = [c for c in project.ir.components if _category(c.mpn) == "switch"]
    on_gpio = pullup = False
    for sw in switches:
        for net in _nets_of(project, sw.refdes):
            if not any(r in _mcu_refdeses(project) for r, _ in net.pins):
                continue
            on_gpio = True
            for r, _ in net.pins:
                comp = next((c for c in project.ir.components if c.refdes == r), None)
                if comp is None or not comp.refdes.startswith("R") or _category(comp.mpn) != "passive":
                    continue
                other = [n for n in _nets_of(project, r) if n is not net]
                if any(tools._net_role(n.name) == "power" for n in other):
                    pullup = True
    return {"switch_present": bool(switches), "on_gpio": on_gpio, "pullup": pullup,
            "ok": bool(switches) and on_gpio and pullup}


def _second_led_on_another_pin(project, leds_before: set[str]) -> dict:
    leds_now = {c.refdes for c in project.ir.components if _category(c.mpn) == "led"}
    new = sorted(leds_now - leds_before)
    old_pins = set().union(*(_mcu_pins_reached(project, r) for r in leds_before)) if leds_before else set()
    new_pins = set().union(*(_mcu_pins_reached(project, r) for r in new)) if new else set()
    return {"new_leds": new, "new_led_mcu_pins": sorted(f"{r}.{p}" for r, p in new_pins),
            "ok": bool(new) and bool(new_pins - old_pins)}


def _quality(project) -> dict:
    from app.services import queries
    issues = project.issues
    return {
        "errors": sum(1 for i in issues if i.severity == "error"),
        "warnings": sum(1 for i in issues if i.severity == "warning"),
        "r43": sum(1 for i in issues if (i.rule or "").startswith("R43") and i.severity == "error"),
        "unconnected": queries.list_unconnected_pins(project, limit=1)["count"],
        "unverified_claims": sum(1 for i in issues if i.rule == "LLM_unverified_status"),
        "components": len(project.ir.components),
        "nets": len(project.ir.nets),
    }


# --------------------------------------------------------------------------- #
# one run
# --------------------------------------------------------------------------- #
def run_turn(mode: str, slug: str, prompt: str, project) -> dict:
    from app.services import agent
    os.environ[agent.QUERIES_ENV] = mode
    leds_before = {c.refdes for c in project.ir.components if _category(c.mpn) == "led"}
    t0 = time.time()
    final_text = ""
    for ev in agent.run(project, prompt):
        if ev.kind == "assistant_message":
            final_text = ev.payload.get("text", "")
    secs = round(time.time() - t0, 1)
    u = dict(agent.USAGE)
    row = {"mode": "queries" if mode == "1" else "baseline", "prompt": slug,
           "prompt_tok": u["prompt_tokens"], "cached_tok": u["cached_tokens"],
           "completion_tok": u["completion_tokens"], "llm_calls": u["llm_calls"],
           "tool_calls": u["tool_calls"], "queries": u["queries"], "queries_denied": u["queries_denied"],
           "state_guesses": u["state_guesses"], "tool_errors": u["tool_errors"],
           "tool_out_chars": u["tool_output_chars"], "secs": secs, **_quality(project)}
    if slug == "rain-edit-button":
        row["change"] = _button_on_gpio_with_pullup(project)
    elif slug == "rain-edit-led":
        row["change"] = _second_led_on_another_pin(project, leds_before)
    row["change_ok"] = row["change"]["ok"] if "change" in row else None
    row["final_text"] = final_text[:600]
    return row


def bench(modes: list[str], reps: int) -> list[dict]:
    from app.models import Project
    rows: list[dict] = []
    for rep in range(reps):
        for mode in modes:
            projects: dict[str, Project] = {}
            for slug, prompt, base in SCENARIOS:
                if base is None:
                    project = Project(id=f"bench-{slug}-{mode}-{rep}", name=slug, path=f"/bench/{slug}")
                    projects[slug] = project
                else:
                    project = projects[base]
                print(f"[{'queries' if mode == '1' else 'baseline'}] {slug} ...", flush=True)
                row = run_turn(mode, slug, prompt, project)
                row["rep"] = rep
                rows.append(row)
                print("   " + ", ".join(f"{k}={row[k]}" for k in COLUMNS[2:]), flush=True)
    return rows


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
_SUM = ["prompt_tok", "cached_tok", "completion_tok", "llm_calls", "tool_calls", "queries",
        "state_guesses", "tool_errors",
        "tool_out_chars", "secs", "errors", "warnings", "r43", "unconnected"]


def _totals(rows: list[dict]) -> dict:
    t = {k: sum(r[k] for r in rows) for k in _SUM}
    t["change_ok"] = f"{sum(1 for r in rows if r['change_ok'])}/{sum(1 for r in rows if r['change_ok'] is not None)}"
    return t


def markdown(rows: list[dict]) -> str:
    lines = ["| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(c, "")) if r.get(c) is not None else "—" for c in COLUMNS) + " |")
    by_mode = {}
    for r in rows:
        by_mode.setdefault(r["mode"], []).append(r)
    for mode, mrows in by_mode.items():
        t = _totals(mrows)
        lines.append("| **" + mode + " total** | | " + " | ".join(str(t[k]) for k in _SUM) + f" | {t['change_ok']} |")
    if {"baseline", "queries"} <= set(by_mode):
        b, q = _totals(by_mode["baseline"]), _totals(by_mode["queries"])

        def pct(k):
            return "n/a" if not b[k] else f"{(q[k] - b[k]) / b[k] * 100:+.0f}%"

        lines.append("| **queries vs baseline** | | " + " | ".join(pct(k) for k in _SUM) + " | |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reps", type=int, default=1, help="repetitions of the whole set (max 2)")
    ap.add_argument("--modes", default="0,1", help="comma list of modes to run, e.g. 0,1")
    args = ap.parse_args()
    reps = max(1, min(args.reps, 2))
    modes = [m.strip() for m in args.modes.split(",") if m.strip() in ("0", "1")]

    from app.services import agent
    if not agent.has_api_key():
        print("No OPENAI_API_KEY in the environment / .env — the benchmark needs the real agent.")
        return 2
    print(f"model {agent._model_name()}, modes {modes}, reps {reps}, {len(SCENARIOS)} turns per mode\n")

    rows = bench(modes, reps)
    out_dir = _BACKEND_ROOT.parent / "bench" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (out_dir / f"{stamp}.json").write_text(json.dumps(
        {"model": agent._model_name(), "modes": modes, "reps": reps, "scenarios": SCENARIOS, "rows": rows},
        indent=2, default=str), encoding="utf-8")
    md = markdown(rows)
    (out_dir / f"{stamp}.md").write_text(md + "\n", encoding="utf-8")
    print("\n" + md)
    print(f"\nwrote bench/results/{stamp}.json and .md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
