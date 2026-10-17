"""CLI ``aliweb``. Тонкий шар: парсинг аргументів → виклик сервісів → друк результату.

Паролі та парольні фрази НІКОЛИ не приймаються аргументами командного рядка (потрапляють
в історію shell/ps): лише інтерактивний запит або змінна середовища.
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
from aliweb.domain.errors import AliWebError, ConfigurationError
from aliweb.domain.secret import Secret
from aliweb.infrastructure.config import Settings
from aliweb.infrastructure.storage.files import atomic_write_private, read_bytes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aliweb", description="AliExpress Web API (каркас)")
    parser.add_argument("--env-file", default=".env", help="шлях до .env (за замовч. ./.env)")
    sub = parser.add_subparsers(dest="group", required=True)

    secret_cmd = sub.add_parser("secret", help="ключ шифрування").add_subparsers(dest="cmd", required=True)
    secret_cmd.add_parser("generate", help="згенерувати ключ для ALIWEB_SESSION_SECRET")

    accounts = sub.add_parser("accounts", help="керування акаунтами").add_subparsers(
        dest="cmd", required=True
    )
    add = accounts.add_parser("add", help="додати акаунт")
    add.add_argument("id")
    add.add_argument("--login", required=True, help="e-mail або телефон")
    add.add_argument("--password-env", help="змінна середовища з паролем (інакше — інтерактивний запит)")
    accounts.add_parser("list", help="список акаунтів")
    remove = accounts.add_parser("remove", help="видалити акаунт")
    remove.add_argument("id")
    rename = accounts.add_parser("rename", help="перейменувати акаунт")
    rename.add_argument("old_id")
    rename.add_argument("new_id")
    use = accounts.add_parser("use", help="зробити акаунт активним")
    use.add_argument("id")

    login = sub.add_parser("login", help="увійти в акаунт")
    login.add_argument("id")
    mode = login.add_mutually_exclusive_group()
    mode.add_argument("--headful", action="store_true", help="показати браузер")
    mode.add_argument("--headless", action="store_true", help="примусово без вікна")
    login.add_argument("--fresh", action="store_true", help="ігнорувати збережену сесію")
    logout = sub.add_parser("logout", help="вийти (очистити сесію)")
    logout.add_argument("id")
    check = sub.add_parser("check", help="перевірити дійсність збереженої сесії")
    check.add_argument("id")
    check.add_argument("--headful", action="store_true")

    session = sub.add_parser("session", help="експорт/імпорт сесій").add_subparsers(dest="cmd", required=True)
    export = session.add_parser("export", help="експорт у зашифрований .bin")
    export.add_argument("id")
    export.add_argument("file", type=Path)
    export.add_argument("--include-credentials", action="store_true", help="включити логін/пароль")
    export.add_argument("--passphrase-env", help="змінна з окремою парольною фразою пакета")
    imp = session.add_parser("import", help="імпорт із .bin")
    imp.add_argument("file", type=Path)
    imp.add_argument("--as", dest="as_id", help="імпортувати під іншим id")
    imp.add_argument("--overwrite", action="store_true")
    imp.add_argument("--passphrase-env", help="змінна з парольною фразою пакета")

    debug = sub.add_parser("debug", help="інструменти дебагу").add_subparsers(dest="cmd", required=True)
    open_cmd = debug.add_parser("open", help="відкрити URL активним акаунтом і зробити дамп")
    open_cmd.add_argument("url")
    open_cmd.add_argument("--headful", action="store_true")
    return parser


def _passphrase(env_name: str | None) -> Secret | None:
    if not env_name:
        return None
    value = os.environ.get(env_name)
    if not value:
        raise ConfigurationError(f"Змінна середовища {env_name} порожня або не задана")
    return Secret(value)


def _headless_override(args: argparse.Namespace) -> bool | None:
    if getattr(args, "headful", False):
        return False
    if getattr(args, "headless", False):
        return True
    return None


def _read_password(env_name: str | None) -> Secret:
    password = os.environ.get(env_name, "") if env_name else getpass.getpass("Пароль: ")
    if not password:
        raise ConfigurationError("Пароль порожній")
    return Secret(password)


async def _dispatch(api: AliExpressWebApi, args: argparse.Namespace) -> int:
    command = (args.group, getattr(args, "cmd", None))
    match command:
        case ("accounts", "add"):
            credentials = Credentials(args.login, _read_password(args.password_env))
            await api.accounts.add(AccountId.parse(args.id), credentials)
            print(f"Акаунт {args.id} додано")
        case ("accounts", "list"):
            for item in await api.accounts.list_summaries():
                mark = "*" if item.is_active else " "
                state = (
                    f"сесія від {item.session_captured_at:%Y-%m-%d %H:%M}"
                    if item.has_session
                    else "без сесії"
                )
                print(f"{mark} {item.id}  {item.masked_login or '(без логіну)'}  {state}")
        case ("accounts", "remove"):
            await api.accounts.remove(AccountId.parse(args.id))
            print("Видалено")
        case ("accounts", "rename"):
            await api.accounts.rename(AccountId.parse(args.old_id), AccountId.parse(args.new_id))
            print("Перейменовано")
        case ("accounts", "use"):
            await api.accounts.set_active(AccountId.parse(args.id))
            print(f"Активний акаунт: {args.id}")
        case ("login", _):
            await api.auth.login(
                AccountId.parse(args.id), headless=_headless_override(args), fresh=args.fresh
            )
            print("Вхід виконано, сесію збережено")
        case ("logout", _):
            await api.auth.logout(AccountId.parse(args.id))
            print("Сесію очищено")
        case ("check", _):
            valid = await api.auth.check(AccountId.parse(args.id), headless=_headless_override(args))
            print("Сесія дійсна" if valid else "Сесія недійсна або відсутня")
            return 0 if valid else 2
        case ("session", "export"):
            blob = await api.transfer.export_bytes(
                AccountId.parse(args.id),
                include_credentials=args.include_credentials,
                passphrase=_passphrase(args.passphrase_env),
            )
            atomic_write_private(args.file, blob)
            print(f"Експортовано: {args.file}")
        case ("session", "import"):
            account = await api.transfer.import_bytes(
                read_bytes(args.file),
                as_id=AccountId.parse(args.as_id) if args.as_id else None,
                overwrite=args.overwrite,
                passphrase=_passphrase(args.passphrase_env),
            )
            print(f"Імпортовано акаунт {account.id}")
        case ("debug", "open"):
            path = await api.debug.open_and_dump(args.url, headless=_headless_override(args))
            print(f"Дамп збережено: {path}" if path else "Дамп зберегти не вдалося")
        case _:  # pragma: no cover - argparse сюди не пропустить
            raise ConfigurationError(f"Невідома команда: {command}")
    return 0


async def _run(args: argparse.Namespace) -> int:
    if args.group == "secret":
        print(secrets.token_urlsafe(32))
        return 0
    settings = Settings.from_env(dotenv_path=args.env_file)
    async with build_api(settings) as api:
        return await _dispatch(api, args)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except AliWebError as exc:
        print(f"Помилка: {exc}", file=sys.stderr)
        if exc.diagnostics_dir:
            print(f"Діагностика: {exc.diagnostics_dir}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
