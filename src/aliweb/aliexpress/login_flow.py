"""Automated login flow using a username and password.

Based on ali-coins (libs/ui/login.js), rewritten after analyzing real login page dumps (see
memory/07-login-flow.md). The page is a single-page application with these states:

    * AUTHENTICATED — the login page redirected away; the account is already signed in;
    * LANDING       — "Register by passkey"; click "Sign in with email/phone" to continue;
    * ACCOUNT_STEP  — login field;
    * PASSWORD_STEP — password field (login remains visible);
    * CAPTCHA       — slider dialog (baxia punish iframe) shown after an RGV587 response.

All actions use ``HumanInput``: character-by-character typing, mouse movement, and clicks.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from patchright.async_api import ElementHandle, Page, Response

from aliweb.aliexpress.login_screen import (
    LoginScreen,
    detect_screen,
    expected_screens_after,
    is_visible,
    wait_for_screen,
)
from aliweb.aliexpress.outcome import Decision, OutcomeConfig, OutcomeState, Snapshot, decide
from aliweb.aliexpress.selectors import LoginSelectors
from aliweb.aliexpress.signals import (
    LOGIN_RESPONSE_URL_RE,
    RISK_COOLDOWN_HOURS,
    TICKET_COOKIE,
    XMAN_COOKIE,
    LoginVerdict,
    classify_login_ret,
    extract_ret,
    read_cookie,
)
from aliweb.aliexpress.slider import SliderSolver
from aliweb.application.ports.auth import TwoFactorPrompt
from aliweb.domain.accounts import Credentials
from aliweb.domain.errors import (
    BrowserClosedError,
    CaptchaChallengeError,
    PageStructureError,
    RiskControlError,
)
from aliweb.infrastructure.browser.human import HumanInput
from aliweb.infrastructure.browser.session import BrowserSession
from aliweb.infrastructure.config import AliExpressSettings, HumanSettings
from aliweb.infrastructure.redaction import Redactor

_log = logging.getLogger(__name__)

_ACTIONABLE = {
    LoginScreen.AUTHENTICATED,
    LoginScreen.LANDING,
    LoginScreen.ACCOUNT_STEP,
    LoginScreen.PASSWORD_STEP,
    LoginScreen.CAPTCHA,
}
_OVERALL_TIMEOUT_S = 75.0
_MAX_NAVIGATION_STEPS = 6


class LoginResponseCapture:
    """Capture JSON responses from loginByPwd and track the response sequence and latest verdict."""

    def __init__(self, page: Page) -> None:
        self._page = page
        self._latest: dict[str, Any] | None = None
        self.seq = 0
        page.on("response", self._on_response)

    async def _on_response(self, response: Response) -> None:
        try:
            if not LOGIN_RESPONSE_URL_RE.search(response.url):
                return
            content_type = response.headers.get("content-type", "")
            if content_type and "json" not in content_type.lower():
                return
            body = await response.json()
            if isinstance(body, dict):
                self._latest = body
                self.seq += 1
        except Exception:
            _log.debug("Could not read loginByPwd response", exc_info=True)

    def verdict(self) -> LoginVerdict | None:
        return None if self._latest is None else classify_login_ret(extract_ret(self._latest))

    def detach(self) -> None:
        with contextlib.suppress(Exception):
            self._page.remove_listener("response", self._on_response)


class PasswordLoginFlow:
    def __init__(
        self,
        selectors: LoginSelectors,
        slider: SliderSolver,
        two_factor: TwoFactorPrompt,
        settings: AliExpressSettings,
        redactor: Redactor,
        human: HumanSettings,
        *,
        outcome_config: OutcomeConfig | None = None,
    ) -> None:
        self._sel = selectors
        self._slider = slider
        self._two_factor = two_factor
        self._settings = settings
        self._redactor = redactor
        self._human_settings = human
        self._outcome = outcome_config or OutcomeConfig()

    async def run(self, session: BrowserSession, credentials: Credentials) -> bool:
        """Log in using an open browser session.

        Returns:
            ``True`` if the site immediately recognized the account as authenticated.

        Raises:
            PageStructureError: a page state or element could not be recognized.
            RiskControlError: risk control rejected login and no slider appeared.
            CaptchaChallengeError: the slider was not solved or login was not confirmed.
            TwoFactorRequiredError: 2FA is required but a code cannot be entered.
            BrowserClosedError: the browser window closed during login.
        """
        # Credentials appear on the page and in dumps, so register them for redaction.
        self._redactor.register(credentials.password)
        self._redactor.register(credentials.login)

        page = session.page
        human = HumanInput(page, self._human_settings)
        _log.info("Logging in to account", extra={"login": credentials.masked_login})
        await page.goto(self._settings.login_url, wait_until="domcontentloaded")
        await session.checkpoint("login_page_opened")

        screen = await wait_for_screen(page, self._sel, accept=_ACTIONABLE, timeout_s=15)
        if screen is LoginScreen.AUTHENTICATED:
            _log.info("Account is already authenticated (login page redirected to home)")
            return True
        await human.settle()

        screen = await self._advance_to_password(session, human, credentials, screen)
        if screen is LoginScreen.AUTHENTICATED:
            return True

        capture = LoginResponseCapture(page)
        try:
            xman_before = read_cookie(await page.context.cookies(), XMAN_COOKIE)
            await self._submit_password(session, human, credentials)
            await self._await_outcome(session, human, capture, xman_before)
        finally:
            capture.detach()
        await session.checkpoint("login_succeeded")
        _log.info("Login successful", extra={"login": credentials.masked_login})
        return False

    # ── Navigate to the password step ─────────────────────────────────────
    async def _advance_to_password(
        self,
        session: BrowserSession,
        human: HumanInput,
        credentials: Credentials,
        screen: LoginScreen,
    ) -> LoginScreen:
        page = session.page
        landing_clicks = 0
        for _ in range(_MAX_NAVIGATION_STEPS):
            if screen in {LoginScreen.PASSWORD_STEP, LoginScreen.AUTHENTICATED}:
                return screen
            if screen is LoginScreen.CAPTCHA:
                await self._solve_captcha_once(page, human)
            elif screen is LoginScreen.LANDING:
                landing_clicks += 1
                await self._leave_landing(page, human, attempt=landing_clicks)
                await session.checkpoint("after_landing_click")
            elif screen is LoginScreen.ACCOUNT_STEP:
                await self._submit_account(page, human, credentials.login)
                await session.checkpoint("after_account_submit")
            else:
                raise PageStructureError(
                    "Unrecognized login page: no landing screen or login/password fields found. "
                    "The site layout may have changed; see the diagnostic dump."
                )
            # Wait for a different state than the one just acted on (see expected_screens_after).
            # If it does not change before timeout, the next iteration retries or uses a fallback.
            screen = await wait_for_screen(
                page, self._sel, accept=expected_screens_after(screen), timeout_s=8
            )
        raise PageStructureError(
            f"Could not reach the password step in {_MAX_NAVIGATION_STEPS} steps "
            f"(last state: {screen.value})"
        )

    async def _leave_landing(self, page: Page, human: HumanInput, *, attempt: int) -> None:
        """On the "Register by passkey" landing screen, click "Sign in with email/phone"."""
        if attempt > 2:
            raise PageStructureError(
                'Login form did not appear after clicking "Sign in with email/phone"; did the layout change?'
            )
        selector = (
            self._sel.landing_email_button if attempt == 1 else self._sel.landing_existing_account_button
        )
        button = await page.query_selector(selector)
        if button is None or not await button.is_visible():
            raise PageStructureError(f"Landing screen button not found: {selector}")
        await human.click(button)

    async def _submit_account(self, page: Page, human: HumanInput, login: str) -> None:
        field = await page.query_selector(self._sel.username_input)
        if field is None:
            raise PageStructureError("Login field disappeared from the page")
        if await field.input_value() != login:  # Do not type the login twice on a retry.
            await human.fill(field, login)
        await human.pause(0.25, 0.6)
        button = await self._visible(page, self._sel.continue_button)
        if button is not None:
            await human.click(button)
        else:
            await human.press_enter()

    # ── Password submission and outcome ───────────────────────────────────
    async def _submit_password(
        self, session: BrowserSession, human: HumanInput, credentials: Credentials
    ) -> None:
        page = session.page
        field = await page.query_selector(self._sel.password_input)
        if field is None:
            raise PageStructureError("Password field disappeared from the page")
        await human.fill(field, credentials.password.reveal())
        await human.pause(0.3, 0.8)
        await session.checkpoint("before_password_submit")
        await self._click_sign_in(page, human, field)
        await session.checkpoint("after_password_submit")

    async def _click_sign_in(self, page: Page, human: HumanInput, password_field: ElementHandle) -> None:
        button = await self._visible(page, self._sel.sign_in_button)
        if button is not None:
            await human.click(button)
        else:
            await password_field.focus()
            await human.press_enter()

    async def _await_outcome(
        self,
        session: BrowserSession,
        human: HumanInput,
        capture: LoginResponseCapture,
        xman_before: str,
    ) -> None:
        page = session.page
        context = page.context
        state = OutcomeState()
        deadline = time.monotonic() + _OVERALL_TIMEOUT_S
        while True:
            if page.is_closed():
                raise BrowserClosedError("Browser window closed during login")
            now = time.monotonic()
            if now > deadline:
                raise CaptchaChallengeError(
                    "Login was not confirmed before the timeout. Try manual login: "
                    "`aliweb login <id> --headful --manual`"
                )
            screen = await detect_screen(page, self._sel)
            cookies = await context.cookies()
            xman_after = read_cookie(cookies, XMAN_COOKIE)
            snapshot = Snapshot(
                now=now,
                screen=screen,
                verdict=capture.verdict(),
                verdict_seq=capture.seq,
                has_ticket=bool(read_cookie(cookies, TICKET_COOKIE)),
                xman_rotated=bool(xman_after) and xman_after != xman_before,
                two_factor_visible=screen is not LoginScreen.CAPTCHA
                and await is_visible(page, self._sel.two_factor_input),
            )
            decision = decide(snapshot, state, self._outcome)
            match decision:
                case Decision.SUCCESS:
                    return
                case Decision.SOLVE_CAPTCHA:
                    state.record_solve_attempt(snapshot)
                    _log.info(
                        "Login response requires captcha; solving slider",
                        extra={"attempt": state.captcha_attempts},
                    )
                    await session.checkpoint("captcha_dialog")
                    await self._solve_captcha_once(page, human)
                    state.solved_at = time.monotonic()
                case Decision.HANDLE_TWO_FACTOR:
                    state.two_factor_done = True
                    await self._handle_two_factor(page, human)
                case Decision.RESUBMIT:
                    state.resubmits += 1
                    _log.info('No new login request after slider; clicking "Sign in" again')
                    password = await page.query_selector(self._sel.password_input)
                    if password is not None:
                        await self._click_sign_in(page, human, password)
                    state.solved_at = time.monotonic()
                case Decision.FAIL_RISK:
                    raise self._risk_error(snapshot.verdict)
                case Decision.FAIL_CAPTCHA:
                    raise CaptchaChallengeError(
                        f"Slider was not solved after {state.captcha_attempts} attempts. "
                        "Try manual login: `aliweb login <id> --headful --manual`"
                    )
                case Decision.WAIT:
                    await asyncio.sleep(0.4)

    async def _solve_captcha_once(self, page: Page, human: HumanInput) -> None:
        solved = await self._slider.solve(page, human)
        _log.info("Slider %s", "solved" if solved else "not solved")

    async def _handle_two_factor(self, page: Page, human: HumanInput) -> None:
        _log.warning("AliExpress requires a verification code (2FA)")
        code = await self._two_factor.ask_code("Enter the verification code from email or SMS")
        if not code:
            return
        field = await page.query_selector(self._sel.two_factor_input)
        if field is None:
            return
        await human.fill(field, code)
        await human.pause(0.25, 0.6)
        submit = await self._visible(page, self._sel.two_factor_submit)
        if submit is not None:
            await human.click(submit)
        else:
            await human.press_enter()

    # ── Helpers ────────────────────────────────────────────────────────────
    @staticmethod
    async def _visible(page: Page, selector: str) -> ElementHandle | None:
        element = await page.query_selector(selector)
        if element is not None and await element.is_visible():
            return element
        return None

    @staticmethod
    def _risk_error(verdict: LoginVerdict | None) -> RiskControlError:
        raw = " ".join(verdict.items)[:200] if verdict else ""
        _log.error("Login rejected by risk control", extra={"ret": raw})
        return RiskControlError(
            "Login rejected by AliExpress risk control (RGV587); no captcha dialog appeared. "
            f"Recommended cooldown: {RISK_COOLDOWN_HOURS} hours. Or log in manually: "
            "`aliweb login <id> --headful --manual`.",
            cooldown_hours=RISK_COOLDOWN_HOURS,
        )
