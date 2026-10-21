"""scrypt and AES-256-GCM encryption in a custom container.

Blob format (all values below are byte offsets and lengths):

    offset  len  field
    0       4    MAGIC  = b"ALWB"
    4       1    FORMAT_VERSION (=1)
    5       1    log2(N)       scrypt parameter
    6       1    r             scrypt parameter
    7       1    p             scrypt parameter
    8       16   salt          random, unique for each encryption
    24      12   nonce         random
    36      …    ciphertext || GCM-tag(16)

GCM AAD is the header (bytes 0..36) plus the call-specific AAD. Authentication detects tampering
with scrypt parameters or version, and ciphertext moved to an unrelated context.

scrypt parameters come from an untrusted file, so decryption enforces strict limits to prevent
denial-of-service attacks using an excessively large N.
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

# Maximum scrypt memory (approximately 128*N*r bytes) when decrypting an untrusted file.
_MAX_SCRYPT_MEMORY = 256 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ScryptParams:
    log2_n: int = 15  # N = 32768, approximately 32 MiB at r=8.
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
            raise DecryptionError("scrypt parameters in the file are out of range")
        if self.memory_bytes > _MAX_SCRYPT_MEMORY:
            raise DecryptionError("scrypt parameters in the file require too much memory")


class ScryptAesGcmCipher:
    """Implement the ``Cipher`` port. Derive a separate key from the secret for each blob."""

    def __init__(self, secret: Secret, params: ScryptParams | None = None) -> None:
        if len(secret) < MIN_SECRET_LENGTH:
            raise ConfigurationError(f"Encryption key must contain at least {MIN_SECRET_LENGTH} characters")
        self._secret = secret
        self._params = params or ScryptParams()
        self._params.validate_untrusted()  # Validate locally configured parameters as well.

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
            raise DecryptionError("Not an encrypted aliweb file (invalid header)")
        version, log2_n, r, p = blob[4], blob[5], blob[6], blob[7]
        if version != FORMAT_VERSION:
            raise DecryptionError(f"Unsupported container version: {version}")
        params = ScryptParams(log2_n=log2_n, r=r, p=p)
        params.validate_untrusted()

        header = blob[:_HEADER_LEN]
        salt = blob[8 : 8 + _SALT_LEN]
        nonce = blob[8 + _SALT_LEN : _HEADER_LEN]
        try:
            key = self._derive_key(salt, params)
            return AESGCM(key).decrypt(nonce, blob[_HEADER_LEN:], header + aad)
        except InvalidTag as exc:
            raise DecryptionError("Decryption failed: incorrect key or corrupted file") from exc
        except (ValueError, MemoryError) as exc:
            raise CryptoError(f"Key derivation failed: {exc}") from exc


def make_cipher(secret: Secret) -> ScryptAesGcmCipher:
    """Factory implementation for the ``CipherFactory`` port."""
    return ScryptAesGcmCipher(secret)
