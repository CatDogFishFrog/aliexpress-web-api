"""Реалізація порту ``Authenticator`` для AliExpress."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from aliweb.aliexpress.login_flow import PasswordLoginFlow
from aliweb.aliexpress.manual_flow import ManualLoginFlow
from aliweb.aliexpress.session_probe import SessionProbe
from aliweb.application.accounts import utc_now
from aliweb.application.ports.auth import LoginRequest, LoginResult
from aliweb.domain.accounts import SessionState
from aliweb.domain.errors import MissingCredentialsError
from aliweb.infrastructure.browser.session import BrowserSessionFactory
from aliweb.infrastructure.config import AliExpressSettings

_log = logging.getLogger(__name__)


class AliExpressAuthenticator:
    def __init__(
        self,
        factory: BrowserSessionFactory,
        flow: PasswordLoginFlow,
        manual_flow: ManualLoginFlow,
        probe: SessionProbe,
        settings: AliExpressSettings,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._factory = factory
        self._flow = flow
        self._manual = manual_flow
        self._probe = probe
        self._settings = settings
        self._clock = clock

    async def login(self, request: LoginRequest) -> LoginResult:
        mode = request.mode
        manual = mode is not None and mode.manual
        if not manual and request.credentials is None:
            raise MissingCredentialsError("Для автоматичного входу потрібні логін і пароль")
        async with self._factory.open(
            request.storage_state,
            headless=mode.headless if mode else None,
            browser=mode.browser if mode else None,
        ) as session:
            if mode is not None and manual:
                already = await self._manual.run(session, timeout_s=mode.manual_timeout_s)
            else:
                assert request.credentials is not None  # перевірено вище
                already = await self._flow.run(session, request.credentials)
            state = await session.storage_state()
        return LoginResult(
            session=SessionState(storage_state=state, captured_at=self._clock()),
            already_authenticated=already,
        )

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
