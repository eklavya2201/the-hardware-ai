"""PCB endpoints: GET board, POST auto-place, (M2 wave 2) POST auto-route."""

from __future__ import annotations

import threading

from fastapi import APIRouter, Depends, HTTPException

from app.models import (
    Board,
    BoardEditRequest,
    PlacementConstraint,
    Project,
    RatsLine,
)
from app.models_db import UserRow
from app.services import (
    constraints,
    fab_profiles,
    history,
    kicad_bridge,
    kicad_drc,
    library,
    mechanical_derive,
    mechanical_tools,
    mechanical_verifier,
    pcb_verifier,
    placement,
    routing,
    routing_freerouting,
    store,
    verifier,
)
from app.services.auth import current_user

router = APIRouter()


@router.get("/projects/{project_id}/pcb", response_model=Board | None)
def get_board(
    project_id: str, user: UserRow = Depends(current_user)
) -> Board | None:
    project = _require(project_id, user.id)
    return project.board


@router.post("/projects/{project_id}/pcb/auto-place", response_model=Board)
def post_auto_place(
    project_id: str, user: UserRow = Depends(current_user)
) -> Board:
    project = _require(project_id, user.id)
    if not project.ir.components:
        raise HTTPException(400, "Schematic is empty — generate components first.")
    board = placement.auto_place(project)
    project.board = board
    pcb_verifier.merge_into_project(project)
    # R57 (the board must be the size/shape that was asked for) can only speak
    # once a board exists, and the schematic rules are not re-run here — so a
    # Ø42 mm watch that placed at Ø76 mm showed no finding until the next
    # run-drc. Refresh just that rule's rows against the new board.
    project.issues = [
        i for i in project.issues if i.rule != "R57_form_factor"
    ] + verifier.R57_form_factor(project)
    # If an enclosure was already derived, refresh the connector cutouts from the
    # NEW placement so they don't go stale (connectors just moved/rotated). Only
    # the connector-derived cutouts are regenerated — manual mounting holes /
    # keep-outs / enclosure settings are left untouched. Then re-run mechanical DRC.
    mech = project.mechanical
    if mech is not None and mech.cutouts:
        mech.cutouts = mechanical_derive.derive_cutouts(project)
        mechanical_verifier.merge_into_project(project)
    # Every real board bolts to something (all three beta reviews asked for
    # mounting references): seed the outline + collision-aware corner M3 holes
    # unless holes already exist (never clobber a hand-placed or derived set).
    # The placer sizes every board with MOUNT_ROOM past its largest part so
    # the holes normally land clear; a genuinely crowded corner is reported
    # honestly by MECH_R04 rather than hidden.
    if not (mech and mech.mountingHoles):
        mechanical_tools.derive_mounting(project)
        mechanical_verifier.merge_into_project(project)
    saved = store.upsert(project)
    history.commit(
        saved,
        message=f"Auto-placed PCB ({board.width:.1f}×{board.height:.1f}mm, {len(board.components)} parts)",
        author="user",
    )
    return board


@router.post("/projects/{project_id}/pcb/auto-route", response_model=Board)
def post_auto_route(
    project_id: str, user: UserRow = Depends(current_user)
) -> Board:
    project = _require(project_id, user.id)
    if project.board is None:
        raise HTTPException(
            400,
            "Run auto-place first — there's no board layout to route on.",
        )
    board = routing.auto_route(project)
    project.board = board
    pcb_verifier.merge_into_project(project)
    saved = store.upsert(project)
    failed = len(board.rats)
    history.commit(
        saved,
        message=(
            f"Auto-routed PCB ({len(board.traces)} traces, {len(board.vias)} vias"
            f"{f', {failed} unroutable' if failed else ''})"
        ),
        author="user",
    )
    return board


@router.get(
    "/projects/{project_id}/pcb/constraints",
    response_model=list[PlacementConstraint],
)
def get_constraints(
    project_id: str, user: UserRow = Depends(current_user)
) -> list[PlacementConstraint]:
    """Inferred placement constraints (Phase 6 / #34) — what the placer is
    trying to honour (connectors on edge, decouplers near their IC, locks)."""
    project = _require(project_id, user.id)
    return constraints.infer(project)


