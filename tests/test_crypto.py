from __future__ import annotations

import pytest

from aliweb.domain.errors import ConfigurationError, DecryptionError
from aliweb.domain.secret import Secret
from aliweb.infrastructure.crypto.scrypt_aesgcm import (
    MAGIC,
    ScryptAesGcmCipher,
    ScryptParams,
)
from tests.conftest import fast_cipher


def test_roundtrip_and_unique_ciphertexts() -> None:
    cipher = fast_cipher()
    a, b = cipher.encrypt(b"hello"), cipher.encrypt(b"hello")
    assert a != b and a.startswith(MAGIC)
    assert cipher.decrypt(a) == b"hello"


def test_wrong_key_fails() -> None:
    blob = fast_cipher().encrypt(b"data")
    with pytest.raises(DecryptionError):
        fast_cipher(Secret("another-secret-long-enough")).decrypt(blob)


def test_aad_is_authenticated() -> None:
    cipher = fast_cipher()
    blob = cipher.encrypt(b"data", aad=b"vault")
    with pytest.raises(DecryptionError):
        cipher.decrypt(blob, aad=b"bundle")
    assert cipher.decrypt(blob, aad=b"vault") == b"data"


@pytest.mark.parametrize("index", [4, 5, 8, 20, 40, -1])
def test_tampering_detected(index: int) -> None:
    cipher = fast_cipher()
    blob = bytearray(cipher.encrypt(b"payload"))
    blob[index] ^= 0x01
    with pytest.raises(DecryptionError):
        cipher.decrypt(bytes(blob))


def test_garbage_and_truncated_rejected() -> None:
    cipher = fast_cipher()
    for junk in (b"", b"short", b"X" * 100):
        with pytest.raises(DecryptionError):
            cipher.decrypt(junk)


def test_untrusted_scrypt_params_bounded() -> None:
    cipher = fast_cipher()
    blob = bytearray(cipher.encrypt(b"x"))
    blob[5] = 30  # log2(N)=30 requires excessive memory and must be rejected before key derivation.
    with pytest.raises(DecryptionError, match=r"out of range|too much memory"):
        cipher.decrypt(bytes(blob))


def test_short_secret_rejected() -> None:
    with pytest.raises(ConfigurationError):
        ScryptAesGcmCipher(Secret("short"))


def test_default_params_within_limits() -> None:
    params = ScryptParams()
    params.validate_untrusted()
    assert params.memory_bytes == 32 * 1024 * 1024
