# ruff: noqa: RUF001 - Russian text by design
"""The model's mistakes measured on Qwen3.5-4B (06.11.2026), as regression cases: what the CRM
states itself, and what it refuses to show."""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

from modules.ai_insights.domain.deal_brief import (
    DealFacts,
    HistoryEntry,
    build_payload,
    compact_refs,
    deal_checks,
    restore_refs,
)
from modules.ai_insights.domain.redaction import mask_personal_data
from modules.ai_insights.domain.verification import (
    INSUFFICIENT_SUMMARY,
    quote_supports,
    verify_brief,
    verify_call_summary,
)

TODAY = date(2026, 11, 6)


def _deal(**kw: Any) -> DealFacts:
    return DealFacts(
        **{
            "id": uuid4(),
            "title": "Внедрение",
            "status": "open",
            "stage": "Думает",
            "stage_position": 2,
            "stage_count": 5,
            "probability": 40,
            "amount": 1000.0,
            "currency": "KGS",
            "expected_close_date": None,
            "created_on": date(2026, 11, 1),
            **kw,
        }
    )


def _task(on: date, status: str, title: str = "Отправить КП") -> HistoryEntry:
    return HistoryEntry(
        ref=f"activity:{uuid4()}", kind="task", on=on, title=title, text=None, status=status
    )


def _meeting(on: date, status: str, detail: str | None = None) -> HistoryEntry:
    return HistoryEntry(
        ref=f"meeting:{uuid4()}",
        kind="meeting",
        on=on,
        title="Встреча",
        text=None,
        status=status,
        detail=detail,
    )


def _note(text: str) -> HistoryEntry:
    return HistoryEntry(
        ref=f"activity:{uuid4()}", kind="note", on=TODAY, title=None, text=text, status=None
    )


def _point(text: str) -> dict[str, Any]:
    return {"text": text, "refs": ["deal:x"]}


def _brief(**kw: Any) -> dict[str, Any]:
    return {
        "situation": [_point("Сделка на этапе обсуждения.")],
        "risks": [],
        "questions": [],
        "next_step": None,
        "insufficient_data": False,
        **kw,
    }


def test_the_crm_tells_the_model_what_a_date_means() -> None:
    deal = _deal()
    future, late = _task(date(2026, 11, 20), "open"), _task(date(2026, 11, 3), "overdue")
    payload = build_payload(language="ru", today=TODAY, deal=deal, history=[future, late])
    texts = {h["ref"]: h["text"] for h in payload["history"]}
    assert "ещё не наступил, осталось 14 дн." in texts[future.ref]
    assert "просрочена на 3 дн." in texts[late.ref]


def test_counts_come_from_the_crm_and_reach_the_model_first() -> None:
    deal = _deal()
    history = [
        _meeting(date(2026, 11, 2), "completed", "встреча состоялась"),
        _meeting(date(2026, 11, 3), "cancelled", "клиент не пришёл, встреча не состоялась"),
        _meeting(date(2026, 11, 10), "scheduled"),
        _task(date(2026, 11, 20), "open"),
        _note("Просит рассрочку"),
    ]
    checks = deal_checks(deal, history, TODAY, reschedules=1)
    assert (checks["meetings"], checks["meetings_held"], checks["meetings_upcoming"]) == (3, 1, 1)
    assert (checks["open_tasks"], checks["overdue_tasks"], checks["notes"]) == (1, 0, 1)
    payload = build_payload(language="ru", today=TODAY, deal=deal, history=history, checks=checks)
    first = payload["history"][0]
    assert first["title"] == "Факты CRM" and "встреч 3, состоялось 1" in first["text"]
    assert "переносов 1" in first["text"]


def test_a_point_citing_the_facts_cites_the_deal() -> None:
    deal = _deal()
    payload = build_payload(language="ru", today=TODAY, deal=deal, history=[_note("Текст")])
    compact, refs = compact_refs(payload)
    assert compact["history"][0]["ref"] == "facts:1"
    restored = restore_refs({"situation": [{"text": "x", "refs": ["facts:1"]}]}, refs)
    assert restored["situation"][0]["refs"] == [f"deal:{deal.id}"]


def _checks(history: list[HistoryEntry], **deal: Any) -> dict[str, Any]:
    return deal_checks(_deal(**deal), history, TODAY)


def test_a_future_date_called_expired_is_removed() -> None:
    checks = _checks([_task(date(2026, 11, 20), "open")])
    said = "Задача открыта, но дата выполнения (2026-11-20) уже истекла."
    result = verify_brief(_brief(risks=[_point(said)]), checks)
    assert result["risks"] == [] and result["verification"]["dropped"] == 1


def test_a_real_overdue_task_may_be_mentioned() -> None:
    checks = _checks([_task(date(2026, 11, 1), "overdue")])
    said = "Задача просрочена, клиент ждёт предложение."
    assert verify_brief(_brief(risks=[_point(said)]), checks)["risks"][0]["text"] == said


def test_invented_repetition_is_removed() -> None:
    said = "Клиент дважды запросил перенос запуска на декабрь."
    assert verify_brief(_brief(risks=[_point(said)]), _checks([]))["risks"] == []


def test_a_wrong_count_is_removed_and_the_right_one_stays() -> None:
    history = [_meeting(date(2026, 11, 2), "completed")]
    wrong, right = "Прошло 3 встречи с клиентом.", "Прошла 1 встреча с клиентом."
    result = verify_brief(_brief(situation=[_point(wrong), _point(right)]), _checks(history))
    assert [p["text"] for p in result["situation"]] == [right]


