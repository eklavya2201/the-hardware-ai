"""Guard: the agent's static system prompt must stay far below the model's
input limit, whatever the library grows to.

2026-09-06: the prompt embedded an index of EVERY library part. At 1,751 parts
that was ~65k tokens per call; at 23,941 it was ~900k and every build failed
with `context_length_exceeded` (limit 272k). The index now lists only
`library.core_parts()` (hand-curated base + parts with a typicalApplication)
and points at `search_parts` for the rest. Offline, no LLM spend.

Run from backend/:  python -m scripts.regression_prompt_budget
"""
from __future__ import annotations

import sys

from app.services import agent, library

# ~4 chars/token. 120k chars ≈ 30k tokens: room for the design state, the
# tool schemas (~15k tokens) and a long chat, under a 272k-token ceiling.
MAX_CHARS = 120_000
MAX_CORE = 1_500


def main() -> int:
    failures: list[str] = []
    total = len(library.all_parts())
    core = library.core_parts()
    index = agent._library_index_for_prompt()
    prompt = agent.SYSTEM_PROMPT + "\n\n" + index

    checks = [
        (len(core) <= MAX_CORE, f"core index {len(core)} parts <= {MAX_CORE}"),
        (len(core) < total, f"core index ({len(core)}) is a subset of the library ({total})"),
        (len(prompt) <= MAX_CHARS, f"system prompt {len(prompt):,} chars <= {MAX_CHARS:,}"),
        ("search_parts" in index, "index tells the model to use search_parts for the rest"),
        (str(total) in index, "index states the full library size"),
        (all(p.mpn in index for p in core), "every core part is listed"),
    ]
    for ok, label in checks:
        print(("PASS " if ok else "FAIL ") + label)
        if not ok:
            failures.append(label)
    print(f"\n{len(checks) - len(failures)}/{len(checks)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
