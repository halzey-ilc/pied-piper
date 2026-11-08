"""Check the WhatsApp setup for the selected provider.

    python manage.py whatsapp_check
    python manage.py whatsapp_check --public-url https://crm.example.com

Sends no message and never prints secret values. With ``--public-url`` (Green API
only) it also points the instance's webhook at this CRM, which reboots the instance.
"""

from __future__ import annotations

from typing import Any

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from modules.communications.infrastructure.connection import (
    UNAVAILABLE_STATE,
    GreenApiConnection,
)
from modules.integrations.infrastructure.http import build_http_client


class Command(BaseCommand):
    help = "Verify the WhatsApp settings against the provider."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--public-url",
            help="Public https address of this CRM; sets the Green API webhook to it.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        whatsapp = settings.INTEGRATIONS["WHATSAPP"]
        if whatsapp["PROVIDER"] == "green_api":
            self._check_green_api(whatsapp, options.get("public_url"))
        else:
            self._check_cloud_api(whatsapp)

    def _require(self, values: dict[str, object]) -> None:
        missing = [name for name, value in values.items() if not value]
        for name in values:
            self.stdout.write(f"{name}: {'missing' if name in missing else 'set'}")
        if missing:
            raise CommandError("Missing settings: " + ", ".join(missing))

    def _check_green_api(self, whatsapp: dict[str, Any], public_url: str | None) -> None:
        green = whatsapp["GREEN_API"]
        webhook_token = settings.INTEGRATIONS["SECRETS"]["green_api"]
        self._require(
            {
                "GREEN_API_URL": green["URL"],
                "GREEN_API_INSTANCE_ID": green["INSTANCE_ID"],
                "GREEN_API_TOKEN": green["TOKEN"],
                "GREEN_API_WEBHOOK_TOKEN": webhook_token,
                "WHATSAPP_TENANT_SLUG": whatsapp["TENANT_SLUG"],
                "WHATSAPP_LEAD_OWNER_USER_ID": whatsapp["LEAD_OWNER_USER_ID"],
            }
        )
        connection = GreenApiConnection(
            base_url=green["URL"],
            instance_id=green["INSTANCE_ID"],
            token=green["TOKEN"].get_secret_value(),
        )
        if public_url:
            self._set_webhook(
                connection, public_url, whatsapp["TENANT_SLUG"], webhook_token.get_secret_value()
            )
            return
        status = connection.status()
        if status.state == UNAVAILABLE_STATE:
            raise CommandError("Green API did not answer: check the URL, instance id and token.")
        if not status.connected:
            raise CommandError(f"The number is not connected ({status.state}): scan the QR code.")
        self.stdout.write(self.style.SUCCESS("The number is connected."))

    def _set_webhook(
        self, connection: GreenApiConnection, public_url: str, slug: str, token: str
    ) -> None:
        if not public_url.startswith("https://"):
            raise CommandError("--public-url must start with https://")
        url = f"{public_url.rstrip('/')}/api/v1/webhooks/green_api/{slug}/"
        if not connection.set_webhook(url=url, token=token):
            raise CommandError("Green API did not save the webhook settings.")
        self.stdout.write(
            self.style.SUCCESS(f"Webhook set to {url}. The instance reboots: allow 5 minutes.")
        )

    def _check_cloud_api(self, whatsapp: dict[str, Any]) -> None:
        self._require(
            {
                "WHATSAPP_APP_SECRET": settings.INTEGRATIONS["SECRETS"]["whatsapp"],
                "WHATSAPP_VERIFY_TOKEN": whatsapp["VERIFY_TOKEN"],
                "WHATSAPP_ACCESS_TOKEN": whatsapp["ACCESS_TOKEN"],
                "WHATSAPP_PHONE_NUMBER_ID": whatsapp["PHONE_NUMBER_ID"],
                "WHATSAPP_TENANT_SLUG": whatsapp["TENANT_SLUG"],
                "WHATSAPP_LEAD_OWNER_USER_ID": whatsapp["LEAD_OWNER_USER_ID"],
            }
        )
        phone = self._fetch_phone(
            whatsapp["API_BASE_URL"],
            whatsapp["PHONE_NUMBER_ID"],
            whatsapp["ACCESS_TOKEN"].get_secret_value(),
        )
        self.stdout.write(
            self.style.SUCCESS(
                "Meta accepted the token: "
                f"{phone.get('display_phone_number')} ({phone.get('verified_name')})"
            )
        )

    def _fetch_phone(self, base_url: str, phone_number_id: str, token: str) -> dict[str, Any]:
        try:
            with build_http_client(base_url) as client:
                response = client.get(
                    f"/{phone_number_id}",
                    params={"fields": "display_phone_number,verified_name"},
                    headers={"Authorization": f"Bearer {token}"},
                )
        except httpx.HTTPError as exc:
            raise CommandError(f"Could not reach the API: {type(exc).__name__}") from None
        if not response.is_success:
            raise CommandError(f"The API refused the request: HTTP {response.status_code}")
        data: dict[str, Any] = response.json()
        return data
