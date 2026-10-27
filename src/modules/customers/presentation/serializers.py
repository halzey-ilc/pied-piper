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


def customer_to_dict(customer: Customer) -> dict[str, Any]:
    return {
        "id": str(customer.id),
        "name": customer.name,
        "email": str(customer.email) if customer.email else None,
        "phone": str(customer.phone) if customer.phone else None,
        "owner_user_id": str(customer.owner_user_id),
        "version": customer.version,
        "created_at": customer.created_at.isoformat(),
    }
