"""Guard: library search understands multi-word queries (2026-08-20).

Review #38 (ESP32-CAM doorbell): the copilot reported that no suitable
speaker/chime part exists while Speaker_8ohm_SMD and Speaker_Buzzer_SMD sat in
the library. Root cause: `library.search` matched the WHOLE query as one
substring, so any multi-word phrase ("small speaker", "doorbell chime")
returned nothing, and the agent honestly recorded the part as unavailable.

Search is now token-based: each word matches independently, ranked by how many
words matched and where. These checks pin that behaviour down, offline.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import library  # noqa: E402

PASSED = 0
FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [PASS] {label}" + (f"  ({detail})" if detail else ""))
    else:
        FAILED.append(label)
        print(f"  [FAIL] {label}" + (f"  ({detail})" if detail else ""))


def mpns(query: str, category: str | None = None) -> list[str]:
    return [p.mpn for p in library.search(query, category=category)]


def main() -> int:
    print("\n-- single-word queries keep their old behaviour")
    speaker = mpns("speaker")
    check("'speaker' finds both curated speakers",
          "Speaker_8ohm_SMD" in speaker and "Speaker_Buzzer_SMD" in speaker)
    check("'buzzer' finds the active buzzer", "Buzzer_Active_9mm" in mpns("buzzer"))
    check("an MPN fragment ranks the MPN match first",
          mpns("ESP32")[0].startswith("ESP32"))

    print("\n-- multi-word phrases match per word (the review-38 defect)")
    small = mpns("small speaker")
    check("'small speaker' is no longer empty", bool(small))
    check("and a real speaker ranks first",
          bool(small) and small[0] in ("Speaker_8ohm_SMD", "Speaker_Buzzer_SMD"))
    chime = mpns("doorbell chime")
    check("'doorbell chime' finds the chime-capable parts",
          "Speaker_8ohm_SMD" in chime and "Speaker_Buzzer_SMD" in chime)
    exact = mpns("10k resistor 0603")
    check("more matched words outrank fewer (10k 0603 wins)",
          bool(exact) and exact[0] == "RC0603FR-0710KL", exact[0] if exact else "-")

    print("\n-- guard rails")
    check("nonsense still returns nothing", mpns("zzz_no_such_part_zzz") == [])
    check("category filter still applies with a query",
          all(p.category == "actuator" for p in library.search("buzzer", category="actuator")))

    print(f"\n{PASSED} checks passed, {len(FAILED)} failed.")
    if FAILED:
        for name in FAILED:
            print(f"  - {name}")
        return 1
    print("All search checks green.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
