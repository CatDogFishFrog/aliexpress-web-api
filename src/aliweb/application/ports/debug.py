from __future__ import annotations

from typing import Protocol


class PageDebugger(Protocol):
    """Ручні інструменти дебагу сторінок (для розробки екстракторів)."""

    async def open_and_dump(self, url: str, *, headless: bool | None = None) -> str | None:
        """Відкриває ``url`` активним акаунтом і примусово зберігає діагностичний дамп.

        Повертає шлях до теки дампу (або ``None``, якщо зберегти не вдалося).
        """
        ...
