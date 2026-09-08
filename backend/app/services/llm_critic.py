"""LLM-based design critic — PRD §7.4 second half.

Sits on top of the rule-based verifier in `verifier.py`. Where the rules catch
black-and-white violations (missing decoupling cap, floating EN, etc.), the
LLM critic looks for higher-order issues that need design-engineer judgment:

  - Topology smells (wrong protection arrangement, missing reverse-polarity diode)
  - Component sizing (wrong package for power dissipation, voltage margin too tight)
  - EMI / signal-integrity smells
  - Best-practice gaps the rules don't cover

Returns the same `VerifierIssue` shape as rule-based DRC, with `rule` prefix
`LLM_<category>` so the frontend can render them distinctly.

Costs about 1 OpenAI call (~5-15k input tokens depending on board size, ~500
output tokens). Skipped silently when OPENAI_API_KEY is unset.
"""

from __future__ import annotations

import json
import os
import uuid
from textwrap import dedent

from app.models import Project, VerifierIssue


CRITIC_SYSTEM_PROMPT = """\
You are a senior hardware engineer reviewing a PCB schematic for issues that
rule-based DRC cannot catch. The user has already run a rule-based DRC; your
job is to find higher-order problems that need engineering judgment.

Focus on:
- COMPLETENESS — does the design actually contain what the user asked for?
  Compare the "User request" section against the component list: a requested
  part count, subsystem, or interface that is absent or clearly
  under-populated is a finding (basis = the user's own words vs the netlist).
- TOPOLOGY — wrong arrangement, missing reverse-polarity / inrush protection,
  bad current paths, ground loops, decoupling that's electrically present but
  too far from the IC pin it's supposed to bypass.
- SIZING — wrong package for the dissipation (AMS1117 in SOT-23 doing 500mA
  is too hot; bulk caps with insufficient voltage margin; trace width on power
  rails for the expected current).
- POWER PATH — battery charging without protection, USB current draw exceeding
  500mA without negotiation, missing soft-start or inrush limiting.
- EMI / SIGNAL — high-speed signals routed near noisy switchers, missing
  series resistors on USB/Ethernet, no termination on long traces.
- BEST PRACTICES — missing bulk cap near power-switching nodes, programming
  header ordering that's hard to reach, no test points on critical nets.

Output strictly as a single JSON object of this shape:
{
  "issues": [
    {
      "severity": "error" | "warning" | "info",
      "message": "<one short paragraph, plain English, ≤180 chars>",
      "refdes": "U1" (optional),
      "net": "VBUS" (optional),
      "category": "completeness" | "topology" | "sizing" | "power" | "emi" | "signal" | "best_practice",
      "basis": "<the specific fact from the context this finding relies on, e.g. 'AP2112K-3.3 currentMax 600 mA vs three motor drivers on VCC_3V3'>",
      "mpn": "AP2112K-3.3TRG1" (the part whose datasheet/limits back the finding, optional)
    }
  ]
}

Rules:
- Don't restate findings already in the rule-based DRC output.
- Cap your output at 6 issues. Pick the most impactful.
- If the design looks fine, return {"issues": []}.
- "error" = will not work or will damage the board. "warning" = likely to cause
  problems in production. "info" = best-practice suggestion.
- Be specific. Cite the refdes when relevant. No vague hand-waving.
- EVERY issue must carry a "basis" naming the concrete fact(s) from the context
  it rests on (a part's rated limit, a net's fanout, a missing companion). A
  finding you cannot ground in the provided facts must not be emitted — it will
  be discarded.
"""

SKEPTIC_SYSTEM_PROMPT = """\
You are a second, independent hardware reviewer. You receive design-review
findings produced by another reviewer, plus the same design context they saw.
Your ONLY job is to challenge each finding against the context:

- If the stated basis is wrong, contradicted by the context, or does not
  support the conclusion → verdict "drop".
- If the finding is real but overstated (e.g. "error" for something that only
  matters in production corner cases) → verdict "downgrade".
- Otherwise → verdict "keep".

You may NEVER add new findings and NEVER raise a severity. Output strictly:
{"verdicts": [{"id": "<finding id>", "verdict": "keep"|"downgrade"|"drop", "reason": "<short>"}]}
"""


