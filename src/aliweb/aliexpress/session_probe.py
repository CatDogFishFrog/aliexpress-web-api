"""Перевірка, чи збережена сесія досі автентифікована.

Метод (підтверджено дампами): для авторизованого акаунта сторінка входу завантажується й
через ~0,3 с клієнтським скриптом перекидає на головну. Тому «відкрили сторінку входу й нас
перекинуло геть» ⇔ сесія дійсна. Самі cookie про це не свідчать (``xman_us_t`` переживає сесію).
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
        """Відкриває сторінку перевірки на ``page`` і каже, чи сесія дійсна."""
        await page.goto(self._probe_url, wait_until="domcontentloaded")
        screen = await wait_for_screen(page, self._sel, accept=_DECISIVE, timeout_s=timeout_s)
        _log.debug("Перевірка сесії", extra={"screen": screen.value})
        if screen is LoginScreen.CAPTCHA:
            raise CaptchaChallengeError("Під час перевірки сесії показано антибот-сторінку")
        return screen is LoginScreen.AUTHENTICATED

    async def check_in_context(self, context: BrowserContext) -> bool:
        """Перевірка в НОВІЙ вкладці того ж контексту — не чіпає сторінку, з якою працює людина."""
        page = await context.new_page()
        try:
            return await self.check_page(page)
        finally:
            try:
                await page.close()
            except Exception:
                _log.debug("Не вдалося закрити вкладку перевірки", exc_info=True)

    async def is_authenticated(self, session: BrowserSession) -> bool:
        result = await self.check_page(session.page)
        await session.checkpoint("session_probe")
        return result
