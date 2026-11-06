"""Check the WhatsApp setup: every setting is present and Meta accepts the token.

    python manage.py whatsapp_check

Read-only: it asks Meta about the phone number and sends no message. Secret values
are never printed.
"""

from __future__ import annotations

from typing import Any

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from modules.integrations.infrastructure.http import build_http_client


class Command(BaseCommand):
    help = "Verify the WhatsApp settings and the access token against the Cloud API."

    def handle(self, *args: Any, **options: Any) -> None:
        whatsapp = settings.INTEGRATIONS["WHATSAPP"]
        required = {
            "WHATSAPP_APP_SECRET": settings.INTEGRATIONS["SECRETS"]["whatsapp"],
            "WHATSAPP_VERIFY_TOKEN": whatsapp["VERIFY_TOKEN"],
            "WHATSAPP_ACCESS_TOKEN": whatsapp["ACCESS_TOKEN"],
            "WHATSAPP_PHONE_NUMBER_ID": whatsapp["PHONE_NUMBER_ID"],
            "WHATSAPP_LEAD_OWNER_USER_ID": whatsapp["LEAD_OWNER_USER_ID"],
        }
        missing = [name for name, value in required.items() if not value]
        for name in required:
            self.stdout.write(f"{name}: {'missing' if name in missing else 'set'}")
        if missing:
            raise CommandError("Missing settings: " + ", ".join(missing))

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
