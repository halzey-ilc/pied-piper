"""Pipeline and its ordered stages. Pure Python: no Django, no ORM."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
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


class SystemStage(StrEnum):
    """Stages every pipeline has, created by the system (owner decision of 03.11.2026).

    ``sale`` — «Продажа»: the won stage, always last; a deal reaches it only through a
    confirmed payment. ``meeting_rescheduled`` — «Встреча перенесена»: an open stage the
    head of sales may move or switch off; moving a deal there needs a new meeting date.
    Names are fixed; neither can be deleted.
    """

    SALE = "sale"
    MEETING_RESCHEDULED = "meeting_rescheduled"


SYSTEM_STAGE_NAMES = {
    SystemStage.SALE: "Продажа",
    SystemStage.MEETING_RESCHEDULED: "Встреча перенесена",
}


class SystemStageLockedError(DomainValidationError):
    """A system stage keeps its name, cannot be deleted, and «Продажа» stays last and on."""

    code = "system_stage_locked"


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
    system_code: SystemStage | None = None

    @property
    def is_system(self) -> bool:
        return self.system_code is not None


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
    # The sales department that owns this pipeline; None = company-wide. A head of sales
    # configures only their department's pipelines; its managers work in them by default.
    department_id: UUID | None = None

    @classmethod
    def create(
        cls,
        *,
        name: str,
        stages: list[StageDefinition],
        is_default: bool = False,
        department_id: UUID | None = None,
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
            stages=with_system_stages(built),
            department_id=department_id,
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

    def system_stage(self, code: SystemStage) -> Stage | None:
        return next((st for st in self.stages if st.system_code is code), None)

    # --- configuration (director / pipelines.manage) ------------------------------

    def rename(self, name: str) -> bool:
        cleaned = _clean_name(name, "Pipeline")
        if cleaned == self.name:
            return False
        self.name = cleaned
        return True

    def add_stage(self, definition: StageDefinition, position: int | None = None) -> Stage:
        """Insert a stage (at the end by default); later stages shift down."""
        if len(self.stages) >= MAX_STAGES:
            raise DomainValidationError(f"A pipeline can have at most {MAX_STAGES} stages.")
        _check_probability(definition.probability)
        name = self._unique_name(definition.name)
        ordered = sorted(self.stages, key=lambda st: st.position)
        # «Продажа» stays last: new stages go before it
        last = len(ordered) - (1 if ordered and ordered[-1].system_code is SystemStage.SALE else 0)
        index = last if position is None else max(0, min(position, last))
        stage = Stage(
            id=uuid4(),
            name=name,
            position=0,
            probability=definition.probability,
            kind=definition.kind,
        )
        ordered.insert(index, stage)
        self.stages = _renumbered(ordered)
        return self.stage(stage.id)

    def update_stage(
        self, stage_id: UUID, *, name: str | None = None, probability: int | None = None
    ) -> bool:
        """Rename a stage or change its probability. Its kind never changes: deals already
        closed on it would otherwise change meaning."""
        current = self.stage(stage_id)
        updated = current
        if name is not None and current.is_system and " ".join(name.split()) != current.name:
            raise SystemStageLockedError(
                f"{current.name!r} is a system stage and cannot be renamed."
            )
        if name is not None:
            updated = replace(updated, name=self._unique_name(name, exclude=stage_id))
        if probability is not None:
            _check_probability(probability)
            updated = replace(updated, probability=probability)
        if updated == current:
            return False
        self.stages = tuple(updated if st.id == stage_id else st for st in self.stages)
        return True

    def reorder(self, stage_ids: list[UUID]) -> bool:
        """Set the order of all stages (archived ones included) at once."""
        if sorted(map(str, stage_ids)) != sorted(str(st.id) for st in self.stages):
            raise DomainValidationError("stage_ids must list every stage of the pipeline once.")
        by_id = {st.id: st for st in self.stages}
        sale = self.system_stage(SystemStage.SALE)
        if sale is not None and stage_ids[-1] != sale.id:
            raise SystemStageLockedError("The sale stage is a system stage and always comes last.")
        reordered = _renumbered([by_id[sid] for sid in stage_ids])
        if reordered == tuple(sorted(self.stages, key=lambda st: st.position)):
            return False
        self.stages = reordered
        return True

    def set_stage_active(self, stage_id: UUID, active: bool) -> bool:
        """Archive (hide from new moves) or restore a stage; the stage and its history stay."""
        current = self.stage(stage_id)
        if current.is_active == active:
            return False
        if current.system_code is SystemStage.SALE:
            raise SystemStageLockedError(
                "The sale stage is a system stage and cannot be switched off."
            )
        candidate = tuple(
            replace(st, is_active=active) if st.id == stage_id else st for st in self.stages
        )
        _check_still_usable(self.stages, candidate, current)
        self.stages = candidate
        return True

    def remove_stage(self, stage_id: UUID) -> Stage:
        """Delete a stage outright (only one no deal ever stood on); later stages shift up."""
        removed = self.stage(stage_id)
        if removed.is_system:
            raise SystemStageLockedError(
                f"{removed.name!r} is a system stage and cannot be deleted."
            )
        remaining = [
            st for st in sorted(self.stages, key=lambda st: st.position) if st.id != stage_id
        ]
        _check_still_usable(self.stages, tuple(remaining), removed)
        self.stages = _renumbered(remaining)
        return removed

    def _unique_name(self, name: str, *, exclude: UUID | None = None) -> str:
        cleaned = _clean_name(name, "Stage")
        if any(st.name.lower() == cleaned.lower() and st.id != exclude for st in self.stages):
            raise DomainValidationError(f"Duplicate stage name: {cleaned}.")
        return cleaned


def with_system_stages(stages: list[Stage] | tuple[Stage, ...]) -> tuple[Stage, ...]:
    """Make sure the stages carry both system stages, with «Продажа» last.

    The first won stage becomes «Продажа» (its id is kept, so history stays valid); a stage
    already named like a system stage becomes that system stage; missing ones are added.
    «Встреча перенесена» goes right before «Продажа»."""
    ordered = sorted(stages, key=lambda st: st.position)
    names = {code: name.lower() for code, name in SYSTEM_STAGE_NAMES.items()}
    sale = next((st for st in ordered if st.system_code is SystemStage.SALE), None)
    if sale is None:
        sale = next(
            (
                st
                for st in ordered
                if st.kind is StageKind.WON and st.name.lower() == names[SystemStage.SALE]
            ),
            None,
        ) or next((st for st in ordered if st.kind is StageKind.WON), None)
    if sale is None:
        sale = Stage(id=uuid4(), name="", position=0, probability=100, kind=StageKind.WON)
    else:
        ordered = [st for st in ordered if st.id != sale.id]
    sale = replace(
        sale,
        name=SYSTEM_STAGE_NAMES[SystemStage.SALE],
        kind=StageKind.WON,
        is_active=True,
        system_code=SystemStage.SALE,
    )
    # A plain stage that happens to carry the system name would clash with it
    ordered = [
        st
        if st.is_system or st.name.lower() != names[SystemStage.SALE]
        else replace(st, name=f"{st.name} (2)")
        for st in ordered
    ]
    moved = next((st for st in ordered if st.system_code is SystemStage.MEETING_RESCHEDULED), None)
    if moved is None:
        named = next(
            (
                st
                for st in ordered
                if st.kind is StageKind.OPEN
                and st.name.lower() == names[SystemStage.MEETING_RESCHEDULED]
            ),
            None,
        )
        if named is not None:
            ordered = [
                replace(st, system_code=SystemStage.MEETING_RESCHEDULED)
                if st.id == named.id
                else st
                for st in ordered
            ]
        else:
            ordered = [
                st
                if st.name.lower() != names[SystemStage.MEETING_RESCHEDULED]
                else replace(st, name=f"{st.name} (2)")
                for st in ordered
            ]
            # After the last open stage, before the closing ones
            closing = next(
                (i for i, st in enumerate(ordered) if st.kind is not StageKind.OPEN), len(ordered)
            )
            ordered.insert(
                closing,
                Stage(
                    id=uuid4(),
                    name=SYSTEM_STAGE_NAMES[SystemStage.MEETING_RESCHEDULED],
                    position=0,
                    probability=0,
                    kind=StageKind.OPEN,
                    system_code=SystemStage.MEETING_RESCHEDULED,
                ),
            )
    ordered.append(sale)
    return _renumbered(ordered)


def _check_still_usable(
    before: tuple[Stage, ...], after: tuple[Stage, ...], changed: Stage
) -> None:
    """A pipeline keeps an active open stage, and never loses its last active won or lost
    stage: closing a deal must stay possible."""
    if not any(st.kind is StageKind.OPEN and st.is_active for st in after):
        raise DomainValidationError("A pipeline needs at least one active open stage.")
    if changed.kind is StageKind.OPEN:
        return
    had = any(st.kind is changed.kind and st.is_active for st in before)
    if had and not any(st.kind is changed.kind and st.is_active for st in after):
        raise DomainValidationError(
            f"A pipeline needs at least one active {changed.kind.value} stage."
        )


def _check_probability(probability: int) -> None:
    if not MIN_PROBABILITY <= probability <= MAX_PROBABILITY:
        raise DomainValidationError("Stage probability must be between 0 and 100.")


def _renumbered(stages: list[Stage]) -> tuple[Stage, ...]:
    return tuple(replace(st, position=index) for index, st in enumerate(stages))
