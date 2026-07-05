"""Application entry point — the FastAPI app factory.

Every response uses the one envelope: ``{"data": ..., "meta": ..., "errors": ...}``
(``mentorapp.api.envelope``, its one canonical home). Failures stay inside it
via the handlers registered here.
"""

from __future__ import annotations

from fastapi import FastAPI

from mentorapp import __version__
from mentorapp.api.envelope import Envelope, ok
from mentorapp.api.errors import register_error_handlers
from mentorapp.api.routers.auth import router as auth_router
from mentorapp.api.routers.preferences import router as preferences_router
from mentorapp.api.routers.schema import router as schema_router
from mentorapp.observability import get_logger

log = get_logger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(title="CBM Mentoring Application", version=__version__)
    register_error_handlers(app)
    app.include_router(auth_router)
    app.include_router(schema_router)
    app.include_router(preferences_router)

    @app.get("/healthz")
    def healthz() -> Envelope:
        return ok(data={"status": "ok", "version": __version__})

    log.info("application created", extra={"context": {"version": __version__}})
    return app


app = create_app()
