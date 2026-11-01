"""Uniform, safe error responses.

Format: ``{"error": {"code": str, "message": str, "details"?: ..., "request_id": str}}``.
Unexpected exceptions return a generic 500 without stack traces or exception text;
the full exception is logged server-side (with redaction).
"""

from __future__ import annotations

import logging
from typing import Any

from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, JsonResponse
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from platform_core.application.context import current_request_id, current_tenant
from platform_core.domain.tenancy import TenantNotFoundError
from platform_core.infrastructure.tenancy.services import error_recorder
from shared.domain.errors import (
    AccessDeniedError,
    ConflictError,
    DomainError,
    DomainValidationError,
    NotFoundError,
)

logger = logging.getLogger(__name__)

_DOMAIN_STATUS: tuple[tuple[type[DomainError], int], ...] = (
    (TenantNotFoundError, status.HTTP_404_NOT_FOUND),
    (DomainValidationError, status.HTTP_400_BAD_REQUEST),
    (NotFoundError, status.HTTP_404_NOT_FOUND),
    (AccessDeniedError, status.HTTP_403_FORBIDDEN),
    (ConflictError, status.HTTP_409_CONFLICT),
)


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    error["request_id"] = current_request_id()
    return {"error": error}


def api_exception_handler(exc: Exception, context: dict[str, Any]) -> Response:
    for error_type, http_status in _DOMAIN_STATUS:
        if isinstance(exc, error_type):
            return Response(error_body(exc.code, str(exc)), status=http_status)

    response = drf_exception_handler(exc, context)
    if response is not None:
        if isinstance(exc, exceptions.APIException):
            code = exc.default_code if isinstance(exc.default_code, str) else "error"
            if isinstance(exc, exceptions.ValidationError):
                body = error_body("validation_error", "Invalid input.", response.data)
            else:
                detail = response.data.get("detail") if isinstance(response.data, dict) else None
                body = error_body(code, str(detail or exc.default_detail))
        elif isinstance(exc, Http404):
            body = error_body("not_found", "Not found.")
        else:
            body = error_body("permission_denied", "Permission denied.")
        response.data = body
        return response

    logger.exception(
        "unhandled_api_exception",
        extra={"view": type(context.get("view")).__name__, "exc_type": type(exc).__name__},
    )
    _record_error(exc, context)
    return Response(
        error_body("internal_error", "An internal error occurred."),
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def _record_error(exc: Exception, context: dict[str, Any]) -> None:
    """Leave a record for the platform administrator; never fails the response."""
    recorder = error_recorder()
    if recorder is None:
        return
    view = context.get("view")
    request = context.get("request")
    match = getattr(request, "resolver_match", None)
    tenant = current_tenant()
    try:
        recorder.record(
            component=type(view).__module__.split(".presentation")[0] if view else "unknown",
            view=type(view).__name__ if view else "unknown",
            method=getattr(request, "method", "") or "",
            route=(match.route if match is not None and match.route else "unmatched"),
            exception_type=type(exc).__name__,
            request_id=current_request_id(),
            tenant_slug=tenant.slug if tenant else None,
        )
    except Exception:  # recording is best effort: the client still gets a clean 500
        logger.warning("error_record_failed", exc_info=True)


# Django-level handlers for non-DRF views (configured in config/urls.py).


def bad_request(request: HttpRequest, exception: Exception) -> JsonResponse:
    return JsonResponse(error_body("bad_request", "Bad request."), status=400)


def permission_denied(request: HttpRequest, exception: Exception) -> JsonResponse:
    return JsonResponse(error_body("permission_denied", "Permission denied."), status=403)


def not_found(request: HttpRequest, exception: Exception) -> JsonResponse:
    return JsonResponse(error_body("not_found", "Not found."), status=404)


def server_error(request: HttpRequest) -> JsonResponse:
    return JsonResponse(error_body("internal_error", "An internal error occurred."), status=500)


__all__ = [
    "PermissionDenied",
    "api_exception_handler",
    "bad_request",
    "not_found",
    "permission_denied",
    "server_error",
]
