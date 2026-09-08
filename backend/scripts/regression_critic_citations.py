"""Regression guard for the datasheet-cited critic (no LLM spend).

Asserts the three trust disciplines the critic now enforces in code:
1. Grounding — a finding with no "basis" is dropped by _parse_issues.
2. Citations — a grounded finding with a library MPN gets a citation string
   and (when the part has a datasheet URL) a citationUrl, page-anchored when
   the curated provenance carries sourcePages.
3. Downgrade-only skeptic — _apply_verdicts can drop or downgrade findings
   but ignores unknown ids, malformed verdicts, and can never raise severity
   or add findings.

Run: python -m scripts.regression_critic_citations
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import VerifierIssue
from app.services import library
from app.services.llm_critic import _apply_verdicts, _citation_url, _parse_issues


def check(name: str, ok: bool) -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    return ok


def main() -> int:
    results: list[bool] = []

    # --- 1. Grounding: no basis -> dropped; basis -> kept with citation -----
    parsed = _parse_issues(
        {
            "issues": [
                {"severity": "warning", "message": "Vague worry with no basis.", "category": "topology"},
                {
                    "severity": "error",
                    "message": "LDO undersized for the motor rail.",
                    "category": "sizing",
                    "basis": "AP2112K currentMax 600 mA vs motor draw",
                    "refdes": "U2",
                },
            ]
        }
    )
    results.append(check("ungrounded finding dropped", len(parsed) == 1))
    results.append(check("grounded finding kept", parsed and parsed[0].refdes == "U2"))
    results.append(check("citation populated from basis", bool(parsed and parsed[0].citation)))

    # --- 2. Citations from the real library --------------------------------
    with_pages = next(
        (p for p in library.all_parts() if p.provenance and p.provenance.sourcePages
         and (p.datasheetUrl or p.provenance.datasheetUrl or "").lower().endswith(".pdf")),
        None,
    )
    if with_pages is not None:
        url = _citation_url(with_pages.mpn)
        results.append(check(
            f"citationUrl page-anchored for curated part ({with_pages.mpn})",
            bool(url and "#page=" in url),
        ))
    else:
        print("SKIP  no library part with sourcePages + pdf url")
    results.append(check("unknown MPN -> no citationUrl", _citation_url("NOT-A-PART-123") is None))

    # --- 3. Skeptic verdicts: downgrade-only, enforced in code -------------
    def mk(id_: str, sev: str) -> VerifierIssue:
        return VerifierIssue(id=id_, severity=sev, message="m", rule="LLM_x", source="llm")  # type: ignore[arg-type]

    issues = [mk("a", "error"), mk("b", "warning"), mk("c", "info")]
    out = _apply_verdicts(
        issues,
        {
            "verdicts": [
                {"id": "a", "verdict": "downgrade"},
                {"id": "b", "verdict": "drop"},
                {"id": "c", "verdict": "upgrade"},        # invalid -> treated as keep
                {"id": "ghost", "verdict": "drop"},        # unknown id -> ignored
            ]
        },
    )
    by_id = {i.id: i for i in out}
    results.append(check("downgrade error->warning", by_id.get("a") is not None and by_id["a"].severity == "warning"))
    results.append(check("drop removes finding", "b" not in by_id))
    results.append(check("invalid verdict keeps finding unchanged", by_id.get("c") is not None and by_id["c"].severity == "info"))
    results.append(check("skeptic cannot add findings", len(out) == 2))
    results.append(check("malformed payload keeps all", len(_apply_verdicts(issues, {"verdicts": "junk"})) == 3))
    results.append(check("missing verdicts keeps all", len(_apply_verdicts(issues, {})) == 3))

    if all(results):
        print("\nAll critic-citation regressions pass.")
        return 0
    print("\nFAILURES present.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
