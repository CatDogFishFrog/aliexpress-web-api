"""Інтерактивні запити через консоль (код 2FA)."""

from __future__ import annotations

import asyncio
import getpass
import sys

from aliweb.domain.errors import TwoFactorRequiredError


class ConsoleTwoFactorPrompt:
    """Реалізує ``TwoFactorPrompt``: просить код у терміналі (введення приховане).

    Без TTY (cron, CI, Docker без -it) піднімає :class:`TwoFactorRequiredError`.
    Примітка: після таймауту потік із ``getpass`` лишається заблокованим до Enter —
    це обмеження stdlib, для CLI-процесу, що завершується, несуттєво.
    """

    def __init__(self, timeout_s: float = 120.0) -> None:
        self._timeout_s = timeout_s

    async def ask_code(self, hint: str) -> str | None:
        if not sys.stdin.isatty():
            raise TwoFactorRequiredError(
                "AliExpress вимагає код 2FA, а інтерактивне введення недоступне "
                "(немає TTY). Виконайте вхід вручну: `aliweb login <id> --headful`."
            )
        try:
            code = await asyncio.wait_for(
                asyncio.to_thread(getpass.getpass, f">> {hint}: "), timeout=self._timeout_s
            )
        except TimeoutError:
            return None
        return code.strip() or None
