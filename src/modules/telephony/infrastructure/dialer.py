"""Click-to-call dialer.

StubDialer returns a synthetic id without placing a real call: there is no
telephony account wired in the scaffold. A real adapter would call the
provider's API (through the integrations layer) and return its call id.
"""

from __future__ import annotations

from uuid import uuid4


class StubDialer:
    def dial(self, *, from_number: str, to_number: str) -> str:
        return "stub-" + uuid4().hex[:16]
