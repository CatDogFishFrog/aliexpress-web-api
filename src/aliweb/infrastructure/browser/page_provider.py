"""Provide authenticated pages for scraping with the active account."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import defaultdict
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime

from aliweb.application.accounts import AccountService, Clock, utc_now
from aliweb.application.auth import AuthService
from aliweb.application.ports.accounts import AccountRepository
from aliweb.domain.accounts import AccountId, SessionState
from aliweb.infrastructure.browser.session import BrowserSession, BrowserSessionFactory

_log = logging.getLogger(__name__)


class AuthenticatedPageProvider:
    """Provide a :class:`BrowserSession` for the active account.

        Guarantees:
            * verify or refresh the session before opening (``AuthService.ensure_session``);
            * serialize operations per account using an ``asyncio.Lock``;
            * persist updated cookies after use, even when an error occurs.
    """

    def __init__(
        self,
        accounts: AccountService,
        auth: AuthService,
        repository: AccountRepository,
        factory: BrowserSessionFactory,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._accounts = accounts
        self._auth = auth
        self._repo = repository
        self._factory = factory
        self._clock: Callable[[], datetime] = clock
        self._locks: defaultdict[AccountId, asyncio.Lock] = defaultdict(asyncio.Lock)

    @asynccontextmanager
    async def acquire(self, *, headless: bool | None = None) -> AsyncIterator[BrowserSession]:
        account_id = await self._accounts.require_active_id()
        async with self._locks[account_id]:
            session_state = await self._auth.ensure_session(account_id, headless=headless)
            account = await self._accounts.get(account_id)  # ensure_session guarantees a profile exists.
            async with self._factory.open(
                session_state.storage_state, headless=headless, profile=account.profile
            ) as session:
                try:
                    yield session
                finally:
                    await self._persist(account_id, session)

    async def _persist(self, account_id: AccountId, session: BrowserSession) -> None:
        """Best-effort persistence of updated cookies; failures must not mask results or errors."""
        with contextlib.suppress(Exception):
            fresh = SessionState(storage_state=await session.storage_state(), captured_at=self._clock())
            account = await self._accounts.get(account_id)
            await self._repo.save(account.with_session(fresh, now=self._clock()))