def test_a_finished_meeting_cannot_be_ahead() -> None:
    held = _meeting(date(2026, 11, 2), "completed")
    ahead = _meeting(date(2026, 11, 10), "scheduled")
    checks = _checks([held, ahead])
    wrong = ["Запланировано 3 встречи, из которых 2 состоятся.", "Встреча состоится завтра."]
    result = verify_brief(_brief(risks=[_point(wrong[0])]), checks)
    assert result["risks"] == []
    named = HistoryEntry(
        ref=held.ref, kind="meeting", on=held.on, title="Встреча 1", text=None, status="completed"
    )
    said = "Подтвердите, что Встреча 1 состоится."
    assert verify_brief(_brief(risks=[_point(said)]), _checks([named]))["risks"] == []
    assert verify_brief(_brief(risks=[_point(wrong[1])]), checks)["risks"] != []


def test_no_tasks_claim_is_removed_when_a_task_is_open() -> None:
    checks = _checks([_task(date(2026, 11, 20), "open")])
    said = "Отсутствие запланированных действий или задач для продвижения сделки."
    assert verify_brief(_brief(risks=[_point(said)]), checks)["risks"] == []


def test_a_field_name_is_cut_from_the_text() -> None:
    said = "Отсутствует дата закрытия сделки (expected_close_date null)."
    result = verify_brief(_brief(risks=[_point(said)]), _checks([]))
    assert result["risks"][0]["text"] == "Отсутствует дата закрытия сделки."


def test_the_next_step_comes_from_the_crm_when_a_task_exists() -> None:
    task = _task(date(2026, 11, 20), "open", "Отправить договор")
    result = verify_brief(_brief(), _checks([task]))
    assert result["next_step"]["refs"] == [task.ref]
    assert "Отправить договор" in result["next_step"]["text"]
    assert "2026-11-20" in result["next_step"]["text"]
    assert result["verification"]["next_step_from_crm"] is True


def test_without_a_task_the_step_stays_the_models_or_empty() -> None:
    assert verify_brief(_brief(), _checks([]))["next_step"] is None
    step = _point("Уточнить бюджет у директора.")
    assert verify_brief(_brief(next_step=step), _checks([]))["next_step"]["text"] == step["text"]


def test_nothing_left_of_the_situation_is_said_honestly() -> None:
    result = verify_brief(_brief(situation=[_point("Клиент дважды отказал.")]), _checks([]))
    assert result["situation"][0]["text"].startswith("Сделка на этапе «Думает».")


def test_a_quote_about_something_else_does_not_support_a_point() -> None:
    assert not quote_supports(
        "Клиент интересовался сроками поставки и условиями гарантии.",
        "Сказал, что решение примет до конца месяца",
    )
    assert quote_supports(
        "Бюджет не утвержден, директор согласует его на этой неделе.",
        "Бюджет пока не утверждён, директор согласует на этой неделе.",
    )
    assert quote_supports(
        "Объём системы: 20 пользователей с личным кабинетом.",
        "двадцать пользователей, нужен личный кабинет",
    )


NOTES = (
    "Клиент спросил про сроки поставки. Сказал, что решение примет до конца месяца. "
    "Договорились созвониться в понедельник."
)


def _summary(points: list[tuple[str, str]], step: Any = None) -> dict[str, Any]:
    return {
        "summary": "Свободный текст модели.",
        "key_points": [{"text": t, "evidence_quote": q} for t, q in points],
        "next_step": step,
        "insufficient_data": False,
    }


def test_an_unsupported_point_goes_and_the_unchecked_summary_with_it() -> None:
    good = ("Решение будет принято до конца месяца.", "решение примет до конца месяца")
    bad = ("Клиент интересовался гарантией.", "Сказал, что решение примет до конца месяца")
    result = verify_call_summary(_summary([good, bad]), NOTES)
    assert [p["text"] for p in result["key_points"]] == [good[0]]
    assert result["summary"] == good[0]
    assert result["verification"]["dropped"] == 1


def test_an_agreement_in_the_notes_becomes_the_next_step_word_for_word() -> None:
    good = ("Решение будет принято до конца месяца.", "решение примет до конца месяца")
    result = verify_call_summary(_summary([good]), NOTES)
    assert result["next_step"]["text"] == "Договорились созвониться в понедельник."
    assert result["verification"]["next_step_from_notes"] is True
    assert result["summary"] == "Свободный текст модели."


def test_a_failed_agreement_is_not_a_next_step() -> None:
    good = ("Разговор был коротким.", "разговор короткий")
    notes = "Клиент занят, разговор короткий, ни о чём не договорились."
    assert verify_call_summary(_summary([good]), notes)["next_step"] is None


def test_a_deal_without_history_stays_without_history() -> None:
    assert build_payload(language="ru", today=TODAY, deal=_deal(), history=[])["history"] == []


def test_nothing_confirmed_means_not_enough_data() -> None:
    bad = ("Клиент купил лицензию.", "Перезвонил в среду")
    result = verify_call_summary(_summary([bad]), "Разговор ни о чём особенном.")
    assert result["insufficient_data"] is True and result["key_points"] == []
    assert result["summary"] == INSUFFICIENT_SUMMARY


def test_names_are_masked_in_any_case_and_companies_by_their_form() -> None:
    # Cyrillic on purpose: the legal form of a Kyrgyz company.
    masked = mask_personal_data(
        "Говорил с Айданой из ОсОО «Вымысел». Айдана просила счёт для ИП Асанов.",
        ["Айдана Асанова"],
    )
    assert "Айдан" not in masked and "Вымысел" not in masked and "Асанов" not in masked
    assert masked.count("[клиент]") == 2 and masked.count("[компания]") == 2


def test_ordinary_words_and_short_names_stay() -> None:
    text = "Клиент доволен, бюджет утверждён, счёт на 15000 сом."
    assert mask_personal_data(text, ["Ли", "Бек"]) == text
