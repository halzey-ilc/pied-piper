"""Downloads files sent in chats.

The URL comes from a webhook, so it is checked before the server fetches it: only
https, only the provider's storage hosts, and no redirects.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

import httpx

from modules.communications.domain.conversation import MAX_ATTACHMENT_BYTES
from modules.integrations.infrastructure.http import build_http_client
from shared.application.retry import TransientError

logger = logging.getLogger(__name__)

ALLOWED_HOST_SUFFIXES = (".digitaloceanspaces.com", ".greenapi.com", ".green-api.com")
_RETRYABLE_STATUS = 429


class HttpMediaFetcher:
    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport

    def fetch(self, url: str) -> bytes | None:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.scheme != "https" or not host.endswith(ALLOWED_HOST_SUFFIXES):
            logger.warning("media_host_refused", extra={"host": host})
            return None
        try:
            with (
                build_http_client(f"https://{host}", transport=self._transport) as client,
                client.stream("GET", url) as response,
            ):
                if response.is_server_error or response.status_code == _RETRYABLE_STATUS:
                    raise TransientError(f"Media host answered {response.status_code}.")
                if not response.is_success:
                    logger.warning("media_unavailable", extra={"status": response.status_code})
                    return None
                return _read_limited(response)
        except httpx.HTTPError as exc:
            raise TransientError("Media download failed.") from exc


def _read_limited(response: httpx.Response) -> bytes | None:
    body = bytearray()
    for chunk in response.iter_bytes():
        body += chunk
        if len(body) > MAX_ATTACHMENT_BYTES:
            logger.warning("media_too_large", extra={"limit": MAX_ATTACHMENT_BYTES})
            return None
    return bytes(body)
