"""HTTP client for the CRM AI Platform gateway (contracts ai.job.v1 / ai.error.v1).

Each call carries a fresh HS256 token naming exactly one tenant and the user
on whose behalf the CRM acts. The key is shared only with the AI Platform and
is distinct from the CRM's own JWT signing key.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt

from modules.ai_insights.application.ports import AiJobState
from platform_core.application.context import current_correlation_id
from shared.application.retry import PermanentError, TransientError

_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class HttpAiPlatformClient:
    def __init__(
        self,
        *,
        base_url: str,
        token_key: str,
        issuer: str,
        audience: str,
        token_ttl_seconds: int = 300,
        connect_timeout: float = 3.0,
        read_timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._key = token_key
        self._issuer = issuer
        self._audience = audience
        self._ttl = token_ttl_seconds
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
            transport=transport,
            follow_redirects=False,
        )

    def token(self, *, tenant_slug: str, subject: str, scopes: tuple[str, ...]) -> str:
        now = datetime.now(UTC)
        claims = {
            "iss": self._issuer,
            "aud": self._audience,
            "sub": subject,
            "tenant": tenant_slug,
            "scope": " ".join(scopes),
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(seconds=self._ttl),
            "jti": str(uuid.uuid4()),
        }
        return jwt.encode(claims, self._key, algorithm="HS256")

    def submit(
        self,
        *,
        tenant_slug: str,
        subject: str,
        job_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> AiJobState:
        response = self._send(
            "POST",
            "/v1/jobs",
            tenant_slug=tenant_slug,
            subject=subject,
            scopes=("ai.jobs.create",),
            json={"type": job_type, "input": payload},
            headers={"Idempotency-Key": idempotency_key},
        )
        return _job_state(response)

    def fetch(self, *, tenant_slug: str, subject: str, job_id: str) -> AiJobState:
        response = self._send(
            "GET",
            f"/v1/jobs/{job_id}",
            tenant_slug=tenant_slug,
            subject=subject,
            scopes=("ai.jobs.read",),
        )
        return _job_state(response)

    def _send(
        self,
        method: str,
        path: str,
        *,
        tenant_slug: str,
        subject: str,
        scopes: tuple[str, ...],
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        token = self.token(tenant_slug=tenant_slug, subject=subject, scopes=scopes)
        request_headers = {
            "Authorization": f"Bearer {token}",
            "X-Tenant": tenant_slug,
            **(headers or {}),
        }
        correlation_id = current_correlation_id()
        if correlation_id:
            request_headers["X-Correlation-ID"] = correlation_id
        try:
            response = self._http.request(method, path, json=json, headers=request_headers)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise TransientError(f"AI Platform unreachable: {type(exc).__name__}") from exc
        if response.status_code in _RETRYABLE_STATUS:
            raise TransientError(f"AI Platform returned {response.status_code}")
        if response.status_code >= 400:
            raise PermanentError(f"AI Platform rejected the request: {_error_code(response)}")
        return response


def _error_code(response: httpx.Response) -> str:
    try:
        return str(response.json()["error"]["code"])
    except (ValueError, KeyError, TypeError):
        return str(response.status_code)


def _job_state(response: httpx.Response) -> AiJobState:
    try:
        body = response.json()
        if body.get("schema") != "ai.job.v1":
            raise ValueError("unexpected schema")
        result = body.get("result") or {}
        error = body.get("error") or {}
        output = result.get("output")
        return AiJobState(
            job_id=str(body["id"]),
            status=str(body["status"]),
            error_code=error.get("code"),
            schema_id=result.get("schema_id"),
            provider=result.get("provider"),
            model=result.get("model"),
            output=output if isinstance(output, dict) else None,
        )
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise PermanentError("AI Platform response does not match ai.job.v1") from exc
