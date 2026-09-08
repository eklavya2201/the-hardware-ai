"""Simulation endpoints — PRD §7.6.

Two surfaces:
  - GET  /projects/:id/spice-netlist        — returns the generated .cir text
  - POST /projects/:id/simulate             — runs ngspice, returns waveforms

Always returns a structured result; if ngspice isn't installed, the response's
`success: false` plus an `error` string explains how to fix it. The frontend
displays the netlist preview either way (useful for debugging).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.models_db import UserRow
from app.services import spice, store
from app.services.auth import current_user

router = APIRouter()


class SimulateRequest(BaseModel):
    analysis: Literal["op", "tran"] = "tran"
    tran_step: str = "100u"
    tran_stop: str = "10m"


class WaveformPoint(BaseModel):
    t: float
    v: float


class WaveformSeries(BaseModel):
    net: str
    points: list[tuple[float, float]]


class SimulateResponse(BaseModel):
    success: bool
    analysis: str
    netlist: str
    waveforms: list[WaveformSeries]
    op_values: dict[str, float]
    notes: list[str]
    error: str | None
    raw_log: str
    ngspice_available: bool


@router.get("/projects/{project_id}/spice-netlist", response_class=PlainTextResponse)
def get_netlist(
    project_id: str,
    analysis: str = "tran",
    user: UserRow = Depends(current_user),
) -> str:
    project = store.get_project(project_id, user_id=user.id)
    if not project:
        raise HTTPException(404, f"Project {project_id} not found")
    netlist, _ctx, _ = spice.build_netlist(project, analysis=analysis)
    return netlist


@router.post("/projects/{project_id}/simulate", response_model=SimulateResponse)
def post_simulate(
    project_id: str,
    body: SimulateRequest,
    user: UserRow = Depends(current_user),
) -> SimulateResponse:
    project = store.get_project(project_id, user_id=user.id)
    if not project:
        raise HTTPException(404, f"Project {project_id} not found")
    if not project.ir.components:
        raise HTTPException(400, "Schematic is empty — generate components first.")

    result = spice.run_simulation(
        project,
        analysis=body.analysis,
        tran_step=body.tran_step,
        tran_stop=body.tran_stop,
    )

    return SimulateResponse(
        success=result.success,
        analysis=result.analysis,
        netlist=result.netlist,
        waveforms=[
            WaveformSeries(net=net, points=points)
            for net, points in result.waveforms.items()
        ],
        op_values=result.op_values,
        notes=result.notes,
        error=result.error,
        raw_log=result.raw_log,
        ngspice_available=spice.find_ngspice() is not None,
    )
