"""Чисті функції розбору сигналів входу (без браузера — легко тестуються).

Перенесено з ali-coins (libs/ui/login.js): `classifyLoginRet`, `decideLoginSuccess`, `readCookie`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

#: Ендпоінт входу за паролем (/report та /punish антибота сюди не входять).
LOGIN_RESPONSE_URL_RE = re.compile(r"/user/buyer/login/loginByPwd(?:$|\?)", re.IGNORECASE)
#: Коди відхилення системою ризик-контролю (RGV587/TMD).
RISK_RET_RE = re.compile(
    r"RGV587|FAIL_SYS_USER_VALIDATE|FAIL_SYS_TRAFFIC_LIMIT|FAIL_SYS_ILLEGAL_ACCESS", re.IGNORECASE
)
#: Після ризик-блокування AliExpress потрібен довгий «відпочинок» прапора.
RISK_COOLDOWN_HOURS = 48

TICKET_COOKIE = "login_aliyunid_ticket"
XMAN_COOKIE = "xman_us_t"


@dataclass(frozen=True, slots=True)
class LoginVerdict:
    risk: bool
    success: bool
    items: tuple[str, ...]


def classify_login_ret(ret: object) -> LoginVerdict:
    """Класифікує поле ``ret`` відповіді loginByPwd (рядок, список або ``None``)."""
    if ret is None:
        items: tuple[str, ...] = ()
    elif isinstance(ret, (list, tuple)):
        items = tuple(str(x) for x in ret)
    else:
        items = (str(ret),)
    risk = bool(RISK_RET_RE.search(" ".join(items)))
    success = not risk and any(s.strip().upper().startswith("SUCCESS::") for s in items)
    return LoginVerdict(risk=risk, success=success, items=items)


def extract_ret(body: Mapping[str, Any]) -> object:
    """``ret`` може лежати на верхньому рівні або в ``data.ret``."""
    if "ret" in body:
        return body["ret"]
    data = body.get("data")
    return data.get("ret") if isinstance(data, Mapping) else None


def read_cookie(cookies: Iterable[Mapping[str, Any]], name: str) -> str:
    for cookie in cookies:
        if cookie.get("name") == name and cookie.get("value"):
            return str(cookie["value"])
    return ""


def decide_login_success(
    *,
    has_ticket: bool = False,
    xman_rotated: bool = False,
    response_success: bool = False,
    response_risk: bool = False,
) -> bool:
    """Вхід успішний за сильними сигналами: тікет, ротація ``xman_us_t`` або SUCCESS у відповіді.

    Старий незмінений ``xman_us_t`` НЕ рахується (він переживає завершення сесії —
    історичний хибнопозитив ali-coins). Ризик-відхилення перекриває все.
    """
    if response_risk:
        return False
    return has_ticket or xman_rotated or response_success
