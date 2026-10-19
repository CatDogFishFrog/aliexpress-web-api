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
from aliweb.domain.browser import BrowserChoice, BrowserKind, LoginMode
from aliweb.domain.errors import AliWebError, ConfigurationError
from aliweb.domain.profile import BrowserProfile
from aliweb.domain.secret import Secret
from aliweb.infrastructure.config import Settings
from aliweb.infrastructure.crypto.scrypt_aesgcm import MIN_SECRET_LENGTH
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
    add.add_argument(
        "--login",
        help="e-mail або телефон; без нього акаунт створюється без облікових даних "
        "(для ручного входу: login --headful --manual)",
    )
    add.add_argument("--password-env", help="змінна середовища з паролем (інакше — інтерактивний запит)")
    update = accounts.add_parser("update", help="змінити логін/пароль існуючого акаунта")
    update.add_argument("id")
    update.add_argument("--login", required=True, help="новий e-mail або телефон")
    update.add_argument("--password-env", help="змінна середовища з паролем (інакше — запит)")
    accounts.add_parser("list", help="список акаунтів")
    remove = accounts.add_parser("remove", help="видалити акаунт")
    remove.add_argument("id")
    rename = accounts.add_parser("rename", help="перейменувати акаунт")
    rename.add_argument("old_id")
    rename.add_argument("new_id")
    use = accounts.add_parser("use", help="зробити акаунт активним")
    use.add_argument("id")

    login = sub.add_parser(
        "login",
        help="увійти в акаунт",
        description=(
            "Режим входу задається тут, а не в .env. Без прапорців — безголовий вхід у браузері "
            "patchright (підходить для сервера без графіки). Браузер Chrome/Edge/власний доступний "
            "лише з --headful."
        ),
    )
    login.add_argument("id")
    login.add_argument("--headful", action="store_true", help="показати вікно браузера")
    browser = login.add_mutually_exclusive_group()
    browser.add_argument(
        "--browser",
        choices=[kind.value for kind in BrowserKind if kind is not BrowserKind.CUSTOM],
        help="браузер для входу (потрібен --headful): chromium = браузер patchright (за замовч.)",
    )
    browser.add_argument(
        "--browser-path",
        metavar="ШЛЯХ",
        help="шлях до власного chromium-сумісного браузера (потрібен --headful)",
    )
    login.add_argument("--fresh", action="store_true", help="ігнорувати збережену сесію")
    login.add_argument(
        "--manual",
        action="store_true",
        help="ручний вхід: автоматизацію вимкнено, програма чекає, поки ви увійдете самі, "
        "після чого забирає сесію й закриває браузер (потрібен --headful; пароль не потрібен)",
    )
    login.add_argument(
        "--manual-timeout",
        type=float,
        default=600.0,
        metavar="СЕК",
        help="скільки чекати ручного входу (за замовч. 600)",
    )
    logout = sub.add_parser("logout", help="вийти (очистити сесію)")
    logout.add_argument("id")
    check = sub.add_parser("check", help="перевірити дійсність збереженої сесії")
    check.add_argument("id")
    check.add_argument("--headful", action="store_true")

    session = sub.add_parser("session", help="експорт/імпорт сесій").add_subparsers(dest="cmd", required=True)
    export = session.add_parser("export", help="експорт у зашифрований .bin")
    export.add_argument("id")
    export.add_argument("file", type=Path)
    export.add_argument(
        "--include-credentials",
        action="store_true",
        help="покласти в пакет логін і пароль акаунта (щоб після імпорту було можливе автоматичне "
        "входження); за замовчуванням у пакеті лише сесія та відбиток браузера",
    )
    export.add_argument(
        "--no-fingerprint",
        action="store_true",
        help="НЕ включати відбиток браузера (UA, екран, GPU, мова…). Після імпорту для сесії "
        "буде створено новий відбиток",
    )
    _add_passphrase_options(export, "пакет шифрується цією фразою замість ALIWEB_SESSION_SECRET")
    imp = session.add_parser("import", help="імпорт із .bin")
    imp.add_argument("file", type=Path)
    imp.add_argument("--as", dest="as_id", help="імпортувати під іншим id")
    imp.add_argument("--overwrite", action="store_true")
    _add_passphrase_options(imp, "фраза, якою зашифровано пакет (якщо експорт був із --passphrase)")

    profile = sub.add_parser("profile", help="профіль браузерного середовища акаунта").add_subparsers(
        dest="cmd", required=True
    )
    show = profile.add_parser("show", help="показати профіль (UA, екран, GPU, мова…)")
    show.add_argument("id")
    regen = profile.add_parser(
        "regenerate",
        help="створити НОВИЙ профіль (старий втрачається; після цього варто виконати login --fresh)",
    )
    regen.add_argument("id")

    debug = sub.add_parser("debug", help="інструменти дебагу").add_subparsers(dest="cmd", required=True)
    open_cmd = debug.add_parser(
        "open",
        help="відкрити URL і зробити дамп (БЕЗ входу в акаунт)",
        description=(
            "Відкриває сторінку «як є»: з cookie збереженої сесії активного акаунта, якщо вона є, "
            "інакше анонімно. Вхід не виконується, чинність сесії не перевіряється, "
            "сесія не змінюється."
        ),
    )
    open_cmd.add_argument("url")
    open_cmd.add_argument("--headful", action="store_true", help="показати вікно браузера")
    who = open_cmd.add_mutually_exclusive_group()
    who.add_argument("--account", metavar="ID", help="сесія цього акаунта (замість активного)")
    who.add_argument("--anonymous", action="store_true", help="без cookie (ігнорувати сесію)")
    return parser


