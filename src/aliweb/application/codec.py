"""Кодек акаунта: Account ↔ байти (JSON). Єдиний формат і для сховища, і для експорту.

Це чиста функція даних без вводу/виводу. Шифрування — окремо (порт ``Cipher``).
Формат версіонований: при зміні схеми піднімаємо ``FORMAT_VERSION`` і додаємо міграцію
в :func:`decode_account`.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from aliweb.domain.accounts import Account, AccountId, Credentials, SessionState
from aliweb.domain.errors import StorageError
from aliweb.domain.profile import PROFILE_SCHEMA_VERSION, BrowserProfile, ClaimedOs
from aliweb.domain.secret import Secret

FORMAT_NAME = "aliweb.account"
FORMAT_VERSION = 2
#: Версії, які вміє читати decode_account (старіші — через міграцію нижче).
READABLE_VERSIONS = frozenset({1, 2})


def _encode_profile(profile: BrowserProfile) -> dict[str, Any]:
    return {
        "schema_version": profile.schema_version,
        "created_at": profile.created_at.isoformat(),
        "claimed_os": profile.claimed_os.value,
        "locale": profile.locale,
        "timezone_id": profile.timezone_id,
        "viewport": list(profile.viewport),
        "fingerprint": profile.fingerprint,
    }


def _decode_profile(raw: dict[str, Any]) -> BrowserProfile:
    schema = raw.get("schema_version", PROFILE_SCHEMA_VERSION)
    if schema != PROFILE_SCHEMA_VERSION:
        raise StorageError(f"Непідтримувана версія профілю браузера: {schema!r}")
    width, height = raw["viewport"]
    return BrowserProfile(
        created_at=datetime.fromisoformat(raw["created_at"]),
        claimed_os=ClaimedOs(raw["claimed_os"]),
        locale=str(raw["locale"]),
        timezone_id=raw.get("timezone_id"),
        viewport=(int(width), int(height)),
        fingerprint=raw["fingerprint"],
        schema_version=schema,
    )


def encode_account(
    account: Account,
    *,
    include_credentials: bool = True,
    include_session: bool = True,
    include_profile: bool = True,
) -> bytes:
    creds = account.credentials if include_credentials else None
    session = account.session if include_session else None
    profile = account.profile if include_profile else None
    payload: dict[str, Any] = {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "id": account.id.value,
        "created_at": account.created_at.isoformat(),
        "updated_at": account.updated_at.isoformat(),
        "credentials": ({"login": creds.login, "password": creds.password.reveal()} if creds else None),
        "session": (
            {
                "captured_at": session.captured_at.isoformat(),
                "storage_state": session.storage_state,
            }
            if session
            else None
        ),
        "browser_profile": _encode_profile(profile) if profile else None,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def decode_account(data: bytes) -> Account:
    """Raises:
    StorageError: невідомий формат/версія або пошкоджена структура.
    """
    try:
        raw = json.loads(data.decode("utf-8"))
        if raw.get("format") != FORMAT_NAME:
            raise StorageError("Це не файл акаунта aliweb")
        version = raw.get("version")
        if version not in READABLE_VERSIONS:
            raise StorageError(
                f"Непідтримувана версія формату акаунта: {version!r} "
                f"(підтримуються: {sorted(READABLE_VERSIONS)})"
            )
        creds_raw = raw.get("credentials")
        session_raw = raw.get("session")
        profile_raw = raw.get("browser_profile")  # у версії 1 поля немає → None (міграція v1→v2)
        return Account(
            id=AccountId(raw["id"]),
            created_at=datetime.fromisoformat(raw["created_at"]),
            updated_at=datetime.fromisoformat(raw["updated_at"]),
            credentials=(
                Credentials(login=creds_raw["login"], password=Secret(creds_raw["password"]))
                if creds_raw
                else None
            ),
            session=(
                SessionState(
                    storage_state=session_raw["storage_state"],
                    captured_at=datetime.fromisoformat(session_raw["captured_at"]),
                )
                if session_raw
                else None
            ),
            profile=_decode_profile(profile_raw) if profile_raw else None,
        )
    except StorageError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError) as exc:  # включно з невірним ClaimedOs
        raise StorageError(f"Пошкоджена структура файлу акаунта: {exc}") from exc
