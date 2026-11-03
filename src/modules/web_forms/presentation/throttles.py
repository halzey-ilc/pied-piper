"""Rate limits of the public lead endpoint (DRF throttling, rates in settings).

Two independent limits: per client IP (one visitor or bot cannot flood any form) and per
key (one leaked form cannot flood a company from many IPs). The key part of the cache
key is a hash of the token, so tokens never appear in cache keys.
"""

from __future__ import annotations

import hashlib
from typing import Any

from rest_framework.request import Request
from rest_framework.throttling import SimpleRateThrottle


class WebFormIpThrottle(SimpleRateThrottle):
    scope = "web_form_ip"

    def get_cache_key(self, request: Request, view: Any) -> str | None:
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class WebFormTokenThrottle(SimpleRateThrottle):
    scope = "web_form_token"

    def get_cache_key(self, request: Request, view: Any) -> str | None:
        token = str(getattr(view, "kwargs", {}).get("token", ""))
        digest = hashlib.sha256(token.encode()).hexdigest()[:32]
        return self.cache_format % {"scope": self.scope, "ident": digest}
