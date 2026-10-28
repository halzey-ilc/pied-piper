"""Adapters to the operations and organizations modules (public composition APIs)."""

from __future__ import annotations

from uuid import UUID

from modules.access_control.domain.rbac import Principal
from modules.ai_insights.application.digest_ports import FindingsSnapshot, Member
from modules.ai_insights.domain.digest import finding_ref
from modules.operations.composition import operations_service
from modules.organizations.composition import list_active_members


class OperationsFindingsSource:
    def snapshot_for(self, principal: Principal) -> FindingsSnapshot:
        # The operations service applies the principal's own visibility (RBAC).
        report = operations_service().report(principal)
        return FindingsSnapshot(
            thresholds={
                "stale_lead_days": report.thresholds.stale_lead_days,
                "stalled_deal_days": report.thresholds.stalled_deal_days,
            },
            counts=report.counts,
            findings=[
                {
                    "ref": finding_ref(f.record_type.value, f.record_id),
                    "kind": f.kind.value,
                    "age_days": f.age_days,
                }
                for f in report.findings
            ],
        )


class OrganizationMemberDirectory:
    def active_members(self, tenant_id: UUID) -> list[Member]:
        return [Member(user_id=uid, role=role) for uid, role in list_active_members(tenant_id)]
