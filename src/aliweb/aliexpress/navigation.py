"""Безпечна навігація: ретраї, виявлення редиректу на логін та антибот-сторінок."""

from __future__ import annotations

import asyncio
import logging

from patchright.async_api import Error as PlaywrightError

from aliweb.aliexpress.pages import looks_like_challenge_url, looks_like_login_url
from aliweb.domain.errors import CaptchaChallengeError, NavigationError, SessionExpiredError
from aliweb.infrastructure.browser.session import BrowserSession

_log = logging.getLogger(__name__)


async def open_checked(
    session: BrowserSession, url: str, *, attempts: int = 3, base_delay_s: float = 1.0
) -> None:
    """Відкриває ``url`` з ретраями на мережеві збої/таймаути.

    Raises:
        NavigationError: усі спроби вичерпано.
        CaptchaChallengeError: показано антибот-сторінку (ретрай не допоможе).
        SessionExpiredError: перекинуло на сторінку входу (сесія слетіла).
    """
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            await session.page.goto(url, wait_until="domcontentloaded")
            break
        except PlaywrightError as exc:
            last_error = exc
            _log.warning("Навігація не вдалась", extra={"attempt": attempt, "url": url})
            if attempt < attempts:
                await asyncio.sleep(base_delay_s * 2 ** (attempt - 1))
    else:
        raise NavigationError(f"Не вдалося відкрити {url}: {last_error}") from last_error

    final_url = session.page.url
    if looks_like_challenge_url(final_url):
        raise CaptchaChallengeError(f"Антибот-сторінка замість {url}")
    if looks_like_login_url(final_url) and not looks_like_login_url(url):
        raise SessionExpiredError(f"Сесія недійсна: {url} перекинуло на {final_url}")
