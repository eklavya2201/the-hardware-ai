"""Project CRUD + chat + export endpoints."""

from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from fastapi.responses import PlainTextResponse, Response
from sse_starlette.sse import EventSourceResponse

from app.models import (
    ChatRequest,
    CreateProjectRequest,
    DesignSummary,
    DiffActionRequest,
    NetDetail,
    Project,
    PropertyUpdateRequest,
)
from app.models_db import UserRow
from app.services import (
    agent,
    design_summary,
    export,
    gerber,
    history,
    library,
    llm_config,
    llm_critic,
    offers,
    posthog_client,
    search_log,
    store,
    tools,
)
from app.services.auth import current_user, current_user_optional

router = APIRouter()


def _readable_project(project_id: str, user: UserRow | None, share: str | None) -> Project:
    """The project for a READ: the owner's copy, or the shared copy when the
    caller carries its live share token. Anything else is 404 — never 403,
    so an id's existence does not leak (same rule as store.get_project)."""
    if user is not None:
        project = store.get_project(project_id, user_id=user.id)
        if project:
            return project
    if share:
        project = store.get_shared_project(project_id, share)
        if project:
            return project
    raise HTTPException(404, f"Project {project_id} not found")


@router.get("/projects", response_model=list[Project])
def list_projects(user: UserRow = Depends(current_user)) -> list[Project]:
    return store.list_projects(user_id=user.id)


@router.post("/projects", response_model=Project)
def create_project(
    body: CreateProjectRequest, user: UserRow = Depends(current_user)
) -> Project:
    project = store.create_project(body.name, kind=body.kind, user_id=user.id)
    history.commit(project, message=f"Created project ({body.kind})", author="system")
    return project


@router.get("/projects/{project_id}/history")
def list_history(
    project_id: str, user: UserRow = Depends(current_user)
) -> list[dict]:
    if store.get_project(project_id, user_id=user.id) is None:
        raise HTTPException(404, f"Project {project_id} not found")
    return history.list_commits(project_id)


@router.post("/projects/{project_id}/revert/{commit_id}", response_model=Project)
def revert_project(
    project_id: str, commit_id: str, user: UserRow = Depends(current_user)
) -> Project:
    if store.get_project(project_id, user_id=user.id) is None:
        raise HTTPException(404, f"Project {project_id} not found")
    try:
        return history.revert_to(project_id, commit_id)
    except KeyError as exc:
        raise HTTPException(404, f"Commit {commit_id} not found on this project") from exc


@router.get("/projects/{project_id}", response_model=Project)
def get_project(
    project_id: str,
    share: str | None = None,
    user: UserRow | None = Depends(current_user_optional),
) -> Project:
    """Owner read, or a read-only share read (`?share=<token>`). A viewer gets
    the design without the token field; every WRITE route on this project
    still requires the owner's session."""
    return _readable_project(project_id, user, share)


class ShareResponse(BaseModel):
    projectId: str
    token: str | None
    path: str | None


@router.post("/projects/{project_id}/share", response_model=ShareResponse)
def create_share(project_id: str, user: UserRow = Depends(current_user)) -> ShareResponse:
    """Mint (or return the existing) read-only share link. Review #39 Flaw 3:
    "the copied link just opened a blank screen or error page for anyone
    else" — because it was the editor URL. Idempotent: sharing twice hands
    back the same token, so a link already sent keeps working."""
    project = store.get_project(project_id, user_id=user.id)
    if not project:
        raise HTTPException(404, f"Project {project_id} not found")
    token = project.shareToken or secrets.token_urlsafe(24)
    if not project.shareToken:
        store.set_share_token(project_id, user.id, token)
    return ShareResponse(projectId=project_id, token=token,
                         path=f"/shared/{project_id}?share={token}")


@router.delete("/projects/{project_id}/share", response_model=ShareResponse)
def revoke_share(project_id: str, user: UserRow = Depends(current_user)) -> ShareResponse:
    """Revoke the link. Every copy that was ever sent stops working."""
    if store.set_share_token(project_id, user.id, None) is None:
        raise HTTPException(404, f"Project {project_id} not found")
    return ShareResponse(projectId=project_id, token=None, path=None)


