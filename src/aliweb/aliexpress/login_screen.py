"""Визначення, що зараз показано на сторінці входу AliExpress.

Сторінка ``/p/ug-login-page/login.html`` — це односторінковий застосунок із кількома екранами
(підтверджено дампами, див. memory/07-login-flow.md). Замість «шукати поле й падати» ми
розпізнаємо екран і діємо за ним.
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
    AUTHENTICATED = "authenticated"  # з'явилась НЕ сторінка входу: сайт перекинув (вже увійшли)
    LANDING = "landing"  # «Register by passkey» — потрібно натиснути «Sign in with email/phone»
    ACCOUNT_STEP = "account_step"  # видно лише поле логіну
    PASSWORD_STEP = "password_step"  # видно поле пароля (логін може лишатись видимим)  # noqa: S105
    CAPTCHA = "captcha"  # діалог слайдера (iframe baxia punish) або повна сторінка капчі
    UNKNOWN = "unknown"  # ще вантажиться або незнайома верстка


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
    """Які екрани вважати «перехід відбувся» після дії на екрані ``acted_on``.

    Важливо НЕ включати сам ``acted_on``: одразу після кліку/відправки сторінка ще не змінилась, і
    очікування «будь-якого дієвого екрана» повернулось би миттєво — дія повторилась би (напр.
    логін набрався б удруге).
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
    """Чи є видимий елемент за селектором (без очікування)."""
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
    """Чекає, поки екран стане одним із ``accept``. Повертає останній виявлений (можливо UNKNOWN)."""
    deadline = time.monotonic() + timeout_s
    screen = await detect_screen(page, selectors)
    while screen not in accept and time.monotonic() < deadline:
        if page.is_closed():
            break
        await asyncio.sleep(poll_s)
        screen = await detect_screen(page, selectors)
    return screen
