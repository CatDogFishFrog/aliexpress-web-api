"""Потік входу за паролем. Перенесено з ali-coins (libs/ui/login.js, `performMobileLogin`).

Послідовність:
  1. Відкрити сторінку входу.
  2. Якщо видно лише поле пароля (reauth-prompt, акаунт «впізнано») — вводимо лише пароль;
     інакше вводимо логін → Enter → (слайдер) → «Continue», якщо треба.
  3. Вводимо пароль, ловимо відповідь ``loginByPwd`` (ризик/успіх), (слайдер).
  4. Якщо з'явилось поле 2FA — просимо код через ``TwoFactorPrompt``.
  5. До 6 секунд опитуємо сильні сигнали успіху (тікет / ротація xman_us_t / SUCCESS).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from patchright.async_api import ElementHandle, Page, Response

from aliweb.aliexpress.selectors import LoginSelectors
from aliweb.aliexpress.signals import (
    LOGIN_RESPONSE_URL_RE,
    RISK_COOLDOWN_HOURS,
    TICKET_COOKIE,
    XMAN_COOKIE,
    LoginVerdict,
    classify_login_ret,
    decide_login_success,
    extract_ret,
    read_cookie,
)
from aliweb.aliexpress.slider import SliderSolver
from aliweb.application.ports.auth import TwoFactorPrompt
from aliweb.domain.accounts import Credentials
from aliweb.domain.errors import CaptchaChallengeError, PageStructureError, RiskControlError
from aliweb.infrastructure.browser.session import BrowserSession
from aliweb.infrastructure.config import AliExpressSettings
from aliweb.infrastructure.redaction import Redactor

_log = logging.getLogger(__name__)
_VERIFY_ATTEMPTS = 6


class LoginResponseCapture:
    """Перехоплює JSON-відповідь ендпоінта loginByPwd (останню отриману)."""

    def __init__(self, page: Page) -> None:
        self._page = page
        self.body: dict[str, Any] | None = None
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
                self.body = body
        except Exception:
            _log.debug("Відповідь loginByPwd нечитабельна", exc_info=True)

    def verdict(self) -> LoginVerdict | None:
        return None if self.body is None else classify_login_ret(extract_ret(self.body))

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
        *,
        selector_timeout_ms: int = 15_000,
    ) -> None:
        self._sel = selectors
        self._slider = slider
        self._two_factor = two_factor
        self._settings = settings
        self._redactor = redactor
        self._selector_timeout_ms = selector_timeout_ms

    async def run(self, session: BrowserSession, credentials: Credentials) -> None:
        """Виконує вхід у відкритій сесії. Успіх — без винятку.

        Raises:
            PageStructureError: не знайдено поле логіну/пароля.
            RiskControlError: відхилено ризик-контролем.
            CaptchaChallengeError: сильних сигналів успіху немає (виклик не подолано).
            TwoFactorRequiredError: потрібен 2FA без можливості ввести код.
        """
        # Пароль потрапляє в сторінку; реєструємо його, щоб дампи/логи завжди його маскували.
        self._redactor.register(credentials.password)
        page = session.page
        context = page.context
        _log.info("Вхід в акаунт", extra={"login": credentials.masked_login})
        await page.goto(self._settings.login_url, wait_until="domcontentloaded")
        await session.checkpoint("login_page_opened")

        password_input = await self._visible(page, self._sel.password_input, 1200)
        password_only = password_input is not None
        if password_only:
            _log.info("Виявлено reauth-prompt (лише пароль)")

        login_input: ElementHandle | None = None
        if not password_only:
            login_input = await self._find_username_input(page)
            if login_input is None:
                raise PageStructureError(
                    "Поле логіну (e-mail/телефон) не знайдено — можливо, змінилась верстка входу"
                )
            password_input = await self._submit_username(page, login_input, credentials.login)

        if password_input is None:
            raise PageStructureError("Поле пароля не з'явилось після введення логіну")

        capture = LoginResponseCapture(page)
        try:
            xman_before = read_cookie(await context.cookies(), XMAN_COOKIE)
            if password_only:
                await self._slider.try_solve(page)  # капча може бути видна ще до відправки
            await session.checkpoint("before_password_submit")
            await self._submit_password(page, password_input, credentials)
            await session.checkpoint("after_password_submit")

            verdict = capture.verdict()
            if verdict and verdict.risk:
                raise self._risk_error(verdict)

            await self._handle_two_factor(page)
            await self._await_success(session, capture, xman_before)
        finally:
            capture.detach()
        await session.checkpoint("login_succeeded")
        _log.info("Вхід успішний", extra={"login": credentials.masked_login})

    # ── кроки ──────────────────────────────────────────────────────────────
    @staticmethod
    async def _visible(page: Page, selector: str, timeout_ms: int) -> ElementHandle | None:
        try:
            return await page.wait_for_selector(selector, state="visible", timeout=timeout_ms)
        except Exception:
            return None

    async def _find_username_input(self, page: Page) -> ElementHandle | None:
        try:
            return await page.wait_for_selector(self._sel.username_input, timeout=self._selector_timeout_ms)
        except Exception:
            # wait_for_selector відсіює невидимі; прямий пошук — ні, тому перевіряємо видимість.
            fallback = await page.query_selector(self._sel.username_input)
            if fallback is not None and await fallback.is_visible():
                return fallback
            return None

    async def _submit_username(
        self, page: Page, login_input: ElementHandle, login: str
    ) -> ElementHandle | None:
        await login_input.fill(login)
        await login_input.press("Enter")
        await self._wait_hint(page, self._sel.after_username_hint)
        await page.wait_for_timeout(500)
        await self._slider.try_solve(page)

        password_input = await page.query_selector(self._sel.password_input)
        if password_input is None:
            continue_btn = await self._visible(page, self._sel.continue_button, 2000)
            if continue_btn is not None:
                await continue_btn.click()
                await self._wait_hint(page, self._sel.password_input)
                await page.wait_for_timeout(500)
                await self._slider.try_solve(page)
                password_input = await page.query_selector(self._sel.password_input)
        return password_input

    async def _submit_password(
        self, page: Page, password_input: ElementHandle, credentials: Credentials
    ) -> None:
        await password_input.fill(credentials.password.reveal())
        sign_in = await page.query_selector(self._sel.sign_in_button)
        if sign_in is not None:
            await page.evaluate("el => el.click()", sign_in)  # JS-клік обходить перекриття
        else:
            await password_input.press("Enter")
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("domcontentloaded")
        await self._wait_hint(page, self._sel.after_password_hint)
        await page.wait_for_timeout(1000)
        await self._slider.try_solve(page)

    async def _handle_two_factor(self, page: Page) -> None:
        code_input = await page.query_selector(self._sel.two_factor_input)
        # Лише ВИДИМЕ поле — невидимі допоміжні поля давали хибний 2FA в ali-coins.
        if code_input is None or not await code_input.is_visible():
            return
        _log.warning("AliExpress вимагає код підтвердження (2FA)")
        code = await self._two_factor.ask_code("Введіть код підтвердження з e-mail/SMS")
        if not code:
            return
        await code_input.fill(code)
        submit = await page.query_selector(self._sel.two_factor_submit)
        if submit is not None:
            await page.evaluate("el => el.click()", submit)
        else:
            await code_input.press("Enter")
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(1000)

    async def _await_success(
        self, session: BrowserSession, capture: LoginResponseCapture, xman_before: str
    ) -> None:
        context = session.page.context
        for attempt in range(_VERIFY_ATTEMPTS):
            verdict = capture.verdict()
            if verdict and verdict.risk:
                raise self._risk_error(verdict)
            cookies = await context.cookies()
            xman_after = read_cookie(cookies, XMAN_COOKIE)
            if decide_login_success(
                has_ticket=bool(read_cookie(cookies, TICKET_COOKIE)),
                xman_rotated=bool(xman_after) and xman_after != xman_before,
                response_success=bool(verdict and verdict.success),
            ):
                return
            if attempt < _VERIFY_ATTEMPTS - 1:
                await asyncio.sleep(1)
        raise CaptchaChallengeError(
            "Вхід не підтверджено: сильних сигналів автентифікації немає "
            "(ймовірно, не подолано антибот-виклик). Спробуйте `--headful`."
        )

    @staticmethod
    async def _wait_hint(page: Page, selector: str) -> None:
        with contextlib.suppress(Exception):
            await page.wait_for_selector(selector, timeout=2000)

    @staticmethod
    def _risk_error(verdict: LoginVerdict) -> RiskControlError:
        _log.error("Вхід відхилено ризик-контролем", extra={"ret": " ".join(verdict.items)[:200]})
        return RiskControlError(
            "Вхід відхилено ризик-контролем AliExpress (RGV587). "
            f"Рекомендована пауза: {RISK_COOLDOWN_HOURS} год.",
            cooldown_hours=RISK_COOLDOWN_HOURS,
        )
