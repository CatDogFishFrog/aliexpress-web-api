"""Маскування секретів у логах і діагностичних артефактах."""

from __future__ import annotations

import re
import threading

from aliweb.domain.secret import Secret

_MASK = "***"
# Типові чутливі пари у тексті/URL: password=..., token: ..., "ticket":"..." тощо.
_PAIR_RE = re.compile(
    r"""(?ix)
    (?P<key>[\w-]*(?:password|passwd|secret|token|ticket|cookie|authorization|sign)[\w-]*)
    (?P<sep>["']?\s*[:=]\s*["']?)
    (?P<value>[^\s"'&;,}]+)
    """
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")


class Redactor:
    """Замінює зареєстровані секрети та типові чутливі шаблони на ``***``.

    Потокобезпечний. Екземпляр створюється у composition root і передається споживачам.
    """

    MIN_SECRET_LEN = 4  # надто короткі значення дали б хибні спрацювання

    def __init__(self) -> None:
        self._secrets: set[str] = set()
        self._lock = threading.Lock()

    def register(self, secret: Secret | str) -> None:
        value = secret.reveal() if isinstance(secret, Secret) else secret
        if len(value) >= self.MIN_SECRET_LEN:
            with self._lock:
                self._secrets.add(value)

    def redact_exact(self, text: str) -> str:
        """Замінює лише ЗАРЕЄСТРОВАНІ секрети. Для артефактів, де важлива точність вмісту
        (HTML/MHTML-дампи): шаблонні заміни могли б спотворити дані, які ми дебажимо."""
        with self._lock:
            known = sorted(self._secrets, key=len, reverse=True)
        for value in known:
            text = text.replace(value, _MASK)
        return text

    def redact(self, text: str) -> str:
        """Зареєстровані секрети + типові чутливі шаблони (для логів, URL, звітів)."""
        text = self.redact_exact(text)
        text = _BEARER_RE.sub(f"Bearer {_MASK}", text)
        return _PAIR_RE.sub(lambda m: f"{m['key']}{m['sep']}{_MASK}", text)
