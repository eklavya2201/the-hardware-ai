"""Queries — the agent's EYES (read-only, compact, side-effect free).

Three things get labelled, not one (founder's rule, 2026-09-07): operations
(verbs — add_component, connect, add_mounting_hole), entities (nouns — the IR
in `models.py`, catalogued in docs/ENTITIES.md) and QUERIES — the questions an
agent asks about state instead of dumping the whole IR or looking at a render.
Queries are what teams skip, and they are what make a tool-driven agent work.

Guardrails, each enforced by scripts/regression_agent_queries.py:
  * PURE — a query never mutates the project, never calls an LLM or the
    network, and touches no file beyond the library / footprint cache.
  * SMALL — every answer is at most MAX_RESULT_CHARS of JSON (one constant);
    every list is a page (offset/limit) that reports `total`, `truncated`
    and `next_offset` when cut. `run()` enforces the cap as a last resort.
  * TYPED — arguments are validated by the pydantic models below; a wrong
    type or a missing field answers {"error", "expected"}; an unknown refdes /
    net / hole answers {"error": "no such <entity>", "known": [...]}. Never
    a traceback: a query is a question, and every question gets an answer.
  * BOUNDED — the agent may ask QUERY_BUDGET_PER_TURN questions per turn
    (`QueryBudget`); past that it is told to act on what it knows.

Reached two ways with one implementation: the agent tool set (tools.dispatch
falls through to QUERIES) and `GET /api/projects/{id}/q/{name}` for an
external agent (Claude / Codex / an MCP server later). Geometry comes from the
SAME sources the STEP export uses (`geometry.solid_rects`,
`mechanical_derive.body_height_mm`), so the agent's eyes and the fab outputs
never disagree.
"""

from __future__ import annotations

import json
import math
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.models import Component, Project
from app.services import (
    design_summary,
    footprints,
    geometry,
    library,
    mechanical_bridge,
    mechanical_derive,
    tools,
)

# ---- the constants (one place) ---------------------------------------------
MAX_RESULT_CHARS = 1500       # hard cap on one answer's JSON (~375 tokens)
QUERY_BUDGET_PER_TURN = 12    # questions the agent may ask in one turn
MAX_KNOWN = 20                # ids offered with a "no such ..." error
DEFAULT_LIMIT = 40            # default page size
MAX_LIMIT = 200
MAX_MSG = 140                 # one-line issue message
MAX_DESC = 90


# --------------------------------------------------------------------------- #
# argument models (typed inputs)
# --------------------------------------------------------------------------- #
class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(_Args):
    pass


class PageArgs(_Args):
    offset: int = Field(0, ge=0)
    limit: int = Field(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)


class RefdesArgs(_Args):
    refdes: str = Field(..., min_length=1)


class ComponentArgs(PageArgs):
    refdes: str = Field(..., min_length=1)


class PinArgs(_Args):
    refdes: str = Field(..., min_length=1)
    pin: str = Field(..., min_length=1)


class UnconnectedArgs(PageArgs):
    refdes: str | None = None


class NetArgs(PageArgs):
    name: str = Field(..., min_length=1)


class IssuesArgs(PageArgs):
    severity: Literal["error", "warning", "info"] | None = None
    rule: str | None = None


class HoleArgs(_Args):
    id: str = Field(..., min_length=1)


def _expected(model: type[BaseModel]) -> dict[str, str]:
    """{field: "type (required|default)"} — what a caller should have sent."""
    out: dict[str, str] = {}
    for name, f in model.model_fields.items():
        t = getattr(f.annotation, "__name__", None) or str(f.annotation).replace("typing.", "")
        out[name] = f"{t} ({'required' if f.is_required() else f'default {f.default!r}'})"
    return out


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _r(v: float, nd: int = 2) -> float:
    return round(float(v), nd)


def _comp(project: Project, refdes: str) -> Component | None:
    return next((c for c in project.ir.components if c.refdes == refdes), None)


def _no_such(entity: str, ident: str, known: list[str]) -> dict[str, Any]:
    return {"error": f"no such {entity} {ident!r}", "known": known[:MAX_KNOWN],
            "known_total": len(known)}


