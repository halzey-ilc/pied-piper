"""Payments with receipts and their verification (owner decision of 03.11.2026): a sale
counts in KPI, plans and reports only after its payment is confirmed."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from rest_framework.test import APIClient

from modules.access_control.domain.rbac import Role
from modules.audit.infrastructure.models import AuditRecord
from modules.notifications.infrastructure.models import NotificationRecord
from modules.payments.infrastructure.models import PaymentRecord
from tests.api import payment_helpers
from tests.api.payment_helpers import PDF_RECEIPT, PNG_RECEIPT, receipt_file
from tests.api.test_crm_scenarios import _deal, _ok, two_department_company
from tests.conftest import ALL_DATABASES, TenantFixture

pytestmark = pytest.mark.django_db(databases=ALL_DATABASES)


@pytest.fixture
def company(
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> dict[str, Any]:
    return two_department_company(make_user, tenants, client_for)


def _today() -> str:
    return str(datetime.now(UTC).date())


def post_payment(client: APIClient, receipt: Any = None, **body: Any) -> Any:
    body.setdefault("paid_on", _today())  # metrics default to the current month to date
    return payment_helpers.post_payment(client, receipt, **body)


def pay_confirmed(client: APIClient, verifier: APIClient, **body: Any) -> dict[str, Any]:
    body.setdefault("paid_on", _today())
    return payment_helpers.pay_confirmed(client, verifier, **body)


def _metrics(client: APIClient) -> dict[str, Any]:
    metrics: dict[str, Any] = _ok(client.get("/api/v1/payments/metrics/"))
    return metrics


def test_every_payment_needs_a_real_receipt(company: dict[str, Any]) -> None:
    rep = company["c"]["rep_north"]
    deal = _deal(rep, "Course")
    # No receipt: refused with a clear code.
    missing = rep.post(
        "/api/v1/payments/",
        {"deal_id": deal["id"], "amount": "100.00", "kind": "prepayment", "paid_on": _today()},
        format="json",
    )
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "receipt_required"
    # A text file named .pdf is not a receipt.
    fake = post_payment(
        rep,
        receipt_file(b"<html>receipt</html>", "check.pdf"),
        deal_id=deal["id"],
        amount="100.00",
    )
    assert fake.status_code == 400 and fake.json()["error"]["code"] == "receipt_invalid"
    assert PaymentRecord.objects.using("tenant_acme").count() == 0
    # A PNG photo is fine; its metadata comes back with the payment.
    ok = _ok(
        post_payment(
            rep,
            receipt_file(PNG_RECEIPT, "photo.png"),
            deal_id=deal["id"],
            amount="100.00",
            paid_on=_today(),
        ),
        201,
    )
    assert ok["status"] == "pending_verification" and ok["can_verify"] is False
    assert ok["receipt"]["content_type"] == "image/png"
    assert ok["receipt"]["filename"] == "photo.png"
    assert ok["receipt"]["uploaded_by_user_id"] == str(company["users"]["rep_north"].id)


def test_who_may_confirm_a_payment(company: dict[str, Any]) -> None:
    c = company["c"]
    deal = _deal(c["rep_north"], "Course")
    payment = _ok(post_payment(c["rep_north"], deal_id=deal["id"], amount="300.00"), 201)
    url = f"/api/v1/payments/{payment['id']}/confirm/"
    # The seller cannot confirm their own payment; nor can a head of another department.
    assert c["rep_north"].post(url).status_code == 403
    assert c["rop_south"].post(url).status_code in {403, 404}
    queue = _ok(c["rop_north"].get("/api/v1/payments/review/"))
    assert [(p["id"], p["can_verify"]) for p in queue["results"]] == [(payment["id"], True)]
    assert queue["results"][0]["deal"]["title"] == "Course"
    assert c["rep_north"].get("/api/v1/payments/review/").status_code == 403
    # The head of sales recording a payment cannot confirm it either: the director does.
    own = _ok(post_payment(c["rop_north"], deal_id=deal["id"], amount="10.00"), 201)
    assert c["rop_north"].post(f"/api/v1/payments/{own['id']}/confirm/").status_code == 403
    assert _ok(c["director"].post(f"/api/v1/payments/{own['id']}/confirm/"))["status"] == (
        "confirmed"
    )
    # Decided once: a second decision is a conflict.
    _ok(c["rop_north"].post(url))
    again = c["director"].post(url)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "payment_already_verified"


def test_only_director_confirms_own_payment_and_it_is_journalled(
    company: dict[str, Any],
    make_user: Callable[..., Any],
    tenants: dict[str, TenantFixture],
    client_for: Callable[..., APIClient],
) -> None:
    """Owner decision of 03.11.2026: a company's only director confirms their own payments;
    the journal records it as a self-confirmation. With a second director, they cannot."""
    c = company["c"]
    deal = _deal(c["director"], "Director's deal")
    own = _ok(post_payment(c["director"], deal_id=deal["id"], amount="50.00"), 201)
    assert own["can_verify"] is True
    assert _ok(c["director"].post(f"/api/v1/payments/{own['id']}/confirm/"))["status"] == (
        "confirmed"
    )
    journal = AuditRecord.objects.using("tenant_acme").filter(resource_id=own["id"])
    assert list(
        journal.filter(action="payment.self_confirmed").values_list("actor_user_id", flat=True)
    ) == [company["users"]["director"].id]

    second = make_user("director_two", tenant=tenants["acme"], role=Role.DIRECTOR)
    another = _ok(post_payment(c["director"], deal_id=deal["id"], amount="20.00"), 201)
    url = f"/api/v1/payments/{another['id']}/confirm/"
    assert c["director"].post(url).status_code == 403
    assert _ok(client_for(second, "acme").post(url))["status"] == "confirmed"


def test_confirmed_sale_payment_wins_the_deal_and_counts_everywhere(
    company: dict[str, Any],
) -> None:
    c, users = company["c"], company["users"]
    rep_id = str(users["rep_north"].id)
    month = _today()[:7]
    _ok(
        c["director"].put(
            "/api/v1/plans/",
            {
                "scope": "user",
                "scope_id": rep_id,
                "metric": "monthly_sales",
                "period": month,
                "target": "2",
            },
            format="json",
        )
    )
    deal = _deal(c["rep_north"], "Sale", amount="1000.00")
    payment = _ok(
        post_payment(
            c["rep_north"],
            deal_id=deal["id"],
            amount="400.00",
            kind="prepayment",
            paid_on=_today(),
            close_sale=True,
        ),
        201,
    )
    # Pending: not a sale, not money, the deal is still open.
    assert _metrics(c["director"])["sales_count"] == 0
    assert _metrics(c["director"])["received"] == "0"
    assert _ok(c["director"].get(f"/api/v1/sales/deals/{deal['id']}/"))["status"] == "open"
    summary = _ok(c["rep_north"].get(f"/api/v1/payments/deals/{deal['id']}/summary/"))
    assert (summary["confirmed_received"], summary["pending_amount"], summary["debt"]) == (
        "0",
        "400.00",
        "1000.00",
    )

    confirmed = _ok(c["rop_north"].post(f"/api/v1/payments/{payment['id']}/confirm/"))
    assert confirmed["status"] == "confirmed" and confirmed["verification_method"] == "manual"
    assert confirmed["verified_by_user_id"] == str(users["rop_north"].id)

    won = _ok(c["director"].get(f"/api/v1/sales/deals/{deal['id']}/"))
    assert won["status"] == "won"
    stages = {
        s["id"]: s for p in _ok(c["director"].get("/api/v1/sales/pipelines/")) for s in p["stages"]
    }
    assert stages[won["stage_id"]]["kind"] == "won"
    history = _ok(c["director"].get(f"/api/v1/sales/deals/{deal['id']}/stage-history/"))
    assert history["results"][-1]["to_stage_id"] == won["stage_id"]

    metrics = _metrics(c["director"])
    assert (metrics["sales_count"], metrics["received"], metrics["debt"]) == (1, "400.00", "600.00")
    assert (metrics["prepayments_count"], metrics["prepayments_amount"]) == (1, "400.00")
    (sales,) = _ok(c["director"].get(f"/api/v1/plans/progress/?scope_id={rep_id}"))["results"]
    assert sales["actual"] == "1"
    summary = _ok(c["rep_north"].get(f"/api/v1/payments/deals/{deal['id']}/summary/"))
    assert (summary["confirmed_received"], summary["pending_amount"], summary["debt"]) == (
        "400.00",
        "0",
        "600.00",
    )

    # A partial payment later: one sale, less debt.
    pay_confirmed(
        c["rep_north"], c["rop_north"], deal_id=deal["id"], amount="250.00", kind="additional"
    )
    summary = _ok(c["rep_north"].get(f"/api/v1/payments/deals/{deal['id']}/summary/"))
    assert (summary["confirmed_received"], summary["debt"], summary["confirmed_count"]) == (
        "650.00",
        "350.00",
        2,
    )
    assert _metrics(c["director"])["sales_count"] == 1


def test_rejected_payment_never_counts_and_tells_the_seller(company: dict[str, Any]) -> None:
    c, users = company["c"], company["users"]
    deal = _deal(c["rep_north"], "Doubtful", amount="500.00")
    payment = _ok(
        post_payment(c["rep_north"], deal_id=deal["id"], amount="500.00", close_sale=True), 201
    )
    url = f"/api/v1/payments/{payment['id']}/reject/"
    assert c["rop_north"].post(url, {"reason": " "}, format="json").status_code == 400
    rejected = _ok(
        c["rop_north"].post(url, {"reason": "Сумма в чеке 50 вместо 500"}, format="json")
    )
    assert rejected["status"] == "rejected"
    assert rejected["rejection_reason"] == "Сумма в чеке 50 вместо 500"
    assert _ok(c["director"].get(f"/api/v1/sales/deals/{deal['id']}/"))["status"] == "open"
    assert _metrics(c["director"])["sales_count"] == 0
    # History keeps it, with its receipt.
    listed = _ok(c["rep_north"].get(f"/api/v1/payments/?deal_id={deal['id']}"))
    assert [p["status"] for p in listed["results"]] == ["rejected"]
    notes = NotificationRecord.objects.using("tenant_acme").filter(
        recipient_user_id=users["rep_north"].id, category="payment"
    )
    assert notes.count() == 1 and "500" in notes.get().body


def test_receipt_is_streamed_to_those_who_see_the_payment(company: dict[str, Any]) -> None:
    c = company["c"]
    deal = _deal(c["rep_north"], "With receipt")
    payment = _ok(post_payment(c["rep_north"], deal_id=deal["id"], amount="100.00"), 201)
    url = f"/api/v1/payments/{payment['id']}/receipt/"
    response = c["rop_north"].get(url)
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"].startswith("inline;")
    assert response.content == PDF_RECEIPT
    assert c["rop_north"].get(url + "?download=1")["Content-Disposition"].startswith("attachment")
    # Another department's manager does not see it.
    assert c["rep_south"].get(url).status_code == 404


def test_refund_is_kept_apart_and_legacy_payments_still_count(company: dict[str, Any]) -> None:
    c, users = company["c"], company["users"]
    deal = _deal(c["rep_north"], "Refunded later", amount="300.00")
    pay_confirmed(c["rep_north"], c["rop_north"], deal_id=deal["id"], amount="300.00", kind="full")
    refund = _ok(
        post_payment(c["rep_north"], deal_id=deal["id"], amount="100.00", is_refund=True), 201
    )
    assert refund["status"] == "confirmed" and refund["receipt"] is None
    summary = _ok(c["rep_north"].get(f"/api/v1/payments/deals/{deal['id']}/summary/"))
    assert (summary["confirmed_received"], summary["refunded"], summary["debt"]) == (
        "200.00",
        "100.00",
        "100.00",
    )
    listed = _ok(c["rep_north"].get(f"/api/v1/payments/?deal_id={deal['id']}"))
    assert sorted(p["is_refund"] for p in listed["results"]) == [False, True]

    # A payment recorded before verification existed (migrated as confirmed) counts as is.
    legacy_deal = _deal(c["rep_north"], "Legacy", amount="700.00")
    PaymentRecord.objects.using("tenant_acme").create(
        id="00000000-0000-4000-8000-000000000001",
        amount="700.00",
        currency="KGS",
        kind="full",
        paid_on=_today(),
        seller_user_id=users["rep_north"].id,
        owner_user_id=users["rep_north"].id,
        deal_id=legacy_deal["id"],
        created_at=datetime.now(UTC),
    )
    assert _metrics(c["director"])["sales_count"] == 2
