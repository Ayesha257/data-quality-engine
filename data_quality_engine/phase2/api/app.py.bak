"""
Phase 2 -- M4: FastAPI application.

Run locally:
    uvicorn data_quality_engine.phase2.api.app:app --reload

Docs at /docs once running.

--- AUTH REWORK -----------------------------------------------------------
Replaced the old admin-issued "X-API-Key" scheme with real email+password
accounts (see api/auth.py, api/auth_routes.py). The only wiring change
here is including `auth_router` alongside the existing `router`, and
allowing the `Authorization` header through CORS (browsers block it by
default otherwise, same reason X-API-Key needed `allow_headers` before).
---------------------------------------------------------------------------
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from data_quality_engine.phase2 import init_db_session, init_logging, init_rule_resolver
from data_quality_engine.config.settings import SETTINGS
from data_quality_engine.phase2.api import jobs
from data_quality_engine.phase2.api.auth_routes import auth_router
from data_quality_engine.phase2.api.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db_session(environment=os.environ.get("ENVIRONMENT", "development"))
    init_logging()
    init_rule_resolver(config_dir=SETTINGS["rules_config_dir"])
    jobs.configure_executor()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Data Quality Engine API",
        version="2.1.0",
        description=(
            "Phase 2 M4: REST API over the Phase 1 + Phase 2 data quality "
            "pipeline. Sign up, upload any .xlsx/.xls/.xlsm/.csv file, poll "
            "for status, retrieve scores and the AI-enhanced HTML report."
        ),
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    origins_env = os.environ.get("DQE_CORS_ORIGINS", "*")
    origins = [o.strip() for o in origins_env.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,  # auth is a Bearer header, not cookies
        allow_methods=["*"],
        allow_headers=["*"],  # includes Authorization
    )

    app.include_router(auth_router)
    app.include_router(router)
    return app


app = create_app()
