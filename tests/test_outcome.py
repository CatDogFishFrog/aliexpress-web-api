"""Логіка рішень після відправки пароля. Тут жила головна помилка: RGV587 вважали вироком."""

from __future__ import annotations

import pytest

from aliweb.aliexpress.login_screen import LoginScreen
from aliweb.aliexpress.outcome import Decision, OutcomeConfig, OutcomeState, Snapshot, decide
from aliweb.aliexpress.signals import LoginVerdict, classify_login_ret

RISK = classify_login_ret(["FAIL_SYS_USER_VALIDATE", "RGV587_ERROR::SM::busy"])
OK = classify_login_ret(["SUCCESS::ok"])
CFG = OutcomeConfig()


def snap(
    now: float = 0.0,
    screen: LoginScreen = LoginScreen.PASSWORD_STEP,
    verdict: LoginVerdict | None = None,
    seq: int = 0,
    **kw: bool,
) -> Snapshot:
    return Snapshot(now=now, screen=screen, verdict=verdict, verdict_seq=seq, **kw)


class TestSuccess:
    def test_success_response(self) -> None:
        assert decide(snap(verdict=OK, seq=1), OutcomeState(), CFG) is Decision.SUCCESS

    def test_ticket_or_rotation(self) -> None:
        assert decide(snap(has_ticket=True), OutcomeState(), CFG) is Decision.SUCCESS
        assert decide(snap(xman_rotated=True), OutcomeState(), CFG) is Decision.SUCCESS

    def test_redirect_away_from_login_page(self) -> None:
        assert decide(snap(screen=LoginScreen.AUTHENTICATED), OutcomeState(), CFG) is Decision.SUCCESS

    def test_stale_risk_overrides_cookies_and_redirect(self) -> None:
        for kw in ({"has_ticket": True}, {"xman_rotated": True}):
            assert decide(snap(verdict=RISK, seq=1, **kw), OutcomeState(), CFG) is not Decision.SUCCESS
        assert (
            decide(snap(screen=LoginScreen.AUTHENTICATED, verdict=RISK, seq=1), OutcomeState(), CFG)
            is not Decision.SUCCESS
        )

    def test_nothing_yet_waits(self) -> None:
        assert decide(snap(), OutcomeState(), CFG) is Decision.WAIT


class TestRiskIsTriggerNotVerdict:
    def test_regression_risk_response_alone_does_not_fail_immediately(self) -> None:
        """Лог користувача: RGV587 → за 0,2 с RiskControlError, хоча слайдер ще вантажився."""
        state = OutcomeState()
        assert decide(snap(now=100.0, verdict=RISK, seq=1), state, CFG) is Decision.WAIT
        assert (
            decide(snap(now=103.0, verdict=RISK, seq=1), state, CFG) is Decision.WAIT
        )  # слайдер вантажиться ~3 с

    def test_captcha_dialog_is_solved(self) -> None:
        state = OutcomeState()
        assert (
            decide(snap(now=1, screen=LoginScreen.CAPTCHA, verdict=RISK, seq=1), state, CFG)
            is Decision.SOLVE_CAPTCHA
        )

    def test_gives_up_only_after_grace_when_dialog_never_appears(self) -> None:
        state = OutcomeState()
        assert decide(snap(now=0, verdict=RISK, seq=1), state, CFG) is Decision.WAIT
        assert decide(snap(now=CFG.risk_grace_s - 0.1, verdict=RISK, seq=1), state, CFG) is Decision.WAIT
        assert decide(snap(now=CFG.risk_grace_s + 0.1, verdict=RISK, seq=1), state, CFG) is Decision.FAIL_RISK

    def test_after_solving_new_success_response_wins(self) -> None:
        state = OutcomeState()
        first = snap(now=1, screen=LoginScreen.CAPTCHA, verdict=RISK, seq=1)
        assert decide(first, state, CFG) is Decision.SOLVE_CAPTCHA
        state.record_solve_attempt(first)
        state.solved_at = 5.0
        # діалог зник, сторінка сама повторила запит і отримала SUCCESS
        assert decide(snap(now=7, verdict=OK, seq=2), state, CFG) is Decision.SUCCESS

    def test_after_solving_wait_then_resubmit_once_then_fail(self) -> None:
        state = OutcomeState()
        first = snap(now=1, screen=LoginScreen.CAPTCHA, verdict=RISK, seq=1)
        decide(first, state, CFG)
        state.record_solve_attempt(first)
        state.solved_at = 5.0
        stale = lambda t: snap(now=t, verdict=RISK, seq=1)  # noqa: E731 — нового запиту немає
        assert decide(stale(8), state, CFG) is Decision.WAIT
        assert decide(stale(5 + CFG.retry_after_solve_s + 0.1), state, CFG) is Decision.RESUBMIT
        state.resubmits += 1
        state.solved_at = 20.0
        assert decide(stale(20 + CFG.retry_after_solve_s + 0.1), state, CFG) is Decision.FAIL_RISK

    def test_new_risk_response_after_solve_waits_for_next_dialog(self) -> None:
        state = OutcomeState()
        first = snap(now=1, screen=LoginScreen.CAPTCHA, verdict=RISK, seq=1)
        decide(first, state, CFG)
        state.record_solve_attempt(first)
        state.solved_at = 5.0
        assert decide(snap(now=6, verdict=RISK, seq=2), state, CFG) is Decision.WAIT

    def test_captcha_attempts_are_limited(self) -> None:
        state = OutcomeState()
        for i in range(CFG.max_captcha_attempts):
            s = snap(now=i, screen=LoginScreen.CAPTCHA, verdict=RISK, seq=i + 1)
            assert decide(s, state, CFG) is Decision.SOLVE_CAPTCHA
            state.record_solve_attempt(s)
        assert (
            decide(snap(now=9, screen=LoginScreen.CAPTCHA, verdict=RISK, seq=9), state, CFG)
            is Decision.FAIL_CAPTCHA
        )


class TestTwoFactor:
    def test_two_factor_once(self) -> None:
        state = OutcomeState()
        assert decide(snap(two_factor_visible=True), state, CFG) is Decision.HANDLE_TWO_FACTOR
        state.two_factor_done = True
        assert decide(snap(two_factor_visible=True), state, CFG) is Decision.WAIT

    def test_captcha_has_priority_over_two_factor(self) -> None:
        s = snap(screen=LoginScreen.CAPTCHA, two_factor_visible=True)
        assert decide(s, OutcomeState(), CFG) is Decision.SOLVE_CAPTCHA


def test_config_defaults_sane() -> None:
    assert CFG.risk_grace_s >= 6  # слайдер вантажиться ~3 с — запас обов'язковий
    assert CFG.max_captcha_attempts >= 2
    _ = pytest
