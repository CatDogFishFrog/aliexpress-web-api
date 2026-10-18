"""Усі селектори AliExpress — в одному місці (змінилась верстка → правимо тут).

Походження: потік входу перенесено з проєкту ali-coins (libs/selectors.js). Текстові
селектори (``has-text``) залежать від мови інтерфейсу; тут залишено en та pt, як в оригіналі.
CSS-селектори мовно-незалежні — віддаємо їм перевагу.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LoginSelectors:
    """Селектори сторінки входу ``/p/ug-login-page/login.html``.

    Підтверджено дампами (жовтень 2026): сторінка має ДВА стани.
      * LANDING — «Register by passkey»: кнопки Register / «Already have an account?» /
        «Sign in with email/phone» (aria-label="init") / Facebook / Twitter; поля входу немає.
      * Форма входу: ``input#account`` (e-mail/телефон) і ``input#fm-login-password``; на кроці пароля
        поле логіну лишається видимим, з'являється кнопка «Back» і «Sign in» (aria-label="Sign in").
    Класи на кшталт ``my-account--text--2Yt_prE`` — хеші CSS-модулів, тому лише префіксні селектори.
    """

    # Поле логіну. Перші — підтверджені; решта — з ali-coins (мобільний/старий потік).
    username_input: str = (
        '#account, input[name="account"], input[autocomplete~="username"], '
        '#fm-login-id, input[name="loginId"], input.cosmos-input[type="email"], '
        'input.cosmos-input[type="text"], input[type="email"][name*="login" i]'
    )
    password_input: str = 'input[type="password"], #fm-login-password'  # noqa: S105 — CSS-селектор
    # Стартовий екран (LANDING)
    landing_email_button: str = (
        'button[aria-label="init"], button:has-text("Sign in with email/phone"), '
        'button:has-text("email/phone")'
    )
    landing_existing_account_button: str = 'button[aria-label="Already have an account?"]'
    # Кнопка кроку «логін → далі» (мітка не підтверджена; fallback — Enter у полі).
    continue_button: str = (
        'button.cosmos-btn-primary, button:has-text("Continue"), button:has-text("Continuar")'
    )
    sign_in_button: str = (
        'button[aria-label="Sign in"], button.cosmos-btn-primary, button[type="submit"], '
        'button:has-text("Sign in"), button:has-text("Entrar")'
    )
    # 2FA: лише специфічні селектори (загальні `name*=code` давали хибні спрацювання).
    two_factor_input: str = (
        '#fm-login-code, input[name="checkCode"], input[autocomplete="one-time-code"], '
        'input[placeholder*="verification code" i], '
        'input.check-code-input[type="tel"][maxlength="6"]'
    )
    two_factor_submit: str = (
        'button[type="submit"], button.cosmos-btn-primary, '
        'button:has-text("Confirm"), button:has-text("Verify")'
    )
    # Слайдер nocaptcha всередині iframe baxia punish (підтверджено дампом).
    slider_handle: str = '#nc_1_n1z, .btn_slide, span[class*="btn_slide"], div[id*="nocaptcha"] .btn_slide'
    slider_track: str = '#nc_1__n1t, .nc_scale, #nc_1__scale_text, div[id*="nocaptcha"]'
    slider_loading: str = "#nc_1__n1t_loading, .nc-loading-circle"
    captcha_close: str = ".baxia-dialog-close"
    # Маркер авторизованої шапки головної: «Hi, <ім'я>» (текст залежить від мови).
    home_account_marker: str = '[class*="my-account--text"]'


@dataclass(frozen=True, slots=True)
class Selectors:
    login: LoginSelectors = LoginSelectors()


SELECTORS = Selectors()
