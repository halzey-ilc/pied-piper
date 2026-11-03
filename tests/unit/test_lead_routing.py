"""Round-robin lead distribution: order, eligibility, the pick made on capture."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from uuid import UUID, uuid4

from modules.access_control.domain.rbac import Principal, Role
from modules.leads.application.routing import pick_assignee
from modules.leads.domain.lead import Lead
from modules.leads.domain.routing import RoutingRule, candidates, next_assignee

A = UUID("00000000-0000-0000-0000-00000000000a")
B = UUID("00000000-0000-0000-0000-00000000000b")
C = UUID("00000000-0000-0000-0000-00000000000c")
DEPT = uuid4()


def test_next_assignee_rotates_in_stable_order_and_wraps() -> None:
    assert next_assignee([C, A, B], None) == A
    assert next_assignee([C, A, B], A) == B
    assert next_assignee([C, A, B], B) == C
    assert next_assignee([C, A, B], C) == A


def test_next_assignee_continues_after_a_manager_who_left_the_shift() -> None:
    # B got the previous lead and went off shift: C is next, not A again.
    assert next_assignee([A, C], B) == C
    assert next_assignee([], A) is None


def test_eligibility_skips_off_shift_and_absent() -> None:
    rule = RoutingRule(department_id=DEPT, enabled=True)
    found = candidates(rule, [A, B, C], on_shift={A, B}, absent={B})
    assert [(c.user_id, c.eligible(rule)) for c in found] == [(A, True), (B, False), (C, False)]

    # Absence may be ignored, the shift may not: nobody off shift gets a lead.
    relaxed = replace(rule, require_on_shift=False, skip_on_absence=False)
    found = candidates(relaxed, [A, B, C], on_shift={A, B}, absent={B})
    assert [(c.user_id, c.eligible(relaxed)) for c in found] == [(A, True), (B, True), (C, False)]


def test_member_list_limits_who_takes_part() -> None:
    rule = RoutingRule(department_id=DEPT, enabled=True, member_ids=frozenset({B}))
    found = candidates(rule, [A, B], on_shift={A, B}, absent=set())
    assert [c.user_id for c in found if c.eligible(rule)] == [B]


@dataclass
class FakeRouting:
    rules: dict[UUID, RoutingRule] = field(default_factory=dict)
    members: list[UUID] = field(default_factory=list)
    shift: set[UUID] = field(default_factory=set)
    away: set[UUID] = field(default_factory=set)
    alerts: list[UUID] = field(default_factory=list)

    def rule(self, department_id: UUID) -> RoutingRule | None:
        return self.rules.get(department_id)

    def rule_for_update(self, department_id: UUID) -> RoutingRule | None:
        return self.rules.get(department_id)

    def intake_department(self) -> UUID | None:
        return next((d for d, r in self.rules.items() if r.is_company_intake and r.enabled), None)

    def save_rule(self, rule: RoutingRule, *, updated_by: UUID) -> RoutingRule:
        self.rules[rule.department_id] = rule
        return rule

    def remember(self, department_id: UUID, user_id: UUID) -> None:
        self.rules[department_id] = replace(
            self.rules[department_id], last_assigned_user_id=user_id
        )

    def managers(self, department_id: UUID) -> list[UUID]:
        return list(self.members)

    def on_shift(self, user_ids: list[UUID]) -> set[UUID]:
        return self.shift & set(user_ids)

    def absent_today(self, user_ids: list[UUID]) -> set[UUID]:
        return self.away & set(user_ids)

    def department_exists(self, department_id: UUID) -> bool:
        return True

    def notify_assignee_missing(self, department_id: UUID, lead: Lead) -> None:
        self.alerts.append(department_id)


def _principal(role: Role, department_id: UUID | None = DEPT) -> Principal:
    return Principal(user_id=uuid4(), tenant_id=uuid4(), role=role, department_id=department_id)


def test_pick_goes_in_turn_and_moves_the_pointer() -> None:
    routing = FakeRouting(
        rules={DEPT: RoutingRule(department_id=DEPT, enabled=True)},
        members=[A, B, C],
        shift={A, B, C},
    )
    head = _principal(Role.HEAD_OF_SALES)
    picks = [pick_assignee(routing, head) for _ in range(4)]
    assert [p.user_id for p in picks if p] == [A, B, C, A]


def test_pick_skips_off_shift_and_absent_managers() -> None:
    routing = FakeRouting(
        rules={DEPT: RoutingRule(department_id=DEPT, enabled=True)},
        members=[A, B, C],
        shift={A, C},
        away={A},
    )
    pick = pick_assignee(routing, _principal(Role.HEAD_OF_SALES))
    assert pick is not None and pick.user_id == C


def test_disabled_or_missing_rule_and_managers_are_not_routed() -> None:
    routing = FakeRouting(rules={DEPT: RoutingRule(department_id=DEPT)}, members=[A], shift={A})
    assert pick_assignee(routing, _principal(Role.HEAD_OF_SALES)) is None
    assert pick_assignee(FakeRouting(), _principal(Role.DIRECTOR)) is None
    routing.rules[DEPT] = replace(routing.rules[DEPT], enabled=True)
    assert pick_assignee(routing, _principal(Role.SALES_REP)) is None
    assert pick_assignee(routing, _principal(Role.LEAD_CALLER)) is None


def test_no_eligible_manager_returns_an_empty_pick() -> None:
    routing = FakeRouting(
        rules={DEPT: RoutingRule(department_id=DEPT, enabled=True)}, members=[A], shift=set()
    )
    pick = pick_assignee(routing, _principal(Role.HEAD_OF_SALES))
    assert pick is not None and pick.user_id is None and pick.department_id == DEPT
    assert routing.rules[DEPT].last_assigned_user_id is None


def test_member_without_department_uses_the_intake_department() -> None:
    routing = FakeRouting(
        rules={DEPT: RoutingRule(department_id=DEPT, enabled=True, is_company_intake=True)},
        members=[B],
        shift={B},
    )
    pick = pick_assignee(routing, _principal(Role.DIRECTOR, department_id=None))
    assert pick is not None and (pick.department_id, pick.user_id) == (DEPT, B)
