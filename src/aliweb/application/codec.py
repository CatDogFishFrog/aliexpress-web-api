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
from aliweb.domain.secret import Secret

FORMAT_NAME = "aliweb.account"
FORMAT_VERSION = 1


def encode_account(
    account: Account, *, include_credentials: bool = True, include_session: bool = True
) -> bytes:
    creds = account.credentials if include_credentials else None
    session = account.session if include_session else None
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
        if version != FORMAT_VERSION:
            raise StorageError(
                f"Непідтримувана версія формату акаунта: {version!r} (очікується {FORMAT_VERSION})"
            )
        creds_raw = raw.get("credentials")
        session_raw = raw.get("session")
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
        )
    except StorageError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise StorageError(f"Пошкоджена структура файлу акаунта: {exc}") from exc
