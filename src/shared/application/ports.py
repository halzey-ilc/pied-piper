"""Generic application ports."""

from __future__ import annotations

from types import TracebackType
from typing import Protocol, Self


class UnitOfWork(Protocol):
    """Transaction boundary. Everything inside ``with uow:`` commits or rolls back together."""

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
