"""Pure decision logic for the login flow after password submission.

This logic is isolated because an earlier implementation treated RGV587 from ``loginByPwd`` as a
final rejection. It actually triggers a slider (baxia punish iframe) that loads after about three
seconds; after it is solved, the page retries the login request automatically.

The :func:`decide` function does not access the browser and is fully covered by unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from aliweb.aliexpress.login_screen import LoginScreen
from aliweb.aliexpress.signals import LoginVerdict, decide_login_success


class Decision(StrEnum):
    SUCCESS = "success"
    SOLVE_CAPTCHA = "solve_captcha"
    HANDLE_TWO_FACTOR = "handle_two_factor"
    RESUBMIT = "resubmit"  # No new login request after the slider; click "Sign in" again.
    WAIT = "wait"
    FAIL_RISK = "fail_risk"  # Risk-control rejection without a captcha dialog.
    FAIL_CAPTCHA = "fail_captcha"  # Slider could not be solved within the allowed attempts.


@dataclass(frozen=True, slots=True)
class OutcomeConfig:
    risk_grace_s: float = 8.0  # How long to wait for a captcha dialog after RGV587.
    max_captcha_attempts: int = 3
    retry_after_solve_s: float = 10.0  # How long to wait for a new request after solving the slider.
    max_resubmits: int = 1


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Snapshot of the observed state at ``now`` (seconds from a monotonic clock)."""

    now: float
    screen: LoginScreen
    verdict: LoginVerdict | None  # Latest loginByPwd response, if any.
    verdict_seq: int  # Number of loginByPwd responses received.
    has_ticket: bool = False
    xman_rotated: bool = False
    two_factor_visible: bool = False


@dataclass(slots=True)
class OutcomeState:
    """Accumulated state for one login attempt, updated by :func:`decide` and the action runner."""

    risk_first_seen: float | None = None
    captcha_attempts: int = 0
    solved_at: float | None = None
    seq_at_solve: int = 0
    resubmits: int = 0
    two_factor_done: bool = False

    def record_solve_attempt(self, snapshot: Snapshot) -> None:
        self.captcha_attempts += 1
        self.solved_at = snapshot.now
        self.seq_at_solve = snapshot.verdict_seq


def decide(snapshot: Snapshot, state: OutcomeState, config: OutcomeConfig = OutcomeConfig()) -> Decision:  # noqa: B008
    verdict = snapshot.verdict
    risk_active = bool(verdict and verdict.risk)

    # 1. Succeed only if the current response is not a rejection; old cookies cannot override one.
    if not risk_active and (
        snapshot.screen is LoginScreen.AUTHENTICATED
        or decide_login_success(
            has_ticket=snapshot.has_ticket,
            xman_rotated=snapshot.xman_rotated,
            response_success=bool(verdict and verdict.success),
        )
    ):
        return Decision.SUCCESS

    # 2. Solve a visible captcha dialog; RGV587 is its trigger, not a final rejection.
    if snapshot.screen is LoginScreen.CAPTCHA:
        if state.captcha_attempts >= config.max_captcha_attempts:
            return Decision.FAIL_CAPTCHA
        return Decision.SOLVE_CAPTCHA

    # 3. Risk-control rejection without a visible dialog.
    if risk_active:
        if state.risk_first_seen is None:
            state.risk_first_seen = snapshot.now
        if state.captcha_attempts == 0:
            # The dialog takes about three seconds to load; wait before giving up.
            if snapshot.now - state.risk_first_seen > config.risk_grace_s:
                return Decision.FAIL_RISK
            return Decision.WAIT
        # The slider was solved; wait for the page to retry login (a new response).
        assert state.solved_at is not None
        if snapshot.verdict_seq > state.seq_at_solve:
            return Decision.WAIT  # Evaluate the new response on the next iteration.
        if snapshot.now - state.solved_at > config.retry_after_solve_s:
            if state.resubmits < config.max_resubmits:
                return Decision.RESUBMIT
            return Decision.FAIL_RISK
        return Decision.WAIT

    # 4. Two-factor authentication.
    if snapshot.two_factor_visible and not state.two_factor_done:
        return Decision.HANDLE_TWO_FACTOR
    return Decision.WAIT
