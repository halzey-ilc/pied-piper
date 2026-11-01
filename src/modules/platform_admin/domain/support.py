"""Support requests and server-error records (pure domain)."""

from __future__ import annotations

from enum import StrEnum


class TicketKind(StrEnum):
    QUESTION = "question"
    PROBLEM = "problem"
    FEEDBACK = "feedback"
    IDEA = "idea"


class TicketStatus(StrEnum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class ErrorStatus(StrEnum):
    NEW = "new"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
