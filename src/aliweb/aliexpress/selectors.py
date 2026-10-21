"""All AliExpress selectors in one place; update them here when the site layout changes.

Origin: the login flow was adapted from ali-coins (libs/selectors.js). Text selectors (``has-text``)
depend on the interface language; English and Portuguese variants are retained from the original.
Prefer language-independent CSS selectors where possible.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LoginSelectors:
    """Selectors for the ``/p/ug-login-page/login.html`` page.

        Dumps from October 2026 confirm two page states:
            * LANDING — "Register by passkey" with Register, "Already have an account?", "Sign in with
                email/phone" (aria-label="init"), Facebook, and Twitter buttons; no login fields.
            * Login form — ``input#account`` (email/phone) and ``input#fm-login-password``. On the password
                step the login field remains visible, along with "Back" and "Sign in" buttons.
        CSS module classes such as ``my-account--text--2Yt_prE`` are hashes, so use prefix selectors only.
    """

    # Login field. The first selectors are confirmed; the rest come from ali-coins legacy/mobile flows.
    username_input: str = (
        '#account, input[name="account"], input[autocomplete~="username"], '
        '#fm-login-id, input[name="loginId"], input.cosmos-input[type="email"], '
        'input.cosmos-input[type="text"], input[type="email"][name*="login" i]'
    )
    password_input: str = 'input[type="password"], #fm-login-password'  # noqa: S105 — CSS selector.
    # Landing screen.
    landing_email_button: str = (
        'button[aria-label="init"], button:has-text("Sign in with email/phone"), '
        'button:has-text("email/phone")'
    )
    landing_existing_account_button: str = 'button[aria-label="Already have an account?"]'
    # Continue button; its label is unconfirmed, so Enter is the fallback.
    continue_button: str = (
        'button.cosmos-btn-primary, button:has-text("Continue"), button:has-text("Continuar")'
    )
    sign_in_button: str = (
        'button[aria-label="Sign in"], button.cosmos-btn-primary, button[type="submit"], '
        'button:has-text("Sign in"), button:has-text("Entrar")'
    )
    # Use specific 2FA selectors; generic `name*=code` selectors caused false positives.
    two_factor_input: str = (
        '#fm-login-code, input[name="checkCode"], input[autocomplete="one-time-code"], '
        'input[placeholder*="verification code" i], '
        'input.check-code-input[type="tel"][maxlength="6"]'
    )
    two_factor_submit: str = (
        'button[type="submit"], button.cosmos-btn-primary, '
        'button:has-text("Confirm"), button:has-text("Verify")'
    )
    # Nocaptcha slider inside the baxia punish iframe (confirmed by a page dump).
    slider_handle: str = '#nc_1_n1z, .btn_slide, span[class*="btn_slide"], div[id*="nocaptcha"] .btn_slide'
    slider_track: str = '#nc_1__n1t, .nc_scale, #nc_1__scale_text, div[id*="nocaptcha"]'
    slider_loading: str = "#nc_1__n1t_loading, .nc-loading-circle"
    captcha_close: str = ".baxia-dialog-close"
    # Authenticated home page header marker; text depends on the selected language.
    home_account_marker: str = '[class*="my-account--text"]'


@dataclass(frozen=True, slots=True)
class Selectors:
    login: LoginSelectors = LoginSelectors()


SELECTORS = Selectors()
