from __future__ import annotations

from typing import Any

from rest_framework import serializers

from modules.organizations.application.ports import Company, DepartmentInfo, StaffMember


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
