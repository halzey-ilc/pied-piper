from __future__ import annotations

from typing import cast
from uuid import UUID

from drf_spectacular.utils import OpenApiExample, extend_schema, extend_schema_view
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from modules.identity.infrastructure.mfa import MfaService
from modules.identity.infrastructure.oidc import OidcError, verifier_from_settings
from modules.identity.infrastructure.password_reset import PasswordResetService
from modules.identity.presentation.auth_errors import OidcLoginFailed, OidcNoAccount
from modules.identity.presentation.serializers import (
    EnrolmentResponseSerializer,
    HardenedTokenObtainPairSerializer,
    MfaConfirmSerializer,
    MfaDisableSerializer,
    OidcLoginSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RecoveryCodesResponseSerializer,
    TokenPairResponseSerializer,
)
from modules.organizations.composition import list_memberships_for_user
from shared.domain.errors import DomainValidationError

_EXAMPLE_PASSWORD = "your-password"  # noqa: S105  # nosec B105 - OpenAPI doc placeholder


@extend_schema_view(
    post=extend_schema(
        tags=["Authentication"],
        summary="Log in and get tokens",
        description=(
            "Exchange a username and password for a short-lived `access` token and a "
            "longer-lived `refresh` token. Copy the `access` value and paste it into the "
            "**Authorize** dialog at the top of the page. Rate limited.\n\n"
            "If multi-factor authentication is enabled on the account, the first call "
            "returns `401 mfa_required`; resend the same request with an `otp` field "
            "holding the 6-digit code from the authenticator app (or a recovery code). "
            "After too many failed attempts the account is temporarily locked "
            "(`429 account_locked`)."
        ),
        examples=[
            OpenApiExample(
                "Credentials",
                value={"username": "demo", "password": _EXAMPLE_PASSWORD},
                request_only=True,
            ),
            OpenApiExample(
                "Credentials with MFA code",
                value={"username": "demo", "password": _EXAMPLE_PASSWORD, "otp": "123456"},
                request_only=True,
            ),
        ],
    )
)
class TokenObtainView(TokenObtainPairView):
    """Username + password -> access/refresh JWT, with lockout and MFA (scope ``auth``)."""

    serializer_class = HardenedTokenObtainPairSerializer
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


class MfaEnrollView(APIView):
    """Start TOTP enrolment: returns a secret and an otpauth URI to scan, once."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    @extend_schema(
        tags=["Account"],
        summary="Begin MFA enrolment",
        description=(
            "Generates a new TOTP secret for the signed-in user and returns it with an "
            "`otpauth://` URI to add to an authenticator app. The device is not active "
            "until confirmed. Calling this again replaces any unconfirmed secret."
        ),
        request=None,
        responses=EnrolmentResponseSerializer,
    )
    def post(self, request: Request) -> Response:
        enrolment = MfaService().begin_enrolment(
            user_id=cast(UUID, request.user.pk), account_name=request.user.get_username()
        )
        return Response({"secret": enrolment.secret, "otpauth_uri": enrolment.provisioning_uri})


class MfaConfirmView(APIView):
    """Confirm enrolment with a code; returns one-time recovery codes."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    @extend_schema(
        tags=["Account"],
        summary="Confirm MFA enrolment",
        description=(
            "Submit the current 6-digit code from your authenticator app to activate MFA. "
            "The response contains recovery codes shown only once; store them safely."
        ),
        request=MfaConfirmSerializer,
        responses=RecoveryCodesResponseSerializer,
    )
    def post(self, request: Request) -> Response:
        payload = MfaConfirmSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        codes = MfaService().confirm(
            user_id=cast(UUID, request.user.pk), code=payload.validated_data["code"]
        )
        if codes is None:
            raise DomainValidationError("No pending enrolment, or the code is invalid.")
        return Response({"recovery_codes": codes})


class MfaDisableView(APIView):
    """Disable MFA for the signed-in user (requires the current password)."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    @extend_schema(
        tags=["Account"],
        summary="Disable MFA",
        request=MfaDisableSerializer,
        responses={204: None},
    )
    def post(self, request: Request) -> Response:
        payload = MfaDisableSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        if not request.user.check_password(payload.validated_data["password"]):
            raise DomainValidationError("Password is incorrect.")
        MfaService().disable(user_id=cast(UUID, request.user.pk))
        return Response(status=status.HTTP_204_NO_CONTENT)


class OidcLoginView(APIView):
    """Exchange an OIDC ID token from the configured IdP for CRM tokens."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    @extend_schema(
        tags=["Authentication"],
        summary="Log in with OpenID Connect",
        description=(
            "Verifies an ID token issued by the configured identity provider and, on "
            "success, returns CRM `access`/`refresh` tokens. The user must already exist "
            "(matched by verified email) unless auto-provisioning is enabled."
        ),
        request=OidcLoginSerializer,
        responses=TokenPairResponseSerializer,
    )
    def post(self, request: Request) -> Response:
        from django.conf import settings
        from django.contrib.auth import get_user_model

        payload = OidcLoginSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            identity = verifier_from_settings().verify(payload.validated_data["id_token"])
        except OidcError as exc:
            raise OidcLoginFailed(str(exc)) from exc

        user_model = get_user_model()
        user = user_model.objects.filter(email__iexact=identity.email).first()
        if user is None:
            if not settings.OIDC["AUTO_PROVISION"]:
                raise OidcNoAccount()
            user = user_model.objects.create_user(username=identity.email, email=identity.email)
            user.set_unusable_password()
            user.save(update_fields=["password"])
        refresh = RefreshToken.for_user(user)
        return Response({"access": str(refresh.access_token), "refresh": str(refresh)})


class _DetailSerializer(serializers.Serializer[dict[str, str]]):
    detail = serializers.CharField()


class PasswordResetRequestView(APIView):
    """Request a password-reset email. Always succeeds (no account enumeration)."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    @extend_schema(
        tags=["Authentication"],
        summary="Request a password reset",
        description=(
            "Sends a password-reset link to the address if an active account has it. The "
            "response is the same whether or not the email is registered, so it never "
            "reveals which addresses exist."
        ),
        request=PasswordResetRequestSerializer,
        responses=_DetailSerializer,
    )
    def post(self, request: Request) -> Response:
        payload = PasswordResetRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        PasswordResetService().request_reset(payload.validated_data["email"])
        return Response({"detail": "If the email is registered, a reset link has been sent."})


class PasswordResetConfirmView(APIView):
    """Set a new password using a token from the reset email."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    @extend_schema(
        tags=["Authentication"],
        summary="Confirm a password reset",
        description=(
            "Validates the reset token and sets the new password (subject to the password "
            "policy). The token is single-use and expires; completing a reset also unlocks "
            "the account."
        ),
        request=PasswordResetConfirmSerializer,
        responses=_DetailSerializer,
    )
    def post(self, request: Request) -> Response:
        payload = PasswordResetConfirmSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        PasswordResetService().confirm_reset(
            token=payload.validated_data["token"],
            new_password=payload.validated_data["new_password"],
        )
        return Response({"detail": "Your password has been reset."})
