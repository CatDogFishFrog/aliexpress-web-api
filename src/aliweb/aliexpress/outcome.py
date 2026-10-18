"""Чиста логіка рішень після відправки пароля: що робити далі (успіх / капча / чекати / здатися).

Винесена окремо, бо саме тут була помилка: відповідь ``loginByPwd`` з RGV587 вважалась
КІНЦЕВОЮ відмовою, хоча насправді це тригер слайдера (iframe baxia punish), який
завантажується ~3 с і після проходження сторінка САМА повторює запит входу.

Функція :func:`decide` не торкається браузера — її повністю покривають юніт-тести.
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
    RESUBMIT = "resubmit"  # після слайдера нового запиту входу немає — натиснути «Sign in» ще раз
    WAIT = "wait"
    FAIL_RISK = "fail_risk"  # відмова ризик-контролю без діалогу капчі
    FAIL_CAPTCHA = "fail_captcha"  # слайдер не вдалося пройти за відведену кількість спроб


@dataclass(frozen=True, slots=True)
class OutcomeConfig:
    risk_grace_s: float = 8.0  # скільки чекати появи діалогу капчі після RGV587
    max_captcha_attempts: int = 3
    retry_after_solve_s: float = 10.0  # скільки чекати нового запиту після слайдера
    max_resubmits: int = 1


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Знімок спостережуваного стану в момент ``now`` (секунди, монотонний годинник)."""

    now: float
    screen: LoginScreen
    verdict: LoginVerdict | None  # остання відповідь loginByPwd (None — ще не було)
    verdict_seq: int  # лічильник відповідей loginByPwd
    has_ticket: bool = False
    xman_rotated: bool = False
    two_factor_visible: bool = False


@dataclass(slots=True)
class OutcomeState:
    """Накопичений стан одного входу (змінюється самим :func:`decide` і виконавцем дій)."""

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

    # 1. Успіх — лише коли актуальна відповідь не є відмовою (стара відмова + старі cookie — не успіх).
    if not risk_active and (
        snapshot.screen is LoginScreen.AUTHENTICATED
        or decide_login_success(
            has_ticket=snapshot.has_ticket,
            xman_rotated=snapshot.xman_rotated,
            response_success=bool(verdict and verdict.success),
        )
    ):
        return Decision.SUCCESS

    # 2. Діалог капчі видно — вирішуємо її (RGV587 тут — це саме її тригер, а не вирок).
    if snapshot.screen is LoginScreen.CAPTCHA:
        if state.captcha_attempts >= config.max_captcha_attempts:
            return Decision.FAIL_CAPTCHA
        return Decision.SOLVE_CAPTCHA

    # 3. Відмова ризик-контролю, але діалогу немає.
    if risk_active:
        if state.risk_first_seen is None:
            state.risk_first_seen = snapshot.now
        if state.captcha_attempts == 0:
            # Діалог завантажується ~3 с: даємо йому з'явитись, і лише потім здаємось.
            if snapshot.now - state.risk_first_seen > config.risk_grace_s:
                return Decision.FAIL_RISK
            return Decision.WAIT
        # Слайдер уже проходили: чекаємо, що сторінка сама повторить вхід (нова відповідь).
        assert state.solved_at is not None
        if snapshot.verdict_seq > state.seq_at_solve:
            return Decision.WAIT  # прийшла нова відповідь — оцінимо її на наступній ітерації
        if snapshot.now - state.solved_at > config.retry_after_solve_s:
            if state.resubmits < config.max_resubmits:
                return Decision.RESUBMIT
            return Decision.FAIL_RISK
        return Decision.WAIT

    # 4. Двофакторне підтвердження.
    if snapshot.two_factor_visible and not state.two_factor_done:
        return Decision.HANDLE_TWO_FACTOR
    return Decision.WAIT
