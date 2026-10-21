"""Resilient navigation with retries and login redirect or anti-bot page detection."""

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
    """Open ``url`` with retries for network failures and timeouts.

    Raises:
        NavigationError: all attempts failed.
        CaptchaChallengeError: an anti-bot page was shown; retrying will not help.
        SessionExpiredError: redirected to the login page; the session expired.
    """
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            await session.page.goto(url, wait_until="domcontentloaded")
            break
        except PlaywrightError as exc:
            last_error = exc
            _log.warning("Navigation failed", extra={"attempt": attempt, "url": url})
            if attempt < attempts:
                await asyncio.sleep(base_delay_s * 2 ** (attempt - 1))
    else:
        raise NavigationError(f"Failed to open {url}: {last_error}") from last_error

    final_url = session.page.url
    if looks_like_challenge_url(final_url):
        raise CaptchaChallengeError(f"Anti-bot page encountered instead of {url}")
    if looks_like_login_url(final_url) and not looks_like_login_url(url):
        raise SessionExpiredError(f"Session is invalid: {url} redirected to {final_url}")
