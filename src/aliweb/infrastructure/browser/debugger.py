"""Реалізація ``PageDebugger``: відкрити URL і зробити дамп — БЕЗ входу в акаунт."""

from __future__ import annotations

import contextlib
import logging

from aliweb.application.accounts import AccountService
from aliweb.application.ports.debug import DebugOpenResult
from aliweb.domain.accounts import AccountId, StorageState
from aliweb.infrastructure.browser.session import BrowserSessionFactory
from aliweb.infrastructure.diagnostics.collector import DiagnosticsCollector

_log = logging.getLogger(__name__)


class AccountSessionDebugger:
    """Відкриває сторінку «як є»: з cookie збереженої сесії акаунта, якщо вона є, інакше анонімно.

    Принципово НЕ виконує вхід, НЕ перевіряє чинність сесії і НЕ зберігає оновлені cookie —
    інструмент для швидкого огляду того, що зараз бачить скрапер.
    """

    def __init__(
        self,
        accounts: AccountService,
        factory: BrowserSessionFactory,
        diagnostics: DiagnosticsCollector,
    ) -> None:
        self._accounts = accounts
        self._factory = factory
        self._diagnostics = diagnostics

    async def _resolve_session(
        self, account_id: AccountId | None, anonymous: bool
    ) -> tuple[StorageState | None, AccountId | None]:
        if anonymous:
            return None, None
        target = account_id or await self._accounts.get_active_id()
        if target is None:
            return None, None
        account = await self._accounts.get(target)  # AccountNotFoundError, якщо id невірний
        if account.session is None:
            return None, None
        return account.session.storage_state, target

    async def open_and_dump(
        self,
        url: str,
        *,
        headless: bool | None = None,
        account_id: AccountId | None = None,
        anonymous: bool = False,
    ) -> DebugOpenResult:
        storage_state, used = await self._resolve_session(account_id, anonymous)
        _log.info("Debug-відкриття сторінки", extra={"account": str(used) if used else "anonymous"})
        async with self._factory.open(storage_state, headless=headless) as session:
            await session.page.goto(url, wait_until="domcontentloaded")
            with contextlib.suppress(Exception):  # дати JS домалювати сторінку, але не чекати вічно
                await session.page.wait_for_load_state("networkidle", timeout=5000)
            path = await self._diagnostics.capture(
                session.page, reason="debug-open", recorder=session.recorder
            )
        return DebugOpenResult(dump_dir=str(path) if path else None, account_id=used)
