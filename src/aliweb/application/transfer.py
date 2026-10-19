"""Експорт та імпорт акаунтів/сесій у зашифровані bin-пакети (на рівні байтів)."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from aliweb.application.accounts import Clock, utc_now
from aliweb.application.codec import decode_account, encode_account
from aliweb.application.ports.accounts import AccountRepository
from aliweb.application.ports.crypto import Cipher, CipherFactory
from aliweb.application.ports.profiles import ProfileGenerator
from aliweb.domain.accounts import Account, AccountId
from aliweb.domain.errors import (
    AccountAlreadyExistsError,
    AccountNotFoundError,
    EmptyBundleError,
)
from aliweb.domain.secret import Secret

#: AAD, що прив'язує шифротекст до призначення (захист від підміни типу файлу).
BUNDLE_AAD = b"aliweb.bundle.v1"


class SessionTransferService:
    """Створює переносимі bin-пакети та імпортує їх назад.

    Пакет — це той самий формат акаунта, зашифрований ключем із ``.env`` або окремою
    парольною фразою (``passphrase``) — зручно, коли пакет їде на інший хост.
    """

    def __init__(
        self,
        repository: AccountRepository,
        default_cipher: Cipher,
        cipher_factory: CipherFactory,
        profiles: ProfileGenerator,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._repo = repository
        self._default_cipher = default_cipher
        self._cipher_factory = cipher_factory
        self._profiles = profiles
        self._clock = clock

    def _cipher(self, passphrase: Secret | None) -> Cipher:
        return self._cipher_factory(passphrase) if passphrase else self._default_cipher

    async def export_bytes(
        self,
        account_id: AccountId,
        *,
        include_credentials: bool = False,
        include_fingerprint: bool = True,
        passphrase: Secret | None = None,
    ) -> bytes:
        """Зашифрований пакет акаунта.

        За замовчуванням: сесія + відбиток браузера (щоб сесія й далі працювала в тому самому
        середовищі), але БЕЗ логіну/пароля. ``include_fingerprint=False`` прибирає відбиток.
        """
        account = await self._repo.get(account_id)
        if account is None:
            raise AccountNotFoundError(f"Акаунт {account_id} не знайдено")
        plaintext = encode_account(
            account,
            include_credentials=include_credentials,
            include_profile=include_fingerprint,
        )
        return self._cipher(passphrase).encrypt(plaintext, aad=BUNDLE_AAD)

    async def import_bytes(
        self,
        blob: bytes,
        *,
        as_id: AccountId | None = None,
        overwrite: bool = False,
        passphrase: Secret | None = None,
    ) -> Account:
        """Імпортує пакет. ``as_id`` перевизначає ідентифікатор із пакета.

        Відбиток браузера: береться з пакета; якщо його там немає — зберігається відбиток уже існуючого
        акаунта (при ``overwrite``); якщо немає ніякого — створюється новий.
        """
        incoming = decode_account(self._cipher(passphrase).decrypt(blob, aad=BUNDLE_AAD))
        if incoming.session is None and incoming.credentials is None:
            raise EmptyBundleError("Пакет не містить ні сесії, ні облікових даних")

        target = as_id or incoming.id
        existing = await self._repo.get(target)
        if existing is not None and not overwrite:
            raise AccountAlreadyExistsError(f"Акаунт {target} уже існує (використайте overwrite для заміни)")

        profile = incoming.profile or (existing.profile if existing else None)
        if profile is None:
            profile = await asyncio.to_thread(self._profiles.generate)

        now = self._clock()
        account = replace(
            incoming,
            id=target,
            created_at=existing.created_at if existing else now,
            updated_at=now,
            # Пакет без пароля не має стирати збережений пароль існуючого акаунта.
            credentials=incoming.credentials or (existing.credentials if existing else None),
            profile=profile,
        )
        await self._repo.save(account)
        return account
