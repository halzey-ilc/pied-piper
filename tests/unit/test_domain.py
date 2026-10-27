from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from modules.audit.domain.entry import AuditEntry
from modules.customers.domain.customer import Customer, CustomerCreated
from modules.sales_kpi.domain.kpi import Aggregation, KpiDefinition, MetricSample, aggregate
from modules.tenant_registry.domain.placement import (
    ClusterCapacity,
    NoClusterCapacityError,
    choose_cluster,
)
from platform_core.domain.tenancy import database_name_for, validate_tenant_slug
from shared.domain.errors import DomainValidationError
from shared.domain.value_objects import EmailAddress, PhoneNumber


def test_customer_register_normalises_and_emits_event() -> None:
    owner = uuid4()
    customer, event = Customer.register(
        name="  Acme   LLP ", owner_user_id=owner, email="Sales@Acme.TEST", phone="+7 701 123 45 67"
    )
    assert customer.name == "Acme LLP"
    assert str(customer.email) == "sales@acme.test"
    assert str(customer.phone) == "+77011234567"
    assert isinstance(event, CustomerCreated)
    assert event.payload() == {"customer_id": str(customer.id), "owner_user_id": str(owner)}


@pytest.mark.parametrize("name", ["", "   ", "x" * 201])
def test_customer_name_rules(name: str) -> None:
    with pytest.raises(DomainValidationError):
        Customer.register(name=name, owner_user_id=uuid4())


def test_customer_update_tracks_changed_fields_and_version() -> None:
    customer, _ = Customer.register(name="A", owner_user_id=uuid4())
    event = customer.update_details(name="B", email="b@example.test")
    assert event is not None
    assert event.changed_fields == ("name", "email")
    assert customer.version == 2
    assert customer.update_details(name="B") is None  # no-op does not bump version
    assert customer.version == 2


@pytest.mark.parametrize("value", ["not-an-email", "a@b", "a b@c.de"])
def test_invalid_email(value: str) -> None:
    with pytest.raises(DomainValidationError):
        EmailAddress(value)


@pytest.mark.parametrize("value", ["87011234567", "+0123", "+1-abc"])
def test_invalid_phone(value: str) -> None:
    with pytest.raises(DomainValidationError):
        PhoneNumber(value)


@pytest.mark.parametrize("slug", ["ab", "-acme", "acme-", "Acme", "ac_me", "a" * 41, "ac me"])
def test_invalid_tenant_slugs(slug: str) -> None:
    with pytest.raises(DomainValidationError):
        validate_tenant_slug(slug)


def test_database_naming_is_deterministic_and_safe() -> None:
    assert database_name_for("big-corp", "crm_tenant_") == "crm_tenant_big_corp"
    with pytest.raises(DomainValidationError):
        database_name_for("x;drop", "crm_tenant_")


def test_cluster_choice_prefers_most_free_capacity() -> None:
    clusters = {
        "a": ClusterCapacity("a", True, 10, 9),
        "b": ClusterCapacity("b", True, 10, 2),
        "c": ClusterCapacity("c", False, 100, 0),
    }
    assert choose_cluster(clusters) == "b"
    with pytest.raises(NoClusterCapacityError):
        choose_cluster({"a": ClusterCapacity("a", True, 1, 1)})


def test_audit_action_format() -> None:
    AuditEntry(
        actor_user_id=None, action="customer.created", resource_type="customer", resource_id="1"
    )
    with pytest.raises(DomainValidationError):
        AuditEntry(actor_user_id=None, action="Created!", resource_type="customer", resource_id="1")


def _sample(metric: str, value: str) -> MetricSample:
    return MetricSample(metric=metric, value=Decimal(value), occurred_at=datetime.now(UTC))


def test_kpi_aggregations() -> None:
    samples = [_sample("deal_amount", "100"), _sample("deal_amount", "300"), _sample("leads", "4")]
    total = KpiDefinition("revenue", "Revenue", Aggregation.SUM, "deal_amount")
    avg = KpiDefinition("avg_deal", "Average deal", Aggregation.AVG, "deal_amount")
    count = KpiDefinition("deals", "Deals", Aggregation.COUNT, "deal_amount")
    ratio = KpiDefinition("revenue_per_lead", "Rev/lead", Aggregation.RATIO, "deal_amount", "leads")
    assert aggregate(total, samples) == Decimal(400)
    assert aggregate(avg, samples) == Decimal(200)
    assert aggregate(count, samples) == Decimal(2)
    assert aggregate(ratio, samples) == Decimal(100)
    assert aggregate(avg, []) is None
    assert aggregate(ratio, [_sample("deal_amount", "1")]) is None


def test_kpi_definition_validation() -> None:
    with pytest.raises(DomainValidationError):
        KpiDefinition("Bad Code", "x", Aggregation.SUM, "m")
    with pytest.raises(DomainValidationError):
        KpiDefinition("ratio_kpi", "x", Aggregation.RATIO, "m")  # no denominator
