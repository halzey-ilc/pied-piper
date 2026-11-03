"""Workspace layout: which blocks of a manager's «Мой день» are shown and in what order.

The head of sales configures it for their department, the director for the whole company or
any department. A manager only reads the effective layout. Hiding a block is presentation,
never an access rule: the data behind each block keeps its own permissions.
"""

from __future__ import annotations

from dataclasses import dataclass

from shared.domain.errors import DomainValidationError

# Known blocks of «Мой день», in the default order.
BLOCK_IDS: tuple[str, ...] = ("work_session", "counters", "plans", "month_metrics", "focus_deals")


@dataclass(frozen=True, slots=True)
class Block:
    id: str
    visible: bool


@dataclass(frozen=True, slots=True)
class Layout:
    blocks: tuple[Block, ...]

    @classmethod
    def default(cls) -> Layout:
        return cls(tuple(Block(id=block_id, visible=True) for block_id in BLOCK_IDS))

    @classmethod
    def of(cls, blocks: list[tuple[str, bool]]) -> Layout:
        """Validated layout: known ids only, each once; blocks not listed are appended visible
        (a block added to the product later appears instead of silently staying hidden)."""
        seen: list[str] = []
        result: list[Block] = []
        for block_id, visible in blocks:
            if block_id not in BLOCK_IDS:
                raise DomainValidationError(f"Unknown workspace block: {block_id}.")
            if block_id in seen:
                raise DomainValidationError(f"Workspace block listed twice: {block_id}.")
            seen.append(block_id)
            result.append(Block(id=block_id, visible=visible))
        result.extend(Block(id=b, visible=True) for b in BLOCK_IDS if b not in seen)
        return cls(tuple(result))
