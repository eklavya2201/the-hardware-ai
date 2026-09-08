"""Fab capability profiles — engineering-rules plan Phase 0.

"Manufacturable" is relative to a fab's capability class. A profile is a small,
named parameter set (min trace / space / drill / annular / hole-to-hole / edge
clearance + copper weight) that Phase 2 DRC maps onto KiCad's board design
settings so `kicad-cli pcb drc` enforces *that fab's* limits instead of KiCad's
generic defaults. Copper weight also feeds the Phase 3 IPC-2152 trace-width math.

Values are the vendors' published standard 2-layer capabilities (mm). They are
deliberately conservative "standard service" numbers, not the advanced/extra-cost
tiers — a board that passes these is cheap to fab anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FabProfile:
    key: str
    name: str
    min_trace: float          # min conductor width, mm (DRC floor)
    min_space: float          # min conductor clearance, mm (DRC floor)
    min_drill: float          # min finished hole (through), mm (DRC floor)
    min_via_diameter: float   # min via pad diameter, mm (DRC floor)
    min_annular: float        # min via annular ring, mm (DRC floor)
    hole_to_hole: float       # min edge-to-edge hole spacing, mm
    edge_clearance: float     # min copper-to-board-edge, mm
    copper_oz: float          # outer copper weight, oz (for IPC-2152)
    # The via the router/FreeRouting actually places (distinct from the DRC
    # floors above). MUST satisfy (via_diameter - via_drill)/2 >= min_annular
    # and via_drill >= min_drill, or the fab's own vias fail its own DRC.
    via_drill: float = 0.3
    via_diameter: float = 0.6

    def as_design_rules(self) -> dict:
        """Flat dict handed to build_pcb.py → applied to the KiCad board's
        BOARD_DESIGN_SETTINGS so DRC checks against this fab's limits."""
        return {
            "min_clearance": self.min_space,
            "min_track_width": self.min_trace,
            "min_through_drill": self.min_drill,
            "min_via_diameter": self.min_via_diameter,
            "min_annular": self.min_annular,
            "hole_to_hole": self.hole_to_hole,
            "edge_clearance": self.edge_clearance,
        }

    def as_kicad_pro(self) -> dict:
        """A sibling `.kicad_pro` project. KiCad 6+ stores DRC constraints +
        net-class rules in the PROJECT, not the standalone `.kicad_pcb`; writing
        this next to the board makes `kicad-cli pcb drc` (and ExportSpecctraDSN,
        so FreeRouting) honour this fab's clearances/widths — otherwise DRC falls
        back to KiCad's generic defaults and flags fab-compatible part footprints
        (e.g. a USB-C connector's 0.15 mm pad gaps)."""
        route_w = max(self.min_trace, 0.2)
        return {
            "board": {
                "design_settings": {
                    "rules": {
                        "min_clearance": self.min_space,
                        "min_copper_edge_clearance": self.edge_clearance,
                        "min_hole_to_hole": self.hole_to_hole,
                        "min_through_hole": self.min_drill,
                        "min_track_width": self.min_trace,
                        "min_via_annular_width": self.min_annular,
                        "min_via_diameter": self.min_via_diameter,
                    }
                }
            },
            "net_settings": {
                "classes": [
                    {
                        "name": "Default",
                        "clearance": self.min_space,
                        "track_width": route_w,
                        # The router's actual via — sized so its annular ring
                        # clears min_annular (self-consistent with the floors).
                        "via_diameter": self.via_diameter,
                        "via_drill": self.via_drill,
                    }
                ]
            },
        }


# JLCPCB standard 2-layer service (their published "standard" capability).
JLCPCB_2LAYER = FabProfile(
    key="jlcpcb_2layer",
    name="JLCPCB — 2-layer standard",
    min_trace=0.127,        # 5 mil
    min_space=0.127,        # 5 mil
    min_drill=0.2,
    min_via_diameter=0.45,  # 0.2 drill + 2×0.125 annular
    min_annular=0.13,
    hole_to_hole=0.254,
    edge_clearance=0.3,
    copper_oz=1.0,
)

# PCBWay default 2-layer service — marginally tighter trace/space capability.
PCBWAY_2LAYER = FabProfile(
    key="pcbway_2layer",
    name="PCBWay — 2-layer default",
    min_trace=0.1,
    min_space=0.1,
    min_drill=0.2,
    min_via_diameter=0.45,
    min_annular=0.13,
    hole_to_hole=0.254,
    edge_clearance=0.3,
    copper_oz=1.0,
)

# A conservative, fab-agnostic "safe everywhere" class — the default gate so a
# passing board is manufacturable at essentially any vendor.
CONSERVATIVE = FabProfile(
    key="conservative",
    name="Conservative (safe at any fab)",
    min_trace=0.15,
    min_space=0.15,
    min_drill=0.3,
    min_via_diameter=0.6,
    min_annular=0.15,
    hole_to_hole=0.3,
    edge_clearance=0.3,
    copper_oz=1.0,
)

PROFILES: dict[str, FabProfile] = {
    p.key: p for p in (JLCPCB_2LAYER, PCBWAY_2LAYER, CONSERVATIVE)
}

DEFAULT_PROFILE = JLCPCB_2LAYER.key


def get(key: str | None) -> FabProfile:
    """Resolve a profile by key, falling back to the default. Never raises so an
    unknown/None key from the API degrades to the sensible default."""
    if key and key in PROFILES:
        return PROFILES[key]
    return PROFILES[DEFAULT_PROFILE]


def listing() -> list[dict]:
    """Serializable profile list for the UI selector."""
    return [
        {"key": p.key, "name": p.name, **p.as_design_rules(), "copper_oz": p.copper_oz}
        for p in PROFILES.values()
    ]
