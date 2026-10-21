"""Interactive console prompts, including 2FA codes."""

from __future__ import annotations

import asyncio
import getpass
import sys

from aliweb.domain.errors import TwoFactorRequiredError


class ConsoleTwoFactorPrompt:
    """Implement ``TwoFactorPrompt`` by requesting a code in the terminal with hidden input.

    Raise :class:`TwoFactorRequiredError` when no TTY is available (cron, CI, or Docker without -it).
    After a timeout, the ``getpass`` thread remains blocked until Enter is pressed; this standard
    library limitation is acceptable for a CLI process that is about to exit.
    """

    def __init__(self, timeout_s: float = 120.0) -> None:
        self._timeout_s = timeout_s

    async def ask_code(self, hint: str) -> str | None:
        if not sys.stdin.isatty():
            raise TwoFactorRequiredError(
                "AliExpress requires a 2FA code, but interactive input is unavailable "
                "(no TTY). Log in manually with `aliweb login <id> --headful`."
            )
        try:
            code = await asyncio.wait_for(
                asyncio.to_thread(getpass.getpass, f">> {hint}: "), timeout=self._timeout_s
            )
        except TimeoutError:
            return None
        return code.strip() or None
