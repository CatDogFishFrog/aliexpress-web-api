"""Redact secrets from logs and diagnostic artifacts."""

from __future__ import annotations

import re
import threading

from aliweb.domain.secret import Secret

_MASK = "***"
# Common sensitive key-value pairs in text and URLs: password=..., token: ..., "ticket":"...", etc.
_PAIR_RE = re.compile(
    r"""(?ix)
    (?P<key>[\w-]*(?:password|passwd|secret|token|ticket|cookie|authorization|sign)[\w-]*)
    (?P<sep>["']?\s*[:=]\s*["']?)
    (?P<value>[^\s"'&;,}]+)
    """
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")


class Redactor:
    """Replace registered secrets and common sensitive patterns with ``***``.

    Thread-safe. Create an instance in the composition root and pass it to consumers.
    """

    MIN_SECRET_LEN = 4  # Shorter values would cause false positives.

    def __init__(self) -> None:
        self._secrets: set[str] = set()
        self._lock = threading.Lock()

    def register(self, secret: Secret | str) -> None:
        value = secret.reveal() if isinstance(secret, Secret) else secret
        if len(value) >= self.MIN_SECRET_LEN:
            with self._lock:
                self._secrets.add(value)

    def redact_exact(self, text: str) -> str:
        """Replace only registered secrets. Pattern-based replacement could corrupt HTML/MHTML
        artifacts where preserving exact content is important for debugging."""
        with self._lock:
            known = sorted(self._secrets, key=len, reverse=True)
        for value in known:
            text = text.replace(value, _MASK)
        return text

    def redact(self, text: str) -> str:
        """Registered secrets and common sensitive patterns for logs, URLs, and reports."""
        text = self.redact_exact(text)
        text = _BEARER_RE.sub(f"Bearer {_MASK}", text)
        return _PAIR_RE.sub(lambda m: f"{m['key']}{m['sep']}{_MASK}", text)
