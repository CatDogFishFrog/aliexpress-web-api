"""The ``aliweb`` CLI: parse arguments, call services, and print results.

Passwords and passphrases are never accepted as command-line arguments because they may be
recorded in shell history or process listings. Use an interactive prompt or an environment variable.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import secrets
import sys
from collections.abc import Sequence
from pathlib import Path

from aliweb.api import AliExpressWebApi
from aliweb.bootstrap import build_api
from aliweb.domain.accounts import AccountId, Credentials
from aliweb.domain.browser import BrowserChoice, BrowserKind, LoginMode
from aliweb.domain.errors import AliWebError, ConfigurationError
from aliweb.domain.profile import BrowserProfile
from aliweb.domain.secret import Secret
from aliweb.infrastructure.config import Settings
from aliweb.infrastructure.crypto.scrypt_aesgcm import MIN_SECRET_LENGTH
from aliweb.infrastructure.storage.files import atomic_write_private, read_bytes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aliweb", description="Unofficial AliExpress Web API")
    parser.add_argument("--env-file", default=".env", help="path to .env (default: ./.env)")
    sub = parser.add_subparsers(dest="group", required=True)

    secret_cmd = sub.add_parser("secret", help="encryption key").add_subparsers(dest="cmd", required=True)
    secret_cmd.add_parser("generate", help="generate a key for ALIWEB_SESSION_SECRET")

    accounts = sub.add_parser("accounts", help="manage accounts").add_subparsers(
        dest="cmd", required=True
    )
    add = accounts.add_parser("add", help="add an account")
    add.add_argument("id")
    add.add_argument(
        "--login",
        help="email or phone; omit to create an account without credentials "
        "(for manual login: login --headful --manual)",
    )
    add.add_argument("--password-env", help="environment variable containing the password (otherwise prompt)")
    update = accounts.add_parser("update", help="update an account's login or password")
    update.add_argument("id")
    update.add_argument("--login", required=True, help="new email or phone")
    update.add_argument(
        "--password-env", help="environment variable containing the password (otherwise prompt)"
    )
    accounts.add_parser("list", help="list accounts")
    remove = accounts.add_parser("remove", help="remove an account")
    remove.add_argument("id")
    rename = accounts.add_parser("rename", help="rename an account")
    rename.add_argument("old_id")
    rename.add_argument("new_id")
    use = accounts.add_parser("use", help="set the active account")
    use.add_argument("id")

    login = sub.add_parser(
        "login",
        help="log in to an account",
        description=(
            "Choose the login mode here, not in .env. With no options, login runs headless in the "
            "patchright browser (suitable for a server without a display). Chrome, Edge, and custom "
            "browsers require --headful."
        ),
    )
    login.add_argument("id")
    login.add_argument("--headful", action="store_true", help="show the browser window")
    browser = login.add_mutually_exclusive_group()
    browser.add_argument(
        "--browser",
        choices=[kind.value for kind in BrowserKind if kind is not BrowserKind.CUSTOM],
        help="browser for login (requires --headful); chromium uses patchright (default)",
    )
    browser.add_argument(
        "--browser-path",
        metavar="PATH",
        help="path to a custom Chromium-compatible browser (requires --headful)",
    )
    login.add_argument("--fresh", action="store_true", help="ignore the saved session")
    login.add_argument(
        "--manual",
        action="store_true",
        help="manual login: automation is disabled; wait for you to log in, then save the session "
        "and close the browser (requires --headful; no password needed)",
    )
    login.add_argument(
        "--manual-timeout",
        type=float,
        default=600.0,
        metavar="SECONDS",
        help="manual login timeout in seconds (default: 600)",
    )
    logout = sub.add_parser("logout", help="log out and clear the session")
    logout.add_argument("id")
    check = sub.add_parser("check", help="check whether the saved session is valid")
    check.add_argument("id")
    check.add_argument("--headful", action="store_true")

    session = sub.add_parser("session", help="export or import sessions").add_subparsers(
        dest="cmd", required=True
    )
    export = session.add_parser("export", help="export to an encrypted .bin file")
    export.add_argument("id")
    export.add_argument("file", type=Path)
    export.add_argument(
        "--include-credentials",
        action="store_true",
        help="include the account login and password (enables automatic login after import); "
        "by default, the bundle contains only the session and browser fingerprint",
    )
    export.add_argument(
        "--no-fingerprint",
        action="store_true",
        help="omit the browser fingerprint (UA, screen, GPU, locale, etc.); a new fingerprint "
        "will be created for the session after import",
    )
    _add_passphrase_options(
        export, "encrypt the bundle with this passphrase instead of ALIWEB_SESSION_SECRET"
    )
    imp = session.add_parser("import", help="import from a .bin file")
    imp.add_argument("file", type=Path)
    imp.add_argument("--as", dest="as_id", help="import under a different ID")
    imp.add_argument("--overwrite", action="store_true")
    _add_passphrase_options(imp, "passphrase used to encrypt the bundle (if exported with --passphrase)")

    profile = sub.add_parser("profile", help="account browser profile").add_subparsers(
        dest="cmd", required=True
    )
    show = profile.add_parser("show", help="show the profile (UA, screen, GPU, locale, etc.)")
    show.add_argument("id")
    regen = profile.add_parser(
        "regenerate",
        help="create a NEW profile (the old one is lost; consider running login --fresh afterwards)",
    )
    regen.add_argument("id")

    debug = sub.add_parser("debug", help="debugging tools").add_subparsers(dest="cmd", required=True)
    open_cmd = debug.add_parser(
        "open",
        help="open a URL and capture diagnostics (WITHOUT logging in)",
        description=(
            "Open the page as-is: use the active account's saved session cookies if available, "
            "otherwise browse anonymously. This does not log in, verify the session, or modify it."
        ),
    )
    open_cmd.add_argument("url")
    open_cmd.add_argument("--headful", action="store_true", help="show the browser window")
    who = open_cmd.add_mutually_exclusive_group()
    who.add_argument("--account", metavar="ID", help="use this account's session instead of the active one")
    who.add_argument("--anonymous", action="store_true", help="ignore saved sessions and use no cookies")
    return parser


def _add_passphrase_options(parser: argparse.ArgumentParser, purpose: str) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--passphrase",
        action="store_true",
        help=f"enter the passphrase in the terminal (input is hidden): {purpose}",
    )
    group.add_argument(
        "--passphrase-env",
        metavar="VARIABLE",
        help="name of the environment variable containing the passphrase (not the passphrase itself)",
    )


def _resolve_passphrase(args: argparse.Namespace, *, confirm: bool) -> Secret | None:
    """Get the bundle passphrase interactively, from an environment variable, or not at all."""
    if args.passphrase:
        phrase = getpass.getpass("Bundle passphrase: ")
        _check_passphrase_length(phrase)
        if confirm and getpass.getpass("Repeat passphrase: ") != phrase:
            raise ConfigurationError("Passphrases do not match")
        return Secret(phrase)
    env_name = args.passphrase_env
    if not env_name:
        return None
    value = os.environ.get(env_name)
    if not value:
        raise ConfigurationError(
            f"Environment variable {env_name!r} is unset or empty. --passphrase-env expects the "
            "VARIABLE NAME, not the passphrase itself. To enter it in the terminal, use --passphrase"
        )
    _check_passphrase_length(value)
    return Secret(value)


def _check_passphrase_length(phrase: str) -> None:
    if len(phrase) < MIN_SECRET_LENGTH:
        raise ConfigurationError(
            f"Passphrase must contain at least {MIN_SECRET_LENGTH} characters "
            "(the bundle contains session cookies, so weak passphrases can be guessed)"
        )


def _print_profile(account_id: AccountId, profile: BrowserProfile | None) -> None:
    if profile is None:
        print(f"Account {account_id} has no profile yet; one will be created on first login or use.")
        return
    width, height = profile.screen_size
    rows = [
        ("Claimed OS", profile.claimed_os.value),
        ("Locale", profile.locale),
        ("Timezone", profile.timezone_id or "(host default)"),
        ("Screen", f"{width}x{height} @ DPR {profile.device_scale_factor:g}"),
        ("Viewport", f"{profile.viewport[0]}x{profile.viewport[1]}"),
        ("GPU", profile.gpu_renderer or "?"),
        ("Cores / memory", f"{profile.hardware_concurrency} / {profile.device_memory} GB"),
        ("User-Agent*", profile.user_agent),
        ("Created", f"{profile.created_at:%Y-%m-%d %H:%M} UTC"),
    ]
    print(f"Account profile: {account_id}")
    for name, value in rows:
        print(f"  {name:<18}{value}")
    print("  * Chrome version in the UA is matched to the actual browser engine on each launch")


def _headless_override(args: argparse.Namespace) -> bool | None:
    """Scraper mode for debug/check: ``--headful`` shows a window; otherwise use .env settings."""
    return False if getattr(args, "headful", False) else None


def login_mode_from_args(args: argparse.Namespace) -> LoginMode:
    """Build the login mode from CLI options. Defaults to headless patchright.

    ``--browser``/``--browser-path`` without ``--headful`` are rejected by the ``LoginMode`` invariant.
    """
    if args.browser_path:
        choice = BrowserChoice.custom(args.browser_path)
    else:
        choice = BrowserChoice(BrowserKind(args.browser or BrowserKind.CHROMIUM.value))
    return LoginMode(
        headless=not args.headful,
        browser=choice,
        manual=args.manual,
        manual_timeout_s=args.manual_timeout,
    )


def _read_password(env_name: str | None) -> Secret:
    password = os.environ.get(env_name, "") if env_name else getpass.getpass("Password: ")
    if not password:
        raise ConfigurationError("Password cannot be empty")
    return Secret(password)


async def _dispatch(api: AliExpressWebApi, args: argparse.Namespace) -> int:
    command = (args.group, getattr(args, "cmd", None))
    match command:
        case ("accounts", "add"):
            credentials = Credentials(args.login, _read_password(args.password_env)) if args.login else None
            await api.accounts.add(AccountId.parse(args.id), credentials)
            print(f"Account {args.id} added" + ("" if credentials else " (without credentials)"))
        case ("accounts", "update"):
            credentials = Credentials(args.login, _read_password(args.password_env))
            await api.accounts.update_credentials(AccountId.parse(args.id), credentials)
            print(f"Credentials updated for account {args.id}")
        case ("accounts", "list"):
            for item in await api.accounts.list_summaries():
                mark = "*" if item.is_active else " "
                state = (
                    f"session from {item.session_captured_at:%Y-%m-%d %H:%M}"
                    if item.has_session
                    else "no session"
                )
                profile_text = f"profile: {item.profile_label}" if item.profile_label else "no profile"
                print(f"{mark} {item.id}  {item.masked_login or '(no login)'}  {state}  {profile_text}")
        case ("accounts", "remove"):
            await api.accounts.remove(AccountId.parse(args.id))
            print("Removed")
        case ("accounts", "rename"):
            await api.accounts.rename(AccountId.parse(args.old_id), AccountId.parse(args.new_id))
            print("Renamed")
        case ("accounts", "use"):
            await api.accounts.set_active(AccountId.parse(args.id))
            print(f"Active account: {args.id}")
        case ("login", _):
            if args.login_mode.manual:
                print(
                    "The browser will open. Log in manually; automation will not interfere. "
                    f"Waiting for up to {int(args.login_mode.manual_timeout_s)} seconds. "
                    "The browser will close and the session will be saved after login."
                )
            login_result = await api.auth.login(
                AccountId.parse(args.id), mode=args.login_mode, fresh=args.fresh
            )
            print(
                "Account is already authenticated; no new login is needed"
                if login_result.already_authenticated
                else "Login successful; session saved"
            )
        case ("logout", _):
            await api.auth.logout(AccountId.parse(args.id))
            print("Session cleared")
        case ("check", _):
            valid = await api.auth.check(AccountId.parse(args.id), headless=_headless_override(args))
            print("Session is valid" if valid else "Session is invalid or missing")
            return 0 if valid else 2
        case ("session", "export"):
            blob = await api.transfer.export_bytes(
                AccountId.parse(args.id),
                include_credentials=args.include_credentials,
                include_fingerprint=not args.no_fingerprint,
                passphrase=_resolve_passphrase(args, confirm=True),
            )
            atomic_write_private(args.file, blob)
            print(f"Exported to: {args.file}")
        case ("session", "import"):
            account = await api.transfer.import_bytes(
                read_bytes(args.file),
                as_id=AccountId.parse(args.as_id) if args.as_id else None,
                overwrite=args.overwrite,
                passphrase=_resolve_passphrase(args, confirm=False),
            )
            print(f"Imported account {account.id}")
        case ("profile", "show"):
            account = await api.accounts.get(AccountId.parse(args.id))
            _print_profile(account.id, account.profile)
        case ("profile", "regenerate"):
            updated = await api.accounts.regenerate_profile(AccountId.parse(args.id))
            label = updated.profile.label if updated.profile else "?"
            print(f"Created a new profile for {updated.id}: {label}")
            if updated.session is not None:
                print(
                    "Warning: the saved session was created in a DIFFERENT environment. "
                    "Log in again with: "
                    f"aliweb login {updated.id} --fresh"
                )
        case ("debug", "open"):
            debug_result = await api.debug.open_and_dump(
                args.url,
                headless=_headless_override(args),
                account_id=AccountId.parse(args.account) if args.account else None,
                anonymous=args.anonymous,
            )
            print(
                f"Opened page in account environment {debug_result.account_id} "
                f"(session: {'yes' if debug_result.with_session else 'no'})"
                if debug_result.account_id
                else "Opened page in a clean browser (no cookies or profile overrides)"
            )
            print(
                f"Diagnostics saved to: {debug_result.dump_dir}"
                if debug_result.dump_dir
                else "Could not save diagnostics"
            )
        case _:  # pragma: no cover - argparse does not allow this case.
            raise ConfigurationError(f"Unknown command: {command}")
    return 0


async def _run(args: argparse.Namespace) -> int:
    if args.group == "secret":
        print(secrets.token_urlsafe(32))
        return 0
    if args.group == "login":
        # Reject invalid options before loading secrets, storage, or the browser.
        args.login_mode = login_mode_from_args(args)
    settings = Settings.from_env(dotenv_path=args.env_file)
    async with build_api(settings) as api:
        return await _dispatch(api, args)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except AliWebError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if exc.diagnostics_dir:
            print(f"Diagnostics: {exc.diagnostics_dir}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
