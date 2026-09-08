"""Claim validator — Phase 2 of the Suyash-feedback roadmap (feedback #2).

The agent drives the schematic only through tool calls, so the IR is always
correct. But the agent's *prose* is free text, and a model can still write a
sentence like "BIN2 → GPIO23" that the IR doesn't actually back. The Review
panel is grounded by construction; this module extends the same guarantee to
chat prose: every connection an LLM *states* is checked against the IR via
``design_summary.net_contains``. Unbacked claims are downgraded to Missing-Item
verifier issues (``source="llm"``) and the assistant text is annotated so the
claim is never presented as established fact.

Public API:
  validate(project, text) -> (annotated_text, list[VerifierIssue])
"""

from __future__ import annotations

import re

from app.models import Component, Project, VerifierIssue
from app.services import design_summary, library

# A refdes like U1, R3, SW2, MH1, J10. 1–3 leading letters then digits.
_REFDES = r"[A-Z]{1,3}\d{1,3}"
# A pin token: a number ("23"), a name ("SDA", "IO19", "BIN2", "D+"), kept short.
_PIN = r"[A-Za-z0-9/+\-]{1,14}"
# Connection operators the model actually uses in prose.
_ARROW = r"(?:->|-->|–>|—>|→|↔|⇄)"

# "U3.2 -> U1.29" / "U3 SDA → U1 IO29"  (both sides refdes-qualified)
_PIN_TO_PIN = re.compile(
    rf"\b({_REFDES})[.\s]+({_PIN})\s*{_ARROW}\s*({_REFDES})[.\s]+({_PIN})\b"
)
# "<token> → GPIO23" / "ECHO → IO5"  (right side is a bare MCU GPIO label)
_TO_GPIO = re.compile(
    rf"\b({_REFDES})?[.\s]*({_PIN})\s*{_ARROW}\s*((?:GPIO|IO|D)\s*\d{{1,2}})\b",
    re.IGNORECASE,
)

# --- value claims -----------------------------------------------------------
# A resistance the model states in prose: "150 Ω", "4.7k", "330 ohm", "2k2".
_OHMS = r"(\d+(?:\.\d+)?)\s*([kKmM])?\s*(?:ohms?|Ω|R\b)|(\d+)[kKmM](\d+)"
_OHM_TOKEN = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)\s*([kKmM])?\s*(?:ohms?\b|Ω)"
    r"|(?<![\w.])(\d+)([kKmM])(\d+)(?![\w.])",
    re.IGNORECASE,
)
# "R1 (330 Ω)" / "R1 = 220 ohm" / "330 Ω resistor R1" — a refdes and a value
# close enough together to be about each other.
_VALUE_NEAR_REFDES = re.compile(
    rf"\b({_REFDES})\b[^.\n]{{0,24}}?"
    r"(?<![\w.])(\d+(?:\.\d+)?)\s*([kKmM])?\s*(?:ohms?\b|Ω)"
    r"|(?<![\w.])(\d+(?:\.\d+)?)\s*([kKmM])?\s*(?:ohms?\b|Ω)"
    rf"[^.\n]{{0,24}}?\b({_REFDES})\b",
    re.IGNORECASE,
)


def _ohms(number: str, suffix: str | None) -> float:
    mult = {"k": 1e3, "m": 1e6}.get((suffix or "").lower(), 1.0)
    return float(number) * mult


def _stated_ohms(text: str) -> list[float]:
    """Every resistance the prose states, in ohms."""
    out: list[float] = []
    for m in _OHM_TOKEN.finditer(text):
        if m.group(1):
            out.append(_ohms(m.group(1), m.group(2)))
        elif m.group(3):  # "2k2" form
            out.append(_ohms(f"{m.group(3)}.{m.group(5)}", m.group(4)))
    return out


def _component_ohms(comp: Component) -> float | None:
    """The resistance this component actually has, in ohms.

    The IR's `value` is usually unset — a resistor's resistance lives in the
    curated LIBRARY part, and `verifier._part_resistor_ohms` is the existing
    authority for reading it. Reuse it rather than adding a second parser that
    can disagree (a duplicated resistor-value reader silently disabled R55 once
    already).
    """
    if comp is None:
        return None
    from app.services import verifier

    from_part = verifier._part_resistor_ohms(library.get_part(comp.mpn))  # noqa: SLF001
    if from_part:
        return from_part
    if not comp.value:
        return None
    vals = _stated_ohms(comp.value)
    if vals:
        return vals[0]
    # A bare "330" or "4k7" with no unit is the normal way values are stored.
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kKmM])?\s*", comp.value)
    if m:
        return _ohms(m.group(1), m.group(2))
    m = re.fullmatch(r"\s*(\d+)([kKmM])(\d+)\s*", comp.value)
    if m:
        return _ohms(f"{m.group(1)}.{m.group(3)}", m.group(2))
    return None


def _fmt_ohms(v: float | None) -> str:
    if v is None:
        return "?"
    if v >= 1e6:
        return f"{v / 1e6:g} MΩ"
    if v >= 1e3:
        return f"{v / 1e3:g} kΩ"
    return f"{v:g} Ω"


def _mcu_refdes(project: Project) -> str | None:
    for c in project.ir.components:
        part = library.get_part(c.mpn)
        if part and part.category == "mcu":
            return c.refdes
    return None


def _comp(project: Project, refdes: str) -> Component | None:
    return next((c for c in project.ir.components if c.refdes == refdes), None)