def critique(project: Project, llm=None) -> list[VerifierIssue]:
    """Run the LLM critic. Returns a list of additional issues. Returns [] if
    no API key is configured (so callers can mix this in unconditionally)."""
    if not ((llm is not None and llm.api_key) or os.environ.get("OPENAI_API_KEY")):
        return []

    # Lazily import the agent module so this stays optional.
    from app.services.agent import _make_openai_client

    try:
        client = _make_openai_client(llm)
    except Exception:
        return []

    model = llm.model if llm is not None else os.environ.get("OPENAI_MODEL", "gpt-4o")
    user_msg = _build_context(project)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": CRITIC_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=2000,
        )
    except Exception as exc:  # noqa: BLE001
        return [
            VerifierIssue(
                id=f"LLM_NETWORK:{uuid.uuid4().hex[:6]}",
                severity="info",
                message=f"AI critic unavailable: {exc!s}",
                rule="LLM_meta",
                source="llm",
            )
        ]

    content = response.choices[0].message.content or "{}"
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return [
            VerifierIssue(
                id=f"LLM_PARSE:{uuid.uuid4().hex[:6]}",
                severity="info",
                message="AI critic returned malformed JSON; skipping this run.",
                rule="LLM_meta",
                source="llm",
            )
        ]

    issues = _parse_issues(data)
    return _skeptic_pass(client, model, user_msg, issues)


_SEVERITY_DOWN = {"error": "warning", "warning": "info", "info": "info"}


def _skeptic_pass(client, model: str, context: str, issues: list[VerifierIssue]) -> list[VerifierIssue]:
    """Second, independent review of the critic's findings. The skeptic may
    only KEEP, DOWNGRADE, or DROP a finding — enforced here in code, so a
    misbehaving model can never add findings or raise a severity. Any failure
    (network, parse) keeps the findings unchanged."""
    if not issues:
        return issues

    findings_json = json.dumps(
        [
            {
                "id": i.id,
                "severity": i.severity,
                "message": i.message,
                "basis": i.citation,
                "refdes": i.refdes,
                "net": i.net,
            }
            for i in issues
        ],
        ensure_ascii=False,
    )
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SKEPTIC_SYSTEM_PROMPT},
                {"role": "user", "content": f"{context}\n\nFindings to challenge:\n{findings_json}"},
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=1200,
        )
        data = json.loads(response.choices[0].message.content or "{}")
    except Exception:  # noqa: BLE001 — skeptic is best-effort, never blocks findings
        return issues

    return _apply_verdicts(issues, data)


def _apply_verdicts(issues: list[VerifierIssue], data: dict) -> list[VerifierIssue]:
    """Apply skeptic verdicts with downgrade-only enforcement. Unknown ids and
    malformed verdicts are ignored; a finding with no verdict is kept."""
    raw = data.get("verdicts")
    if not isinstance(raw, list):
        return issues
    verdicts: dict[str, str] = {}
    for v in raw:
        if isinstance(v, dict) and isinstance(v.get("id"), str):
            verdicts[v["id"]] = str(v.get("verdict") or "keep")

    kept: list[VerifierIssue] = []
    for issue in issues:
        verdict = verdicts.get(issue.id, "keep")
        if verdict == "drop":
            continue
        if verdict == "downgrade":
            issue.severity = _SEVERITY_DOWN[issue.severity]  # type: ignore[assignment]
        kept.append(issue)
    return kept


def _part_facts(mpn: str) -> str:
    """Datasheet-derived facts for one part, rendered for the critic context.
    Empty string when the part isn't in the library."""
    from app.services import library

    part = library.get_part(mpn)
    if part is None:
        return ""
    facts: list[str] = []
    if part.voltageMin is not None or part.voltageMax is not None:
        facts.append(f"V {part.voltageMin if part.voltageMin is not None else '?'}–{part.voltageMax if part.voltageMax is not None else '?'}V")
    if part.currentMaxMa is not None:
        facts.append(f"Imax {part.currentMaxMa:g}mA")
    if part.powerMaxW is not None:
        facts.append(f"Pmax {part.powerMaxW:g}W")
    if part.logicLevelV is not None:
        facts.append(f"logic {part.logicLevelV:g}V")
    if part.recommendedSupplyV is not None:
        facts.append(f"supply {part.recommendedSupplyV:g}V")
    if part.defaultI2cAddress:
        facts.append(f"I2C {part.defaultI2cAddress}")
    ds = _datasheet_ref(mpn)
    if ds:
        facts.append(ds)
    return f" [{'; '.join(facts)}]" if facts else ""


def _datasheet_ref(mpn: str) -> str:
    """Short human-readable datasheet reference, e.g. 'datasheet pp. 3, 14'."""
    from app.services import library

    part = library.get_part(mpn)
    if part is None:
        return ""
    prov = part.provenance
    if prov and prov.sourcePages:
        pages = ", ".join(str(p) for p in prov.sourcePages[:4])
        return f"datasheet pp. {pages}"
    if part.datasheetUrl or (prov and prov.datasheetUrl):
        return "datasheet on file"
    return ""


def _citation_url(mpn: str) -> str | None:
    """Datasheet URL for a part, anchored to its first curated source page."""
    from app.services import library

    part = library.get_part(mpn)
    if part is None:
        return None
    prov = part.provenance
    url = part.datasheetUrl or (prov.datasheetUrl if prov else None)
    if not url:
        return None
    if prov and prov.sourcePages and url.lower().endswith(".pdf"):
        return f"{url}#page={prov.sourcePages[0]}"
    return url


