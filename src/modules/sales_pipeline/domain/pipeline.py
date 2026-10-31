"""Pipeline and its ordered stages. Pure Python: no Django, no ORM."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from shared.domain.errors import DomainValidationError, NotFoundError

MAX_NAME_LENGTH = 100
MAX_STAGES = 20
MIN_PROBABILITY = 0
MAX_PROBABILITY = 100


class StageKind(StrEnum):
    """What reaching a stage means for the deal's status.

    Moving a deal into a ``won``/``lost`` stage closes it; ``open`` stages keep it
    open. A pipeline keeps at least one open stage so new deals have somewhere to
    start.
    """

    OPEN = "open"
    WON = "won"
    LOST = "lost"


class StageNotInPipelineError(NotFoundError):
    code = "stage_not_in_pipeline"


def _clean_name(name: str, label: str) -> str:
    cleaned = " ".join(name.split())
    if not cleaned:
        raise DomainValidationError(f"{label} name must not be empty.")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise DomainValidationError(f"{label} name must be at most {MAX_NAME_LENGTH} characters.")
    return cleaned


@dataclass(frozen=True, slots=True)
class Stage:
    id: UUID
    name: str
    position: int
    probability: int
    kind: StageKind = StageKind.OPEN
    is_active: bool = True


@dataclass(frozen=True, slots=True)
class StageDefinition:
    """Input for creating a stage (no id/position yet)."""

    name: str
    probability: int = 0
    kind: StageKind = StageKind.OPEN


@dataclass(slots=True)
class Pipeline:
    id: UUID
    name: str
    is_default: bool
    stages: tuple[Stage, ...]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(
        cls, *, name: str, stages: list[StageDefinition], is_default: bool = False
    ) -> Pipeline:
        if not stages:
            raise DomainValidationError("A pipeline needs at least one stage.")
        if len(stages) > MAX_STAGES:
            raise DomainValidationError(f"A pipeline can have at most {MAX_STAGES} stages.")
        seen: set[str] = set()
        built: list[Stage] = []
        for position, definition in enumerate(stages):
            stage_name = _clean_name(definition.name, "Stage")
            key = stage_name.lower()
            if key in seen:
                raise DomainValidationError(f"Duplicate stage name: {stage_name}.")
            seen.add(key)
            if not MIN_PROBABILITY <= definition.probability <= MAX_PROBABILITY:
                raise DomainValidationError("Stage probability must be between 0 and 100.")
            built.append(
                Stage(
                    id=uuid4(),
                    name=stage_name,
                    position=position,
                    probability=definition.probability,
                    kind=definition.kind,
                )
            )
        if not any(stage.kind is StageKind.OPEN for stage in built):
            raise DomainValidationError("A pipeline needs at least one open stage.")
        return cls(
            id=uuid4(),
            name=_clean_name(name, "Pipeline"),
            is_default=is_default,
            stages=tuple(built),
        )

    @property
    def initial_stage(self) -> Stage:
        """Where a new deal starts: the first active open stage (lowest position)."""
        candidates = [s for s in self.stages if s.kind is StageKind.OPEN and s.is_active]
        return min(candidates or self.stages, key=lambda s: s.position)

    def stage(self, stage_id: UUID) -> Stage:
        for stage in self.stages:
            if stage.id == stage_id:
                return stage
        raise StageNotInPipelineError("Stage does not belong to this pipeline.")

    def has_stage(self, stage_id: UUID) -> bool:
        return any(stage.id == stage_id for stage in self.stages)
