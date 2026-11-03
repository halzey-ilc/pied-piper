"""Input validation and output shaping only; business rules live in the domain."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from modules.customers.domain.customer import MAX_NAME_LENGTH, Customer


class CustomerCreateSerializer(serializers.Serializer[dict[str, Any]]):
    name = serializers.CharField(max_length=MAX_NAME_LENGTH)
    email = serializers.EmailField(required=False, allow_blank=True, max_length=254)
    phone = serializers.CharField(required=False, allow_blank=True, max_length=32)


class CustomerUpdateSerializer(serializers.Serializer[dict[str, Any]]):
    name = serializers.CharField(required=False, max_length=MAX_NAME_LENGTH)
    email = serializers.EmailField(required=False, allow_blank=True, max_length=254)
    phone = serializers.CharField(required=False, allow_blank=True, max_length=32)
    version = serializers.IntegerField(required=False, min_value=1)


class CustomerReadSerializer(serializers.Serializer[dict[str, Any]]):
    """Output shape for API documentation; the views return ``customer_to_dict``."""

    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    email = serializers.EmailField(read_only=True, allow_null=True)
    phone = serializers.CharField(read_only=True, allow_null=True)
    owner_user_id = serializers.UUIDField(read_only=True)
    team_id = serializers.UUIDField(read_only=True, allow_null=True)
    archived_at = serializers.DateTimeField(read_only=True, allow_null=True)
    merged_into_id = serializers.UUIDField(read_only=True, allow_null=True)
    version = serializers.IntegerField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class CustomerAssignSerializer(serializers.Serializer[dict[str, Any]]):
    owner_user_id = serializers.UUIDField()


class CustomerListSerializer(serializers.Serializer[dict[str, Any]]):
    count = serializers.IntegerField(read_only=True)
    results = CustomerReadSerializer(many=True, read_only=True)


def customer_to_dict(customer: Customer) -> dict[str, Any]:
    return {
        "id": str(customer.id),
        "name": customer.name,
        "email": str(customer.email) if customer.email else None,
        "phone": str(customer.phone) if customer.phone else None,
        "owner_user_id": str(customer.owner_user_id),
        "team_id": str(customer.team_id) if customer.team_id else None,
        "archived_at": customer.archived_at.isoformat() if customer.archived_at else None,
        "merged_into_id": str(customer.merged_into_id) if customer.merged_into_id else None,
        "version": customer.version,
        "created_at": customer.created_at.isoformat(),
    }