def _no_such_component(project: Project, refdes: str) -> dict[str, Any]:
    return _no_such("component", refdes, [c.refdes for c in project.ir.components])


def _pin_nets(project: Project) -> dict[tuple[str, str], str]:
    """(refdes, pin_number) -> net name. One pin, one net (R21)."""
    out: dict[tuple[str, str], str] = {}
    for net in project.ir.nets:
        for r, p in net.pins:
            out.setdefault((r, p), net.name)
    return out


def _placed(project: Project, refdes: str):
    board = project.board
    if board is None:
        return None
    return next((pc for pc in board.components if pc.refdes == refdes), None)


def _one_line(text: str | None, cap: int) -> str | None:
    if not text:
        return None
    t = " ".join(text.split())
    return t if len(t) <= cap else t[: cap - 1] + "…"


def _page(out: dict[str, Any], key: str, rows: list, offset: int, limit: int) -> dict[str, Any]:
    """Put one page of `rows` under `key` with the pagination contract:
    total / offset / truncated / next_offset. `_page` remembers which key is
    the page so `_fit` can shrink it if the answer is still over the cap."""
    page = rows[offset: offset + limit]
    out[key] = page
    out["total"] = len(rows)
    out["offset"] = offset
    end = offset + len(page)
    out["truncated"] = end < len(rows)
    out["next_offset"] = end if end < len(rows) else None
    out["_page"] = key
    return out


def _fit(out: dict[str, Any]) -> dict[str, Any]:
    """Last-resort size guard: an answer over MAX_RESULT_CHARS loses rows from
    its page (or its largest list) until it fits, and says so."""
    key = out.pop("_page", None)

    def size() -> int:
        return len(json.dumps(out, default=str))

    if size() <= MAX_RESULT_CHARS:
        return out
    if key is None:
        lists = [k for k, v in out.items() if isinstance(v, list) and v]
        if not lists:
            return out
        key = max(lists, key=lambda k: len(json.dumps(out[k], default=str)))
        out.setdefault("total", len(out[key]))
        out.setdefault("offset", 0)
    total = int(out.get("total", len(out[key])))
    offset = int(out.get("offset", 0))

    def stamp() -> None:
        end = offset + len(out[key])
        out["truncated"] = end < total
        out["next_offset"] = end if out["truncated"] else None

    stamp()  # the marker keys count toward the cap, so they go on before trimming
    while len(out[key]) > 1 and size() > MAX_RESULT_CHARS:
        keep = max(1, min(len(out[key]) - 1, int(len(out[key]) * MAX_RESULT_CHARS / size())))
        out[key] = out[key][:keep]
        stamp()
    return out


def _pad_index(board) -> list[tuple[float, float, str]]:
    """Every pad's world position with its "refdes.pin", so a rats line can
    be named by the pads it joins."""
    out: list[tuple[float, float, str]] = []
    for pc in board.components:
        for pad in pc.footprint.pads:
            x, y = geometry.pad_world(pc, pad)
            out.append((x, y, f"{pc.refdes}.{pad.pin_number}"))
    return out


# A rats endpoint is a pad centre, but routers snap to a grid and a pad's
# rounded centre may be a hair off — name the pad if one is this close.
_PAD_MATCH_MM = 0.6


def _nearest_pad(pads: list[tuple[float, float, str]], x: float, y: float) -> str | None:
    best, best_d = None, _PAD_MATCH_MM
    for px, py, name in pads:
        d = math.hypot(px - x, py - y)
        if d < best_d:
            best, best_d = name, d
    return best


def _rat_len(r) -> float:
    return math.hypot(r.x2 - r.x1, r.y2 - r.y1)


