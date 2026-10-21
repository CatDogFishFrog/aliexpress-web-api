"""Manual login: automation is disabled and the program waits for the user to sign in.

After successful login, ``AliExpressAuthenticator`` captures the session and closes the browser.
Useful when automated login is blocked and the user must solve a captcha or use a passkey, QR code,
or social login. A regular Chrome or Edge browser is recommended (``--browser``).
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
_SETTLE_AFTER_SUCCESS_S = 2.0  # Allow the page to finish writing cookies.


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
        """Open the login page and wait for the user to sign in.

        Confirm success reliably rather than relying on navigation away from the login page, which
        could happen if the user only clicked the logo. Once there are signs of login, check in a
        new tab that the login page redirects away, as it does for authenticated accounts.

        Returns:
            ``True`` if the account was already authenticated before waiting began.

        Raises:
            ManualLoginTimeoutError: login did not complete within ``timeout_s``.
            BrowserClosedError: browser window closed before login completed.
        """
        page = session.page
        await page.goto(self._settings.login_url, wait_until="domcontentloaded")
        screen = await wait_for_screen(page, self._sel, accept=_ACTIONABLE, timeout_s=15)
        if screen is LoginScreen.AUTHENTICATED:
            _log.info("Account is already authenticated; manual login is not needed")
            return True

        _log.info(
            "Manual login: sign in using the browser window. Automation is disabled; waiting",
            extra={"timeout_s": int(timeout_s)},
        )
        xman_before = read_cookie(await page.context.cookies(), XMAN_COOKIE)
        deadline = self._clock() + timeout_s
        last_probe = float("-inf")
        while True:
            if page.is_closed():
                raise BrowserClosedError(
                    "Browser window closed before login completed; session was not saved"
                )
            now = self._clock()
            if now > deadline:
                raise ManualLoginTimeoutError(
                    f"Manual login did not complete within {int(timeout_s)} seconds "
                    "(increase --manual-timeout)"
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
                    _log.info("Login confirmed; capturing session")
                    await self._sleep(_SETTLE_AFTER_SUCCESS_S)
                    return False
            await self._sleep(self._poll_s)
