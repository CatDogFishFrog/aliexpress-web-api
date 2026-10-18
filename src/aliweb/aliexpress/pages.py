"""Розпізнавання сторінок AliExpress за URL (чисті функції без браузера)."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

_LOGIN_HOST_RE = re.compile(r"^(login|passport)\.", re.IGNORECASE)
_LOGIN_PATH_RE = re.compile(r"login|sign-?in|passport", re.IGNORECASE)
#: Антибот-сторінки: /punish, nocaptcha, /_____tmd_____/…
CHALLENGE_URL_RE = re.compile(r"punish|captcha|_____tmd_____", re.IGNORECASE)
#: URL iframe-діалогу капчі (baxia punish).
CAPTCHA_FRAME_RE = re.compile(r"_____tmd_____/punish", re.IGNORECASE)


def looks_like_login_url(url: str) -> bool:
    """Чи це сторінка входу. Дивимось лише на хост і шлях (query може містити що завгодно)."""
    parts = urlsplit(url)
    return bool(_LOGIN_HOST_RE.match(parts.hostname or "") or _LOGIN_PATH_RE.search(parts.path))


def looks_like_challenge_url(url: str) -> bool:
    return bool(CHALLENGE_URL_RE.search(urlsplit(url).path + "?" + urlsplit(url).query))


def is_captcha_frame_url(url: str) -> bool:
    return bool(CAPTCHA_FRAME_RE.search(url))
