"""Автоматичний потік входу за логіном і паролем.

Основа — ali-coins (libs/ui/login.js), але переписано за результатами аналізу дампів реальної
сторінки входу (див. memory/07-login-flow.md). Сторінка — односторінковий застосунок із екранами:

  * AUTHENTICATED — сторінка входу сама перекинула геть (акаунт уже увійшов);
  * LANDING       — «Register by passkey»: поля немає, треба натиснути «Sign in with email/phone»;
  * ACCOUNT_STEP  — поле логіну;
  * PASSWORD_STEP — поле пароля (логін лишається видимим);
  * CAPTCHA       — діалог слайдера (iframe baxia punish), що з'являється ПІСЛЯ відповіді RGV587.

Усі дії виконуються людиноподібно (``HumanInput``): набір по символах, рух миші, натискання.
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
    """Перехоплює JSON-відповіді ендпоінта loginByPwd. Веде лічильник ``seq`` і останній вердикт."""

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
            _log.debug("Відповідь loginByPwd нечитабельна", exc_info=True)

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
        """Виконує вхід у відкритій сесії.

        Returns:
            ``True`` — сайт одразу визнав акаунт увійшовшим (вхід не виконувався).

        Raises:
            PageStructureError: не вдалося розпізнати екран / знайти елементи.
            RiskControlError: відхилено ризик-контролем і слайдер не з'явився.
            CaptchaChallengeError: слайдер не пройдено або вхід не підтверджено.
            TwoFactorRequiredError: потрібен 2FA без можливості ввести код.
            BrowserClosedError: вікно закрито під час входу.
        """
        # Пароль і логін потрапляють на сторінку, а отже й у дампи — реєструємо для маскування.
        self._redactor.register(credentials.password)
        self._redactor.register(credentials.login)

        page = session.page
        human = HumanInput(page, self._human_settings)
        _log.info("Вхід в акаунт", extra={"login": credentials.masked_login})
        await page.goto(self._settings.login_url, wait_until="domcontentloaded")
        await session.checkpoint("login_page_opened")

        screen = await wait_for_screen(page, self._sel, accept=_ACTIONABLE, timeout_s=15)
        if screen is LoginScreen.AUTHENTICATED:
            _log.info("Акаунт уже авторизований (сторінка входу перекинула на головну)")
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
        _log.info("Вхід успішний", extra={"login": credentials.masked_login})
        return False

    # ── навігація екранами до кроку пароля ─────────────────────────────────
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
                    "Сторінка входу не розпізнана: немає ні стартового екрана, ні поля логіну/пароля. "
                    "Можливо, змінилась верстка — див. діагностичний дамп."
                )
            # Чекаємо ІНШИЙ екран, ніж той, на якому щойно діяли (див. expected_screens_after);
            # якщо за таймаут нічого не змінилось — наступна ітерація повторить/спробує запасний шлях.
            screen = await wait_for_screen(
                page, self._sel, accept=expected_screens_after(screen), timeout_s=8
            )
        raise PageStructureError(
            f"Не вдалося дійти до кроку пароля за {_MAX_NAVIGATION_STEPS} кроків "
            f"(останній екран: {screen.value})"
        )

    async def _leave_landing(self, page: Page, human: HumanInput, *, attempt: int) -> None:
        """Стартовий екран «Register by passkey»: тиснемо «Sign in with email/phone»."""
        if attempt > 2:
            raise PageStructureError(
                "Після кліку по «Sign in with email/phone» форма входу не з'явилась — змінилась верстка?"
            )
        selector = (
            self._sel.landing_email_button if attempt == 1 else self._sel.landing_existing_account_button
        )
        button = await page.query_selector(selector)
        if button is None or not await button.is_visible():
            raise PageStructureError(f"Кнопку стартового екрана не знайдено: {selector}")
        await human.click(button)

    async def _submit_account(self, page: Page, human: HumanInput, login: str) -> None:
        field = await page.query_selector(self._sel.username_input)
        if field is None:
            raise PageStructureError("Поле логіну зникло з сторінки")
        if await field.input_value() != login:  # повторна ітерація не має набирати логін двічі
            await human.fill(field, login)
        await human.pause(0.25, 0.6)
        button = await self._visible(page, self._sel.continue_button)
        if button is not None:
            await human.click(button)
        else:
            await human.press_enter()

    # ── пароль та очікування результату ────────────────────────────────────
    async def _submit_password(
        self, session: BrowserSession, human: HumanInput, credentials: Credentials
    ) -> None:
        page = session.page
        field = await page.query_selector(self._sel.password_input)
        if field is None:
            raise PageStructureError("Поле пароля зникло з сторінки")
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
                raise BrowserClosedError("Вікно браузера закрито під час входу")
            now = time.monotonic()
            if now > deadline:
                raise CaptchaChallengeError(
                    "Вхід не підтверджено за відведений час. Спробуйте ручний вхід: "
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
                        "Відповідь входу вимагає капчу — розв'язую слайдер",
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
                    _log.info("Після слайдера нового запиту немає — натискаю «Sign in» ще раз")
                    password = await page.query_selector(self._sel.password_input)
                    if password is not None:
                        await self._click_sign_in(page, human, password)
                    state.solved_at = time.monotonic()
                case Decision.FAIL_RISK:
                    raise self._risk_error(snapshot.verdict)
                case Decision.FAIL_CAPTCHA:
                    raise CaptchaChallengeError(
                        f"Слайдер не пройдено за {state.captcha_attempts} спроби. "
                        "Спробуйте ручний вхід: `aliweb login <id> --headful --manual`"
                    )
                case Decision.WAIT:
                    await asyncio.sleep(0.4)

    async def _solve_captcha_once(self, page: Page, human: HumanInput) -> None:
        solved = await self._slider.solve(page, human)
        _log.info("Слайдер %s", "пройдено" if solved else "не пройдено")

    async def _handle_two_factor(self, page: Page, human: HumanInput) -> None:
        _log.warning("AliExpress вимагає код підтвердження (2FA)")
        code = await self._two_factor.ask_code("Введіть код підтвердження з e-mail/SMS")
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

    # ── допоміжне ──────────────────────────────────────────────────────────
    @staticmethod
    async def _visible(page: Page, selector: str) -> ElementHandle | None:
        element = await page.query_selector(selector)
        if element is not None and await element.is_visible():
            return element
        return None

    @staticmethod
    def _risk_error(verdict: LoginVerdict | None) -> RiskControlError:
        raw = " ".join(verdict.items)[:200] if verdict else ""
        _log.error("Вхід відхилено ризик-контролем", extra={"ret": raw})
        return RiskControlError(
            "Вхід відхилено ризик-контролем AliExpress (RGV587), діалог капчі не з'явився. "
            f"Рекомендована пауза: {RISK_COOLDOWN_HOURS} год. Або увійдіть вручну: "
            "`aliweb login <id> --headful --manual`.",
            cooldown_hours=RISK_COOLDOWN_HOURS,
        )
