"""What the company can see about its channel connections: is inbound configured, can
the CRM send through the channel, what arrived recently and what failed.

Provider credentials are platform settings (not per company): a channel is "configured"
for inbound only when its webhook secret is set. Outbound sending needs provider
credentials and a real adapter; none is wired in this installation, so outbound is
always reported as not connected — never as working.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from modules.access_control.domain.rbac import AccessPolicy, Permission, Principal
from modules.integrations.domain.providers import Provider

TITLES: Mapping[Provider, str] = {
    Provider.WHATSAPP: "WhatsApp",
    Provider.TELEGRAM: "Telegram",
    Provider.INSTAGRAM: "Instagram",
    Provider.TELEPHONY: "Телефония",
}


@dataclass(frozen=True, slots=True)
class InboundError:
    received_at: datetime
    status: str
    error: str


@dataclass(frozen=True, slots=True)
class InboundStats:
    received_total: int
    received_last_24h: int
    failed_total: int
    last_received_at: datetime | None
    recent_errors: list[InboundError]


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    provider: Provider
    title: str
    inbound_configured: bool
    webhook_path: str
    outbound_connected: bool
    stats: InboundStats


@dataclass(frozen=True, slots=True)
class CheckItem:
    name: str
    ok: bool
    message: str


class InboundEvents(Protocol):
    def stats(self, provider: Provider, now: datetime) -> InboundStats: ...

    def reachable(self) -> bool: ...


class IntegrationStatusService:
    def __init__(
        self,
        inbound_secrets: Mapping[Provider, bool],
        events: InboundEvents,
        tenant_slug: str,
        policy: AccessPolicy | None = None,
    ) -> None:
        self._secrets = inbound_secrets
        self._events = events
        self._slug = tenant_slug
        self._policy = policy or AccessPolicy()

    def statuses(self, principal: Principal, now: datetime) -> list[ProviderStatus]:
        self._policy.require(principal, Permission.INTEGRATIONS_MANAGE)
        return [self._status(provider, now) for provider in Provider]

    def check(self, principal: Principal, provider: Provider) -> list[CheckItem]:
        """Checks what can be checked from inside the CRM. Reaching the provider itself
        needs its credentials and a public webhook address — reported, not faked."""
        self._policy.require(principal, Permission.INTEGRATIONS_MANAGE)
        configured = self._secrets.get(provider, False)
        return [
            CheckItem(
                "inbound_secret",
                configured,
                "Секрет подписи входящих событий задан"
                if configured
                else "Секрет подписи не задан на сервере — входящие события отклоняются (503)",
            ),
            CheckItem(
                "event_store",
                self._events.reachable(),
                "Хранилище входящих событий компании доступно"
                if self._events.reachable()
                else "Хранилище входящих событий недоступно",
            ),
            CheckItem(
                "outbound",
                False,
                "Отправка через канал не подключена: нужны учётные данные провайдера "
                "(токен, номер или бот) и адаптер отправки",
            ),
            CheckItem(
                "provider_reachability",
                False,
                "Связь с провайдером не проверяется из CRM: провайдер должен прислать событие "  # noqa: RUF001
                f"на публичный адрес {self._path(provider)}",
            ),
        ]

    def _path(self, provider: Provider) -> str:
        return f"/api/v1/webhooks/{provider.value}/{self._slug}/"

    def _status(self, provider: Provider, now: datetime) -> ProviderStatus:
        return ProviderStatus(
            provider=provider,
            title=TITLES[provider],
            inbound_configured=self._secrets.get(provider, False),
            webhook_path=self._path(provider),
            outbound_connected=False,
            stats=self._events.stats(provider, now),
        )
