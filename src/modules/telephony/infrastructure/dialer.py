"""Click-to-call dialer.

StubDialer returns a synthetic id without placing a real call: there is no
telephony account wired in the scaffold. A real adapter would call the
provider's API (through the integrations layer) and return its call id.
"""

from __future__ import annotations

from uuid import UUID, uuid4


class StubDialer:
    def dial(self, *, from_number: str, to_number: str, owner_user_id: UUID | None = None) -> str:
        return "stub-" + uuid4().hex[:16]
