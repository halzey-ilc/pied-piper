"""Input validation and output shaping only; business rules live in the domain."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from rest_framework import serializers

from modules.leads.application.ports import DuplicateCandidate
from modules.leads.domain.lead import MAX_NAME_LENGTH, Lead, LeadSource


class LeadCreateSerializer(serializers.Serializer[dict[str, Any]]):
    name = serializers.CharField(max_length=MAX_NAME_LENGTH)
    # Field name is the JSON key "source"; it shadows DRF's Field.source in the stubs only.
    source = serializers.ChoiceField(  # type: ignore[assignment]
        choices=[s.value for s in LeadSource], default=LeadSource.OTHER.value
    )
    email = serializers.EmailField(required=False, allow_blank=True, max_length=254)
    phone = serializers.CharField(required=False, allow_blank=True, max_length=32)


class LeadUpdateSerializer(serializers.Serializer[dict[str, Any]]):
    name = serializers.CharField(required=False, max_length=MAX_NAME_LENGTH)
    email = serializers.EmailField(required=False, allow_blank=True, max_length=254)
    phone = serializers.CharField(required=False, allow_blank=True, max_length=32)
    version = serializers.IntegerField(required=False, min_value=1)


class LeadDisqualifySerializer(serializers.Serializer[dict[str, Any]]):
    reason = serializers.CharField(max_length=500)


class LeadReadSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    email = serializers.EmailField(read_only=True, allow_null=True)
    phone = serializers.CharField(read_only=True, allow_null=True)
    source = serializers.CharField(read_only=True)  # type: ignore[assignment]
    status = serializers.CharField(read_only=True)
    owner_user_id = serializers.UUIDField(read_only=True)
    disqualify_reason = serializers.CharField(read_only=True, allow_null=True)
    converted_customer_id = serializers.UUIDField(read_only=True, allow_null=True)
    version = serializers.IntegerField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class LeadListSerializer(serializers.Serializer[dict[str, Any]]):
    count = serializers.IntegerField(read_only=True)
    results = LeadReadSerializer(many=True, read_only=True)


class ConvertDealSerializer(serializers.Serializer[dict[str, Any]]):
    title = serializers.CharField(max_length=200)
    amount = serializers.DecimalField(max_digits=16, decimal_places=2, min_value=Decimal(0))
    currency = serializers.CharField(required=False, max_length=3)
    pipeline_id = serializers.UUIDField(required=False)


class LeadConvertSerializer(serializers.Serializer[dict[str, Any]]):
    # Optionally open a deal for the new customer as part of the conversion.
    create_deal = ConvertDealSerializer(required=False)


class ConversionResultSerializer(serializers.Serializer[dict[str, Any]]):
    lead = LeadReadSerializer(read_only=True)
    customer_id = serializers.UUIDField(read_only=True)
    already_converted = serializers.BooleanField(read_only=True)
    # Present only when create_deal was requested.
    deal_id = serializers.UUIDField(read_only=True, allow_null=True)


class DuplicateCandidateSerializer(serializers.Serializer[dict[str, Any]]):
    kind = serializers.CharField(read_only=True)
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    email = serializers.EmailField(read_only=True, allow_null=True)
    phone = serializers.CharField(read_only=True, allow_null=True)
    status = serializers.CharField(read_only=True, allow_null=True)


class DuplicatesListSerializer(serializers.Serializer[dict[str, Any]]):
    count = serializers.IntegerField(read_only=True)
    results = DuplicateCandidateSerializer(many=True, read_only=True)


def duplicate_to_dict(candidate: DuplicateCandidate) -> dict[str, Any]:
    return {
        "kind": candidate.kind,
        "id": str(candidate.id),
        "name": candidate.name,
        "email": candidate.email,
        "phone": candidate.phone,
        "status": candidate.status,
    }


def lead_to_dict(lead: Lead) -> dict[str, Any]:
    return {
        "id": str(lead.id),
        "name": lead.name,
        "email": str(lead.email) if lead.email else None,
        "phone": str(lead.phone) if lead.phone else None,
        "source": lead.source.value,
        "status": lead.status.value,
        "owner_user_id": str(lead.owner_user_id),
        "disqualify_reason": lead.disqualify_reason,
        "converted_customer_id": (
            str(lead.converted_customer_id) if lead.converted_customer_id else None
        ),
        "version": lead.version,
        "created_at": lead.created_at.isoformat(),
    }
