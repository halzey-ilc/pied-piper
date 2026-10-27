from __future__ import annotations

from typing import cast
from uuid import UUID

from drf_spectacular.utils import OpenApiExample, extend_schema, extend_schema_view
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from modules.organizations.composition import list_memberships_for_user


@extend_schema_view(
    post=extend_schema(
        tags=["Authentication"],
        summary="Log in and get tokens",
        description=(
            "Exchange a username and password for a short-lived `access` token and a "
            "longer-lived `refresh` token. Copy the `access` value and paste it into the "
            "**Authorize** dialog at the top of the page. Rate limited."
        ),
        examples=[
            OpenApiExample(
                "Credentials",
                value={"username": "demo", "password": "your-password"},
                request_only=True,
            )
        ],
    )
)
class TokenObtainView(TokenObtainPairView):
    """Username + password -> access/refresh JWT. Rate limited (scope ``auth``)."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"


@extend_schema_view(
    post=extend_schema(
        tags=["Authentication"],
        summary="Refresh an access token",
        description=(
            "Send a valid `refresh` token to receive a new `access` token (and, with "
            "rotation enabled, a new `refresh` token). Use this when the access token "
            "has expired instead of logging in again."
        ),
        examples=[
            OpenApiExample("Refresh token", value={"refresh": "<refresh token>"}, request_only=True)
        ],
    )
)
class TokenRefreshThrottledView(TokenRefreshView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"


class _MembershipSerializer(serializers.Serializer[dict[str, str]]):
    tenant = serializers.CharField()
    role = serializers.CharField()


class MeSerializer(serializers.Serializer[dict[str, object]]):
    id = serializers.UUIDField()
    username = serializers.CharField()
    memberships = _MembershipSerializer(many=True)


class MeView(APIView):
    """Current user and the tenants they can access (control-plane data only)."""

    @extend_schema(
        tags=["Account"],
        summary="Who am I",
        description=(
            "Returns the signed-in user and the companies (tenants) they belong to, with "
            "the role in each. Use a tenant's `tenant` value as the `X-Tenant` header on "
            "business endpoints. Requires a valid access token."
        ),
        responses=MeSerializer,
        examples=[
            OpenApiExample(
                "Example",
                value={
                    "id": "f6d54480-3330-451e-90ac-6e8c621900c7",
                    "username": "demo",
                    "memberships": [{"tenant": "demo", "role": "owner"}],
                },
                response_only=True,
            )
        ],
    )
    def get(self, request: Request) -> Response:
        user = request.user
        user_id = cast(UUID, user.pk)  # IsAuthenticated guarantees a persisted user
        return Response(
            {
                "id": str(user_id),
                "username": user.get_username(),
                "memberships": list_memberships_for_user(user_id),
            }
        )
