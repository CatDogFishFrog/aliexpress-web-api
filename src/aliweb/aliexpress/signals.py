"""Pure functions for parsing login signals, easy to test without a browser.

Adapted from ali-coins (libs/ui/login.js): ``classifyLoginRet``, ``decideLoginSuccess``, and ``readCookie``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

#: Password login endpoint (/report and /punish anti-bot endpoints are excluded).
LOGIN_RESPONSE_URL_RE = re.compile(r"/user/buyer/login/loginByPwd(?:$|\?)", re.IGNORECASE)
#: Risk-control rejection codes (RGV587/TMD).
RISK_RET_RE = re.compile(
    r"RGV587|FAIL_SYS_USER_VALIDATE|FAIL_SYS_TRAFFIC_LIMIT|FAIL_SYS_ILLEGAL_ACCESS", re.IGNORECASE
)
#: A long cooldown is recommended after an AliExpress risk-control block.
RISK_COOLDOWN_HOURS = 48

TICKET_COOKIE = "login_aliyunid_ticket"
XMAN_COOKIE = "xman_us_t"


@dataclass(frozen=True, slots=True)
class LoginVerdict:
    risk: bool
    success: bool
    items: tuple[str, ...]


def classify_login_ret(ret: object) -> LoginVerdict:
    """Classify the loginByPwd ``ret`` field (string, list, or ``None``)."""
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
    """Extract ``ret`` from either the top level or ``data.ret``."""
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
    """Determine login success from strong signals: ticket, ``xman_us_t`` rotation, or SUCCESS response.

    An unchanged ``xman_us_t`` does not count because it can outlive a session (a historical false
    positive in ali-coins). Risk-control rejection overrides all success signals.
    """
    if response_risk:
        return False
    return has_ticket or xman_rotated or response_success
