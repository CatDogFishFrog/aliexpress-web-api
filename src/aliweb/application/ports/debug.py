from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from aliweb.domain.accounts import AccountId


@dataclass(frozen=True, slots=True)
class DebugOpenResult:
    """Підсумок ``open_and_dump``: де дамп і з чиєю сесією відкривали сторінку."""

    dump_dir: str | None
    account_id: AccountId | None  # None — відкрито анонімно (без cookie)


class PageDebugger(Protocol):
    """Ручні інструменти дебагу сторінок (для розробки екстракторів)."""

    async def open_and_dump(
        self,
        url: str,
        *,
        headless: bool | None = None,
        account_id: AccountId | None = None,
        anonymous: bool = False,
    ) -> DebugOpenResult:
        """Відкриває ``url`` і зберігає діагностичний дамп. НЕ виконує вхід і НЕ змінює збережену сесію.

        Якщо в обраного акаунта (``account_id`` або активного) є збережена сесія — її cookie
        використовуються як є (без перевірки чинності); інакше сторінка відкривається анонімно.
        ``anonymous=True`` — завжди без cookie.
        """
        ...
