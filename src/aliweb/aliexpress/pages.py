"""Recognize AliExpress pages by URL using pure functions without a browser."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

_LOGIN_HOST_RE = re.compile(r"^(login|passport)\.", re.IGNORECASE)
_LOGIN_PATH_RE = re.compile(r"login|sign-?in|passport", re.IGNORECASE)
#: Anti-bot pages: /punish, nocaptcha, /_____tmd_____/…
CHALLENGE_URL_RE = re.compile(r"punish|captcha|_____tmd_____", re.IGNORECASE)
#: Captcha dialog iframe URL (baxia punish).
CAPTCHA_FRAME_RE = re.compile(r"_____tmd_____/punish", re.IGNORECASE)


def looks_like_login_url(url: str) -> bool:
    """Check whether this is a login page, using only the host and path (query may contain anything)."""
    parts = urlsplit(url)
    return bool(_LOGIN_HOST_RE.match(parts.hostname or "") or _LOGIN_PATH_RE.search(parts.path))


def looks_like_challenge_url(url: str) -> bool:
    return bool(CHALLENGE_URL_RE.search(urlsplit(url).path + "?" + urlsplit(url).query))


def is_captcha_frame_url(url: str) -> bool:
    return bool(CAPTCHA_FRAME_RE.search(url))