@router.delete("/projects/{project_id}")
def delete_project(
    project_id: str, user: UserRow = Depends(current_user)
) -> dict:
    """Permanently delete one of the caller's own projects.

    Until 2026-08-20 there was no delete at all: a reviewer's throwaway test
    builds were permanent, and the product had nothing to say about what
    happens to a design once you are done with it. Irreversible by design —
    the UI confirms first; the design, its board, its history and its chat all
    go together, because a partial delete is the dishonest kind.
    """
    if not store.delete_project(project_id, user_id=user.id):
        raise HTTPException(404, f"Project {project_id} not found")
    return {"deleted": project_id}


@router.post("/projects/{project_id}/chat")
def chat(
    project_id: str,
    body: ChatRequest,
    user: UserRow = Depends(current_user),
):
    """Streams Server-Sent Events.

    Event names: user_message, delta, tool_use, assistant_message, error, done.
    """
    project = store.get_project(project_id, user_id=user.id)
    if not project:
        raise HTTPException(404, f"Project {project_id} not found")

    # Analytics: record the build prompt (email stamped server-side). Best-effort.
    search_log.log_search(user.email, body.text, source="build_prompt")
    posthog_client.capture(
        user.email, "ai_prompt", {"project_id": project_id, "length": len(body.text)}
    )

    def stream():
        try:
            for event in agent.run(project, body.text, llm=llm_config.runtime_for_user(user)):
                yield {
                    "event": event.kind,
                    "data": json.dumps(event.payload, default=str),
                }
        finally:
            tools.recompute_bom(project)
            store.upsert(project)
            # One commit per chat turn — message = the user's prompt (truncated).
            history.commit(
                project,
                message=f"Chat: {body.text[:120]}",
                author="agent",
            )
            yield {"event": "snapshot", "data": project.model_dump_json()}

    return EventSourceResponse(stream())


@router.post("/projects/{project_id}/messages/{message_id}/diff")
def update_diff(
    project_id: str,
    message_id: str,
    body: DiffActionRequest,
    user: UserRow = Depends(current_user),
) -> Project:
    project = store.get_project(project_id, user_id=user.id)
    if not project:
        raise HTTPException(404, f"Project {project_id} not found")

    msg = next((m for m in project.chat if m.id == message_id), None)
    if not msg or not msg.diff:
        raise HTTPException(404, "Message has no diff")

    msg.diff.status = "accepted" if body.action == "accept" else "rejected"
    saved = store.upsert(project)
    history.commit(
        saved,
        message=(
            f"{'Accepted' if body.action == 'accept' else 'Rejected'} diff "
            f"({len(msg.diff.changes)} change{'s' if len(msg.diff.changes) != 1 else ''})"
        ),
        author="user",
    )
    return saved


def _board_component(project: Project, refdes: str):
    """The placed component on the PCB, or a 400 — board edits (canvas drag,
    rotate) address the placement, never the schematic symbol."""
    board = project.board
    pc = None
    if board is not None:
        pc = next((c for c in board.components if c.refdes == refdes), None)
    if pc is None:
        raise HTTPException(400, f"{refdes} is not placed on the board.")
    return pc


def _set_board_position(project: Project, refdes: str, key: str, value: str) -> None:
    pc = _board_component(project, refdes)
    try:
        setattr(pc, key, float(value))
    except ValueError as exc:
        raise HTTPException(400, f"{key} must be a number, got {value!r}") from exc


def _set_board_rotation(project: Project, refdes: str, value: str) -> None:
    pc = _board_component(project, refdes)
    try:
        rot = int(value)
    except ValueError as exc:
        raise HTTPException(400, f"rotation must be an integer, got {value!r}") from exc
    if rot not in (0, 90, 180, 270):
        raise HTTPException(400, "rotation must be 0, 90, 180, or 270.")
    pc.rotation = rot  # type: ignore[assignment]