@router.post(
    "/projects/{project_id}/pcb/components/{refdes}/edit",
    response_model=Board,
)
def edit_placed_component(
    project_id: str,
    refdes: str,
    body: BoardEditRequest,
    user: UserRow = Depends(current_user),
) -> Board:
    """Move / rotate / re-layer / lock one placed component (Phase 6 / #32).

    When the part physically moves, only the nets touching it are ripped up and
    rerouted — the rest of the layout is preserved (partial reroute, #33)."""
    project = _require(project_id, user.id)
    if project.board is None:
        raise HTTPException(400, "No board yet — run auto-place first.")
    pc = next((c for c in project.board.components if c.refdes == refdes), None)
    if pc is None:
        raise HTTPException(404, f"{refdes} is not placed on the board.")

    moved = False
    repositioned = False
    if body.x is not None and body.x != pc.x:
        pc.x = body.x
        moved = repositioned = True
    if body.y is not None and body.y != pc.y:
        pc.y = body.y
        moved = repositioned = True
    if body.rotation is not None and body.rotation != pc.rotation:
        pc.rotation = body.rotation
        moved = True
    elif repositioned and body.rotation is None:
        # Dragged to a (possibly different) edge with no explicit rotation:
        # re-derive the mating rotation so a USB/barrel/terminal keeps facing
        # off-board instead of keeping the stale facing of its old edge.
        rot = placement.edge_facing_rotation(
            library.get_part(pc.mpn),
            pc.x, pc.y, project.board.width, project.board.height,
        )
        if rot is not None and rot != pc.rotation:
            pc.rotation = rot  # type: ignore[assignment]
    if body.layer is not None and body.layer != pc.layer:
        pc.layer = body.layer
        moved = True
    if body.locked is not None:
        pc.locked = body.locked
        pc.placementReason = "Locked by the user." if body.locked else None

    if moved:
        # Refresh the ratsnest endpoints for this component's nets, then reroute
        # only those nets so the rest of the board is left intact.
        affected = routing.nets_touching(project, {refdes})
        _refresh_rats(project, affected)
        routing.reroute_nets(project, affected)

    pcb_verifier.merge_into_project(project)
    saved = store.upsert(project)
    history.commit(
        saved,
        message=(
            f"Edited {refdes} on PCB"
            + (" + partial reroute" if moved else "")
        ),
        author="user",
    )
    return saved.board  # type: ignore[return-value]


def _refresh_rats(project: Project, nets: set[str]) -> None:
    """Recompute ratsnest endpoints for the given nets from current pad
    positions, so a moved part's rats line tracks it before rerouting."""
    board = project.board
    if board is None:
        return
    pad_pos: dict[tuple[str, str], tuple[float, float]] = {}
    for pc in board.components:
        for pad in pc.footprint.pads:
            pad_pos[(pc.refdes, pad.pin_number)] = (pc.x + pad.x, pc.y + pad.y)
    board.rats = [r for r in board.rats if r.net not in nets]
    for net in project.ir.nets:
        if net.name not in nets or len(net.pins) < 2:
            continue
        first = pad_pos.get(tuple(net.pins[0]))
        if not first:
            continue
        for other_pin in net.pins[1:]:
            other = pad_pos.get(tuple(other_pin))
            if not other:
                continue
            board.rats.append(
                RatsLine(
                    net=net.name, x1=first[0], y1=first[1], x2=other[0], y2=other[1]
                )
            )


@router.post("/projects/{project_id}/pcb/run-drc", response_model=Project)
def post_run_pcb_drc(
    project_id: str, user: UserRow = Depends(current_user)
) -> Project:
    project = _require(project_id, user.id)
    pcb_verifier.merge_into_project(project)
    return store.upsert(project)


# Finalize spawns a JVM for minutes — one per project at a time. A page reload
# resets the frontend's local busy state, so the guard must live server-side.
_finalize_running: set[str] = set()
_finalize_mutex = threading.Lock()


