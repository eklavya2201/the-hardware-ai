"""Fluid-loop rules.

A coolant loop is a net with *directed* pins. Seen that way, the reported
failure — "the assistant did not understand which direction the liquid was
supposed to flow in and out of the loop" — stops being a geometry problem and
becomes an unconnected-pin check on a directed graph, which this codebase
already knows how to do.

No new IR field is needed. Pumps, radiators, water blocks and reservoirs all
name their ports (INLET/OUTLET, WATER_IN/WATER_OUT), so the direction is
already in the pin names the library carries.
"""

from __future__ import annotations

from app.models import Project, VerifierIssue


def _fluid_role(pin_name: str) -> str | None:
    """Return "in", "out", or None for a pin that is not a fluid port.

    Deliberately narrow. An electrical pin called VIN, DIN or DOUT must never
    be read as plumbing, so a bare in/out only counts when the name also says
    it carries fluid.
    """
    n = (pin_name or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not n:
        return None

    # Unambiguous on their own.
    if n.startswith("inlet"):
        return "in"
    if n.startswith("outlet"):
        return "out"

    fluid_words = ("water", "coolant", "fluid", "liquid", "hose", "pipe", "loop")
    if not any(w in n for w in fluid_words):
        return None

    # Fluid-qualified: WATER_IN, COOLANT_OUT, LOOP_RETURN...
    if "out" in n or "return" in n or "discharge" in n:
        return "out"
    if "in" in n or "supply" in n or "feed" in n:
        return "in"
    return None


def R82_flow_loop_direction(project: Project) -> list[VerifierIssue]:
    """A fluid loop must be closed, and every segment must run outlet to inlet.

    Three distinct faults, each reported once:
      1. a port on no net at all — an open pipe end
      2. a segment joining only outlets, or only inlets — flow with nowhere to go
      3. a path that never returns to its start — an open loop
    """
    inlets: list[tuple[str, str]] = []
    outlets: list[tuple[str, str]] = []
    for c in project.ir.components:
        for pin in c.pins:
            role = _fluid_role(pin.name)
            if role == "in":
                inlets.append((c.refdes, pin.number))
            elif role == "out":
                outlets.append((c.refdes, pin.number))

    # Not a fluid design. Every ordinary board leaves here.
    if not inlets or not outlets:
        return []

    issues: list[VerifierIssue] = []
    net_of: dict[tuple[str, str], str] = {}
    for n in project.ir.nets:
        for refdes, pin_number in n.pins:
            net_of[(refdes, pin_number)] = n.name

    # 1. Open ends.
    for port in inlets + outlets:
        if port not in net_of:
            refdes, pin_number = port
            issues.append(
                VerifierIssue(
                    id=f"R82:{refdes}.{pin_number}",
                    severity="error",
                    message=(
                        f"Fluid port {refdes}.{pin_number} is not connected to "
                        "anything. Every port has to join a segment, or the loop "
                        "is open at that end."
                    ),
                    refdes=refdes,
                    rule="R82_flow_loop_direction",
                )
            )

    # 2. A segment carries flow from one part's outlet to the next part's inlet.
    #    Two outlets on one segment push against each other; two inlets both
    #    draw from a segment nothing fills.
    inlet_set = set(inlets)
    outlet_set = set(outlets)
    for n in project.ir.nets:
        members = [p for p in n.pins if tuple(p) in inlet_set or tuple(p) in outlet_set]
        if len(members) < 2:
            continue
        outs = [m for m in members if tuple(m) in outlet_set]
        ins = [m for m in members if tuple(m) in inlet_set]
        if outs and ins:
            continue
        side = "outlets" if outs else "inlets"
        joined = ", ".join(f"{r}.{q}" for r, q in members[:4])
        issues.append(
            VerifierIssue(
                id=f"R82:{n.name}",
                severity="error",
                message=(
                    f"Segment {n.name!r} joins only {side} ({joined}). Flow leaves "
                    "one part and enters the next, so a segment needs an outlet at "
                    "one end and an inlet at the other."
                ),
                net=n.name,
                rule="R82_flow_loop_direction",
            )
        )

    # 3. Does it close? Walk outlet -> segment -> the next part's inlet -> that
    #    part's own outlet, and see whether every part is reached.
    if not issues:
        out_by_part: dict[str, tuple[str, str]] = {}
        for refdes, pin_number in outlets:
            out_by_part.setdefault(refdes, (refdes, pin_number))

        start = outlets[0]
        seen_parts: set[str] = set()
        cursor: tuple[str, str] | None = start
        while cursor is not None:
            refdes = cursor[0]
            if refdes in seen_parts:
                break
            seen_parts.add(refdes)
            net = net_of.get(cursor)
            nxt: tuple[str, str] | None = None
            if net:
                for cand in inlets:
                    if net_of.get(cand) == net and cand[0] != refdes:
                        nxt = out_by_part.get(cand[0])
                        break
            cursor = nxt

        unreached = {r for r, _ in outlets} - seen_parts
        if unreached:
            issues.append(
                VerifierIssue(
                    id="R82:open-loop",
                    severity="error",
                    message=(
                        "The fluid path does not close: "
                        + ", ".join(sorted(unreached))
                        + " is never reached by following the flow from "
                        f"{start[0]}.{start[1]}. A cooling loop has to return to "
                        "its start or the coolant has nowhere to go."
                    ),
                    rule="R82_flow_loop_direction",
                )
            )

    return issues
