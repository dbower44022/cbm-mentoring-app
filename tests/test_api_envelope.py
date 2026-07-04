"""The envelope + structured-error contract (REQ-059): one shape, all failures
in one round trip, recovery bodies on 409, opaque logged 500s.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from mentorapp.api.envelope import Envelope, field_error, ok, request_error
from mentorapp.api.errors import (
    CODE_DUPLICATE_CANDIDATES,
    CODE_INTERNAL,
    CODE_NOT_FOUND,
    CODE_STALE_ROW_VERSION,
    ApiValidationError,
    DuplicateCandidatesError,
    RecordNotFoundError,
    StaleRowVersionError,
    register_error_handlers,
)


def test_ok_envelope_shape() -> None:
    body = ok(data={"x": 1}, meta={"count": 1})
    assert body == {"data": {"x": 1}, "meta": {"count": 1}, "errors": None}
    assert ok() == {"data": None, "meta": {}, "errors": None}


def test_error_entry_shapes() -> None:
    assert field_error("mentorName", "required", "Name is required.") == {
        "fieldName": "mentorName",
        "code": "required",
        "message": "Name is required.",
    }
    assert request_error("notFound", "gone")["fieldName"] is None


class _CreateBody(BaseModel):
    mentor_name: str
    mentor_email: str


def _app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/boom/validation")
    def boom_validation() -> Envelope:
        raise ApiValidationError(
            [
                field_error("mentorName", "required", "Name is required."),
                field_error("mentorEmail", "invalidEmail", "Not a valid email."),
            ]
        )

    @app.post("/boom/create")
    def boom_create(body: _CreateBody) -> Envelope:
        return ok(data=body.model_dump())

    @app.patch("/boom/stale")
    def boom_stale() -> Envelope:
        raise StaleRowVersionError({"mentorID": "abc", "rowVersion": 7})

    @app.post("/boom/duplicate")
    def boom_duplicate() -> Envelope:
        raise DuplicateCandidatesError([{"mentorID": "abc"}, {"mentorID": "def"}])

    @app.get("/boom/missing")
    def boom_missing() -> Envelope:
        raise RecordNotFoundError("mentor", "abc")

    @app.get("/boom/unhandled")
    def boom_unhandled() -> Envelope:
        raise RuntimeError("secret internals")

    return app


def _client() -> TestClient:
    return TestClient(_app(), raise_server_exceptions=False)


def test_validation_reports_all_failures_in_one_round_trip() -> None:
    resp = _client().post("/boom/validation")
    assert resp.status_code == 422
    body = resp.json()
    assert set(body) == {"data", "meta", "errors"}
    assert [e["fieldName"] for e in body["errors"]] == ["mentorName", "mentorEmail"]
    assert body["errors"][1]["code"] == "invalidEmail"


def test_request_validation_speaks_the_same_per_field_shape() -> None:
    resp = _client().post("/boom/create", json={})
    assert resp.status_code == 422
    errors = resp.json()["errors"]
    assert {e["fieldName"] for e in errors} == {"mentor_name", "mentor_email"}
    assert all(e["code"] and e["message"] for e in errors)


def test_stale_row_version_is_409_with_current_record_in_data() -> None:
    resp = _client().patch("/boom/stale")
    assert resp.status_code == 409
    body = resp.json()
    assert body["data"] == {"mentorID": "abc", "rowVersion": 7}
    assert body["errors"][0]["code"] == CODE_STALE_ROW_VERSION


def test_duplicate_create_is_409_with_candidates_in_data() -> None:
    resp = _client().post("/boom/duplicate")
    assert resp.status_code == 409
    body = resp.json()
    assert [c["mentorID"] for c in body["data"]] == ["abc", "def"]
    assert body["errors"][0]["code"] == CODE_DUPLICATE_CANDIDATES


def test_not_found_is_404_in_the_envelope() -> None:
    resp = _client().get("/boom/missing")
    assert resp.status_code == 404
    body = resp.json()
    assert body["data"] is None
    assert body["errors"][0]["code"] == CODE_NOT_FOUND


def test_unhandled_exception_is_opaque_500_in_the_envelope() -> None:
    resp = _client().get("/boom/unhandled")
    assert resp.status_code == 500
    body = resp.json()
    assert body["errors"][0]["code"] == CODE_INTERNAL
    assert "secret internals" not in resp.text
