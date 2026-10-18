"""Ручний вхід: автоматизація вимкнена, програма лише чекає, поки людина увійде сама.

Після успіху (див. :meth:`ManualLoginFlow.run`) сесію забирає ``AliExpressAuthenticator``, а
браузер закривається. Підходить для випадків, коли автоматичний вхід блокує антибот: людина
проходить капчу/пасскі/QR/соцмережі сама. Рекомендується з реальним Chrome/Edge (``--browser``).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from aliweb.aliexpress.login_screen import LoginScreen, wait_for_screen
from aliweb.aliexpress.pages import looks_like_challenge_url, looks_like_login_url
from aliweb.aliexpress.selectors import LoginSelectors
from aliweb.aliexpress.session_probe import SessionProbe
from aliweb.aliexpress.signals import TICKET_COOKIE, XMAN_COOKIE, read_cookie
from aliweb.domain.errors import BrowserClosedError, ManualLoginTimeoutError
from aliweb.infrastructure.browser.session import BrowserSession
from aliweb.infrastructure.config import AliExpressSettings

_log = logging.getLogger(__name__)
_ACTIONABLE = {
    LoginScreen.AUTHENTICATED,
    LoginScreen.LANDING,
    LoginScreen.ACCOUNT_STEP,
    LoginScreen.PASSWORD_STEP,
    LoginScreen.CAPTCHA,
}
_PROBE_INTERVAL_S = 3.0
_SETTLE_AFTER_SUCCESS_S = 2.0  # дати сторінці дописати cookie (mtop cookie.render тощо)


class ManualLoginFlow:
    def __init__(
        self,
        selectors: LoginSelectors,
        probe: SessionProbe,
        settings: AliExpressSettings,
        *,
        poll_s: float = 1.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sel = selectors
        self._probe = probe
        self._settings = settings
        self._poll_s = poll_s
        self._sleep = sleep
        self._clock = clock

    async def run(self, session: BrowserSession, *, timeout_s: float) -> bool:
        """Відкриває сторінку входу й чекає на ручний вхід.

        Success визначається НАДІЙНО, а не за самим фактом переходу зі сторінки входу (людина
        могла просто клацнути логотип): коли є ознаки входу, у НОВІЙ вкладці перевіряється, що
        сторінка входу перекидає геть (так поводиться лише авторизований акаунт).

        Returns:
            ``True`` — акаунт уже був авторизований до початку (чекати не довелось).

        Raises:
            ManualLoginTimeoutError: вхід не завершено за ``timeout_s``.
            BrowserClosedError: вікно закрито до завершення входу.
        """
        page = session.page
        await page.goto(self._settings.login_url, wait_until="domcontentloaded")
        screen = await wait_for_screen(page, self._sel, accept=_ACTIONABLE, timeout_s=15)
        if screen is LoginScreen.AUTHENTICATED:
            _log.info("Акаунт уже авторизований — ручний вхід не потрібен")
            return True

        _log.info(
            "Ручний вхід: увійдіть в акаунт у вікні браузера. Програма не втручається й чекає",
            extra={"timeout_s": int(timeout_s)},
        )
        xman_before = read_cookie(await page.context.cookies(), XMAN_COOKIE)
        deadline = self._clock() + timeout_s
        last_probe = float("-inf")
        while True:
            if page.is_closed():
                raise BrowserClosedError("Вікно браузера закрито до завершення входу — сесію не збережено")
            now = self._clock()
            if now > deadline:
                raise ManualLoginTimeoutError(
                    f"Ручний вхід не завершено за {int(timeout_s)} с (збільште --manual-timeout)"
                )
            cookies = await page.context.cookies()
            xman_after = read_cookie(cookies, XMAN_COOKIE)
            url = page.url
            looks_done = (
                (
                    url.startswith("http")
                    and not looks_like_login_url(url)
                    and not looks_like_challenge_url(url)
                )
                or bool(read_cookie(cookies, TICKET_COOKIE))
                or (bool(xman_after) and xman_after != xman_before)
            )
            if looks_done and now - last_probe >= _PROBE_INTERVAL_S:
                last_probe = now
                if await self._probe.check_in_context(page.context):
                    _log.info("Вхід підтверджено — забираю сесію")
                    await self._sleep(_SETTLE_AFTER_SUCCESS_S)
                    return False
            await self._sleep(self._poll_s)
