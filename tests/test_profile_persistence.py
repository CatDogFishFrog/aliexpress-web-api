"""Profile persistence: codec migration, services, export/import, domain, and CLI passphrases."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from aliweb.application.accounts import AccountService
from aliweb.application.auth import AuthService
from aliweb.application.codec import FORMAT_VERSION, decode_account, encode_account
from aliweb.application.transfer import SessionTransferService
from aliweb.cli.main import _check_passphrase_length, _resolve_passphrase, build_parser
from aliweb.domain.accounts import AccountId
from aliweb.domain.errors import ConfigurationError, StorageError
from aliweb.domain.profile import BrowserProfile, ClaimedOs
from aliweb.infrastructure.crypto.scrypt_aesgcm import ScryptAesGcmCipher, ScryptParams
from tests.conftest import (
    FakeAuthenticator,
    FakeProfileGenerator,
    MemoryActiveStore,
    MemoryRepository,
    fast_cipher,
    make_account,
    make_profile,
    make_session,
)

A, B = AccountId("a"), AccountId("b")


# ── Domain ─────────────────────────────────────────────────────────────────
class TestBrowserProfileModel:
    def test_accessors(self) -> None:
        profile = make_profile("X")
        assert profile.screen_size == (1536, 864) and profile.device_scale_factor == 1.25
        assert profile.gpu_renderer == "ANGLE (AMD X)" and profile.hardware_concurrency == 8
        assert profile.device_memory == 8.0 and "Chrome/147" in profile.user_agent
        assert profile.label == "windows 1536x864"

    def test_tolerates_sparse_fingerprint(self) -> None:
        sparse = BrowserProfile(
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            claimed_os=ClaimedOs.LINUX,
            locale="en-US",
            timezone_id=None,
            viewport=(1200, 700),
            fingerprint={},
        )
        assert sparse.user_agent == "" and sparse.screen_size == (0, 0)
        assert sparse.device_scale_factor == 1.0 and sparse.hardware_concurrency is None


# ── Codec ──────────────────────────────────────────────────────────────────
class TestCodecProfile:
    def test_roundtrip_preserves_profile_exactly(self) -> None:
        account = make_account(session=make_session(), profile=make_profile("Z"))
        assert decode_account(encode_account(account)) == account

    def test_profile_can_be_excluded(self) -> None:
        account = make_account(session=make_session(), profile=make_profile())
        assert decode_account(encode_account(account, include_profile=False)).profile is None

    def test_current_format_version_is_2(self) -> None:
        assert json.loads(encode_account(make_account()))["version"] == FORMAT_VERSION == 2

    def test_old_v1_files_still_load_without_profile(self) -> None:
        """Migration v1→v2: files saved before profiles were introduced still load."""
        v1 = json.loads(encode_account(make_account(session=make_session())))
        v1["version"] = 1
        v1.pop("browser_profile")
        account = decode_account(json.dumps(v1).encode())
        assert account.profile is None and account.session == make_session()

    def test_unknown_profile_schema_is_a_storage_error(self) -> None:
        payload: dict[str, Any] = json.loads(encode_account(make_account(profile=make_profile())))
        payload["browser_profile"]["schema_version"] = 99
        with pytest.raises(StorageError, match="browser profile"):
            decode_account(json.dumps(payload).encode())

    def test_corrupted_profile_is_a_storage_error(self) -> None:
        payload: dict[str, Any] = json.loads(encode_account(make_account(profile=make_profile())))
        payload["browser_profile"]["claimed_os"] = "beos"
        with pytest.raises(StorageError):
            decode_account(json.dumps(payload).encode())


# ── AccountService ─────────────────────────────────────────────────────────
@pytest.fixture
def accounts(
    repo: MemoryRepository, active: MemoryActiveStore, profiles: FakeProfileGenerator
) -> AccountService:
    return AccountService(repo, active, profiles)


class TestEnsureProfile:
    async def test_created_once_persisted_and_then_stable(
        self, accounts: AccountService, repo: MemoryRepository, profiles: FakeProfileGenerator
    ) -> None:
        await accounts.add(A)
        first = await accounts.ensure_profile(A)
        again = await accounts.ensure_profile(A)
        assert first.profile is not None and again.profile == first.profile
        assert profiles.calls == 1  # The second call does not create another profile.
        assert repo.items[A].profile == first.profile

    async def test_each_account_gets_its_own_environment(self, accounts: AccountService) -> None:
        await accounts.add(A)
        await accounts.add(B)
        pa, pb = (await accounts.ensure_profile(A)).profile, (await accounts.ensure_profile(B)).profile
        assert pa is not None and pb is not None and pa != pb

    async def test_existing_profile_is_never_overwritten(
        self, accounts: AccountService, repo: MemoryRepository, profiles: FakeProfileGenerator
    ) -> None:
        repo.items[A] = make_account("a", profile=make_profile("KEEP"))
        assert (await accounts.ensure_profile(A)).profile == make_profile("KEEP")
        assert profiles.calls == 0

    async def test_regenerate_replaces_profile_but_keeps_session(
        self, accounts: AccountService, repo: MemoryRepository
    ) -> None:
        repo.items[A] = make_account("a", session=make_session(), profile=make_profile("OLD"))
        updated = await accounts.regenerate_profile(A)
        assert updated.profile != make_profile("OLD") and updated.session == make_session()

    async def test_rename_keeps_profile(self, accounts: AccountService, repo: MemoryRepository) -> None:
        repo.items[A] = make_account("a", profile=make_profile("KEEP"))
        await accounts.rename(A, B)
        assert repo.items[B].profile == make_profile("KEEP")

    async def test_summary_shows_profile_label(
        self, accounts: AccountService, repo: MemoryRepository
    ) -> None:
        repo.items[A] = make_account("a", profile=make_profile())
        repo.items[B] = make_account("b")
        labels = {s.id.value: s.profile_label for s in await accounts.list_summaries()}
        assert labels == {"a": "windows 1536x864", "b": None}


# ── AuthService ────────────────────────────────────────────────────────────
class TestAuthUsesProfile:
    def make(self, accounts: AccountService, repo: MemoryRepository, fake: FakeAuthenticator) -> AuthService:
        return AuthService(accounts, repo, fake, verify_ttl_s=100, monotonic=lambda: 0.0)

    async def test_login_creates_profile_and_passes_it_to_the_browser(
        self, accounts: AccountService, repo: MemoryRepository
    ) -> None:
        fake = FakeAuthenticator()
        await accounts.add(A, make_account().credentials)
        await self.make(accounts, repo, fake).login(A)
        stored = repo.items[A].profile
        assert stored is not None and fake.login_calls[0].profile == stored
        assert repo.items[A].session is not None  # Session and profile are stored together.

    async def test_next_day_uses_the_same_environment(
        self, accounts: AccountService, repo: MemoryRepository, profiles: FakeProfileGenerator
    ) -> None:
        """Core requirement: the account uses the same environment on the next run."""
        fake = FakeAuthenticator()
        await accounts.add(A, make_account().credentials)
        auth = self.make(accounts, repo, fake)
        await auth.login(A)
        await auth.login(A, fresh=True)
        await auth.ensure_session(A)
        assert len({id(c.profile) for c in fake.login_calls}) == 1
        assert fake.login_calls[0].profile is fake.login_calls[1].profile
        assert profiles.calls == 1

    async def test_session_validation_uses_account_profile(
        self, accounts: AccountService, repo: MemoryRepository
    ) -> None:
        fake = FakeAuthenticator()
        repo.items[A] = make_account("a", session=make_session(), profile=make_profile("P"))
        await self.make(accounts, repo, fake).check(A)
        assert fake.validity_profiles == [make_profile("P")]

    async def test_legacy_session_without_profile_gets_one_on_first_use(
        self, accounts: AccountService, repo: MemoryRepository
    ) -> None:
        """An account with a legacy session gets a profile generated and saved on first use."""
        fake = FakeAuthenticator()
        repo.items[A] = make_account("a", session=make_session())
        auth = AuthService(accounts, repo, fake, verify_ttl_s=0, monotonic=lambda: 0.0)
        await auth.ensure_session(A)
        assert repo.items[A].profile is not None
        assert fake.validity_profiles == [repo.items[A].profile]

    async def test_check_without_session_does_not_create_profile(
        self, accounts: AccountService, repo: MemoryRepository
    ) -> None:
        repo.items[A] = make_account("a")
        assert await self.make(accounts, repo, FakeAuthenticator()).check(A) is False
        assert repo.items[A].profile is None


# ── Export / import ────────────────────────────────────────────────────────
class TestTransferFingerprint:
    @pytest.fixture
    def service(self, repo: MemoryRepository, profiles: FakeProfileGenerator) -> SessionTransferService:
        return SessionTransferService(
            repo,
            fast_cipher(),
            lambda s: ScryptAesGcmCipher(s, ScryptParams(12, 8, 1)),
            profiles,
        )

    async def test_fingerprint_travels_with_the_session_by_default(
        self, service: SessionTransferService, repo: MemoryRepository, profiles: FakeProfileGenerator
    ) -> None:
        repo.items[A] = make_account("a", session=make_session(), profile=make_profile("ORIG"))
        blob = await service.export_bytes(A)  # Default options.
        repo.items.clear()
        imported = await service.import_bytes(blob)
        assert imported.profile == make_profile("ORIG") and imported.credentials is None
        assert profiles.calls == 0  # No profile was generated.

    async def test_no_fingerprint_flag_strips_it_and_import_creates_a_new_one(
        self, service: SessionTransferService, repo: MemoryRepository, profiles: FakeProfileGenerator
    ) -> None:
        repo.items[A] = make_account("a", session=make_session(), profile=make_profile("ORIG"))
        blob = await service.export_bytes(A, include_fingerprint=False)
        repo.items.clear()
        imported = await service.import_bytes(blob)
        assert imported.profile is not None and imported.profile != make_profile("ORIG")
        assert profiles.calls == 1

    async def test_bundle_without_profile_gets_one_generated(
        self, service: SessionTransferService, repo: MemoryRepository
    ) -> None:
        repo.items[A] = make_account("a", session=make_session())  # Legacy account without a profile.
        blob = await service.export_bytes(A)
        repo.items.clear()
        assert (await service.import_bytes(blob)).profile is not None

    async def test_overwrite_without_bundle_profile_keeps_existing_environment(
        self, service: SessionTransferService, repo: MemoryRepository, profiles: FakeProfileGenerator
    ) -> None:
        repo.items[A] = make_account("a", session=make_session("new"))
        blob = await service.export_bytes(A, include_fingerprint=False)
        repo.items[A] = make_account("a", session=make_session("old"), profile=make_profile("MINE"))
        merged = await service.import_bytes(blob, overwrite=True)
        assert merged.profile == make_profile("MINE") and profiles.calls == 0

    async def test_bundle_profile_wins_over_existing(
        self, service: SessionTransferService, repo: MemoryRepository
    ) -> None:
        repo.items[A] = make_account("a", session=make_session(), profile=make_profile("FROM_BUNDLE"))
        blob = await service.export_bytes(A)
        repo.items[A] = replace(repo.items[A], profile=make_profile("LOCAL"))
        merged = await service.import_bytes(blob, overwrite=True)
        assert merged.profile == make_profile("FROM_BUNDLE")  # Session and environment travel together.

    async def test_credentials_flag_is_independent_of_fingerprint(
        self, service: SessionTransferService, repo: MemoryRepository
    ) -> None:
        repo.items[A] = make_account("a", session=make_session(), profile=make_profile("ORIG"))
        blob = await service.export_bytes(A, include_credentials=True, include_fingerprint=False)
        repo.items.clear()
        imported = await service.import_bytes(blob, as_id=B)
        assert imported.credentials is not None  # Credentials allow automatic login.
        assert imported.profile != make_profile("ORIG")  # A new profile is generated.


# ── CLI: bundle passphrase ─────────────────────────────────────────────────
def export_args(*extra: str) -> Any:
    return build_parser().parse_args(["session", "export", "main", "main.bin", *extra])


class TestPassphraseOptions:
    def test_flags_are_mutually_exclusive(self) -> None:
        with pytest.raises(SystemExit):
            export_args("--passphrase", "--passphrase-env", "X")

    def test_export_flags_parse(self) -> None:
        args = export_args("--include-credentials", "--no-fingerprint", "--passphrase")
        assert args.include_credentials and args.no_fingerprint and args.passphrase

    def test_no_passphrase_means_machine_key(self) -> None:
        assert _resolve_passphrase(export_args(), confirm=True) is None

    def test_interactive_prompt_is_hidden_and_confirmed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        answers = iter(["correct horse battery staple", "correct horse battery staple"])
        prompts: list[str] = []
        monkeypatch.setattr("getpass.getpass", lambda prompt="": prompts.append(prompt) or next(answers))
        secret = _resolve_passphrase(export_args("--passphrase"), confirm=True)
        assert secret is not None and secret.reveal() == "correct horse battery staple"
        assert len(prompts) == 2  # Initial entry and confirmation.

    def test_mismatching_confirmation_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        answers = iter(["correct horse battery staple", "different phrase entirely!!"])
        monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
        with pytest.raises(ConfigurationError, match="do not match"):
            _resolve_passphrase(export_args("--passphrase"), confirm=True)

    def test_import_does_not_ask_for_confirmation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []
        monkeypatch.setattr("getpass.getpass", lambda prompt="": calls.append(prompt) or "x" * 20)
        args = build_parser().parse_args(["session", "import", "f.bin", "--passphrase"])
        assert _resolve_passphrase(args, confirm=False) is not None and len(calls) == 1

    def test_short_phrase_is_rejected_with_reason(self) -> None:
        with pytest.raises(ConfigurationError, match="at least 16"):
            _check_passphrase_length("2305217")

    def test_env_variant_explains_name_vs_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Regression: the user passed the passphrase itself as the environment variable name."""
        monkeypatch.delenv("2305217", raising=False)
        with pytest.raises(ConfigurationError, match="VARIABLE NAME") as excinfo:
            _resolve_passphrase(export_args("--passphrase-env", "2305217"), confirm=True)
        assert "--passphrase" in str(excinfo.value)

    def test_env_variant_reads_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_PHRASE", "a-sufficiently-long-phrase")
        secret = _resolve_passphrase(export_args("--passphrase-env", "MY_PHRASE"), confirm=True)
        assert secret is not None and secret.reveal() == "a-sufficiently-long-phrase"


class TestProfileCommands:
    def test_profile_subcommands_parse(self) -> None:
        parser = build_parser()
        assert parser.parse_args(["profile", "show", "main"]).cmd == "show"
        assert parser.parse_args(["profile", "regenerate", "main"]).cmd == "regenerate"
        with pytest.raises(SystemExit):
            parser.parse_args(["profile", "show"])


# ── Disk storage: profile persists across restarts ─────────────────────────
async def test_profile_survives_encrypted_storage_roundtrip(tmp_path: Path) -> None:
    from aliweb.infrastructure.storage.file_repository import FileAccountRepository

    repo = FileAccountRepository(tmp_path, fast_cipher())
    await repo.save(make_account("a", session=make_session(), profile=make_profile("DISK")))
    raw = (tmp_path / "a.bin").read_bytes()
    assert b"ANGLE" not in raw and b"Chrome/147" not in raw  # Fingerprint is encrypted.
    reopened = FileAccountRepository(tmp_path, fast_cipher())  # Simulate reopening the next day.
    loaded = await reopened.get(A)
    assert loaded is not None and loaded.profile == make_profile("DISK")
