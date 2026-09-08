"""Placement constraints — Phase 6 of the Suyash-feedback roadmap (#34).

A small constraint model the placer consumes so a layout reflects intent rather
than raw force-directed physics. Constraints are inferred from the project
archetype (a motor connector belongs on the board edge; a decoupling cap belongs
next to its IC) and from user edits (a locked part is fixed in place).

Kinds: edge · near · group · separate · fixed · keepout.

Public API:
  infer(project) -> list[PlacementConstraint]
"""

from __future__ import annotations

from app.models import PlacementConstraint, Project
from app.services import library

# Connector kinds that belong on the board edge (you plug into them).
_EDGE_KEYWORDS = (
    "USB", "JST", "TERMINAL", "BARREL", "HEADER", "RJ45", "SCREW", "JACK",
    "BATTERY", "POWER", "MOTOR",
)


def _decoupling_cap_refdeses(project: Project) -> set[str]:
    return {
        c.refdes
        for c in project.ir.components
        if (p := library.get_part(c.mpn)) and p.symbol.lower() in {"device:c"}
    }


def _host_ic_for_cap(project: Project, cap_refdes: str) -> str | None:
    """The IC a decoupling cap sits next to: the non-cap component sharing one
    of the cap's power-rail nets."""
    caps = _decoupling_cap_refdeses(project)
    for net in project.ir.nets:
        upper = net.name.upper()
        if not upper.startswith(("VCC", "VDD", "+3V", "+5V", "3V3", "5V")):
            continue
        refdeses = {r for r, _ in net.pins}
        if cap_refdes not in refdeses:
            continue
        for r in refdeses:
            if r == cap_refdes or r in caps:
                continue
            part = library.get_part(
                next(c.mpn for c in project.ir.components if c.refdes == r)
            )
            if part and part.category in {"mcu", "regulator", "charger", "sensor", "actuator"}:
                return r
    return None


def infer(project: Project) -> list[PlacementConstraint]:
    """Infer placement constraints from the design (archetype + locks)."""
    constraints: list[PlacementConstraint] = []
    by_ref = {c.refdes: c for c in project.ir.components}

    # 1. Connectors → board edge. Off-board sensor modules (HC-SR04-class:
    #    package MODULE_*, mounted via a pin header) get the same constraint —
    #    the transducer has to see past the board, mid-board it is useless.
    for c in project.ir.components:
        part = library.get_part(c.mpn)
        if not part:
            continue
        if part.category == "connector":
            token = (c.mpn + " " + part.description + " " + part.package).upper()
            if not any(k in token for k in _EDGE_KEYWORDS):
                continue
            reason = "Connectors sit on the board edge for access."
        elif part.category == "sensor" and "MODULE" in part.package.upper():
            reason = "Off-board sensor modules face outward from the board edge."
        else:
            continue
        constraints.append(
            PlacementConstraint(kind="edge", refdes=c.refdes, reason=reason)
        )

    # 2. Decoupling caps → near their host IC.
    for cap in sorted(_decoupling_cap_refdeses(project)):
        host = _host_ic_for_cap(project, cap)
        if host:
            constraints.append(
                PlacementConstraint(
                    kind="near",
                    refdes=cap,
                    target=host,
                    reason=f"Decoupling cap belongs next to {host}.",
                )
            )

    # 3. Locked placed components → fixed (a user choice, not inferred).
    if project.board is not None:
        for pc in project.board.components:
            if pc.locked:
                constraints.append(
                    PlacementConstraint(
                        kind="fixed",
                        refdes=pc.refdes,
                        reason=pc.placementReason or "Locked by the user.",
                        inferred=False,
                    )
                )

    return constraints
