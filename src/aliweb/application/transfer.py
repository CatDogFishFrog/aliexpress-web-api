"""Export and import accounts and sessions as encrypted binary bundles."""

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

#: AAD binds ciphertext to its purpose, preventing file type substitution.
BUNDLE_AAD = b"aliweb.bundle.v1"


class SessionTransferService:
    """Create portable binary bundles and import them back.

    A bundle uses the account format, encrypted with the key from ``.env`` or a separate
    passphrase, which is useful when transferring it to another host.
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
        """Return an encrypted account bundle.

        By default, include the session and browser profile but not the login or password.
        ``include_fingerprint=False`` omits the profile.
        """
        account = await self._repo.get(account_id)
        if account is None:
            raise AccountNotFoundError(f"Account {account_id} was not found")
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
        """Import a bundle. ``as_id`` overrides the identifier in the bundle.

        Use the profile from the bundle. If absent, preserve the existing account profile when
        overwriting; otherwise create a new profile if needed.
        """
        incoming = decode_account(self._cipher(passphrase).decrypt(blob, aad=BUNDLE_AAD))
        if incoming.session is None and incoming.credentials is None:
            raise EmptyBundleError("Bundle contains neither a session nor credentials")

        target = as_id or incoming.id
        existing = await self._repo.get(target)
        if existing is not None and not overwrite:
            raise AccountAlreadyExistsError(f"Account {target} already exists (use overwrite to replace it)")

        profile = incoming.profile or (existing.profile if existing else None)
        if profile is None:
            profile = await asyncio.to_thread(self._profiles.generate)

        now = self._clock()
        account = replace(
            incoming,
            id=target,
            created_at=existing.created_at if existing else now,
            updated_at=now,
            # A bundle without credentials must not erase existing saved credentials.
            credentials=incoming.credentials or (existing.credentials if existing else None),
            profile=profile,
        )
        await self._repo.save(account)
        return account
