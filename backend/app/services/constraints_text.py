"""Explicit design constraints extracted from the user's own words.

The single most repeated complaint across the 2026-08-20 review batch is that
**the tool says one thing and builds another**:

  * "It should have used a 150 Ω resistor, but it picked a 100 Ω one instead.
     The tool's own explanation text correctly said 150 Ω."
  * "asked for a through-hole resistor but it gave a surface-mount version"
  * "asked for a 5mm LED, but it used a 3mm one"
  * "in the detailed prompt the model did not properly follow the component
     list provided by me"

`record_requirements` already captures COUNTABLE asks (R43), and
`agent.declared_board_size` captures a stated board size (R57). This module
captures the third kind: an explicit PART constraint — a component value, a
package/mount style, a size, or a named MPN — parsed deterministically from the
prompt so the model cannot skip it, then checked against the built IR by
`verifier.R73_explicit_part_constraint`.

Nothing here guesses. A phrase only becomes a constraint when the user wrote a
concrete number or a concrete word; anything ambiguous is dropped, because a
false constraint is worse than a missing one — it would block a correct design.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = ["PartConstraint", "extract_constraints", "describe"]


# --------------------------------------------------------------------------- #
# Value parsing
# --------------------------------------------------------------------------- #
_OHM = r"(?:ohms?|Ω|Ω|R\b)"
_MULT = {"": 1.0, "k": 1e3, "K": 1e3, "m": 1e-3, "M": 1e6, "µ": 1e-6, "u": 1e-6,
         "n": 1e-9, "p": 1e-12, "µ": 1e-6}

# "150 Ω", "150R", "4.7k", "10 kΩ", "2k2"
_RES_RE = re.compile(
    rf"(?<![\w.])(\d+(?:\.\d+)?)\s*([kKmM])?\s*(?:{_OHM})|(?<![\w.])(\d+)([kKrR])(\d+)\b"
)
# "100 nF", "10uF", "22 pF", "4.7 µF"
_CAP_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*([pnuµμm]?)\s*F\b", re.IGNORECASE)
# "10 µH", "33uH"
_IND_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*([pnuµμm]?)\s*H\b")

_RES_CONTEXT = re.compile(r"\bresistors?\b|\bpull-?ups?\b|\bpull-?downs?\b|\bcurrent[- ]limit", re.IGNORECASE)
_CAP_CONTEXT = re.compile(r"\bcaps?\b|\bcapacitors?\b|\bdecoupl|\bbypass\b|\bbulk\b", re.IGNORECASE)
_IND_CONTEXT = re.compile(r"\binductors?\b|\bchoke\b", re.IGNORECASE)

# --------------------------------------------------------------------------- #
# Package / mount style
# --------------------------------------------------------------------------- #
_THT_RE = re.compile(r"\b(through[- ]?hole|thru[- ]?hole|THT|DIP|radial|axial)\b", re.IGNORECASE)
_SMD_RE = re.compile(r"\b(surface[- ]?mount|SMD|SMT)\b", re.IGNORECASE)
_CHIP_SIZE_RE = re.compile(r"\b(0201|0402|0603|0805|1206|1210|2010|2512)\b")
# "5mm LED", "3 mm led"
_LED_SIZE_RE = re.compile(r"\b(\d(?:\.\d)?)\s*mm\b[^.,;]{0,18}\bled\b", re.IGNORECASE)
_LED_WORD_RE = re.compile(r"\bled\b", re.IGNORECASE)

# A bare multiplier ("4.7k", "10K") — only meaningful in a resistor sentence.
_BARE_K_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*([kKmM])(?![\w])")


def _nearest_target(chunk: str, at: int) -> str:
    """Whether `resistor` or `capacitor` is closer to position `at`."""
    best, best_d = "resistor", 10**9
    for word, tgt in (("resistor", "resistor"), ("cap", "capacitor"), ("capacitor", "capacitor")):
        for m in re.finditer(word, chunk, re.IGNORECASE):
            d = abs(m.start() - at)
            if d < best_d:
                best, best_d = tgt, d
    return best


_MPN_RE = re.compile(r"\b([A-Z]{2,}[A-Z0-9]*(?:[-/][A-Z0-9]+){1,3})\b")


@dataclass
class PartConstraint:
    """One explicit thing the user asked for, in machine-checkable form."""
    kind: str                    # "value" | "mount" | "size" | "mpn"
    target: str                  # what it applies to: "resistor" / "capacitor" / "led" / "any"
    value: str                   # canonical form, e.g. "150", "tht", "5mm", "CH340N"
    text: str                    # the user's own words, for the message
    tolerance: float = 0.0       # relative tolerance for numeric values
    meta: dict = field(default_factory=dict)


def _res_ohms(m: re.Match) -> float | None:
    if m.group(1) is not None:
        base = float(m.group(1))
        mult = m.group(2) or ""
        return base * (1e3 if mult in ("k", "K") else 1e6 if mult == "M" else 1.0)
    # "2k2" / "4R7" style
    whole, unit, frac = m.group(3), m.group(4), m.group(5)
    try:
        v = float(f"{whole}.{frac}")
    except ValueError:
        return None
    return v * (1e3 if unit in ("k", "K") else 1.0)


def _farads(m: re.Match) -> float | None:
    base = float(m.group(1))
    return base * _MULT.get(m.group(2) or "", 1.0)


def extract_constraints(text: str) -> list[PartConstraint]:
    """Explicit part constraints stated in `text`. Empty list when the prompt
    states nothing concrete — silence is never a constraint."""
    out: list[PartConstraint] = []
    if not text:
        return out
    # Sentence-ish chunks so a value binds to the thing next to it. Split on
    # real breaks only ("4.7k" must not split at its decimal point).
    for chunk in re.split(r"(?<!\d)\.(?!\d)|[;\n]|,\s+(?=[A-Z])", text):
        if not chunk or not chunk.strip():
            continue
        low = chunk.lower()

        if _RES_CONTEXT.search(chunk) or ("resistor" in low):
            m = _RES_RE.search(chunk)
            if m is None:
                # "4.7k pull-ups" — engineers drop the ohm sign constantly, but
                # only trust a bare multiplier inside a resistor sentence.
                m = _BARE_K_RE.search(chunk)
            if m:
                ohms = _res_ohms(m)
                if ohms and 0.01 <= ohms <= 1e7:
                    out.append(PartConstraint(
                        kind="value", target="resistor", value=f"{ohms:g}",
                        text=m.group(0).strip(), tolerance=0.05,
                        meta={"ohms": ohms},
                    ))
        if _CAP_CONTEXT.search(chunk):
            m = _CAP_RE.search(chunk)
            if m:
                farads = _farads(m)
                if farads and 1e-13 <= farads <= 1.0:
                    out.append(PartConstraint(
                        kind="value", target="capacitor", value=f"{farads:g}",
                        text=m.group(0).strip(), tolerance=0.2,
                        meta={"farads": farads},
                    ))
        if _IND_CONTEXT.search(chunk):
            m = _IND_RE.search(chunk)
            if m:
                henries = float(m.group(1)) * _MULT.get(m.group(2) or "", 1.0)
                if henries and 1e-9 <= henries <= 1.0:
                    out.append(PartConstraint(
                        kind="value", target="inductor", value=f"{henries:g}",
                        text=m.group(0).strip(), tolerance=0.25,
                        meta={"henries": henries},
                    ))

        # Mount style — bind it to the nearest named part class in the chunk.
        tht, smd = _THT_RE.search(chunk), _SMD_RE.search(chunk)
        if tht or smd:
            style = "tht" if tht else "smd"
            word = (tht or smd).group(0)
            targets = [w for w in ("resistor", "capacitor", "led", "header", "connector")
                       if w in low]
            for tgt in (targets or ["any"]):
                out.append(PartConstraint(
                    kind="mount", target=tgt, value=style, text=word,
                ))

        m = _LED_SIZE_RE.search(chunk)
        if m:
            out.append(PartConstraint(
                kind="size", target="led", value=f"{float(m.group(1)):g}mm",
                text=m.group(0).strip(),
            ))
        m = _CHIP_SIZE_RE.search(chunk)
        if m and (_RES_CONTEXT.search(chunk) or _CAP_CONTEXT.search(chunk)):
            # Bind the size to whichever part word sits NEARER it: "4.7k
            # resistors and 0603 capacitors" sizes the capacitors.
            tgt = _nearest_target(chunk, m.start())
            out.append(PartConstraint(
                kind="size", target=tgt, value=m.group(1), text=m.group(0),
            ))
    return _dedupe(out)


def _dedupe(items: list[PartConstraint]) -> list[PartConstraint]:
    seen: set[tuple[str, str, str]] = set()
    out: list[PartConstraint] = []
    for c in items:
        key = (c.kind, c.target, c.value)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def describe(c: PartConstraint) -> str:
    """The constraint in the user's terms, for a finding message."""
    if c.kind == "value" and c.target == "resistor":
        ohms = c.meta.get("ohms", 0.0)
        pretty = f"{ohms / 1000:g} kΩ" if ohms >= 1000 else f"{ohms:g} Ω"
        return f"a {pretty} resistor"
    if c.kind == "value" and c.target == "capacitor":
        f = c.meta.get("farads", 0.0)
        pretty = (f"{f * 1e6:g} µF" if f >= 1e-6 else f"{f * 1e9:g} nF" if f >= 1e-9 else f"{f * 1e12:g} pF")
        return f"a {pretty} capacitor"
    if c.kind == "value" and c.target == "inductor":
        h = c.meta.get("henries", 0.0)
        return f"a {h * 1e6:g} µH inductor"
    if c.kind == "mount":
        style = "through-hole" if c.value == "tht" else "surface-mount"
        return f"{style} {c.target}" if c.target != "any" else f"{style} parts"
    if c.kind == "size":
        return f"a {c.value} {c.target}"
    return c.text