def _add_passphrase_options(parser: argparse.ArgumentParser, purpose: str) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--passphrase",
        action="store_true",
        help=f"ввести парольну фразу в терміналі (приховано): {purpose}",
    )
    group.add_argument(
        "--passphrase-env",
        metavar="ЗМІННА",
        help="ІМ'Я змінної середовища, що містить парольну фразу (не сама фраза!)",
    )


def _resolve_passphrase(args: argparse.Namespace, *, confirm: bool) -> Secret | None:
    """Парольна фраза пакета: інтерактивно (``--passphrase``), зі змінної (``--passphrase-env``) або немає."""
    if args.passphrase:
        phrase = getpass.getpass("Парольна фраза пакета: ")
        _check_passphrase_length(phrase)
        if confirm and getpass.getpass("Повторіть парольну фразу: ") != phrase:
            raise ConfigurationError("Фрази не збігаються")
        return Secret(phrase)
    env_name = args.passphrase_env
    if not env_name:
        return None
    value = os.environ.get(env_name)
    if not value:
        raise ConfigurationError(
            f"Змінна середовища {env_name!r} не задана або порожня. Прапорець --passphrase-env приймає "
            "ІМ'Я змінної, а не саму фразу. Щоб ввести фразу прямо в терміналі, використайте --passphrase"
        )
    _check_passphrase_length(value)
    return Secret(value)


def _check_passphrase_length(phrase: str) -> None:
    if len(phrase) < MIN_SECRET_LENGTH:
        raise ConfigurationError(
            f"Парольна фраза має містити щонайменше {MIN_SECRET_LENGTH} символів "
            "(пакет містить cookie сесії — слабка фраза піддається підбору)"
        )


def _print_profile(account_id: AccountId, profile: BrowserProfile | None) -> None:
    if profile is None:
        print(f"У {account_id} ще немає профілю — його буде створено при першому вході/використанні.")
        return
    width, height = profile.screen_size
    rows = [
        ("ОС (заявлена)", profile.claimed_os.value),
        ("Мова", profile.locale),
        ("Часовий пояс", profile.timezone_id or "(як у хоста)"),
        ("Екран", f"{width}x{height} @ DPR {profile.device_scale_factor:g}"),
        ("Вікно (viewport)", f"{profile.viewport[0]}x{profile.viewport[1]}"),
        ("GPU", profile.gpu_renderer or "?"),
        ("Ядра / пам'ять", f"{profile.hardware_concurrency} / {profile.device_memory} ГБ"),
        ("User-Agent*", profile.user_agent),
        ("Створено", f"{profile.created_at:%Y-%m-%d %H:%M} UTC"),
    ]
    print(f"Профіль акаунта {account_id}:")
    for name, value in rows:
        print(f"  {name:<18}{value}")
    print("  * версія Chrome в UA підганяється під реальний рушій при кожному запуску")


def _headless_override(args: argparse.Namespace) -> bool | None:
    """Режим СКРАПЕРА для debug/check: ``--headful`` → з вікном, інакше з .env (браузер patchright)."""
    return False if getattr(args, "headful", False) else None


