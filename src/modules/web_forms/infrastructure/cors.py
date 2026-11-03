"""CORS for the public lead endpoint only.

Every other API path keeps the explicit ``CORS_ALLOWED_ORIGINS`` list. A company's
website may live on any origin, so the public lead path answers any origin — without
credentials (``CORS_ALLOW_CREDENTIALS`` stays False), and the endpoint reads no cookies
or tokens.
"""

from __future__ import annotations

from typing import Any

from corsheaders.signals import check_request_enabled

PUBLIC_LEADS_PREFIX = "/api/v1/public/leads/"


def _allow(sender: Any, request: Any, **kwargs: Any) -> bool:
    return str(getattr(request, "path", "")).startswith(PUBLIC_LEADS_PREFIX)


def connect_cors() -> None:
    check_request_enabled.connect(_allow, dispatch_uid="web_forms_public_leads_cors")