@router.post(
    "/projects/{project_id}/components/{refdes}/property",
    response_model=Project,
)
def set_component_property(
    project_id: str,
    refdes: str,
    body: PropertyUpdateRequest,
    user: UserRow = Depends(current_user),
) -> Project:
    """Edit a single property on a placed component. `value` field maps to the
    `set_value` tool; everything else (footprint, x, y, rotation) maps to
    `set_property`. Returns the updated project so the frontend can re-render."""
    project = store.get_project(project_id, user_id=user.id)
    if not project:
        raise HTTPException(404, f"Project {project_id} not found")
    try:
        if body.key == "value":
            tools.dispatch(
                project, "set_value", {"refdes": refdes, "value": body.value}
            )
        elif body.target == "board" and body.key in {"x", "y"}:
            _set_board_position(project, refdes, body.key, body.value)
        elif body.target == "board" and body.key == "rotation":
            _set_board_rotation(project, refdes, body.value)
        else:
            tools.dispatch(
                project,
                "set_property",
                {"refdes": refdes, "key": body.key, "value": body.value},
            )
    except tools.ToolError as exc:
        raise HTTPException(400, str(exc)) from exc
    tools.recompute_bom(project)
    saved = store.upsert(project)
    history.commit(
        saved,
        message=f"{refdes}.{body.key} = {body.value}",
        author="user",
    )
    return saved


@router.get("/projects/{project_id}/summary", response_model=DesignSummary)
def project_summary(
    project_id: str,
    share: str | None = None,
    user: UserRow | None = Depends(current_user_optional),
) -> DesignSummary:
    """IR-derived design summary (Phase 1). Computed on read from the current
    project state — components, pin mapping, power architecture, warnings, and
    missing items — so every row is traceable to the schematic, never to chat
    prose. Readable through a share link too: the Review tab is the half of a
    design a reviewer most wants to see."""
    project = _readable_project(project_id, user, share)
    return design_summary.build(project)


@router.get("/projects/{project_id}/nets/{net_name}", response_model=NetDetail)
def net_detail(
    project_id: str, net_name: str, user: UserRow = Depends(current_user)
) -> NetDetail:
    """All pins on a net + routed status (Phase 4 / feedback #8). Backs the
    net-detail panel; computed from the IR + board so every endpoint is real."""
    project = store.get_project(project_id, user_id=user.id)
    if not project:
        raise HTTPException(404, f"Project {project_id} not found")
    detail = design_summary.net_detail(project, net_name)
    if detail is None:
        raise HTTPException(404, f"Net {net_name!r} not found in this project")
    return detail


@router.post("/projects/{project_id}/run-drc", response_model=Project)
def run_drc(project_id: str, user: UserRow = Depends(current_user)) -> Project:
    """Run rule-based DRC AND the LLM critic (PRD §7.4). The agent's tool-level
    `run_drc` does rules only — the LLM call is reserved for this user-triggered
    endpoint so the agent can't run up cost calling itself."""
    project = store.get_project(project_id, user_id=user.id)
    if not project:
        raise HTTPException(404, f"Project {project_id} not found")
    tools.dispatch(project, "run_drc", {})
    llm_critic.merge_into_project(project, llm=llm_config.runtime_for_user(user))
    tools.recompute_bom(project)
    return store.upsert(project)


class AcknowledgeRequest(BaseModel):
    """Mark a warning intentional, with a reason (review round 2026-08-20)."""

    issue_id: str
    reason: str = ""


