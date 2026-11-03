from __future__ import annotations

from typing import Any
from uuid import UUID

from rest_framework import serializers

from modules.organizations.composition import member_display_name
from modules.web_forms.domain.key import (
    MAX_COMMENT_LENGTH,
    MAX_EMAIL_LENGTH,
    MAX_KEY_NAME_LENGTH,
    MAX_NAME_LENGTH,
    MAX_PHONE_LENGTH,
    MAX_URL_LENGTH,
    MAX_UTM_LENGTH,
    IntakeKey,
)

PUBLIC_PATH = "/api/v1/public/leads/{token}/"
HONEYPOT_FIELD = "website"  # hidden in the form; people leave it empty, bots fill it


class KeyCreateSerializer(serializers.Serializer[dict[str, Any]]):
    name = serializers.CharField(max_length=MAX_KEY_NAME_LENGTH)
    department_id = serializers.UUIDField(required=False, allow_null=True, default=None)


class KeyReadSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    department_id = serializers.UUIDField()
    department_name = serializers.CharField(allow_null=True)
    token = serializers.CharField()
    endpoint_path = serializers.CharField()
    is_active = serializers.BooleanField()
    created_by = serializers.UUIDField()
    created_by_name = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField()
    revoked_at = serializers.DateTimeField(allow_null=True)
    last_used_at = serializers.DateTimeField(allow_null=True)
    leads_count = serializers.IntegerField()


class KeyListSerializer(serializers.Serializer[dict[str, Any]]):
    count = serializers.IntegerField()
    results = KeyReadSerializer(many=True)


def _text(max_length: int) -> serializers.CharField:
    return serializers.CharField(
        max_length=max_length, required=False, allow_blank=True, allow_null=True, default=None
    )


class PublicLeadSerializer(serializers.Serializer[dict[str, Any]]):
    name = serializers.CharField(max_length=MAX_NAME_LENGTH)
    phone = _text(MAX_PHONE_LENGTH)
    email = _text(MAX_EMAIL_LENGTH)
    comment = _text(MAX_COMMENT_LENGTH)
    page_url = _text(MAX_URL_LENGTH)
    utm_source = _text(MAX_UTM_LENGTH)
    utm_medium = _text(MAX_UTM_LENGTH)
    utm_campaign = _text(MAX_UTM_LENGTH)
    website = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        default=None,
        help_text="Honeypot: leave empty (hide the input). A filled value is ignored silently.",
    )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not (attrs.get("phone") or "").strip() and not (attrs.get("email") or "").strip():
            raise serializers.ValidationError({"phone": ["Укажите телефон или e-mail."]})
        return attrs


class PublicLeadAcceptedSerializer(serializers.Serializer[dict[str, Any]]):
    status = serializers.CharField()


def key_to_dict(
    key: IntakeKey, departments: dict[UUID, str], names: dict[UUID, str | None]
) -> dict[str, Any]:
    return {
        "id": str(key.id),
        "name": key.name,
        "department_id": str(key.department_id),
        "department_name": departments.get(key.department_id),
        "token": key.token,
        "endpoint_path": PUBLIC_PATH.format(token=key.token),
        "is_active": key.is_active,
        "created_by": str(key.created_by),
        "created_by_name": names.get(key.created_by),
        "created_at": key.created_at.isoformat(),
        "revoked_at": key.revoked_at.isoformat() if key.revoked_at else None,
        "last_used_at": key.last_used_at.isoformat() if key.last_used_at else None,
        "leads_count": key.leads_count,
    }


def creator_names(tenant_id: UUID, keys: list[IntakeKey]) -> dict[UUID, str | None]:
    return {uid: member_display_name(tenant_id, uid) for uid in {k.created_by for k in keys}}
