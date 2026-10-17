from __future__ import annotations

from typing import Protocol

from aliweb.domain.secret import Secret


class Cipher(Protocol):
    """Симетричне шифрування блоку байтів. ``aad`` автентифікується, але не шифрується."""

    def encrypt(self, plaintext: bytes, *, aad: bytes = b"") -> bytes: ...

    def decrypt(self, blob: bytes, *, aad: bytes = b"") -> bytes:
        """Raises:
        DecryptionError: хибний ключ або пошкоджені дані.
        """
        ...


class CipherFactory(Protocol):
    """Створює шифр з довільного ключа (потрібно для експорту з іншою парольною фразою)."""

    def __call__(self, secret: Secret) -> Cipher: ...
