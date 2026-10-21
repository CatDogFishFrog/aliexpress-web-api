"""Check whether the saved session is still authenticated.

Observed behavior: for an authenticated account, the login page loads and a client script redirects
to the home page after about 0.3 seconds. This redirect indicates a valid session; cookies alone do
not, because ``xman_us_t`` can outlive a session.
"""

from __future__ import annotations

import logging

from patchright.async_api import BrowserContext, Page

from aliweb.aliexpress.login_screen import LoginScreen, wait_for_screen
from aliweb.aliexpress.selectors import LoginSelectors
from aliweb.domain.errors import CaptchaChallengeError
from aliweb.infrastructure.browser.session import BrowserSession
from aliweb.infrastructure.config import AliExpressSettings

_log = logging.getLogger(__name__)
_DECISIVE = {
    LoginScreen.AUTHENTICATED,
    LoginScreen.LANDING,
    LoginScreen.ACCOUNT_STEP,
    LoginScreen.PASSWORD_STEP,
    LoginScreen.CAPTCHA,
}


class SessionProbe:
    def __init__(self, selectors: LoginSelectors, settings: AliExpressSettings) -> None:
        self._sel = selectors
        self._settings = settings

    @property
    def _probe_url(self) -> str:
        return self._settings.session_probe_url or self._settings.login_url

    async def check_page(self, page: Page, *, timeout_s: float = 8.0) -> bool:
        """Open the probe page on ``page`` and return whether the session is valid."""
        await page.goto(self._probe_url, wait_until="domcontentloaded")
        screen = await wait_for_screen(page, self._sel, accept=_DECISIVE, timeout_s=timeout_s)
        _log.debug("Session probe", extra={"screen": screen.value})
        if screen is LoginScreen.CAPTCHA:
            raise CaptchaChallengeError("An anti-bot page appeared during session verification")
        return screen is LoginScreen.AUTHENTICATED

    async def check_in_context(self, context: BrowserContext) -> bool:
        """Check in a new tab in the same context without disturbing the user's current page."""
        page = await context.new_page()
        try:
            return await self.check_page(page)
        finally:
            try:
                await page.close()
            except Exception:
                _log.debug("Failed to close session probe tab", exc_info=True)

    async def is_authenticated(self, session: BrowserSession) -> bool:
        result = await self.check_page(session.page)
        await session.checkpoint("session_probe")
        return result
