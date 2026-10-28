"""Outbound channel sender.

StubSender returns a synthetic id without contacting a provider; no real
messaging account is wired in the scaffold. A real adapter would send through
the provider (reusing modules.integrations.infrastructure.adapters).
"""

from __future__ import annotations

from uuid import uuid4

from modules.communications.domain.conversation import Channel


class StubSender:
    def send(self, *, channel: Channel, to_contact: str, text: str) -> str:
        return f"stub-{channel.value}-{uuid4().hex[:12]}"
