"""The production Espo HTTP transport (WTK-010): request shape, decoding, outcomes.

Exercises :class:`HttpxEspoTransport` over ``httpx.MockTransport`` — the
transport contract (answer for any status, raise only on no answer, JSON or
``None`` payload), the ``api/v1`` base joining, and the env-driven deployment
construction — plus the gateway's containment of a real network failure.
"""

from __future__ import annotations

import json as jsonlib

import httpx
import pytest

from mentorapp.crm import CrmUnavailableError, EspoAuthGateway, espo_gateway_from_env
from mentorapp.crm.http import HttpxEspoTransport


class RecordingHandler:
    """MockTransport handler: one canned httpx answer, every request recorded."""

    def __init__(self, response: httpx.Response | Exception) -> None:
        self._response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _transport(
    handler: RecordingHandler, site_url: str = "https://crm.example.org"
) -> HttpxEspoTransport:
    return HttpxEspoTransport(site_url, transport=httpx.MockTransport(handler))


def test_requests_land_on_the_sites_api_v1_base_with_everything_forwarded() -> None:
    handler = RecordingHandler(httpx.Response(200, json={"ok": True}))
    # Trailing slash on the site URL must not double up or drop path segments.
    transport = _transport(handler, site_url="https://crm.example.org/")

    answer = transport.send(
        "POST",
        "User/passwordChangeRequest",
        headers={"Espo-Authorization": "abc"},
        params={"select": "id"},
        json={"userName": "mentor"},
    )

    request = handler.requests[0]
    assert str(request.url) == (
        "https://crm.example.org/api/v1/User/passwordChangeRequest?select=id"
    )
    assert request.method == "POST"
    assert request.headers["Espo-Authorization"] == "abc"
    assert jsonlib.loads(request.read()) == {"userName": "mentor"}
    assert answer.status_code == 200
    assert answer.payload == {"ok": True}


def test_any_http_status_comes_back_as_a_response_not_a_raise() -> None:
    transport = _transport(RecordingHandler(httpx.Response(401, json={"message": "no"})))

    answer = transport.send("GET", "App/user", headers={})

    assert answer.status_code == 401
    assert answer.payload == {"message": "no"}


def test_a_body_that_is_not_json_decodes_to_a_none_payload() -> None:
    transport = _transport(RecordingHandler(httpx.Response(502, text="<html>bad gateway")))

    answer = transport.send("GET", "App/user", headers={})

    assert answer.status_code == 502
    assert answer.payload is None


def test_a_network_failure_raises_and_the_gateway_maps_it_to_unavailable() -> None:
    handler = RecordingHandler(httpx.ConnectError("connection refused"))
    transport = _transport(handler)

    with pytest.raises(httpx.ConnectError):
        transport.send("GET", "App/user", headers={})

    with pytest.raises(CrmUnavailableError):
        EspoAuthGateway(transport).verify("mentor@cbm.org", "correct horse")


def test_espo_gateway_from_env_fails_loud_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MENTORAPP_ESPO_URL", raising=False)

    with pytest.raises(RuntimeError, match="MENTORAPP_ESPO_URL"):
        espo_gateway_from_env()


def test_espo_gateway_from_env_builds_the_deployment_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MENTORAPP_ESPO_URL", "https://crm.example.org")

    assert isinstance(espo_gateway_from_env(), EspoAuthGateway)
