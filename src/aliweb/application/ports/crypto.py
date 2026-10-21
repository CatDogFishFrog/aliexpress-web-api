from __future__ import annotations

from typing import Protocol

from aliweb.domain.secret import Secret


class Cipher(Protocol):
    """Symmetric encryption for byte strings. ``aad`` is authenticated but not encrypted."""

    def encrypt(self, plaintext: bytes, *, aad: bytes = b"") -> bytes: ...

    def decrypt(self, blob: bytes, *, aad: bytes = b"") -> bytes:
        """Raises:
        DecryptionError: incorrect key or corrupted data.
        """
        ...


class CipherFactory(Protocol):
    """Create a cipher from an arbitrary key, used for exports with a separate passphrase."""

    def __call__(self, secret: Secret) -> Cipher: ...
