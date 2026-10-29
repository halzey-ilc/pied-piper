"""DRF base view for tenant-scoped endpoints.

Isolation is layered; this view is only the first layer:

1. The caller must be authenticated (JWT) and a member of the tenant named in
   the ``X-Tenant`` header; otherwise 404 (no tenant enumeration).
2. The tenant context routes tenant-plane models to that tenant's own database;
   with no context the router raises instead of falling back.
3. Use cases enforce RBAC (role + ownership scope) on every operation.
"""

from __future__ import annotations

from contextvars import Token
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponseBase
from rest_framework.request import Request
from rest_framework.views import APIView

from platform_core.application.context import TenantContext, activate_tenant, deactivate_tenant
from platform_core.application.ports import TenantMembership
from platform_core.domain.tenancy import TenantNotFoundError
from platform_core.infrastructure.tenancy.services import connection_registry, tenant_resolver
from shared.domain.errors import AccessDeniedError


class TenantScopedAPIView(APIView):
    tenant: TenantContext
    membership: TenantMembership
    _tenant_token: Token[TenantContext | None] | None = None

    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        super().initial(request, *args, **kwargs)  # authentication, permissions, throttling
        user_id = getattr(request.user, "id", None)
        if user_id is None:
            raise TenantNotFoundError("Tenant not found.")
        if getattr(request.user, "must_change_password", False):
            # Signed in with a temporary password: only the password change is allowed.
            raise AccessDeniedError("Change your temporary password first.")
        header = settings.TENANCY["HEADER"]
        resolved = tenant_resolver().resolve_for_user(request.headers.get(header), user_id)
        if resolved.membership is None:
            raise TenantNotFoundError("Tenant not found.")
        self.tenant = resolved.context
        self.membership = resolved.membership
        self._tenant_token = activate_tenant(resolved.context)

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        try:
            return super().dispatch(request, *args, **kwargs)
        finally:
            if self._tenant_token is not None:
                deactivate_tenant(self._tenant_token)
                self._tenant_token = None
                connection_registry().release(self.tenant.db_alias)
