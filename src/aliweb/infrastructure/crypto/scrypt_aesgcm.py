"""Шифрування scrypt + AES-256-GCM у власному контейнері.

Формат blob (усі числа — big-endian, але тут лише байти):

    offset  len  поле
    0       4    MAGIC  = b"ALWB"
    4       1    FORMAT_VERSION (=1)
    5       1    log2(N)       параметр scrypt
    6       1    r             параметр scrypt
    7       1    p             параметр scrypt
    8       16   salt          випадковий, окремий для КОЖНОГО шифрування
    24      12   nonce         випадковий
    36      …    ciphertext || GCM-tag(16)

AAD для GCM = заголовок (байти 0..36) + AAD виклику. Тому підміна параметрів scrypt чи
версії, а також перенесення шифротексту в «чужий» контекст, виявляються автентифікацією.

Параметри scrypt читаються з недовіреного файлу, тому на розшифруванні жорстко обмежені
(захист від DoS через гігантський N).
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from aliweb.domain.errors import ConfigurationError, CryptoError, DecryptionError
from aliweb.domain.secret import Secret

MAGIC = b"ALWB"
FORMAT_VERSION = 1
_SALT_LEN = 16
_NONCE_LEN = 12
_HEADER_LEN = 4 + 1 + 3 + _SALT_LEN + _NONCE_LEN  # 36
_TAG_LEN = 16
MIN_SECRET_LENGTH = 16

# Верхня межа пам'яті scrypt (≈128·N·r байт) при розшифруванні недовіреного файлу.
_MAX_SCRYPT_MEMORY = 256 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ScryptParams:
    log2_n: int = 15  # N = 32768 → ≈32 MiB при r=8
    r: int = 8
    p: int = 1

    @property
    def n(self) -> int:
        return 1 << self.log2_n

    @property
    def memory_bytes(self) -> int:
        return 128 * self.n * self.r

    def validate_untrusted(self) -> None:
        if not (12 <= self.log2_n <= 20 and 1 <= self.r <= 16 and 1 <= self.p <= 4):
            raise DecryptionError("Параметри scrypt у файлі поза допустимими межами")
        if self.memory_bytes > _MAX_SCRYPT_MEMORY:
            raise DecryptionError("Параметри scrypt у файлі вимагають надто багато пам'яті")


class ScryptAesGcmCipher:
    """Реалізація порту ``Cipher``. Ключ виводиться з секрету окремо для кожного blob."""

    def __init__(self, secret: Secret, params: ScryptParams | None = None) -> None:
        if len(secret) < MIN_SECRET_LENGTH:
            raise ConfigurationError(f"Ключ шифрування має містити щонайменше {MIN_SECRET_LENGTH} символів")
        self._secret = secret
        self._params = params or ScryptParams()
        self._params.validate_untrusted()  # власні параметри теж мусять бути в межах

    def _derive_key(self, salt: bytes, params: ScryptParams) -> bytes:
        return hashlib.scrypt(
            self._secret.reveal().encode("utf-8"),
            salt=salt,
            n=params.n,
            r=params.r,
            p=params.p,
            maxmem=params.memory_bytes * 2 + 1024 * 1024,
            dklen=32,
        )

    def encrypt(self, plaintext: bytes, *, aad: bytes = b"") -> bytes:
        params = self._params
        salt = os.urandom(_SALT_LEN)
        nonce = os.urandom(_NONCE_LEN)
        header = MAGIC + bytes([FORMAT_VERSION, params.log2_n, params.r, params.p]) + salt + nonce
        key = self._derive_key(salt, params)
        return header + AESGCM(key).encrypt(nonce, plaintext, header + aad)

    def decrypt(self, blob: bytes, *, aad: bytes = b"") -> bytes:
        if len(blob) < _HEADER_LEN + _TAG_LEN or blob[:4] != MAGIC:
            raise DecryptionError("Це не зашифрований файл aliweb (хибний заголовок)")
        version, log2_n, r, p = blob[4], blob[5], blob[6], blob[7]
        if version != FORMAT_VERSION:
            raise DecryptionError(f"Непідтримувана версія контейнера: {version}")
        params = ScryptParams(log2_n=log2_n, r=r, p=p)
        params.validate_untrusted()

        header = blob[:_HEADER_LEN]
        salt = blob[8 : 8 + _SALT_LEN]
        nonce = blob[8 + _SALT_LEN : _HEADER_LEN]
        try:
            key = self._derive_key(salt, params)
            return AESGCM(key).decrypt(nonce, blob[_HEADER_LEN:], header + aad)
        except InvalidTag as exc:
            raise DecryptionError("Не вдалося розшифрувати: хибний ключ або файл пошкоджено") from exc
        except (ValueError, MemoryError) as exc:
            raise CryptoError(f"Помилка виведення ключа: {exc}") from exc


def make_cipher(secret: Secret) -> ScryptAesGcmCipher:
    """Фабрика, що відповідає порту ``CipherFactory``."""
    return ScryptAesGcmCipher(secret)