@router.post("/projects/{project_id}/issues/acknowledge", response_model=Project)
def acknowledge_issue(
    project_id: str, body: AcknowledgeRequest, user: UserRow = Depends(current_user)
) -> Project:
    """Acknowledge a WARNING with a reason: it stays visible and annotated but
    stops counting as an open finding. Errors are refused (400) — an error
    means the board does not work, and a note does not change that."""
    project = store.get_project(project_id, user_id=user.id)
    if not project:
        raise HTTPException(404, f"Project {project_id} not found")
    try:
        _, diff = tools.dispatch(
            project,
            "acknowledge_finding",
            {"issue_id": body.issue_id, "reason": body.reason},
        )
    except tools.ToolError as e:
        raise HTTPException(400, str(e)) from e
    tools.dispatch(project, "run_drc", {})
    saved = store.upsert(project)
    if diff is not None:
        history.commit(saved, message=diff.summary, author="user")
    return saved


@router.post("/projects/{project_id}/issues/unacknowledge", response_model=Project)
def unacknowledge_issue(
    project_id: str, body: AcknowledgeRequest, user: UserRow = Depends(current_user)
) -> Project:
    """Undo an acknowledgement — the finding counts again."""
    project = store.get_project(project_id, user_id=user.id)
    if not project:
        raise HTTPException(404, f"Project {project_id} not found")
    try:
        _, diff = tools.dispatch(
            project, "unacknowledge_finding", {"issue_id": body.issue_id}
        )
    except tools.ToolError as e:
        raise HTTPException(400, str(e)) from e
    tools.dispatch(project, "run_drc", {})
    saved = store.upsert(project)
    if diff is not None:
        history.commit(saved, message=diff.summary, author="user")
    return saved


@router.post("/projects/{project_id}/bom/refresh-prices")
def refresh_bom_prices(
    project_id: str, user: UserRow = Depends(current_user)
) -> dict:
    """Fetch live distributor prices/stock for the project's BOM MPNs (honest
    sourcing). User-triggered like run-drc — network I/O happens only here;
    recompute_bom reads the resulting cache offline. Degrades gracefully: with
    no MOUSER_API_KEY the response says so and prices stay labelled "est."."""
    from app.services import pricing

    project = store.get_project(project_id, user_id=user.id)
    if not project:
        raise HTTPException(404, f"Project {project_id} not found")
    # Recompute first so a BOM that was never computed (or predates an edit)
    # still gets every MPN priced, not the stale line list.
    tools.recompute_bom(project)
    mpns = [line.mpn for line in project.bom if line.mpn]
    res = pricing.refresh(mpns) if pricing.available() else pricing.RefreshResult(total=len(mpns))
    tools.recompute_bom(project)
    saved = store.upsert(project)
    message: str | None = None
    if not pricing.available():
        # Never name our own environment variables to a user — a server-side
        # config key is not something they can act on, and reading one in the
        # UI reads as a leaked internal (2026-08-20 review).
        message = (
            "Live distributor pricing is not switched on yet — the prices below are "
            "reference estimates, not quotes. Check the vendor before ordering."
        )
    elif res.error:
        # B4: a partial failure is reported even when other parts priced. The
        # old code surfaced the error only when NOTHING priced, so a quota cut
        # halfway through a refresh reported success.
        left = len(res.failed) + len(res.skipped)
        message = res.error + (f" — {left} part{'s' if left != 1 else ''} not priced." if left else "")
    elif res.failed:
        message = (f"{len(res.failed)} part{'s' if len(res.failed) != 1 else ''} not listed by "
                   f"{' / '.join(pricing.vendors_configured())}: {', '.join(res.failed[:6])}"
                   f"{'…' if len(res.failed) > 6 else ''}")
    return {
        "project": saved.model_dump(),
        "available": pricing.available(),
        "vendors": pricing.vendors_configured(),
        # Kept for older clients: `updated` = fetched this run, `live` = fresh now.
        "updated": res.fetched,
        "live": res.priced,
        "fetched": res.fetched,
        "fromCache": res.from_cache,
        "failed": res.failed,
        "skipped": len(res.skipped),
        "total": res.total,
        "message": message,
    }


