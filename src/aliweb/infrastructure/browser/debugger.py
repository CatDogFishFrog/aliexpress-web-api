"""``PageDebugger`` implementation: open a URL and capture diagnostics without logging in."""

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
    """Open a page as-is, using saved account cookies if available or browsing anonymously otherwise.

    Does not log in, verify the session, or save updated cookies. This tool quickly inspects what
    the scraper currently sees, using the account profile so the page has the same environment.
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
        """Account session and profile (``anonymous`` disables both for a clean browser)."""
        if anonymous:
            return None, None, None
        target = account_id or await self._accounts.get_active_id()
        if target is None:
            return None, None, None
        account = await self._accounts.ensure_profile(target)  # May raise AccountNotFoundError.
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
        _log.info("Opened page for debugging", extra={"account": str(used) if used else "anonymous"})
        async with self._factory.open(storage_state, headless=headless, profile=profile) as session:
            await session.page.goto(url, wait_until="domcontentloaded")
            with contextlib.suppress(Exception):  # Let page scripts settle, but do not wait indefinitely.
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
