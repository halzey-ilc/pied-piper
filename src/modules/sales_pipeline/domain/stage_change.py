"""Deal stage-change history entry. Pure Python: no Django, no ORM.

One append-only record per deal transition (move, win, lost, reopen): where the
deal went, who moved it, the deal version after the change and, for a loss, the
reason. History begins at migration; older transitions are not backfilled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4


@dataclass(slots=True)
class StageChange:
    id: UUID
    deal_id: UUID
    from_stage_id: UUID | None
    to_stage_id: UUID
    author_user_id: UUID
    deal_version: int
    reason: str | None = None
    loss_reason_id: UUID | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def record(
        cls,
        *,
        deal_id: UUID,
        from_stage_id: UUID | None,
        to_stage_id: UUID,
        author_user_id: UUID,
        deal_version: int,
        reason: str | None = None,
        loss_reason_id: UUID | None = None,
    ) -> StageChange:
        return cls(
            id=uuid4(),
            deal_id=deal_id,
            from_stage_id=from_stage_id,
            to_stage_id=to_stage_id,
            author_user_id=author_user_id,
            deal_version=deal_version,
            reason=reason,
            loss_reason_id=loss_reason_id,
        )
