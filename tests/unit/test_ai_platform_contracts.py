"""What the CRM sends to the AI Platform must match the platform's published JSON Schemas.

Inputs are closed, so a value the schema does not know (say, a meeting status added to the CRM
later) makes the platform reject the job. The schemas in tests/contracts/ai_platform are copies
of the platform's own files.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from modules.ai_insights.domain import agent, deal_brief, insight, kpi_payload, reply, report
from modules.ai_insights.domain.agent import Subject, Tool
from modules.ai_insights.domain.deal_brief import DealFacts, HistoryEntry, meeting_status
from modules.ai_insights.domain.kpi_payload import KpiReading
from modules.ai_insights.domain.redaction import redact_contacts
from modules.ai_insights.domain.reply import MessageSnapshot
from modules.communications.domain.conversation import Channel
from modules.meetings.domain.meeting import MeetingStatus
from modules.operations.domain.rules import FindingKind
from modules.risk.domain.rules import SignalKind
from modules.sales_pipeline.domain.deal import DealStatus

_SCHEMAS = Path(__file__).parents[1] / "contracts" / "ai_platform"


def _schema(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((_SCHEMAS / f"{name}.json").read_text())
    return loaded


def _assert_valid(name: str, document: dict[str, Any]) -> None:
    validator = Draft202012Validator(_schema(name), format_checker=FormatChecker())
    errors = [f"{list(e.absolute_path)}: {e.message}" for e in validator.iter_errors(document)]
    assert not errors, errors


def _deal() -> DealFacts:
    return DealFacts(
        id=uuid4(),
        title="Польша: Айдана",
        status="open",
        stage="Думает",
        stage_position=2,
        stage_count=6,
        probability=40,
        amount=1500.5,
        currency="KGS",
        expected_close_date=None,
        created_on=date(2026, 11, 1),
    )


@pytest.mark.parametrize("status", [s.value for s in MeetingStatus])
def test_deal_brief_accepts_every_meeting_status(status: str) -> None:
    entry = HistoryEntry(
        ref=f"meeting:{uuid4()}",
        kind="meeting",
        on=date(2026, 11, 2),
        title="Встреча",
        text=None,
        status=meeting_status(status),
    )
    payload = deal_brief.build_payload(
        language="ru", today=date(2026, 11, 5), deal=_deal(), history=[entry]
    )
    _assert_valid("deal_coach.input.v1", payload)


@pytest.mark.parametrize("status", [s.value for s in DealStatus])
def test_deal_brief_accepts_every_deal_status_and_task_state(status: str) -> None:
    today = date(2026, 11, 5)
    states = [
        deal_brief.task_status(completed=True, due=None, today=today),
        deal_brief.task_status(completed=False, due=date(2026, 11, 1), today=today),
        deal_brief.task_status(completed=False, due=None, today=today),
        deal_brief.task_status(completed=False, due=None, today=today, cancelled=True),
    ]
    assert states == ["done", "overdue", "open", "cancelled"]
    history = [
        HistoryEntry(
            ref=f"activity:{uuid4()}", kind="task", on=today, title="Шаг", text="x", status=state
        )
        for state in states
    ]
    facts = _deal()
    payload = deal_brief.build_payload(language="ru", today=today, deal=facts, history=history)
    payload["deal"]["status"] = status
    _assert_valid("deal_coach.input.v1", payload)


def test_call_summary_input() -> None:
    _assert_valid(
        "call_summary.input.v1",
        {"transcript": redact_contacts("Клиент перезвонит"), "language": "ru", "call_ref": "c-1"},
    )


@pytest.mark.parametrize("channel", [c.value for c in Channel])
def test_reply_accepts_every_channel(channel: str) -> None:
    payload = reply.build_payload(
        language="ru",
        channel=channel,
        messages=[MessageSnapshot(id=uuid4(), direction="inbound", text="Сколько стоит?")],
    )
    _assert_valid("message_reply.input.v1", payload)


def test_agent_input() -> None:
    payload = agent.build_payload(
        language="ru",
        today="2026-11-05",
        request="Поставь задачу",
        tools=list(Tool),
        subjects=[Subject(ref=f"deal:{uuid4()}", kind="deal", title="Польша")],
    )
    _assert_valid("agent_plan.input.v1", payload)


@pytest.mark.parametrize("period", kpi_payload.PERIODS)
def test_kpi_input(period: str) -> None:
    readings = [
        KpiReading(code="deals_won", name="Сделок выиграно", unit="count", value=Decimal(3)),
        KpiReading(code="win_rate", name="Доля побед", unit="ratio", value=Decimal("0.25")),
        KpiReading(code="revenue_won", name="Выручка", unit="money", value=None),
    ]
    payload = kpi_payload.build_payload(
        language="ru", period=period, start=date(2026, 11, 2), current=readings, previous=readings
    )
    _assert_valid("kpi_explanation.input.v1", payload)


def test_operations_digest_knows_every_finding_kind() -> None:
    known = set(_schema("operations_digest.input.v1")["$defs"]["kind"]["enum"])
    assert {k.value for k in FindingKind} == known


def test_risk_review_knows_every_signal_kind() -> None:
    known = set(_schema("risk_review.input.v1")["$defs"]["kind"]["enum"])
    assert {k.value for k in SignalKind} == known


def test_expected_output_schemas_are_published() -> None:
    expected = [
        *insight.OUTPUT_SCHEMAS[insight.InsightKind.CALL_SUMMARY],
        deal_brief.OUTPUT_SCHEMA,
        reply.OUTPUT_SCHEMA,
        agent.OUTPUT_SCHEMA,
        *report.OUTPUT_SCHEMAS.values(),
    ]
    for name in expected:
        assert (_SCHEMAS / f"{name}.json").is_file(), name


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Звонить +996 555 100 200 вечером", "Звонить [телефон] вечером"),
        ("тел: 0555-10-02-00", "тел: [телефон]"),
        ("8 (701) 111-22-33, спросить Айбека", "[телефон], спросить Айбека"),
        ("996555100200", "[телефон]"),
        ("почта aidana.k+crm@mail.kg.", "почта [email]."),
        # Amounts, dates and short numbers are facts of the deal, not contacts.
        ("Сумма 1 500 000 сом, 2026-11-05 10:30, договор 12345", None),
        ("оплата 15000.50 до 05.11.2026", None),
    ],
)
def test_contacts_are_masked_in_outgoing_text(text: str, expected: str | None) -> None:
    assert redact_contacts(text) == (text if expected is None else expected)


def test_payloads_carry_no_contact_details() -> None:
    message = MessageSnapshot(id=uuid4(), direction="inbound", text="Мой номер +996555100200")
    payload = reply.build_payload(language="ru", channel="whatsapp", messages=[message])
    assert payload["messages"][0]["text"] == "Мой номер [телефон]"
    entry = HistoryEntry(
        ref=f"activity:{uuid4()}",
        kind="note",
        on=date(2026, 11, 2),
        title=None,
        text="Написать на a@b.kg",
        status=None,
    )
    brief = deal_brief.build_payload(
        language="ru", today=date(2026, 11, 5), deal=_deal(), history=[entry]
    )
    assert brief["history"][0]["text"] == "Написать на [email]"
