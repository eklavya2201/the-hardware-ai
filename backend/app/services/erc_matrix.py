"""ERC pin-type connection matrix — engineering-rules plan Phase 1.

The professional ERC "correctness floor" is a **pin-type connection matrix**: a
square table indexed by pin electrical-type on both axes whose cells say whether
that pairing on a net is OK / Warning / Error. Unlike our hand-picked R-rules
(each recognises one pattern), the matrix is a *closed* model — every pin pairing
on every net is classified, so nothing slips through for lack of a bespoke rule.

This module is pure data + tiny helpers (no Project dependency); `verifier.py`
R24/R25/R26 evaluate it against the IR. The matrix is symmetric by construction
(asserted at import).

Severity ramp (plan §6.7): turning an exhaustive matrix on flags many existing
designs, so Error cells promote in TWO stages, not one binary flip:
  1. `_HARD_ERROR` — the UNAMBIGUOUS Error cells (driver contention, two sources
     fighting, a rail shorted to ground / to a driven output). These are always
     genuine defects — verified not to fire on known-good designs — so they
     report as **errors** now (and feed the agent's fix-up loop).
  2. `STRICT_ERRORS` — the remaining, context-dependent Error cells (a logic pin
     wired DIRECTLY to a rail, e.g. an EN input tied to VCC — often valid). These
     stay **warnings** until `STRICT_ERRORS` is flipped True after production
     telemetry confirms they're clean. Warning cells stay warnings throughout.
"""

from __future__ import annotations

from app.models import PinType, VerifierSeverity

# Promote the remaining (ambiguous) Error cells to hard errors. Left False until
# telemetry confirms the logic-pin-on-rail cells don't false-positive; the
# unambiguous shorts/contention below are already hard errors regardless.
STRICT_ERRORS = False

# Compact verdicts: "OK" allowed · "W" warning · "E" error.
Verdict = str  # Literal["OK", "W", "E"]

# Column/row order for the table below. MUST match `_TABLE` rows 1:1.
_ORDER: list[PinType] = [
    "power_in",
    "power_out",
    "input",
    "output",
    "bidirectional",
    "passive",
    "ground",
    "unspecified",
]

# The connection matrix (plan §5). Row = col order = _ORDER. Symmetric.
# output<->bidirectional is OK (as in KiCad's ERC map): MCU GPIOs are typed
# bidirectional, so a sensor/driver output into an MCU pin -- the commonest
# connection there is -- used to warn on every design (2026-08-16).
#            pin  pout  in   out  bidir pass  gnd  unsp
_TABLE: list[list[Verdict]] = [
    ["W",  "OK", "E",  "E",  "W",  "OK", "E",  "W"],   # power_in
    ["OK", "E",  "E",  "E",  "W",  "OK", "E",  "W"],   # power_out
    ["E",  "E",  "W",  "OK", "OK", "OK", "E",  "W"],   # input
    ["E",  "E",  "OK", "E",  "OK", "OK", "E",  "W"],   # output
    ["W",  "W",  "OK", "OK", "OK", "OK", "E",  "W"],   # bidirectional
    ["OK", "OK", "OK", "OK", "OK", "OK", "OK", "W"],   # passive
    ["E",  "E",  "E",  "E",  "E",  "OK", "OK", "W"],   # ground
    ["W",  "W",  "W",  "W",  "W",  "W",  "W",  "W"],   # unspecified
]

_INDEX: dict[PinType, int] = {t: i for i, t in enumerate(_ORDER)}

# Verify symmetry once, at import — a typo in the table is a bug, not a warning.
for _i in range(len(_ORDER)):
    for _j in range(len(_ORDER)):
        assert _TABLE[_i][_j] == _TABLE[_j][_i], (
            f"ERC matrix asymmetric at {_ORDER[_i]}×{_ORDER[_j]}"
        )


def verdict(a: PinType, b: PinType) -> Verdict:
    """Raw matrix verdict ("OK" / "W" / "E") for two pin types sharing a net.
    Unknown types fall back to 'unspecified' (always a warning)."""
    ia = _INDEX.get(a, _INDEX["unspecified"])
    ib = _INDEX.get(b, _INDEX["unspecified"])
    return _TABLE[ia][ib]


# The unambiguous Error cells — always a genuine short or driver contention,
# independent of net context. Promoted to hard errors now (stage 1 of the ramp).
_HARD_ERROR: set[frozenset] = {
    frozenset({"output", "output"}),        # two outputs fighting (bus contention)
    frozenset({"power_out", "power_out"}),  # two sources on one rail (subsumes R20)
    frozenset({"ground", "power_in"}),      # power rail shorted to ground
    frozenset({"ground", "power_out"}),     # source output shorted to ground
    frozenset({"ground", "output"}),        # output shorted to ground
}


def severity(a: PinType, b: PinType) -> VerifierSeverity | None:
    """The reportable severity for a pin-type pairing, honouring the two-stage
    ramp: 'OK' -> None (legal); 'W' -> "warning"; 'E' -> "error" when the pairing
    is an unambiguous short/contention (`_HARD_ERROR`) or `STRICT_ERRORS` is on,
    else "warning" (context-dependent cell still in the ramp)."""
    v = verdict(a, b)
    if v == "OK":
        return None
    if v == "W":
        return "warning"
    if STRICT_ERRORS or frozenset({a, b}) in _HARD_ERROR:
        return "error"
    return "warning"


# Human phrasing per pin-type pairing verdict, so a finding teaches *why*.
_REASON = {
    frozenset({"output", "output"}): "two outputs driving each other (bus contention)",
    frozenset({"power_out", "power_out"}): "two sources driving one rail (regulators fighting)",
    frozenset({"output", "power_out"}): "an output tied to a power source",
    frozenset({"output", "power_in"}): "an output tied directly to a power rail",
    frozenset({"input", "power_out"}): "an input tied directly to a source output",
    frozenset({"input", "power_in"}): "an input tied directly to a power rail",
    frozenset({"ground", "power_in"}): "a power rail shorted to ground",
    frozenset({"ground", "power_out"}): "a source output shorted to ground",
    frozenset({"ground", "input"}): "an input shorted to ground",
    frozenset({"ground", "output"}): "an output shorted to ground",
    frozenset({"ground", "bidirectional"}): "a bidirectional pin shorted to ground",
}


def reason(a: PinType, b: PinType) -> str:
    """Short human explanation of why a pairing is flagged."""
    key = frozenset({a, b})
    if key in _REASON:
        return _REASON[key]
    if "unspecified" in (a, b):
        return "a pin has no electrical type — verify the part's pin typing"
    return f"{a} on the same net as {b}"
