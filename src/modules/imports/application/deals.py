"""Deal rows of an import resolved against the company (requirement 20.5).

A row becomes a deal exactly as one opened by hand: on the pipeline's first stage, handed to
its owner, then moved to the stage of the file with the move's own rules. Owner decisions
hold with no back door: a won stage («Продажа» or any other) is reached only by a confirmed
payment, a lost stage needs a loss reason, «Встреча перенесена» needs a new meeting time
(rows asking for it are refused), any other later open stage needs a next step.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from modules.access_control.domain.rbac import (
    AccessPolicy,
    MemberRef,
    Permission,
    Principal,
    ResourceOwnership,
    Scope,
)
from modules.activities.application.next_step import NextStep
from modules.imports.application.ports import (
    DealDraft,
    DealReferences,
    ExistingContacts,
    ExistingMatch,
    NewCustomer,
)
from modules.imports.domain.rows import DealImportRow, ImportEntity
from modules.sales_pipeline.domain.loss_reason import LossReason
from modules.sales_pipeline.domain.pipeline import Pipeline, Stage, StageKind, SystemStage


@dataclass(frozen=True, slots=True)
class DealPlan:
    """What will be created for a row, shown in the preview."""

    draft: DealDraft
    pipeline_name: str
    stage_name: str
    customer_name: str | None = None
    customer_action: str | None = None  # "match" | "create" | None


@dataclass(frozen=True, slots=True)
class PlannedRow:
    row: DealImportRow
    plan: DealPlan | None
    errors: tuple[str, ...] = ()
    duplicate_reason: str | None = None
    duplicate_line: int | None = None
    duplicate_of: ExistingMatch | None = None


def _same(a: str, b: str) -> bool:
    return " ".join(a.split()).lower() == " ".join(b.split()).lower()


class _RowError(Exception):
    pass


class DealRowPlanner:
    def __init__(
        self,
        principal: Principal,
        refs: DealReferences,
        contacts: ExistingContacts,
        policy: AccessPolicy,
        default_owner: UUID,
    ) -> None:
        self._principal = principal
        self._refs = refs
        self._contacts = contacts
        self._policy = policy
        self._default_owner = default_owner
        self._logins: dict[str, MemberRef] | None = None

    def plan(self, rows: list[DealImportRow]) -> list[PlannedRow]:
        valid = [r for r in rows if not r.errors]
        self._pipelines = self._refs.pipelines(self._principal)
        self._default_pipeline = self._refs.default_pipeline(self._principal.department_id)
        self._reasons: list[LossReason] = self._refs.loss_reasons()
        self._currency = self._refs.default_currency()
        self._customers = self._contacts.find(
            ImportEntity.DEALS,
            phones={r.phone for r in valid if r.phone},
            emails={r.email for r in valid if r.email},
        )
        existing = self._refs.existing_deals({r.title.lower() for r in valid})
        seen: dict[tuple[str, str], int] = {}
        result: list[PlannedRow] = []
        for row in rows:
            if row.errors:
                result.append(PlannedRow(row, None, row.errors))
                continue
            try:
                plan, match = self._resolve(row)
            except _RowError as error:
                result.append(PlannedRow(row, None, (str(error),)))
                continue
            customer_id = plan.draft.customer_id
            file_key = (row.title.lower(), str(customer_id or row.phone or row.email or ""))
            if (row.title.lower(), customer_id) in existing and (
                customer_id is not None or not row.has_customer
            ):
                whose = f" у клиента «{match.name}»" if match else " без клиента"  # noqa: RUF001
                result.append(
                    PlannedRow(
                        row,
                        plan,
                        duplicate_reason=f"Уже есть сделка «{row.title}»{whose}",
                        duplicate_of=match,
                    )
                )
            elif file_key in seen:
                line = seen[file_key]
                result.append(
                    PlannedRow(
                        row,
                        plan,
                        duplicate_reason=f"Повторяет строку {line} этого файла",
                        duplicate_line=line,
                    )
                )
            else:
                result.append(PlannedRow(row, plan))
            seen.setdefault(file_key, row.line)
        return result

    # --- one row ------------------------------------------------------------

    def _resolve(self, row: DealImportRow) -> tuple[DealPlan, ExistingMatch | None]:
        owner = self._owner(row)
        pipeline = self._pipeline(row)
        stage, move = self._stage(row, pipeline)
        loss_reason_id, loss_text = self._loss_reason(row, stage)
        step = (
            NextStep(title=row.next_step, due_date=row.next_step_due)
            if row.next_step and row.next_step_due and stage.kind is StageKind.OPEN
            else None
        )
        if stage.kind is StageKind.OPEN and move and step is None:
            raise _RowError(
                f"Для этапа «{stage.name}» нужен следующий шаг: "
                "заполните колонки «Следующий шаг» и «Срок шага»"
            )
        match, new_customer = self._customer(row)
        draft = DealDraft(
            title=row.title,
            amount=row.amount if row.amount is not None else Decimal(0),
            currency=row.currency or self._currency,
            pipeline_id=pipeline.id,
            stage_id=stage.id,
            move_to_stage=move,
            owner_user_id=owner,
            customer_id=match.id if match else None,
            new_customer=new_customer,
            source=row.source,
            expected_close_date=row.expected_close_date,
            next_step=step,
            loss_reason_id=loss_reason_id,
            loss_reason=loss_text,
        )
        plan = DealPlan(
            draft=draft,
            pipeline_name=pipeline.name,
            stage_name=stage.name,
            customer_name=match.name if match else new_customer.name if new_customer else None,
            customer_action="match" if match else "create" if new_customer else None,
        )
        return plan, match

    def _owner(self, row: DealImportRow) -> UUID:
        if row.owner:
            if self._logins is None:
                self._logins = self._refs.members_by_login()
            member = self._logins.get(row.owner)
            if member is None:
                raise _RowError(f"Ответственный «{row.owner}» не найден среди активных сотрудников")
            owner = member.user_id
        else:
            owner = self._default_owner
            member = None
        if owner == self._principal.user_id:
            return owner
        # Same rule as reassigning a deal by hand (check_assignable), said in Russian.
        scope = self._policy.scope_for(self._principal, Permission.DEALS_UPDATE)
        if scope is None or scope is Scope.OWN:
            raise _RowError("Вы можете назначить сделку только себе")  # noqa: RUF001
        member = member or self._refs.member(owner)
        if member is None:
            raise _RowError("Ответственный не является активным сотрудником компании")
        if scope is Scope.TEAM and (
            member.department_id is None or member.department_id != self._principal.department_id
        ):
            raise _RowError("Вы можете назначить сделку только сотруднику своего отдела")
        return owner

    def _pipeline(self, row: DealImportRow) -> Pipeline:
        if not row.pipeline:
            if self._default_pipeline is None:
                raise _RowError("В компании нет воронки по умолчанию — укажите воронку")  # noqa: RUF001
            return self._default_pipeline
        found = [p for p in self._pipelines if _same(p.name, row.pipeline)]
        if len(found) > 1:
            own = self._principal.department_id
            found = [p for p in found if p.department_id == own] or [
                p for p in found if p.department_id is None
            ]
        if not found:
            raise _RowError(f"Воронка «{row.pipeline}» не найдена")
        if len(found) > 1:
            raise _RowError(f"Несколько воронок называются «{row.pipeline}» — переименуйте одну")
        return found[0]

    def _stage(self, row: DealImportRow, pipeline: Pipeline) -> tuple[Stage, bool]:
        initial = pipeline.initial_stage
        if not row.stage:
            return initial, False
        stage = next((s for s in pipeline.stages if _same(s.name, row.stage)), None)
        if stage is None:
            raise _RowError(f"В воронке «{pipeline.name}» нет этапа «{row.stage}»")  # noqa: RUF001
        if stage.kind is StageKind.WON:
            raise _RowError(
                f"Этап «{stage.name}» означает продажу: сделка становится выигранной только "
                "после подтверждённой оплаты. Загрузите её на открытый этап и внесите оплату"
            )
        if stage.system_code is SystemStage.MEETING_RESCHEDULED:
            raise _RowError(
                "На этап «Встреча перенесена» сделку переводят с новой датой встречи: "  # noqa: RUF001
                "загрузите её на другой этап и назначьте встречу в карточке сделки"
            )
        if not stage.is_active:
            raise _RowError(f"Этап «{stage.name}» отключён")
        return stage, stage.id != initial.id

    def _loss_reason(self, row: DealImportRow, stage: Stage) -> tuple[UUID | None, str | None]:
        if stage.kind is not StageKind.LOST:
            return None, None
        if not row.loss_reason:
            raise _RowError(
                f"Для этапа «{stage.name}» укажите причину проигрыша (колонка «Причина проигрыша»)"
            )
        catalog = next((r for r in self._reasons if _same(r.name, row.loss_reason)), None)
        if catalog is not None:
            return catalog.id, None
        return None, row.loss_reason

    def _customer(self, row: DealImportRow) -> tuple[ExistingMatch | None, NewCustomer | None]:
        if not row.has_customer:
            return None, None
        match = next(
            (self._customers[k] for k in (row.phone, row.email) if k and k in self._customers),
            None,
        )
        if match is None:
            if not row.customer_name:
                raise _RowError(
                    "Клиент с этим телефоном или e-mail не найден — укажите имя клиента, "  # noqa: RUF001
                    "чтобы создать его"  # noqa: RUF001
                )
            return None, NewCustomer(row.customer_name, row.phone, row.email)
        visible = self._policy.allows(
            self._principal,
            Permission.CUSTOMERS_VIEW,
            ResourceOwnership(owner_user_id=match.owner_user_id, team_id=match.team_id),
        )
        if not visible:
            raise _RowError(
                "Клиент с этим телефоном или e-mail уже есть в базе, "  # noqa: RUF001
                "но закреплён за другим сотрудником"
            )
        if match.is_archived:
            raise _RowError(f"Клиент «{match.name}» в архиве — сначала восстановите его")  # noqa: RUF001
        return match, None


# Domain error codes of the deal services -> what an importing user reads.
ROW_ERROR_MESSAGES = {
    "sale_requires_payment": "Сделка становится выигранной только после подтверждённой оплаты",
    "next_step_required": "Нужен следующий шаг: колонки «Следующий шаг» и «Срок шага»",
    "meeting_date_required": "Для этапа «Встреча перенесена» нужна новая дата встречи",
    "access_denied": "Недостаточно прав для этой строки",
    "plan_limit_reached": "Достигнут лимит тарифа компании: строка не создана",
}
