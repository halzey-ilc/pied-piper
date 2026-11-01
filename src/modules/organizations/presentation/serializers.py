from __future__ import annotations

from typing import Any

from rest_framework import serializers

from modules.organizations.application.ports import (
    Company,
    DepartmentInfo,
    StaffMember,
    StaffRequestInfo,
)


class CompanySerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField(read_only=True)
    slug = serializers.CharField(read_only=True)
    name = serializers.CharField(max_length=200, required=False)
    country = serializers.CharField(max_length=2, required=False, help_text="ISO 3166-1, e.g. KG")
    currency = serializers.CharField(max_length=3, required=False, help_text="ISO 4217, e.g. KGS")
    timezone = serializers.CharField(
        max_length=64, required=False, help_text="IANA name, e.g. Asia/Bishkek"
    )


class DepartmentSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(max_length=100)
    is_active = serializers.BooleanField(required=False)


class StaffNameSerializer(serializers.Serializer[dict[str, Any]]):
    user_id = serializers.UUIDField(read_only=True)
    full_name = serializers.CharField(read_only=True)


class StaffSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField(read_only=True, help_text="Membership id")
    user_id = serializers.UUIDField(read_only=True)
    login = serializers.CharField(read_only=True)
    full_name = serializers.CharField(read_only=True)
    email = serializers.EmailField(read_only=True, allow_null=True)
    phone = serializers.CharField(read_only=True)
    role = serializers.CharField(read_only=True)
    product_role = serializers.CharField(read_only=True)
    department = DepartmentSerializer(read_only=True, allow_null=True)
    status = serializers.CharField(read_only=True, help_text="active | invited | deactivated")
    last_login = serializers.DateTimeField(read_only=True, allow_null=True)
    created_by = serializers.UUIDField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)


class StaffCreateSerializer(serializers.Serializer[dict[str, Any]]):
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    role = serializers.CharField(
        help_text="director | head_of_sales | sales_rep | lead_caller | operations | viewer"
    )
    login = serializers.CharField(max_length=150, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(max_length=16, required=False, allow_blank=True, default="")
    department_id = serializers.UUIDField(required=False, allow_null=True)


class StaffUpdateSerializer(serializers.Serializer[dict[str, Any]]):
    role = serializers.CharField(required=False)
    department_id = serializers.UUIDField(required=False, allow_null=True)


class StaffCreatedSerializer(serializers.Serializer[dict[str, Any]]):
    member = StaffSerializer()
    temporary_password = serializers.CharField(help_text="Shown once; must be changed at sign-in")


class TemporaryPasswordSerializer(serializers.Serializer[dict[str, Any]]):
    temporary_password = serializers.CharField()


class HireRequestSerializer(serializers.Serializer[dict[str, Any]]):
    kind = serializers.ChoiceField(choices=["hire", "dismiss"])
    # hire
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    role = serializers.CharField(required=False, help_text="sales_rep | lead_caller")
    login = serializers.CharField(max_length=150, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(max_length=16, required=False, allow_blank=True, default="")
    # dismiss
    membership_id = serializers.UUIDField(required=False)
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if attrs["kind"] == "hire" and not (attrs.get("first_name") and attrs.get("role")):
            raise serializers.ValidationError("A hire request needs first_name and role.")
        if attrs["kind"] == "dismiss" and not attrs.get("membership_id"):
            raise serializers.ValidationError("A dismissal request needs membership_id.")
        return attrs


class RequestDecisionSerializer(serializers.Serializer[dict[str, Any]]):
    note = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")


def staff_request_to_dict(r: StaffRequestInfo) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "kind": r.kind,
        "status": r.status,
        "department": department_to_dict(r.department),
        "requested_by": {"id": str(r.requested_by), "name": r.requested_by_name},
        "created_at": r.created_at.isoformat(),
        "candidate": {
            "full_name": " ".join(p for p in (r.first_name, r.last_name) if p),
            "login": r.login or None,
            "email": r.email,
            "phone": r.phone,
            "role": r.role,
        }
        if r.kind == "hire"
        else None,
        "target": {"membership_id": str(r.target_membership_id), "name": r.target_name}
        if r.target_membership_id
        else None,
        "reason": r.reason,
        "decided_by": r.decided_by_name,
        "decided_at": r.decided_at.isoformat() if r.decided_at else None,
        "decision_note": r.decision_note,
        "result_membership_id": str(r.result_membership_id) if r.result_membership_id else None,
    }


def company_to_dict(c: Company) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "slug": c.slug,
        "name": c.name,
        "country": c.country,
        "currency": c.currency,
        "timezone": c.timezone,
    }


def department_to_dict(d: DepartmentInfo) -> dict[str, Any]:
    return {"id": str(d.id), "name": d.name, "is_active": d.is_active}


def staff_to_dict(m: StaffMember) -> dict[str, Any]:
    from modules.access_control.domain.rbac import Role, product_role

    try:
        prod = product_role(Role(m.role))
    except ValueError:
        prod = m.role
    if not m.is_active:
        status = "deactivated"
    elif m.must_change_password and m.last_login is None:
        status = "invited"
    else:
        status = "active"
    return {
        "id": str(m.membership_id),
        "user_id": str(m.user_id),
        "login": m.login,
        "full_name": m.full_name,
        "email": m.email,
        "phone": m.phone,
        "role": m.role,
        "product_role": prod,
        "department": department_to_dict(m.department) if m.department else None,
        "status": status,
        "last_login": m.last_login.isoformat() if m.last_login else None,
        "created_by": str(m.created_by) if m.created_by else None,
        "created_at": m.created_at.isoformat(),
    }
