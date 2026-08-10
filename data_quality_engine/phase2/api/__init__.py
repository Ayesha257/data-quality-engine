"""
Phase 2 -- M4: REST API.

    data_quality_engine/phase2/api/
    ├── app.py              FastAPI application + lifespan wiring
    ├── routes.py           HTTP endpoints
    ├── jobs.py             Background execution of run_pipeline()
    ├── headless_prompt.py  Non-interactive UserPrompt for server use
    └── schemas.py          Request/response models specific to the HTTP layer

Nothing here duplicates Phase 1 or M1-M3 logic -- this package only
adapts the existing, already-generic pipeline (main.run_pipeline) to
HTTP. See jobs.py and routes.py module docstrings for the reasoning
behind specific choices (thread pool vs. Redis/RQ, generic aggregation
across sheets, etc).
"""

from __future__ import annotations

# Deliberately NOT importing app.py's `app`/`create_app` here. app.py does
# `from data_quality_engine.phase2.api import jobs`, and jobs.py sits in
# this same package -- if this __init__ eagerly imported app.py, that
# would be a circular import (this package -> app.py -> this package)
# that resolves to a *partially initialized* app.py module before its
# app.include_router(router) call has run, silently producing a FastAPI
# app with no routes. Import app.py directly instead:
#
#     from data_quality_engine.phase2.api.app import app
#
# or run `uvicorn data_quality_engine.phase2.api.app:app`.
__all__: list[str] = []
