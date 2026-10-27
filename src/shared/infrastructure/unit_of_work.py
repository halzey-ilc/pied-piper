"""Django implementation of the UnitOfWork port."""

from __future__ import annotations

from types import TracebackType
from typing import Self

from django.db import transaction


class DjangoUnitOfWork:
    """Wraps ``transaction.atomic`` on one explicit database alias."""

    def __init__(self, using: str) -> None:
        self.using = using
        self._atomic: transaction.Atomic | None = None

    def __enter__(self) -> Self:
        self._atomic = transaction.atomic(using=self.using)
        self._atomic.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._atomic is None:
            raise RuntimeError("UnitOfWork exited without being entered")
        atomic, self._atomic = self._atomic, None
        atomic.__exit__(exc_type, exc, tb)