# --------------------------------------------------------------------------- #
# components + pins
# --------------------------------------------------------------------------- #
def get_component(project: Project, *, refdes: str, offset: int = 0,
                  limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """Everything about one part: mpn, value, category, footprint, package
    size (from the footprint cache), height, its pins as [number, name, type,
    net|null] (paged), and where it sits on the board if placed."""
    comp = _comp(project, refdes)
    if comp is None:
        return _no_such_component(project, refdes)
    part = library.get_part(comp.mpn)
    fp = footprints.for_mpn(comp.mpn)
    nets = _pin_nets(project)
    pins = [[p.number, p.name, p.type, nets.get((refdes, p.number))] for p in comp.pins]
    pc = _placed(project, refdes)
    out: dict[str, Any] = {
        "refdes": refdes,
        "mpn": comp.mpn,
        "value": comp.value,
        "category": part.category if part else None,
        "description": _one_line(part.description if part else comp.description, MAX_DESC),
        "footprint": fp.name,
        "package": part.package if part else None,
        "body_mm": [_r(fp.width), _r(fp.height)],
        "height_mm": _r(mechanical_derive.body_height_mm(comp.mpn), 1),
        "off_board": bool(part and part.offBoard),
        "pin_count": len(comp.pins),
        "unconnected_pins": sum(1 for row in pins if row[3] is None),
        "placed": (
            {"side": pc.layer, "x": _r(pc.x), "y": _r(pc.y), "rot": pc.rotation,
             "locked": pc.locked} if pc else None
        ),
        "pin_fields": ["number", "name", "type", "net"],
    }
    return _page(out, "pins", pins, offset, limit)


def get_pin_net(project: Project, *, refdes: str, pin: str) -> dict[str, Any]:
    """Is this pin on a net, and which? `pin` may be a number, a name or an
    alias (the same resolver `connect` uses)."""
    comp = _comp(project, refdes)
    if comp is None:
        return _no_such_component(project, refdes)
    try:
        number = tools._resolve_pin(comp, str(pin))
    except tools.ToolError as e:
        return {"error": str(e), "refdes": refdes,
                "known": [f"{p.number}({p.name})" for p in comp.pins[:MAX_KNOWN]],
                "known_total": len(comp.pins)}
    pin_obj = next((p for p in comp.pins if p.number == number), None)
    net_name = _pin_nets(project).get((refdes, number))
    out: dict[str, Any] = {
        "refdes": refdes, "pin": number,
        "name": pin_obj.name if pin_obj else str(pin),
        "type": pin_obj.type if pin_obj else None,
        "net": net_name,
    }
    if net_name:
        net = next(n for n in project.ir.nets if n.name == net_name)
        out["net_role"] = tools._net_role(net_name)
        out["net_pins"] = len(net.pins)
    return out


def list_unconnected_pins(project: Project, *, refdes: str | None = None,
                          offset: int = 0, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """Pins on no net — supply/ground pins first (those are the real bugs),
    then everything else, as [pin, name, type]. `refdes` narrows to one part."""
    if refdes is not None and _comp(project, refdes) is None:
        return _no_such_component(project, refdes)
    nets = _pin_nets(project)
    rows: list[tuple[int, str, str, str]] = []
    by_refdes: dict[str, int] = {}
    for c in project.ir.components:
        if refdes is not None and c.refdes != refdes:
            continue
        part = library.get_part(c.mpn)
        if part is not None and part.category == "mechanical":
            continue
        for p in c.pins:
            if (c.refdes, p.number) in nets:
                continue
            rank = 0 if p.type in ("power_in", "power_out", "ground") else 1
            rows.append((rank, f"{c.refdes}.{p.number}", p.name, p.type))
            by_refdes[c.refdes] = by_refdes.get(c.refdes, 0) + 1
    rows.sort(key=lambda r: (r[0], r[1]))
    out: dict[str, Any] = {
        "count": len(rows),
        "unwired_power_or_ground": sum(1 for r in rows if r[0] == 0),
        "by_refdes": dict(sorted(by_refdes.items(), key=lambda kv: -kv[1])[:MAX_KNOWN]),
        "pin_fields": ["pin", "name", "type"],
    }
    return _page(out, "pins", [list(r[1:]) for r in rows], offset, limit)


def list_components(project: Project, *, offset: int = 0,
                    limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """Compact roster: [refdes, mpn, value, category, side|null]. Paged."""
    placed = {pc.refdes: pc.layer for pc in (project.board.components if project.board else [])}
    rows = []
    for c in project.ir.components:
        part = library.get_part(c.mpn)
        rows.append([c.refdes, c.mpn, c.value, part.category if part else None,
                     placed.get(c.refdes)])
    out: dict[str, Any] = {"fields": ["refdes", "mpn", "value", "category", "side"]}
    return _page(out, "components", rows, offset, limit)


# --------------------------------------------------------------------------- #
# nets
# --------------------------------------------------------------------------- #
def get_net(project: Project, *, name: str, offset: int = 0,
            limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """One net: role (power/gnd/signal), its pins as [refdes, pin, pin name,
    pin type] (paged), the MCU pin it reaches, whether it is routed, and the
    unrouted (ratsnest) length in mm."""
    detail = design_summary.net_detail(project, name)
    if detail is None:
        return _no_such("net", name, [n.name for n in project.ir.nets])
    board = project.board
    rats_mm = sum(_rat_len(r) for r in board.rats if r.net == name) if board else 0.0
    plane = next((p.layer for p in board.planes if p.net == name), None) if board else None
    out: dict[str, Any] = {
        "net": name,
        "role": detail.role,
        "pin_count": len(detail.endpoints),
        "mcu_pin": f"{detail.mcuRefdes}.{detail.mcuPin}" if detail.mcuRefdes else None,
        "routed": detail.routed,
        "traces": detail.traceCount,
        "vias": detail.vias,
        "rats": detail.ratsnest,
        "ratsnest_mm": _r(rats_mm, 1),
        "plane": plane,
        "issues": [i.id for i in detail.issues[:10]],
        "pin_fields": ["refdes", "pin", "name", "type"],
    }
    rows = [[e.refdes, e.pin, e.name, e.pinType] for e in detail.endpoints]
    return _page(out, "pins", rows, offset, limit)


def list_nets(project: Project, *, offset: int = 0, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """Compact roster: [name, role, pin count, routed|null]. Paged."""
    nets = project.ir.nets
    board = project.board
    routed: dict[str, bool] = {}
    if board is not None:
        traced = {t.net for t in board.traces}
        ratted = {r.net for r in board.rats}
        planed = {p.net for p in board.planes}
        for n in nets:
            gnd_pour = tools._net_role(n.name) == "gnd" and board.has_ground_pour
            routed[n.name] = (n.name not in ratted) and (
                n.name in traced or n.name in planed or gnd_pour)
    rows = [[n.name, tools._net_role(n.name), len(n.pins), routed.get(n.name)] for n in nets]
    out: dict[str, Any] = {"fields": ["name", "role", "pins", "routed"]}
    return _page(out, "nets", rows, offset, limit)


# --------------------------------------------------------------------------- #
# issues
# --------------------------------------------------------------------------- #
_SEV_ORDER = {"error": 0, "warning": 1, "info": 2}


def list_issues(project: Project, *, severity: str | None = None, rule: str | None = None,
                offset: int = 0, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """Verifier findings, compact: {id, sev, rule, refdes|net, msg}. Counts are
    always over ALL findings; the rows honour the filters. `rule` matches a
    prefix ("R21", "PCB_", "MECH_R04"). Acknowledged warnings carry ack=true
    and are excluded from `open`."""
    acked = {a.issue_id for a in project.ir.acknowledgements}
    issues = list(project.issues)
    counts = {"error": 0, "warning": 0, "info": 0}
    for i in issues:
        counts[i.severity] = counts.get(i.severity, 0) + 1
    matched = [
        i for i in issues
        if (severity is None or i.severity == severity)
        and (rule is None or (i.rule or "").startswith(rule) or i.id.startswith(rule))
    ]
    matched.sort(key=lambda i: (_SEV_ORDER.get(i.severity, 9), i.rule or "", i.id))
    rows = []
    for i in matched:
        row: dict[str, Any] = {"id": i.id, "sev": i.severity, "rule": i.rule,
                               "msg": _one_line(i.message, MAX_MSG)}
        if i.refdes:
            row["refdes"] = i.refdes
        if i.pinNumber:
            row["pin"] = i.pinNumber
        if i.net:
            row["net"] = i.net
        if i.source not in ("verifier", "ir"):
            row["src"] = i.source
        if i.id in acked:
            row["ack"] = True
        rows.append(row)
    out: dict[str, Any] = {
        "counts": counts,
        "open": {"error": counts["error"],
                 "warning": sum(1 for i in issues if i.severity == "warning" and i.id not in acked)},
        "acknowledged": sum(1 for i in issues if i.id in acked),
        "drc_run": bool(issues) or project.drcErrorCount is not None,
    }
    return _page(out, "issues", rows, offset, limit)


# --------------------------------------------------------------------------- #
# board
# --------------------------------------------------------------------------- #
def _no_board(project: Project) -> dict[str, Any]:
    return {
        "board": None,
        "note": "no board yet — the schematic has to be placed first (auto-place)",
        "components": len(project.ir.components),
        "nets": len(project.ir.nets),
    }


def list_unrouted_ratlines(project: Project, *, offset: int = 0,
                           limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """Unrouted connections from Board.rats, longest first, each named by its
    pads ("U1.12-R3.1") so the answer is actionable. id = net:pads."""
    board = project.board
    if board is None:
        return _no_board(project)
    pads = _pad_index(board)
    rows = []
    for r in board.rats:
        a = _nearest_pad(pads, r.x1, r.y1)
        b = _nearest_pad(pads, r.x2, r.y2)
        rows.append({
            "id": f"{r.net}:{a or '?'}-{b or '?'}",
            "net": r.net,
            "from": a or [_r(r.x1), _r(r.y1)],
            "to": b or [_r(r.x2), _r(r.y2)],
            "mm": _r(_rat_len(r), 1),
        })
    rows.sort(key=lambda row: -row["mm"])
    out: dict[str, Any] = {
        "count": len(rows),
        "total_mm": _r(sum(row["mm"] for row in rows), 1),
        "nets": len({row["net"] for row in rows}),
        "routed": board.auto_routed,
    }
    return _page(out, "rats", rows, offset, limit)


def get_board_summary(project: Project) -> dict[str, Any]:
    """The board at a glance: outline, layers/planes, holes (first 12), parts
    per side, the bounding box of the parts, the tallest part, routing status,
    and the enclosure if one is derived. Read it BEFORE placing or routing."""
    board = project.board
    ff = project.ir.form_factor
    if board is None:
        out = _no_board(project)
        if ff is not None:
            out["form_factor"] = ff.model_dump(exclude_none=True, exclude_defaults=True)
        return out
    mech = project.mechanical
    top = sum(1 for pc in board.components if pc.layer == "top")
    xs: list[float] = []
    ys: list[float] = []
    for pc in board.components:
        for x0, y0, x1, y1 in geometry.solid_rects(pc):
            xs += [x0, x1]
            ys += [y0, y1]
    bbox = [_r(min(xs)), _r(min(ys)), _r(max(xs)), _r(max(ys))] if xs else None
    tall_h, tall_ref = mechanical_derive.tallest_on_side(project, "top")
    bot_h, bot_ref = mechanical_derive.tallest_on_side(project, "bottom")
    holes = mech.mountingHoles if mech else []
    out: dict[str, Any] = {
        "board": {
            "shape": board.shape, "width": _r(board.width), "height": _r(board.height),
            "thickness": mechanical_bridge.BOARD_THICKNESS,
            "layers": board.layer_count,
            "planes": [[p.layer, p.net] for p in board.planes],
            "outline_points": len(board.outline_points),
            "ground_pour": board.has_ground_pour,
        },
        "parts": {"total": len(board.components), "top": top,
                  "bottom": len(board.components) - top,
                  "unplaced": max(0, len(project.ir.components) - len(board.components))},
        "parts_bbox": bbox,
        "tallest_top": {"refdes": tall_ref, "mm": _r(tall_h, 1)} if tall_ref else None,
        "tallest_bottom": {"refdes": bot_ref, "mm": _r(bot_h, 1)} if bot_ref else None,
        "holes": [{"id": h.id, "x": _r(h.x), "y": _r(h.y), "d": _r(h.diameter)} for h in holes[:12]],
        "holes_total": len(holes),
        "routing": {"traces": len(board.traces), "vias": len(board.vias), "rats": len(board.rats),
                    "auto_placed": board.auto_placed, "auto_routed": board.auto_routed},
        "issues": {"pcb": sum(1 for i in project.issues if (i.rule or "").startswith("PCB_")),
                   "mech": sum(1 for i in project.issues if (i.rule or "").startswith("MECH_"))},
        "enclosure": None,
    }
    if ff is not None:
        out["form_factor"] = ff.model_dump(exclude_none=True, exclude_defaults=True)
    if mech is not None and mech.enclosure and mech.enclosure.kind != "none":
        e = mech.enclosure
        out["enclosure"] = {"kind": e.kind, "wall": e.wall_thickness, "standoff": e.standoff_height,
                            "clearance_top": e.clearance_top, "clearance_bottom": e.clearance_bottom}
    return out


def get_part_envelope(project: Project, *, refdes: str) -> dict[str, Any]:
    """The space a part occupies on the board — the solid rectangles the
    overlap rule and the STEP export use — plus its height. An unplaced part
    still answers its footprint size so the placer can be reasoned about."""
    comp = _comp(project, refdes)
    if comp is None:
        return _no_such_component(project, refdes)
    part = library.get_part(comp.mpn)
    height = _r(mechanical_derive.body_height_mm(comp.mpn), 1)
    pc = _placed(project, refdes)
    if pc is None:
        fp = footprints.for_mpn(comp.mpn)
        return {
            "refdes": refdes, "mpn": comp.mpn, "placed": False, "footprint": fp.name,
            "body_mm": [_r(fp.width), _r(fp.height)],
            "courtyard_mm": [_r(fp.courtyard_w or fp.width), _r(fp.courtyard_h or fp.height)],
            "height_mm": height, "off_board": bool(part and part.offBoard),
        }
    rects = [[_r(x0), _r(y0), _r(x1), _r(y1)] for x0, y0, x1, y1 in geometry.solid_rects(pc)]
    xs = [v for r in rects for v in (r[0], r[2])]
    ys = [v for r in rects for v in (r[1], r[3])]
    return {
        "refdes": refdes, "mpn": comp.mpn, "placed": True,
        "side": pc.layer, "x": _r(pc.x), "y": _r(pc.y), "rot": pc.rotation,
        "footprint": pc.footprint.name, "rects": rects,
        "bbox": [min(xs), min(ys), max(xs), max(ys)],
        "size_mm": [_r(max(xs) - min(xs)), _r(max(ys) - min(ys))],
        "height_mm": height, "off_board": bool(part and part.offBoard), "locked": pc.locked,
    }


# --------------------------------------------------------------------------- #
# mechanical
# --------------------------------------------------------------------------- #
def list_holes(project: Project, *, offset: int = 0, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """Mounting holes from the mechanical IR: {id, x, y, d, plated, standoff}."""
    mech = project.mechanical
    rows = [{"id": h.id, "x": _r(h.x), "y": _r(h.y), "d": _r(h.diameter),
             "plated": h.plated, "standoff": h.standoff}
            for h in (mech.mountingHoles if mech else [])]
    return _page({}, "holes", rows, offset, limit)


def get_hole(project: Project, *, id: str) -> dict[str, Any]:
    """One mounting hole and the placed part nearest to it (clearance from the
    hole centre to that part's body edge, mm) — what MECH_R04 reasons about."""
    mech = project.mechanical
    holes = mech.mountingHoles if mech else []
    hole = next((h for h in holes if h.id == id), None)
    if hole is None:
        return _no_such("hole", id, [h.id for h in holes])
    nearest: tuple[str, float] | None = None
    if project.board is not None:
        for pc in project.board.components:
            for x0, y0, x1, y1 in geometry.solid_rects(pc):
                dx = max(x0 - hole.x, 0.0, hole.x - x1)
                dy = max(y0 - hole.y, 0.0, hole.y - y1)
                d = math.hypot(dx, dy)
                if nearest is None or d < nearest[1]:
                    nearest = (pc.refdes, d)
    return {
        "id": hole.id, "x": _r(hole.x), "y": _r(hole.y), "d": _r(hole.diameter),
        "plated": hole.plated, "standoff": hole.standoff,
        "nearest_part": {"refdes": nearest[0], "clearance_mm": _r(nearest[1], 1)} if nearest else None,
        "issues": [i.id for i in project.issues if hole.id in i.id][:10],
    }


def list_cutouts(project: Project, *, offset: int = 0, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """Enclosure-wall openings from the mechanical IR: {id, refdes, edge, box}."""
    mech = project.mechanical
    rows = [{"id": c.id, "refdes": c.refdes, "edge": c.edge, "x": _r(c.x), "y": _r(c.y),
             "width": _r(c.width), "height": _r(c.height)}
            for c in (mech.cutouts if mech else [])]
    return _page({}, "cutouts", rows, offset, limit)


def list_keepouts(project: Project, *, offset: int = 0, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """Keep-out zones from the mechanical IR: {id, layer, box, reason}."""
    mech = project.mechanical
    rows = [{"id": z.id, "layer": z.layer, "x": _r(z.x), "y": _r(z.y),
             "width": _r(z.width), "height": _r(z.height), "reason": z.reason}
            for z in (mech.keepouts if mech else [])]
    return _page({}, "keepouts", rows, offset, limit)


# --------------------------------------------------------------------------- #
# registry + dispatch
# --------------------------------------------------------------------------- #
QUERIES: dict[str, tuple[Callable[..., dict[str, Any]], type[BaseModel]]] = {
    "get_component": (get_component, ComponentArgs),
    "get_pin_net": (get_pin_net, PinArgs),
    "list_unconnected_pins": (list_unconnected_pins, UnconnectedArgs),
    "list_components": (list_components, PageArgs),
    "get_net": (get_net, NetArgs),
    "list_nets": (list_nets, PageArgs),
    "list_issues": (list_issues, IssuesArgs),
    "list_unrouted_ratlines": (list_unrouted_ratlines, PageArgs),
    "get_board_summary": (get_board_summary, NoArgs),
    "get_part_envelope": (get_part_envelope, RefdesArgs),
    "list_holes": (list_holes, PageArgs),
    "get_hole": (get_hole, HoleArgs),
    "list_cutouts": (list_cutouts, PageArgs),
    "list_keepouts": (list_keepouts, PageArgs),
}


def run(project: Project, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Run one query by name: validate the arguments, answer, fit the cap.
    Never raises — an unknown name, a bad argument or an internal fault all
    come back as a JSON error the caller can read."""
    entry = QUERIES.get(name)
    if entry is None:
        return {"error": f"no such query {name!r}", "known": sorted(QUERIES)}
    fn, model = entry
    try:
        parsed = model.model_validate(args or {})
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(str(p) for p in err['loc']) or 'args'}: {err['msg']}"
                             for err in e.errors())
        return {"error": f"bad arguments for {name}: {problems}", "expected": _expected(model)}
    try:
        return _fit(fn(project, **parsed.model_dump()))
    except Exception as e:  # noqa: BLE001 — a query answers, it never throws
        return {"error": f"{name} failed: {type(e).__name__}: {e}", "expected": _expected(model)}


class QueryBudget:
    """Per-turn loop guard: at most QUERY_BUDGET_PER_TURN queries. `take()`
    returns None while budget remains, else the error the tool should answer
    with — the agent is told to act on what it already knows."""

    def __init__(self, limit: int = QUERY_BUDGET_PER_TURN) -> None:
        self.limit = limit
        self.used = 0

    def take(self, name: str) -> dict[str, Any] | None:
        if name not in QUERIES:
            return None
        if self.used >= self.limit:
            return {"error": "query budget exhausted",
                    "hint": "act on what you know or ask the user",
                    "limit": self.limit}
        self.used += 1
        return None
