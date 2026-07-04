"""Application entry point — the FastAPI app factory.

Every response uses the one envelope: ``{"data": ..., "meta": ..., "errors": ...}``.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from mentorapp import __version__
from mentorapp.observability import get_logger

log = get_logger(__name__)


def envelope(
    data: Any = None,
    meta: dict[str, Any] | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The single response envelope every endpoint speaks."""
    return {"data": data, "meta": meta or {}, "errors": errors}


def create_app() -> FastAPI:
    app = FastAPI(title="CBM Mentoring Application", version=__version__)

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return envelope(data={"status": "ok", "version": __version__})

    log.info("application created", extra={"context": {"version": __version__}})
    return app


app = create_app()
