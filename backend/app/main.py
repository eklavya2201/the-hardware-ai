"""FastAPI entrypoint — `uvicorn app.main:app --reload`."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin as admin_api
from app.api import auth as auth_api
from app.api import build_guide as build_guide_api
from app.api import events as events_api
from app.api import feedback as feedback_api
from app.api import firmware as firmware_api
from app.api import library as library_api
from app.api import mechanical as mechanical_api
from app.api import pcb, projects, search, simulation
from app.db import init_db
from app.services import agent
from app.services import blender_bridge
from app.services import freecad_bridge
from app.services import idf_bridge
from app.services import kicad_bridge
from app.services import mechanical_bridge
from app.services import posthog_client

load_dotenv()


def create_app() -> FastAPI:
    docs_enabled = os.environ.get("ENABLE_API_DOCS", "").lower() in {"1", "true", "yes"}
    app = FastAPI(
        title="Synqit API",
        version="0.1.0",
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )

    # Prod origins (synqit.in + www) plus dev/preview hosts. Extra comma-separated
    # origins can be added via CORS_ORIGINS without a code change.
    extra_origins = [
        o.strip()
        for o in os.environ.get("CORS_ORIGINS", "").split(",")
        if o.strip()
    ]
    allow_origins = [
        "https://synqit.in",
        "https://www.synqit.in",
        *extra_origins,
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_origin_regex=r"https?://(localhost(:\d+)?|127\.0\.0\.1(:\d+)?|.*\.netlify\.app|.*\.onrender\.com)",
        allow_methods=["*"],
        allow_headers=["*"],
        # Required so the session cookie flows on cross-origin requests
        # (synqit.in frontend → api.synqit.in backend).
        allow_credentials=True,
    )

    app.include_router(auth_api.router, prefix="/api")
    app.include_router(projects.router, prefix="/api")
    app.include_router(pcb.router, prefix="/api")
    app.include_router(mechanical_api.router, prefix="/api")
    app.include_router(simulation.router, prefix="/api")
    app.include_router(library_api.router, prefix="/api")
    app.include_router(firmware_api.router, prefix="/api")
    app.include_router(build_guide_api.router, prefix="/api")
    app.include_router(search.router, prefix="/api")
    app.include_router(events_api.router, prefix="/api")
    app.include_router(feedback_api.router, prefix="/api")
    app.include_router(admin_api.router, prefix="/api")

    @app.get("/api/health")
    def health() -> dict:
        return {
            "status": "ok",
            "agent_mode": "openai" if agent.has_api_key() else "stub",
            "kicad_mode": kicad_bridge.kicad_status(),
            "freerouting_mode": (
                "available" if kicad_bridge.freerouting_available() else "absent"
            ),
            "mechanical_mode": mechanical_bridge.status(),
            "idf_mode": idf_bridge.status(),
            "freecad_mode": freecad_bridge.status(),
            "blender_mode": blender_bridge.status(),
        }

    @app.on_event("startup")
    def _startup() -> None:
        init_db()

    @app.on_event("shutdown")
    def _shutdown() -> None:
        # Flush any buffered PostHog events so nothing is lost on redeploy.
        posthog_client.shutdown()

    return app


app = create_app()
