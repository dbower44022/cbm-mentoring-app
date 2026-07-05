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
from mentorapp.api.routers.grids import router as grids_router
from mentorapp.api.routers.home import router as home_router
from mentorapp.api.routers.preferences import router as preferences_router
from mentorapp.api.routers.records import router as records_router
from mentorapp.api.routers.schema import router as schema_router
from mentorapp.api.routers.shell import router as shell_router
from mentorapp.api.wiring import install_auth_wiring, install_home_wiring
from mentorapp.observability import get_logger

log = get_logger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(title="CBM Mentoring Application", version=__version__)
    register_error_handlers(app)
    # Production auth backends (WTK-191): DB-backed stores + the Espo
    # verifier. Config is read per request and fails loudly when absent, so
    # binding here keeps app creation environment-free; tests re-override the
    # same provider keys.
    install_auth_wiring(app)
    # Stored admin-message persistence (WTK-192) behind the home router's
    # message seams; the catalog seam stays unwired until WTK-025 lands.
    install_home_wiring(app)
    app.include_router(auth_router)
    app.include_router(schema_router)
    app.include_router(preferences_router)
    app.include_router(home_router)
    app.include_router(records_router)
    app.include_router(grids_router)
    app.include_router(shell_router)

    @app.get("/healthz")
    def healthz() -> Envelope:
        return ok(data={"status": "ok", "version": __version__})

    log.info("application created", extra={"context": {"version": __version__}})
    return app


app = create_app()
