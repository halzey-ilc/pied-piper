"""drf-spectacular glue: document the custom JWT authenticator as bearer auth.

Defining this subclass registers an OpenAPI security scheme for
``FallbackJWTAuthentication`` (otherwise schema generation warns that the
authenticator is unresolved). It reuses the stock SimpleJWT scheme.
"""

from __future__ import annotations

from drf_spectacular.contrib.rest_framework_simplejwt import SimpleJWTScheme


class FallbackJWTScheme(SimpleJWTScheme):  # type: ignore[no-untyped-call]
    target_class = "modules.identity.infrastructure.authentication.FallbackJWTAuthentication"
    name = "jwtAuth"