@router.get("/projects/{project_id}/export/readiness")
def export_readiness(
    project_id: str, boards: int = 1, user: UserRow = Depends(current_user)
) -> dict:
    """Manufacturability report (Phase 5 / #30): per-component footprint
    confidence + the critical parts that block a fabrication export, plus the
    sourcing verdict (B5) for a run of `boards`."""
    project = store.get_project(project_id, user_id=user.id)
    if not project:
        raise HTTPException(404, f"Project {project_id} not found")
    return export.manufacturability_report(project, boards=max(1, min(boards, 100000)))


@router.get("/projects/{project_id}/export/netlist")
def export_netlist(
    project_id: str,
    force: bool = False,
    user: UserRow = Depends(current_user),
):
    project = store.get_project(project_id, user_id=user.id)
    if not project:
        raise HTTPException(404, f"Project {project_id} not found")
    # Manufacturing export gate (#30): refuse when a critical part has an
    # unverified (generic/missing) footprint, unless explicitly forced.
    report = export.manufacturability_report(project)
    if export.export_blockers(report) and not force:
        names = ", ".join(
            f"{b['refdes']} ({b['mpn']})" for b in export.export_blockers(report)[:8]
        )
        raise HTTPException(
            409,
            f"Export blocked: {len(report['blockers'])} critical part(s) use an "
            f"unverified footprint — {names}. Confirm the real footprints or "
            f"retry with ?force=true to export anyway.",
        )
    body = export.kicad_netlist(project)
    return PlainTextResponse(
        body,
        headers={"Content-Disposition": f'attachment; filename="{project.id}.net"'},
    )


@router.get("/projects/{project_id}/export/kicad_pcb")
def export_kicad_pcb(
    project_id: str,
    force: bool = False,
    user: UserRow = Depends(current_user),
):
    """Task 3.4 — a real `.kicad_pcb` generated from the IR board (one-way:
    IR -> KiCad, never the reverse). Same manufacturability gate as the netlist;
    503 when KiCad isn't installed, 409 when there's no placed board."""
    project = store.get_project(project_id, user_id=user.id)
    if not project:
        raise HTTPException(404, f"Project {project_id} not found")
    report = export.manufacturability_report(project)
    if export.export_blockers(report) and not force:
        names = ", ".join(
            f"{b['refdes']} ({b['mpn']})" for b in export.export_blockers(report)[:8]
        )
        raise HTTPException(
            409,
            f"Export blocked: {len(report['blockers'])} critical part(s) use an "
            f"unverified footprint — {names}. Confirm the real footprints or "
            f"retry with ?force=true to export anyway.",
        )
    try:
        body = export.kicad_pcb(project)
    except export.KicadPcbError as exc:
        msg = str(exc)
        # No board -> 409 (client should place first); KiCad missing -> 503.
        code = 503 if "kicad" in msg.lower() or "pcbnew" in msg.lower() else 409
        raise HTTPException(code, msg)
    return PlainTextResponse(
        body,
        headers={
            "Content-Disposition": f'attachment; filename="{project.id}.kicad_pcb"'
        },
    )


@router.get("/projects/{project_id}/export/kicad_project")
def export_kicad_project(
    project_id: str,
    force: bool = False,
    user: UserRow = Depends(current_user),
):
    """A full, openable KiCad project (zip): schematic + project file + netlist
    + BOM, plus the board when it's placed and KiCad is available. The schematic
    is generated in pure Python, so this works on a KiCad-less server — a
    missing board degrades to a README note, never a failed export."""
    project = store.get_project(project_id, user_id=user.id)
    if not project:
        raise HTTPException(404, f"Project {project_id} not found")
    if not project.ir.components:
        raise HTTPException(409, "Nothing to export — the schematic is empty.")
    report = export.manufacturability_report(project)
    if export.export_blockers(report) and not force:
        names = ", ".join(f"{b['refdes']} ({b['mpn']})" for b in export.export_blockers(report)[:8])
        raise HTTPException(
            409,
            f"Export blocked: {len(report['blockers'])} critical part(s) use an "
            f"unverified footprint — {names}. Confirm the real footprints or "
            f"retry with ?force=true to export anyway.",
        )
    body, _notes = export.kicad_project(project)
    return Response(
        content=body,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{project.id}_kicad.zip"'
        },
    )


