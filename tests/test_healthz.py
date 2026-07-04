"""The walking-skeleton gate: the app starts and speaks the envelope."""

from fastapi.testclient import TestClient

from mentorapp import __version__
from mentorapp.main import create_app


def test_healthz_returns_envelope() -> None:
    client = TestClient(create_app())
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"data", "meta", "errors"}
    assert body["data"]["status"] == "ok"
    assert body["data"]["version"] == __version__
    assert body["errors"] is None
