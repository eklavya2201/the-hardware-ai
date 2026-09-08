"""Run the beta-review acceptance suite against a DEPLOYED Synqit.

Same prompts and same checks as `eval_reviews.py`, but driven over HTTP against
a real server (default https://api.synqit.in) instead of in-process — so it
answers "is the thing our users hit actually fixed?", not "does my working copy
pass?". It signs up a throwaway account, builds each design through the live
agent, runs auto-place, then evaluates the checks locally against the IR the
server returns.

    python -m scripts.eval_reviews_prod
    python -m scripts.eval_reviews_prod --base https://api.synqit.in/api
    python -m scripts.eval_reviews_prod --only buzzer --json prod.json

Leaves its projects behind under the throwaway account (printed at the end) so
you can open them in the UI; delete the account from the admin portal after.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

import requests  # noqa: E402

from app.models import Project  # noqa: E402
from scripts.eval_reviews import SUITE, _universal_checks  # noqa: E402


def build_on_server(s: requests.Session, base: str, spec: dict) -> tuple[Project | None, dict]:
    """Create a project, run the prompt through the live agent, auto-place."""
    meta: dict = {"tools": {}, "seconds": 0.0, "error": None}
    started = time.time()
    try:
        pid = s.post(f"{base}/projects",
                     json={"name": f"prodeval-{spec['name']}", "kind": "hardware"},
                     timeout=60).json()["id"]
        meta["project_id"] = pid
        with s.post(f"{base}/projects/{pid}/chat", json={"text": spec["prompt"]},
                    stream=True, timeout=1200) as resp:
            resp.raise_for_status()
            event = None
            for raw in resp.iter_lines(decode_unicode=True):
                if raw.startswith("event:"):
                    event = raw.split(":", 1)[1].strip()
                elif raw.startswith("data:") and event == "tool_use":
                    name = json.loads(raw[5:])["name"]
                    meta["tools"][name] = meta["tools"].get(name, 0) + 1
        s.post(f"{base}/projects/{pid}/pcb/auto-place", timeout=600)
        project = Project.model_validate(s.get(f"{base}/projects/{pid}", timeout=60).json())
        meta["ready"] = s.get(f"{base}/projects/{pid}/export/readiness", timeout=60).json()["ready"]
    except Exception as exc:  # noqa: BLE001
        meta["error"] = f"{exc!s}"[:160]
        meta["seconds"] = round(time.time() - started, 1)
        return None, meta
    meta["seconds"] = round(time.time() - started, 1)
    return project, meta


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="https://api.synqit.in/api")
    ap.add_argument("--only", default=None)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    suite = [x for x in SUITE if not args.only or args.only.lower() in x["name"].lower()]
    email = f"prodeval_{uuid.uuid4().hex[:8]}@example.com"
    s = requests.Session()
    r = s.post(f"{args.base}/auth/signup",
               json={"name": "Prod Eval", "email": email, "password": "ProdEval123!"}, timeout=60)
    if r.status_code >= 400:
        print(f"signup failed: {r.status_code} {r.text[:120]}")
        return 2
    s.post(f"{args.base}/auth/accept-policies", timeout=60)

    health = s.get(f"{args.base}/health", timeout=30).json()
    print(f"target : {args.base}")
    print(f"health : {json.dumps(health)}")
    print(f"account: {email}\n")

    results = []
    grand_passed = grand_total = 0
    for spec in suite:
        project, meta = build_on_server(s, args.base, spec)
        if project is None:
            print(f"  {spec['name']:<14} ERROR {meta['error']}")
            results.append({"name": spec["name"], **meta})
            continue
        board = project.board
        checks: dict[str, bool] = {}
        for cid, _desc, fn in spec["checks"] + _universal_checks(spec):
            try:
                checks[cid] = bool(fn(project, board))
            except Exception:  # noqa: BLE001
                checks[cid] = False
        passed, total = sum(checks.values()), len(checks)
        grand_passed += passed
        grand_total += total
        dims = f"{board.width:.1f}x{board.height:.1f}mm" if board else "no board"
        print(f"  {spec['name']:<14} {passed}/{total:<6} {len(project.ir.components):>3} parts  "
              f"{dims:<15} ready={str(meta.get('ready')):<5} {meta['seconds']:>5.0f}s  {meta['project_id']}")
        failed = [c for c, ok in checks.items() if not ok]
        if failed:
            print(f"       failed: {', '.join(failed)}")
        results.append({"name": spec["name"], "checks": checks, "passed": passed,
                        "total": total, **meta})

    rate = grand_passed / grand_total if grand_total else 0.0
    print(f"\nPROD RESULT: {grand_passed}/{grand_total} checks passed = {rate:.0%}")
    print(f"projects left under {email} (delete the account from /admin when done)")
    if args.json:
        Path(args.json).write_text(json.dumps({"rate": rate, "results": results,
                                               "account": email, "health": health}, indent=2),
                                   encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