def _build_context(project: Project) -> str:
    components_summary: list[str] = []
    for c in project.ir.components:
        bits = [c.refdes, c.mpn]
        if c.value:
            bits.append(c.value)
        if c.footprint:
            bits.append(f"fp={c.footprint}")
        components_summary.append("- " + " ".join(bits) + _part_facts(c.mpn))

    nets_summary: list[str] = []
    for n in project.ir.nets:
        head = n.pins[:8]
        suffix = f" +{len(n.pins) - 8} more" if len(n.pins) > 8 else ""
        joined = ", ".join(f"{r}.{p}" for r, p in head) + suffix
        nets_summary.append(f"- {n.name} ({len(n.pins)} pins): {joined}")

    rule_issues: list[str] = []
    for i in project.issues:
        if i.rule and i.rule.startswith("LLM_"):
            continue  # don't show prior LLM output to itself
        ctx_bits = []
        if i.refdes:
            ctx_bits.append(f"refdes={i.refdes}")
        if i.net:
            ctx_bits.append(f"net={i.net}")
        ctx = f" [{', '.join(ctx_bits)}]" if ctx_bits else ""
        rule_issues.append(f"- [{i.severity}] {i.rule or 'rule'}: {i.message}{ctx}")

    # The user's own words + the recorded countable requirements — so the
    # critic can judge completeness against the actual request, not just the
    # netlist's internal consistency (LED-matrix review, 2026-08-15).
    user_asks = [m.text.strip() for m in project.chat if m.role == "user" and m.text.strip()]
    request_lines = [f'- "{t[:280]}"' for t in user_asks[-3:]]
    for req in getattr(project.ir, "requirements", None) or []:
        target = req.mpn or req.category or "?"
        request_lines.append(
            f"- requirement: {req.quantity}x {req.description} ({target})"
        )

    bom_total = sum(line.qty * line.unitCost for line in project.bom)
    return dedent(
        f"""\
        Project: {project.name}
        BOM total: ${bom_total:.2f} ({len(project.bom)} lines)

        User request (judge completeness against this):
        {chr(10).join(request_lines) if request_lines else "(no chat history)"}

        Components ({len(project.ir.components)}):
        {chr(10).join(components_summary) if components_summary else "(empty)"}

        Nets ({len(project.ir.nets)}):
        {chr(10).join(nets_summary) if nets_summary else "(empty)"}

        Rule-based DRC findings ({len(rule_issues)}):
        {chr(10).join(rule_issues) if rule_issues else "(no findings)"}

        Component facts in [brackets] are datasheet-derived library data
        (rated limits, logic levels, curated datasheet page references).
        Ground every finding's "basis" in those facts or the netlist above.

        Review the above for higher-order design issues per your system prompt.
        Do NOT restate the rule-based DRC findings.
        """
    )


def _parse_issues(data: dict) -> list[VerifierIssue]:
    issues: list[VerifierIssue] = []
    raw_list = data.get("issues") or []
    if not isinstance(raw_list, list):
        return []

    for item in raw_list[:6]:  # bound to 6 even if model ignores the prompt
        if not isinstance(item, dict):
            continue
        severity = item.get("severity", "info")
        if severity not in {"error", "warning", "info"}:
            severity = "info"
        category = item.get("category", "general") or "general"
        category = "".join(c if c.isalnum() or c == "_" else "_" for c in category.lower())[:24]

        message = (item.get("message") or "").strip()
        if not message:
            continue
        if len(message) > 280:
            message = message[:277] + "…"

        # Grounding requirement: a finding that names no basis is discarded —
        # the market-trust discipline is cite-or-drop, never vague hand-waving.
        basis = (item.get("basis") or "").strip()
        if not basis:
            continue
        if len(basis) > 200:
            basis = basis[:197] + "…"
        mpn = (item.get("mpn") or "").strip() or None
        citation = basis
        if mpn:
            ds = _datasheet_ref(mpn)
            if ds:
                citation = f"{basis} ({mpn} {ds})"

        issues.append(
            VerifierIssue(
                id=f"LLM_{category.upper()}:{uuid.uuid4().hex[:6]}",
                severity=severity,  # type: ignore[arg-type]
                message=message,
                refdes=item.get("refdes") or None,
                net=item.get("net") or None,
                rule=f"LLM_{category}",
                source="llm",
                citation=citation,
                citationUrl=_citation_url(mpn) if mpn else None,
            )
        )

    return issues


def merge_into_project(project: Project, llm=None) -> None:
    """Run the critic and replace any existing LLM_* issues on the project."""
    new_issues = critique(project, llm)
    project.issues = [
        i for i in project.issues if not (i.rule and i.rule.startswith("LLM_"))
    ]
    project.issues.extend(new_issues)
