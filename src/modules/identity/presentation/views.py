from __future__ import annotations

from typing import cast
from uuid import UUID

from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from modules.organizations.composition import list_memberships_for_user


class TokenObtainView(TokenObtainPairView):
    """Username + password -> access/refresh JWT. Rate limited (scope ``auth``)."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"


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

    @extend_schema(responses=MeSerializer)
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
