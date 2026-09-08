"""Mechanical IR endpoints — Week 3 (tasks 5.1/5.2/5.4).

Exposes the mechanical IR (outline, mounting holes, keep-outs, connector cutouts,
enclosure) and its tool mutations, plus the CadQuery STEP export. Mirrors the PCB
router: every mutation is a revertable history commit; the STEP export degrades
gracefully (503) when the CAD kernel isn't installed.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app.models import MechanicalIR, Project
from app.models_db import UserRow
from app.services import (
    history,
    idf_bridge,
    blender_bridge,
    freecad_bridge,
    mechanical_bridge,
    mechanical_tools,
    mechanical_verifier,
    store,
)
from app.services import cad_brief
from app.services.auth import current_user

router = APIRouter()

# Product renders are cached on disk per project (a Cycles frame costs real
# CPU on the box); a new placement or enclosure invalidates it by comparing
# the product scene's hash.
RENDER_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "renders"


def _derive_enclosure_if_missing(project: Project) -> Project:
    """The product is board + parts + case. If nobody derived a case yet,
    derive it (same tool as the Enclosure button) so no CAD export or render
    is ever a bare board."""
    mech = project.mechanical
    if project.board is not None and project.board.components and (
        mech is None or mech.enclosure is None or mech.enclosure.kind == "none"
    ):
        try:
            mechanical_tools.dispatch(project, "derive_from_board", {})
            mechanical_verifier.merge_into_project(project)
            project = store.upsert(project)
        except mechanical_tools.MechToolError:
            pass
    return project


@router.get("/projects/{project_id}/export/fcstd")
def export_fcstd(
    project_id: str,
    enclosure: bool = True,
    user: UserRow = Depends(current_user),
):
    """The product as an EDITABLE FreeCAD document: named parametric features
    (Board, Hole_n, Part_<refdes>, Case, Cutout_<refdes>, Standoff_n). 503 when
    FreeCAD is not installed; 409 when there is nothing physical yet."""
    project = _require(project_id, user.id)
    if enclosure:
        project = _derive_enclosure_if_missing(project)
    res = freecad_bridge.build_fcstd(project, include_enclosure=enclosure)
    if not res.success:
        code = 503 if "not found" in (res.error or "").lower() else 409
        raise HTTPException(code, res.error or "FreeCAD export failed.")
    return Response(
        res.data["fcstd"],
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{project.id}.FCStd"'},
    )


def _scene_hash(project: Project) -> str:
    import hashlib
    import json

    scene = mechanical_bridge.product_scene(project, True) or {}
    return hashlib.sha1(json.dumps(scene, sort_keys=True, default=str).encode()).hexdigest()[:16]


@router.post("/projects/{project_id}/render")
def render_product(
    project_id: str,
    user: UserRow = Depends(current_user),
) -> dict:
    """Render the product (board + parts + case) with Blender, cache the PNG,
    return where to fetch it. 503 when Blender is absent."""
    project = _require(project_id, user.id)
    if project.board is None or not project.board.components:
        raise HTTPException(409, "Place the board first — there is nothing to render yet.")
    project = _derive_enclosure_if_missing(project)
    RENDER_DIR.mkdir(parents=True, exist_ok=True)
    key = _scene_hash(project)
    png = RENDER_DIR / f"{project.id}-{key}.png"
    seconds = 0.0
    if not png.exists():
        import time

        t0 = time.time()
        data, error = blender_bridge.render(project)
        if data is None:
            code = 503 if "not found" in (error or "").lower() else 500
            raise HTTPException(code, error or "Render failed.")
        for old in RENDER_DIR.glob(f"{project.id}-*.png"):
            old.unlink(missing_ok=True)
        png.write_bytes(data)
        seconds = round(time.time() - t0, 1)
    return {"url": f"/api/projects/{project.id}/render.png?v={key}", "cached": seconds == 0.0, "seconds": seconds}


@router.get("/projects/{project_id}/render.png")
def render_image(project_id: str, user: UserRow = Depends(current_user)):
    project = _require(project_id, user.id)
    for png in sorted(RENDER_DIR.glob(f"{project.id}-*.png")):
        return Response(png.read_bytes(), media_type="image/png",
                        headers={"Cache-Control": "no-cache"})
    raise HTTPException(404, "No render yet — click Render first.")


class MechToolRequest(BaseModel):
    tool: str
    args: dict = {}


class ImportIdfRequest(BaseModel):
    emn: str  # the .emn board-file text


@router.get("/projects/{project_id}/cad-brief")
def get_cad_brief(project_id: str, user: UserRow = Depends(current_user)) -> dict:
    """The board as nouns for Synqit CAD: outline, holes, part envelopes,
    connector openings, enclosure clearances — as compact JSON, as an
    OpenSCAD prelude (`synqit_board()`), and as a few lines of prompt. 404
    until the project has a placed board or a mechanical outline."""
    project = _require(project_id, user.id)
    brief = cad_brief.build_brief(project)
    if brief is None:
        raise HTTPException(404, "This project has no board yet — place parts first.")
    return brief


@router.get("/projects/{project_id}/mechanical", response_model=MechanicalIR | None)
def get_mechanical(
    project_id: str, user: UserRow = Depends(current_user)
) -> MechanicalIR | None:
    return _require(project_id, user.id).mechanical


@router.post("/projects/{project_id}/mechanical/tool", response_model=Project)
def post_mechanical_tool(
    project_id: str,
    body: MechToolRequest,
    user: UserRow = Depends(current_user),
) -> Project:
    """Dispatch a single mechanical tool call (set_board_outline, add_mounting_hole,
    add_connector_cutout, set_enclosure, derive_from_board, …). Same strict,
    typed surface the agent uses — the mechanical IR is only ever mutated this
    way (anti-hallucination contract, task 5.4)."""
    project = _require(project_id, user.id)
    try:
        _, diff = mechanical_tools.dispatch(project, body.tool, body.args)
    except mechanical_tools.MechToolError as exc:
        raise HTTPException(400, str(exc))
    except TypeError as exc:  # bad args for the tool
        raise HTTPException(400, f"Invalid args for {body.tool}: {exc}")
    # Refresh mechanical DRC (task 6.2) so the Review panel reflects the new
    # geometry — deterministic + cheap, so it runs on every mutation (unlike the
    # KiCad DRC, which shells out and is user-triggered). Replaces only MECH_* rows.
    mechanical_verifier.merge_into_project(project)
    saved = store.upsert(project)
    history.commit(saved, message=diff.summary if diff else f"Mechanical: {body.tool}", author="user")
    return saved


@router.get("/projects/{project_id}/export/step")
def export_step(
    project_id: str,
    enclosure: bool = True,
    user: UserRow = Depends(current_user),
):
    """Export the board-as-a-3D-solid (+ enclosure) as a STEP file (task 5.2).
    503 when the CadQuery CAD kernel isn't installed; 409 when there's nothing to
    build from."""
    project = _require(project_id, user.id)
    # "STEP" means the product: board, parts and the case. If nobody derived an
    # enclosure yet, derive it now (same tool the Enclosure button runs) so the
    # first download is never a bare board.
    mech = project.mechanical
    if enclosure and project.board is not None and project.board.components and (
        mech is None or mech.enclosure is None or mech.enclosure.kind == "none"
    ):
        try:
            mechanical_tools.dispatch(project, "derive_from_board", {})
            mechanical_verifier.merge_into_project(project)
            project = store.upsert(project)
        except mechanical_tools.MechToolError:
            pass  # fall through: export whatever exists
    data, error = mechanical_bridge.build_step(project, include_enclosure=enclosure)
    if data is None:
        code = 503 if "cadquery" in (error or "").lower() else 409
        raise HTTPException(code, error or "STEP export failed.")
    return Response(
        data,
        media_type="application/step",
        headers={
            "Content-Disposition": f'attachment; filename="{project.id}.step"'
        },
    )


@router.get("/projects/{project_id}/export/idf")
def export_idf(
    project_id: str, user: UserRow = Depends(current_user)
):
    """Export the board+mechanical IR as an IDF 3.0 `.emn`/`.emp` pair (zipped) for
    MCAD (task 6.3). One-way writer like Gerber/STEP. 409 when there's no board."""
    project = _require(project_id, user.id)
    try:
        data = idf_bridge.export_idf_zip(project)
    except idf_bridge.IdfError as exc:
        raise HTTPException(409, str(exc))
    return Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{project.id}_idf.zip"'},
    )


@router.post("/projects/{project_id}/mechanical/import-idf", response_model=Project)
def import_idf(
    project_id: str,
    body: ImportIdfRequest,
    user: UserRow = Depends(current_user),
) -> Project:
    """Import an IDF `.emn` from MCAD (task 6.3). Applies ONLY mechanical features
    (outline / mounting holes / keep-outs / connector cutouts) and ONLY through the
    validated mechanical tools — component moves are reported, never written back
    (see the architectural contract in idf_bridge). Runs mechanical DRC after."""
    project = _require(project_id, user.id)
    result = idf_bridge.import_idf(project, body.emn)
    mechanical_verifier.merge_into_project(project)
    saved = store.upsert(project)
    history.commit(saved, message=f"IDF import — {result.summary()}", author="user")
    return saved


def _require(project_id: str, user_id: str) -> Project:
    project = store.get_project(project_id, user_id=user_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    return project