def login_mode_from_args(args: argparse.Namespace) -> LoginMode:
    """Режим входу з прапорців. Без прапорців — безголовий вхід у браузері patchright.

    ``--browser``/``--browser-path`` без ``--headful`` відхиляються інваріантом ``LoginMode``.
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
    password = os.environ.get(env_name, "") if env_name else getpass.getpass("Пароль: ")
    if not password:
        raise ConfigurationError("Пароль порожній")
    return Secret(password)


async def _dispatch(api: AliExpressWebApi, args: argparse.Namespace) -> int:
    command = (args.group, getattr(args, "cmd", None))
    match command:
        case ("accounts", "add"):
            credentials = Credentials(args.login, _read_password(args.password_env)) if args.login else None
            await api.accounts.add(AccountId.parse(args.id), credentials)
            print(f"Акаунт {args.id} додано" + ("" if credentials else " (без облікових даних)"))
        case ("accounts", "update"):
            credentials = Credentials(args.login, _read_password(args.password_env))
            await api.accounts.update_credentials(AccountId.parse(args.id), credentials)
            print(f"Облікові дані акаунта {args.id} оновлено")
        case ("accounts", "list"):
            for item in await api.accounts.list_summaries():
                mark = "*" if item.is_active else " "
                state = (
                    f"сесія від {item.session_captured_at:%Y-%m-%d %H:%M}"
                    if item.has_session
                    else "без сесії"
                )
                profile_text = f"профіль: {item.profile_label}" if item.profile_label else "без профілю"
                print(f"{mark} {item.id}  {item.masked_login or '(без логіну)'}  {state}  {profile_text}")
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
            if args.login_mode.manual:
                print(
                    "Відкриється браузер. Увійдіть в акаунт самостійно — програма не втручається; "
                    f"очікую до {int(args.login_mode.manual_timeout_s)} с. Після входу браузер "
                    "закриється автоматично, а сесія збережеться."
                )
            login_result = await api.auth.login(
                AccountId.parse(args.id), mode=args.login_mode, fresh=args.fresh
            )
            print(
                "Акаунт уже авторизований (сесія активна), повторний вхід не потрібен"
                if login_result.already_authenticated
                else "Вхід виконано, сесію збережено"
            )
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
                include_fingerprint=not args.no_fingerprint,
                passphrase=_resolve_passphrase(args, confirm=True),
            )
            atomic_write_private(args.file, blob)
            print(f"Експортовано: {args.file}")
        case ("session", "import"):
            account = await api.transfer.import_bytes(
                read_bytes(args.file),
                as_id=AccountId.parse(args.as_id) if args.as_id else None,
                overwrite=args.overwrite,
                passphrase=_resolve_passphrase(args, confirm=False),
            )
            print(f"Імпортовано акаунт {account.id}")
        case ("profile", "show"):
            account = await api.accounts.get(AccountId.parse(args.id))
            _print_profile(account.id, account.profile)
        case ("profile", "regenerate"):
            updated = await api.accounts.regenerate_profile(AccountId.parse(args.id))
            label = updated.profile.label if updated.profile else "?"
            print(f"Створено новий профіль для {updated.id}: {label}")
            if updated.session is not None:
                print(
                    "Увага: збережена сесія створена в ІНШОМУ середовищі. Рекомендується увійти заново: "
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
                f"Сторінку відкрито в середовищі акаунта {debug_result.account_id} "
                f"(сесія: {'так' if debug_result.with_session else 'ні'})"
                if debug_result.account_id
                else "Сторінку відкрито «чистим» браузером (без cookie й без підміни середовища)"
            )
            print(
                f"Дамп збережено: {debug_result.dump_dir}"
                if debug_result.dump_dir
                else "Дамп зберегти не вдалося"
            )
        case _:  # pragma: no cover - argparse сюди не пропустить
            raise ConfigurationError(f"Невідома команда: {command}")
    return 0


async def _run(args: argparse.Namespace) -> int:
    if args.group == "secret":
        print(secrets.token_urlsafe(32))
        return 0
    if args.group == "login":
        # Некоректні прапорці відхиляємо ДО будь-яких інших дій (ключ, сховище, браузер).
        args.login_mode = login_mode_from_args(args)
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