def _refdes_exists(project: Project, refdes: str) -> bool:
    return any(c.refdes == refdes for c in project.ir.components)


def validate(project: Project, text: str) -> tuple[str, list[VerifierIssue]]:
    """Check every connection claim in ``text`` against the IR.

    Returns the (possibly annotated) text and a list of Missing-Item issues for
    every claim that is *not* backed by a shared net. Conservative by design:
    only high-confidence patterns (an explicit arrow between two pin references)
    are treated as claims, so ordinary prose is never mangled.
    """
    if not text or not text.strip():
        return text, []

    issues: list[VerifierIssue] = []
    seen: set[tuple[str, str, str, str]] = set()
    mcu = _mcu_refdes(project)

    def record(r1: str, p1: str, r2: str, p2: str, claim: str) -> None:
        key = (r1, p1, r2, p2)
        if key in seen:
            return
        seen.add(key)
        # Only flag a claim when both endpoints actually exist — otherwise it's
        # probably not a real connection statement (avoids false positives on
        # prose that happens to look like "A1 to B2").
        if not (_refdes_exists(project, r1) and _refdes_exists(project, r2)):
            return
        if design_summary.net_contains(project, [(r1, p1), (r2, p2)]):
            return  # grounded — nothing to do
        issues.append(
            VerifierIssue(
                id=f"LLM_claim:{r1}.{p1}-{r2}.{p2}",
                severity="warning",
                message=(
                    f"The explanation states {r1}.{p1} → {r2}.{p2}, but no net "
                    f"connects those pins. Either wire it or disregard the claim."
                ),
                refdes=r1,
                rule="LLM_unverified_claim",
                source="llm",
            )
        )

    def record_value(refdes: str, stated: float) -> None:
        """A resistance the prose attributes to a part must be the one in the
        design. "The tool's own explanation said 150 Ohm but it used the wrong
        part" was reported by three of five reviewers on 2026-08-20; R73 covers
        it only when the USER named the value, and a number the model invents
        and then contradicts was checked by nothing."""
        comp = _comp(project, refdes)
        actual = _component_ohms(comp) if comp else None
        if actual is None or actual <= 0:
            return
        if abs(stated - actual) / actual <= 0.02:
            return
        key = (refdes, "value", f"{stated:g}", f"{actual:g}")
        if key in seen:
            return
        seen.add(key)
        issues.append(
            VerifierIssue(
                id=f"LLM_value:{refdes}:{stated:g}",
                severity="warning",
                message=(
                    f"The explanation says {refdes} is {_fmt_ohms(stated)}, but the "
                    f"design actually holds {_fmt_ohms(actual)}. Trust the design, "
                    f"not the sentence — then fix whichever one is wrong."
                ),
                refdes=refdes,
                rule="LLM_value_mismatch",
                source="llm",
            )
        )

    for m in _VALUE_NEAR_REFDES.finditer(text):
        if m.group(1):                       # "R1 ... 330 Ω"
            ref, num, suf = m.group(1), m.group(2), m.group(3)
        else:                                 # "330 Ω ... R1"
            ref, num, suf = m.group(6), m.group(4), m.group(5)
        if ref and num:
            record_value(ref.upper(), _ohms(num, suf))

    # Internal consistency: if the design holds exactly ONE resistor and the
    # reply quotes two or more DIFFERENT resistances, the reply contradicts
    # itself whatever the design says — the "value inconsistent across four
    # places in the same response" report.
    resistors = [
        c for c in project.ir.components
        if (getattr(library.get_part(c.mpn), "category", "") or "").lower()
        in ("resistor", "passive")
        and _component_ohms(c) is not None
    ]
    stated = {round(v, 6) for v in _stated_ohms(text)}
    if len(resistors) == 1 and len(stated) > 1:
        actual = _component_ohms(resistors[0])
        issues.append(
            VerifierIssue(
                id="LLM_value:inconsistent",
                severity="warning",
                message=(
                    f"The explanation quotes {len(stated)} different resistances "
                    f"({', '.join(_fmt_ohms(v) for v in sorted(stated))}) for a design "
                    f"with one resistor, {resistors[0].refdes} at {_fmt_ohms(actual)}. "
                    f"At most one of those numbers can be right."
                ),
                refdes=resistors[0].refdes,
                rule="LLM_value_mismatch",
                source="llm",
            )
        )

    for m in _PIN_TO_PIN.finditer(text):
        r1, p1, r2, p2 = m.group(1), m.group(2), m.group(3), m.group(4)
        record(r1, p1, r2, p2, m.group(0))

    if mcu:
        for m in _TO_GPIO.finditer(text):
            left_ref, left_pin, gpio = m.group(1), m.group(2), m.group(3)
            # Right side is an MCU GPIO; left side is a peripheral pin. If the
            # left wasn't refdes-qualified, we can't resolve it reliably — skip.
            if not left_ref:
                continue
            gpio_pin = re.sub(r"\s+", "", gpio).upper()
            record(left_ref, left_pin, mcu, gpio_pin, m.group(0))

    annotated = text
    if issues:
        notes = "\n".join(f"  • {i.message}" for i in issues)
        annotated = (
            f"{text.rstrip()}\n\n"
            f"⚠️ Unverified in the design — these stated connections are not on "
            f"any net (see Missing items):\n{notes}"
        )
    return annotated, issues
