"""Усі селектори AliExpress — в одному місці (змінилась верстка → правимо тут).

Походження: потік входу перенесено з проєкту ali-coins (libs/selectors.js). Текстові
селектори (``has-text``) залежать від мови інтерфейсу; тут залишено en та pt, як в оригіналі.
CSS-селектори мовно-незалежні — віддаємо їм перевагу.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LoginSelectors:
    # Специфічні селектори входу: загальні input[type=text/email] зачіпають пошукові поля.
    username_input: str = (
        '#fm-login-id, input[name="loginId"], input.cosmos-input[type="email"], '
        'input.cosmos-input[type="text"], input[type="email"][name*="login" i]'
    )
    password_input: str = 'input[type="password"], #fm-login-password'  # noqa: S105 — CSS-селектор, не пароль
    continue_button: str = (
        'button.cosmos-btn-primary, button:has-text("Continue"), button:has-text("Continuar")'
    )
    sign_in_button: str = (
        'button.cosmos-btn-primary, button[type="submit"], '
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
    slider_handle: str = (
        '#nc_1_n1z, .btn_slide, span[class*="btn_slide"], #nc_1__scale_text .btn_slide, '
        'div[id*="nocaptcha"] .btn_slide'
    )
    slider_track: str = '#nc_1__scale_text, .nc_scale, div[id*="nocaptcha"]'
    # Що може з'явитися після відправки логіну/пароля (для короткого очікування).
    after_username_hint: str = (
        'input[type="password"], #fm-login-password, button.cosmos-btn-primary, #nc_1_n1z'
    )
    after_password_hint: str = 'input[placeholder*="code" i], input[type="tel"], [class*="aecoin"], #nc_1_n1z'  # noqa: S105 — CSS-селектор, не пароль


@dataclass(frozen=True, slots=True)
class Selectors:
    login: LoginSelectors = LoginSelectors()


SELECTORS = Selectors()