@router.get("/projects/{project_id}/export/gerbers")
def export_gerbers(
    project_id: str,
    force: bool = False,
    user: UserRow = Depends(current_user),
):
    """Task 4.1 — a real, fab-ready manufacturing package (Gerbers + Excellon
    drill + CSV pick-and-place), zipped, generated by kicad-cli from the IR board
    (one-way: IR -> KiCad, never the reverse). Same manufacturability gate as the
    netlist; 503 when KiCad/kicad-cli isn't installed, 409 when there's no board."""
    project = store.get_project(project_id, user_id=user.id)
    if not project:
        raise HTTPException(404, f"Project {project_id} not found")
    report = export.manufacturability_report(project)
    if export.export_blockers(report) and not force:
        names = ", ".join(
            f"{b['refdes']} ({b['mpn']})" for b in export.export_blockers(report)[:8]
        )
        raise HTTPException(
            409,
            f"Export blocked: {len(report['blockers'])} critical part(s) use an "
            f"unverified footprint — {names}. Confirm the real footprints or "
            f"retry with ?force=true to export anyway.",
        )
    try:
        blob = export.fab_package(project)
    except export.KicadPcbError as exc:
        msg = str(exc)
        # KiCad absent -> fall back to the in-house CAM writer (services/gerber.py),
        # which needs no external tool. Only a genuinely missing board is fatal.
        if "kicad" in msg.lower() or "pcbnew" in msg.lower() or "kicad-cli" in msg.lower():
            try:
                blob = gerber.fab_zip(project)
            except ValueError as fallback_exc:
                raise HTTPException(409, str(fallback_exc)) from fallback_exc
        else:
            raise HTTPException(409, msg)
    return Response(
        blob,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{project.id}-gerbers.zip"'
        },
    )


@router.get("/projects/{project_id}/panel")
def get_panel(project_id: str, user: UserRow = Depends(current_user)) -> dict:
    """The panel this board would be built on: the one recorded on the design,
    plus what we would suggest and the largest array that fits a fab's frame.

    Fabs build and ship PANELS, and small boards are quoted per panel, so the
    numbers here (boards per panel, panel size, how much of it is finished
    board) are the ones an order is actually placed on. Two review rounds asked
    for this and found nothing in the product (2026-08-20)."""
    from app.services import panelize

    project = store.get_project(project_id, user_id=user.id)
    if not project:
        raise HTTPException(404, f"Project {project_id} not found")
    board = project.board
    if board is None or not board.components:
        return {"available": False, "reason": "No PCB layout yet — place the board first."}

    def describe(spec) -> dict | None:
        if spec is None:
            return None
        panel = panelize.derive(board, spec)
        return {
            "spec": spec.model_dump(),
            "boards": panel.boards,
            "width": round(panel.width, 2),
            "height": round(panel.height, 2),
            "utilisationPct": round(panel.utilisation * 100),
            "vScoreLines": len(panel.v_scores),
            "tabs": len(panel.tabs),
            "toolingHoles": len(panel.tooling_holes),
            "fiducials": len(panel.fiducials),
        }

    current = project.mechanical.panel if project.mechanical else None
    suggestion = panelize.suggest(board)
    method = "v_score" if board.shape == "rect" else "mouse_bites"
    return {
        "available": True,
        "board": {"width": board.width, "height": board.height, "shape": board.shape},
        "current": describe(current),
        "suggested": describe(suggestion),
        "maxFit": describe(panelize.best_fit(board, method=method)),
        "methods": ["v_score", "mouse_bites"] if board.shape == "rect" else ["mouse_bites"],
    }


