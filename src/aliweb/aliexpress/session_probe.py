"""Перевірка, чи збережена сесія досі автентифікована."""

from __future__ import annotations

import contextlib
import re

from aliweb.aliexpress.selectors import LoginSelectors
from aliweb.domain.errors import CaptchaChallengeError
from aliweb.infrastructure.browser.session import BrowserSession
from aliweb.infrastructure.config import AliExpressSettings

LOGIN_URL_RE = re.compile(r"login|sign-?in|passport", re.IGNORECASE)
CHALLENGE_URL_RE = re.compile(r"punish|captcha|_____tmd_____", re.IGNORECASE)


def looks_like_login_url(url: str) -> bool:
    return bool(LOGIN_URL_RE.search(url))


class SessionProbe:
    """Відкриває сторінку, що вимагає входу, і дивиться, чи не перекинуло на логін.

    Самі cookie про дійсність не свідчать (``xman_us_t`` переживає сесію), тому перевірка —
    навігаційна. Адреса задається ``ALIWEB_SESSION_PROBE_URL`` (див. memory/11 — потребує
    підтвердження на живому сайті).
    """

    def __init__(self, selectors: LoginSelectors, settings: AliExpressSettings) -> None:
        self._sel = selectors
        self._settings = settings

    async def is_authenticated(self, session: BrowserSession) -> bool:
        page = session.page
        await page.goto(self._settings.session_probe_url, wait_until="domcontentloaded")
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=5000)
        await session.checkpoint("session_probe")

        if CHALLENGE_URL_RE.search(page.url):
            raise CaptchaChallengeError("Під час перевірки сесії показано антибот-сторінку")
        if looks_like_login_url(page.url):
            return False
        for selector in (self._sel.username_input, self._sel.password_input):
            element = await page.query_selector(selector)
            if element is not None and await element.is_visible():
                return False
        return True
