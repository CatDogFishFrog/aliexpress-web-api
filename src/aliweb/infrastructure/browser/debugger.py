"""Реалізація ``PageDebugger``: відкрити URL і зробити дамп — БЕЗ входу в акаунт."""

from __future__ import annotations

import contextlib
import logging

from aliweb.application.accounts import AccountService
from aliweb.application.ports.debug import DebugOpenResult
from aliweb.domain.accounts import AccountId, StorageState
from aliweb.domain.profile import BrowserProfile
from aliweb.infrastructure.browser.session import BrowserSessionFactory
from aliweb.infrastructure.diagnostics.collector import DiagnosticsCollector

_log = logging.getLogger(__name__)


class AccountSessionDebugger:
    """Відкриває сторінку «як є»: з cookie збереженої сесії акаунта, якщо вона є, інакше анонімно.

    Принципово НЕ виконує вхід, НЕ перевіряє чинність сесії і НЕ зберігає оновлені cookie —
    інструмент для швидкого огляду того, що зараз бачить скрапер. Відкриває у середовищі (профілі)
    акаунта, щоб сторінка виглядала так само, як для скрапера.
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

    async def _resolve(
        self, account_id: AccountId | None, anonymous: bool
    ) -> tuple[StorageState | None, BrowserProfile | None, AccountId | None]:
        """Сесія та профіль акаунта (``anonymous`` — ні того, ні іншого: «чистий» браузер)."""
        if anonymous:
            return None, None, None
        target = account_id or await self._accounts.get_active_id()
        if target is None:
            return None, None, None
        account = await self._accounts.ensure_profile(target)  # AccountNotFoundError, якщо id невірний
        storage = account.session.storage_state if account.session else None
        return storage, account.profile, target

    async def open_and_dump(
        self,
        url: str,
        *,
        headless: bool | None = None,
        account_id: AccountId | None = None,
        anonymous: bool = False,
    ) -> DebugOpenResult:
        storage_state, profile, used = await self._resolve(account_id, anonymous)
        _log.info("Debug-відкриття сторінки", extra={"account": str(used) if used else "anonymous"})
        async with self._factory.open(storage_state, headless=headless, profile=profile) as session:
            await session.page.goto(url, wait_until="domcontentloaded")
            with contextlib.suppress(Exception):  # дати JS домалювати сторінку, але не чекати вічно
                await session.page.wait_for_load_state("networkidle", timeout=5000)
            path = await self._diagnostics.capture(
                session.page, reason="debug-open", recorder=session.recorder
            )
        return DebugOpenResult(
            dump_dir=str(path) if path else None,
            account_id=used,
            with_session=storage_state is not None,
            with_profile=profile is not None,
        )
