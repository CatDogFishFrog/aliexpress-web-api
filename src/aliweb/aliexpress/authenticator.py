"""Реалізація порту ``Authenticator`` для AliExpress."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from aliweb.aliexpress.login_flow import PasswordLoginFlow
from aliweb.aliexpress.session_probe import SessionProbe
from aliweb.application.accounts import utc_now
from aliweb.application.ports.auth import LoginRequest
from aliweb.domain.accounts import SessionState
from aliweb.infrastructure.browser.session import BrowserSessionFactory
from aliweb.infrastructure.config import AliExpressSettings

_log = logging.getLogger(__name__)


class AliExpressAuthenticator:
    def __init__(
        self,
        factory: BrowserSessionFactory,
        flow: PasswordLoginFlow,
        probe: SessionProbe,
        settings: AliExpressSettings,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._factory = factory
        self._flow = flow
        self._probe = probe
        self._settings = settings
        self._clock = clock

    async def login(self, request: LoginRequest) -> SessionState:
        mode = request.mode
        async with self._factory.open(
            request.storage_state,
            headless=mode.headless if mode else None,
            browser=mode.browser if mode else None,
        ) as session:
            await self._flow.run(session, request.credentials)
            return SessionState(storage_state=await session.storage_state(), captured_at=self._clock())

    async def is_session_valid(self, session: SessionState, *, headless: bool | None = None) -> bool:
        async with self._factory.open(session.storage_state, headless=headless) as browser_session:
            return await self._probe.is_authenticated(browser_session)

    async def logout(self, session: SessionState) -> None:
        """Якщо задано ``ALIWEB_LOGOUT_URL`` — відкриває його; інакше вихід лише локальний."""
        if not self._settings.logout_url:
            _log.debug("ALIWEB_LOGOUT_URL не задано — вихід лише локальний")
            return
        async with self._factory.open(session.storage_state) as browser_session:
            await browser_session.page.goto(self._settings.logout_url, wait_until="domcontentloaded")