@router.post("/projects/{project_id}/pcb/finalize-routing", response_model=Board)
def post_finalize_routing(
    project_id: str,
    profile: str | None = None,
    user: UserRow = Depends(current_user),
) -> Board:
    """Manufacturing-grade routing via FreeRouting (the routing offload). Routes
    the board completely and DRC-clean against the fab `profile`, replacing the
    in-house A* preview routes. 503 when FreeRouting/KiCad isn't installed (the
    in-house router stays the fallback), 409 when there's no placed board or a
    finalize is already running for this project."""
    project = _require(project_id, user.id)
    if project.board is None or not project.board.components:
        raise HTTPException(409, "No placed board — run auto-place first.")
    # A pad on TWO nets (R21) or a board out of sync with the schematic can never
    # be routed — FreeRouting just loops on it (review #22: 250+ passes at
    # "8 unrouted", three 4-minute Finalizes on a board with F1.2 on two nets).
    # Refuse up front and name the fix, instead of burning the wall clock.
    from app.services import verifier as _verifier
    blockers = [
        i for i in _verifier.run_all(project)
        if i.severity == "error" and (i.rule or "").startswith(("R21_", "R09_"))
    ] + pcb_verifier.board_sync_issues(project)
    if blockers:
        names = "; ".join(f"{b.rule.split('_')[0]}: {b.message[:110]}" for b in blockers[:3])
        raise HTTPException(
            409,
            f"This board cannot be fully routed until the schematic is fixed — {names}"
            + (f" (+{len(blockers) - 3} more)" if len(blockers) > 3 else "")
            + ". Fix those in the Review tab, re-run auto-place, then Finalize.",
        )
    with _finalize_mutex:
        if project_id in _finalize_running:
            raise HTTPException(
                409, "Finalize is already running for this project — wait for it to finish."
            )
        _finalize_running.add(project_id)
    try:
        routed, error = routing_freerouting.route(project, profile)
    finally:
        with _finalize_mutex:
            _finalize_running.discard(project_id)
    if routed is None:
        detail = error or "FreeRouting could not route this board."
        if not project.board.traces:
            # Finalize-before-preview: don't leave the board unrouted — fall
            # back to the in-house router so there's at least a preview route.
            project.board = routing.auto_route(project)
            pcb_verifier.merge_into_project(project)
            history.commit(
                store.upsert(project),
                message="In-house preview route (FreeRouting fallback)",
                author="user",
            )
            detail += " The in-house preview router routed the board instead."
        raise HTTPException(503, detail)
    project.board = routed
    pcb_verifier.merge_into_project(project)
    saved = store.upsert(project)
    history.commit(
        saved,
        message=(
            f"FreeRouting: manufacturing-grade route "
            f"({len(routed.traces)} traces, {len(routed.vias)} vias, "
            f"{len({r.net for r in routed.rats})} unrouted)"
        ),
        author="user",
    )
    return saved.board  # type: ignore[return-value]


@router.get("/pcb/fab-profiles")
def get_fab_profiles() -> dict:
    """Fab capability profiles (engineering-rules Phase 0) the KiCad DRC can
    check against. Public — no per-project state."""
    return {"default": fab_profiles.DEFAULT_PROFILE, "profiles": fab_profiles.listing()}


@router.post("/projects/{project_id}/pcb/kicad-drc")
def post_kicad_drc(
    project_id: str,
    profile: str | None = None,
    user: UserRow = Depends(current_user),
) -> dict:
    """Task 4.2 + Phase 2 — run KiCad's industry DRC engine (kicad-cli) on the IR
    board against a fab-capability `profile` (min clearance/track/drill/via/
    annular derived from the chosen fab) and merge the violations into
    `project.issues` as `KICAD_*` / source="kicad" diagnostics, ALONGSIDE (never
    replacing) the in-house R01–R26 / PCB_R0x rules.

    Returns `{project, kicad_available, ran, count, error, profile}` so the UI can
    tell a clean board apart from KiCad-not-installed. 409 when the board is
    missing; a KiCad-absent box still returns 200 with `kicad_available=false` so
    the offline demo degrades gracefully."""
    project = _require(project_id, user.id)
    if project.board is None or not project.board.components:
        raise HTTPException(
            409, "No placed board — run auto-place/route before KiCad DRC."
        )
    available = kicad_bridge.cli_available()
    resolved = fab_profiles.get(profile)
    error, count = kicad_drc.merge_into_project(project, resolved.key)
    saved = store.upsert(project)
    return {
        "project": saved.model_dump(),
        "kicad_available": available,
        "ran": error is None,
        "count": count,
        "error": error,
        "profile": {"key": resolved.key, "name": resolved.name},
    }


def _require(project_id: str, user_id: str) -> Project:
    project = store.get_project(project_id, user_id=user_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    return project
