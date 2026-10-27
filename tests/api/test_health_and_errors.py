"""Health endpoints (item 2) and safe error handling (item 7)."""

from __future__ import annotations

import pytest
from django.test import Client


def test_liveness_needs_no_dependencies() -> None:
    response = Client().get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response["X-Request-ID"]
    assert response["Cache-Control"].startswith("max-age=0")


@pytest.mark.django_db
def test_readiness_reports_dependencies() -> None:
    response = Client().get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "cache": "ok"}}


def test_readiness_returns_503_when_a_dependency_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    from platform_core.infrastructure import health

    monkeypatch.setattr(
        health, "probe_database", lambda alias="default": health.ProbeResult("database", False)
    )
    response = Client().get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "unavailable"


@pytest.mark.urls("tests.api.urls_errors")
def test_unexpected_exception_returns_generic_500_without_details() -> None:
    response = Client(raise_request_exception=False).get("/boom/")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "internal_error"
    assert body["error"]["request_id"]
    text = response.content.decode()
    assert "hunter2" not in text and "Traceback" not in text and "RuntimeError" not in text


def test_unknown_url_is_json_404() -> None:
    response = Client().get("/definitely-not-here")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_unauthenticated_api_call_is_401_json() -> None:
    response = Client().get("/api/v1/customers/")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


def test_request_id_header_is_sanitised() -> None:
    response = Client().get("/health/live", HTTP_X_REQUEST_ID="evil\r\nSet-Cookie: x")
    assert "evil" not in response["X-Request-ID"]
    good = Client().get("/health/live", HTTP_X_REQUEST_ID="client-req-0001")
    assert good["X-Request-ID"] == "client-req-0001"


def test_security_headers_present() -> None:
    response = Client().get("/health/live")
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["X-Frame-Options"] == "DENY"
    assert response["Referrer-Policy"] == "same-origin"


def test_metrics_endpoint_exposes_prometheus_format() -> None:
    Client().get("/health/live")
    response = Client().get("/metrics")
    assert response.status_code == 200
    assert b"crm_http_requests_total" in response.content
