"""Detect the current AliExpress login page state.

The ``/p/ug-login-page/login.html`` page is a single-page application with multiple states
(see memory/07-login-flow.md). Detect the current state and act accordingly instead of failing
when an expected field is not immediately present.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Collection
from enum import StrEnum

from patchright.async_api import Page

from aliweb.aliexpress.pages import is_captcha_frame_url, looks_like_challenge_url, looks_like_login_url
from aliweb.aliexpress.selectors import LoginSelectors


class LoginScreen(StrEnum):
    AUTHENTICATED = "authenticated"  # The login page redirected; the account is already signed in.
    LANDING = "landing"  # Click "Sign in with email/phone" on the "Register by passkey" screen.
    ACCOUNT_STEP = "account_step"  # Only the login field is visible.
    PASSWORD_STEP = "password_step"  # The password field is visible; the login may remain visible.  # noqa: S105
    CAPTCHA = "captcha"  # Slider dialog (baxia punish iframe) or full captcha page.
    UNKNOWN = "unknown"  # Page is loading or has an unknown layout.


_ALL_ACTIONABLE = frozenset(
    {
        LoginScreen.AUTHENTICATED,
        LoginScreen.LANDING,
        LoginScreen.ACCOUNT_STEP,
        LoginScreen.PASSWORD_STEP,
        LoginScreen.CAPTCHA,
    }
)


def expected_screens_after(acted_on: LoginScreen) -> frozenset[LoginScreen]:
    """Return the states that indicate a transition after an action on ``acted_on``.

    Do not include ``acted_on`` itself. Immediately after a click or submit, the page has not changed;
    accepting the current state would return immediately and repeat the action (for example, typing
    the login a second time).
    """
    match acted_on:
        case LoginScreen.LANDING:
            return _ALL_ACTIONABLE - {LoginScreen.LANDING}
        case LoginScreen.ACCOUNT_STEP:
            return frozenset({LoginScreen.PASSWORD_STEP, LoginScreen.CAPTCHA, LoginScreen.AUTHENTICATED})
        case LoginScreen.CAPTCHA:
            return _ALL_ACTIONABLE - {LoginScreen.CAPTCHA}
        case _:
            return _ALL_ACTIONABLE


async def is_visible(page: Page, selector: str) -> bool:
    """Check whether an element matching the selector is visible without waiting."""
    try:
        return await page.locator(selector).first.is_visible()
    except Exception:
        return False


def captcha_frame_present(page: Page) -> bool:
    return any(is_captcha_frame_url(frame.url) for frame in page.frames if frame != page.main_frame)


async def detect_screen(page: Page, selectors: LoginSelectors) -> LoginScreen:
    url = page.url
    if not url or url.startswith("about:"):
        return LoginScreen.UNKNOWN
    if looks_like_challenge_url(url) or captcha_frame_present(page):
        return LoginScreen.CAPTCHA
    if not looks_like_login_url(url):
        return LoginScreen.AUTHENTICATED
    if await is_visible(page, selectors.password_input):
        return LoginScreen.PASSWORD_STEP
    if await is_visible(page, selectors.username_input):
        return LoginScreen.ACCOUNT_STEP
    if await is_visible(page, selectors.landing_email_button):
        return LoginScreen.LANDING
    return LoginScreen.UNKNOWN


async def wait_for_screen(
    page: Page,
    selectors: LoginSelectors,
    *,
    accept: Collection[LoginScreen],
    timeout_s: float,
    poll_s: float = 0.15,
) -> LoginScreen:
    """Wait until the screen matches ``accept`` and return the last detected state."""
    deadline = time.monotonic() + timeout_s
    screen = await detect_screen(page, selectors)
    while screen not in accept and time.monotonic() < deadline:
        if page.is_closed():
            break
        await asyncio.sleep(poll_s)
        screen = await detect_screen(page, selectors)
    return screen