@router.get("/projects/{project_id}/export/panel-gerbers")
def export_panel_gerbers(
    project_id: str,
    force: bool = False,
    user: UserRow = Depends(current_user),
):
    """The CAM package for the PANEL — what a fab quotes and builds.

    Uses the recorded panel; falls back to the suggested one so the button
    always produces something honest rather than erroring on an unset field.
    Same manufacturability gate as the single-board export."""
    from app.services import panelize

    project = store.get_project(project_id, user_id=user.id)
    if not project:
        raise HTTPException(404, f"Project {project_id} not found")
    report = export.manufacturability_report(project)
    if export.export_blockers(report) and not force:
        names = ", ".join(f"{b['refdes']} ({b['mpn']})" for b in export.export_blockers(report)[:8])
        raise HTTPException(
            409,
            f"Export blocked: {len(report['blockers'])} issue(s) — {names}. "
            f"Fix them, or retry with ?force=true to export anyway.",
        )
    spec = project.mechanical.panel if project.mechanical else None
    if spec is None:
        if project.board is None or not project.board.components:
            raise HTTPException(409, "No PCB layout yet — place the board first.")
        spec = panelize.suggest(project.board)
    if spec is None:
        raise HTTPException(
            409,
            "This board already fills a standard panel, so there is no array to "
            "export — order it one-up with the normal Gerbers.",
        )
    try:
        blob = gerber.panel_zip(project, spec)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return Response(
        blob,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{project.id}-panel-gerbers.zip"'
        },
    )


@router.get("/projects/{project_id}/export/bom")
def export_bom(
    project_id: str,
    format: str = "jlc",
    boards: int = 1,
    user: UserRow = Depends(current_user),
):
    """BOM CSV. `format`: jlc (default, assembly-style), digikey or mouser
    (column layouts those distributors' BOM-upload wizards auto-map).
    `boards` multiplies every quantity — an order is for a run, not a board."""
    project = store.get_project(project_id, user_id=user.id)
    if not project:
        raise HTTPException(404, f"Project {project_id} not found")
    builders = {
        "jlc": export.bom_csv,
        "digikey": export.digikey_bom_csv,
        "mouser": export.mouser_bom_csv,
    }
    builder = builders.get(format)
    if builder is None:
        raise HTTPException(422, f"Unknown BOM format {format!r}; use jlc, digikey, or mouser")
    body = builder(project, boards=max(1, min(boards, 100000)))
    suffix = "" if format == "jlc" else f"-{format}"
    return Response(
        body,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{project.id}-bom{suffix}.csv"'
        },
    )


@router.get("/projects/{project_id}/order")
def order_info(project_id: str, boards: int = 1, user: UserRow = Depends(current_user)) -> dict:
    """Everything the Order panel needs (affiliate Phase 2): per-distributor
    BOM upload targets, PCB-fab quote links (referral-tagged when configured),
    the export-readiness verdict and the per-line BUYABILITY verdict (B5) for
    a run of `boards`."""
    from app.services import sourcing

    project = store.get_project(project_id, user_id=user.id)
    if not project:
        raise HTTPException(404, f"Project {project_id} not found")
    boards = max(1, min(boards, 100000))
    report = export.manufacturability_report(project, boards=boards)
    verdict = sourcing.assess(project, boards=boards)
    distributors = [
        {
            "vendor": "DigiKey",
            "format": "digikey",
            "uploadUrl": "https://www.digikey.com/en/mylists",
            "instructions": "Create a List → upload the CSV; DigiKey maps the columns and fills a cart.",
        },
        {
            "vendor": "Mouser",
            "format": "mouser",
            "uploadUrl": "https://www.mouser.com/bom/",
            "instructions": "Open the BOM tool → upload the CSV; Mouser matches the parts and builds a cart.",
        },
    ]
    lines = sum(1 for _ in {c.mpn for c in project.ir.components})
    return {
        "bomLines": lines,
        "distributors": distributors,
        "fabs": offers.fab_order_links(),
        "hasBoard": project.board is not None and bool(project.board.components),
        "readiness": {
            "ready": report["ready"],
            "blockers": len(report["blockers"]),
            "exportBlockers": len(export.export_blockers(report)),
        },
        "sourcing": verdict,
    }


# Library endpoints moved to api/library.py
