from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from aliweb.aliexpress.login_screen import (
    LoginScreen,
    detect_screen,
    expected_screens_after,
    wait_for_screen,
)
from aliweb.aliexpress.selectors import SELECTORS
from tests.fakes_browser import FakeFrame, FakePage

SEL = SELECTORS.login
LOGIN = "https://www.aliexpress.com/p/ug-login-page/login.html"
CAPTCHA_FRAME = "https://login.aliexpress.com:443//user/buyer/login/loginByPwd/_____tmd_____/punish?x5step=2&action=captcha"


async def screen_of(page: FakePage) -> LoginScreen:
    return await detect_screen(cast(Any, page), SEL)


class TestDetectScreen:
    async def test_blank_page_is_unknown(self) -> None:
        assert await screen_of(FakePage("about:blank")) is LoginScreen.UNKNOWN

    async def test_redirect_away_means_authenticated(self) -> None:
        """The login page redirected an authenticated account to home (dumps 014733/014840)."""
        assert await screen_of(FakePage("https://www.aliexpress.com/")) is LoginScreen.AUTHENTICATED

    async def test_landing_register_by_passkey(self) -> None:
        page = FakePage(LOGIN)
        page.visible_selectors = {SEL.landing_email_button}
        assert await screen_of(page) is LoginScreen.LANDING

    async def test_account_step(self) -> None:
        page = FakePage(LOGIN)
        page.visible_selectors = {SEL.username_input}
        assert await screen_of(page) is LoginScreen.ACCOUNT_STEP

    async def test_password_step_even_if_login_field_still_visible(self) -> None:
        page = FakePage(LOGIN)
        page.visible_selectors = {SEL.username_input, SEL.password_input}  # This is the password step.
        assert await screen_of(page) is LoginScreen.PASSWORD_STEP

    async def test_captcha_dialog_frame_wins(self) -> None:
        page = FakePage(LOGIN)
        page.frames.append(FakeFrame(CAPTCHA_FRAME))
        page.visible_selectors = {SEL.password_input}
        assert await screen_of(page) is LoginScreen.CAPTCHA

    async def test_full_page_captcha(self) -> None:
        assert await screen_of(FakePage("https://x.aliexpress.com/punish?x5secdata=1")) is LoginScreen.CAPTCHA

    async def test_main_frame_with_captcha_like_url_is_not_a_dialog(self) -> None:
        page = FakePage(LOGIN)
        page.main_frame.url = CAPTCHA_FRAME  # Only non-main frames count as the dialog.
        assert await screen_of(page) is LoginScreen.UNKNOWN


class TestWaitForScreen:
    async def test_returns_immediately_when_already_accepted(self) -> None:
        page = FakePage(LOGIN)
        page.visible_selectors = {SEL.username_input}
        assert (
            await wait_for_screen(cast(Any, page), SEL, accept={LoginScreen.ACCOUNT_STEP}, timeout_s=5)
            is LoginScreen.ACCOUNT_STEP
        )

    async def test_waits_for_client_side_redirect(self) -> None:
        page = FakePage(LOGIN)  # Initially appears to be the login page.

        async def redirect_later() -> None:
            await asyncio.sleep(0.05)
            page.url = "https://www.aliexpress.com/"  # Then a script redirects it to home.

        task = asyncio.create_task(redirect_later())
        screen = await wait_for_screen(
            cast(Any, page), SEL, accept={LoginScreen.AUTHENTICATED}, timeout_s=2, poll_s=0.01
        )
        await task
        assert screen is LoginScreen.AUTHENTICATED

    async def test_timeout_returns_last_screen(self) -> None:
        page = FakePage(LOGIN)
        screen = await wait_for_screen(
            cast(Any, page), SEL, accept={LoginScreen.PASSWORD_STEP}, timeout_s=0.05, poll_s=0.01
        )
        assert screen is LoginScreen.UNKNOWN

    async def test_stops_when_page_closed(self) -> None:
        page = FakePage(LOGIN)
        page.closed = True
        screen = await wait_for_screen(
            cast(Any, page), SEL, accept={LoginScreen.PASSWORD_STEP}, timeout_s=30, poll_s=0.01
        )
        assert screen is LoginScreen.UNKNOWN

    @pytest.mark.parametrize("selector", [SEL.username_input, SEL.password_input, SEL.landing_email_button])
    def test_selectors_cover_confirmed_dom(self, selector: str) -> None:
        confirmed = {
            SEL.username_input: "#account",
            SEL.password_input: "#fm-login-password",
            SEL.landing_email_button: 'button[aria-label="init"]',
        }
        assert confirmed[selector] in selector  # Key selectors confirmed by page dumps are present.


class TestExpectedScreensAfter:
    """Regression: wait for a different screen after submission to avoid typing the login twice."""

    @pytest.mark.parametrize("acted_on", [LoginScreen.LANDING, LoginScreen.ACCOUNT_STEP, LoginScreen.CAPTCHA])
    def test_never_includes_the_screen_just_acted_on(self, acted_on: LoginScreen) -> None:
        assert acted_on not in expected_screens_after(acted_on)

    def test_account_step_leads_to_password_or_challenge_or_redirect(self) -> None:
        assert expected_screens_after(LoginScreen.ACCOUNT_STEP) == {
            LoginScreen.PASSWORD_STEP,
            LoginScreen.CAPTCHA,
            LoginScreen.AUTHENTICATED,
        }

    def test_landing_click_may_lead_to_form_or_password(self) -> None:
        after = expected_screens_after(LoginScreen.LANDING)
        assert {LoginScreen.ACCOUNT_STEP, LoginScreen.PASSWORD_STEP} <= after
