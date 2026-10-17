# aliweb — неофіційний AliExpress Web API (каркас)

Скрапінг AliExpress через [patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python). Python **>= 3.14**.

## Встановлення

```bash
uv venv --python 3.14 && source .venv/bin/activate     # або python3.14 -m venv .venv
uv pip install -e ".[dev]"
patchright install chromium                            # браузер patchright: потрібен скраперу завжди
cp .env.example .env
aliweb secret generate                                 # вставте результат у ALIWEB_SESSION_SECRET
```

> Збережіть `ALIWEB_SESSION_SECRET` окремо: без нього акаунти та `.bin`-файли не розшифрувати.

## Браузери

| Що | Який браузер | Де задається |
|---|---|---|
| **Скрапер** (робота API) | **Завжди браузер patchright** (`patchright install chromium`) | режим безголовий/з вікном — `ALIWEB_HEADLESS` у `.env` |
| **Вхід в акаунт** | на вибір — під час запуску логіну | прапорці `aliweb login` |

Вхід (`aliweb login <id> …`):

```bash
aliweb login main                                  # безголовий, браузер patchright (сервер лише з терміналом)
aliweb login main --headful                        # з вікном, браузер patchright
aliweb login main --headful --browser chrome       # з вікном, встановлений Google Chrome
aliweb login main --headful --browser msedge       # з вікном, встановлений Microsoft Edge
aliweb login main --headful --browser-path "C:\Program Files\Brave\brave.exe"   # власний chromium-сумісний
```

- Безголовий вхід **лише** в браузері patchright; `--browser`/`--browser-path` без `--headful` дають помилку.
- Chrome/Edge/власний браузер повинні бути встановлені в системі, завантажувати їх через patchright не треба.
- Після входу сесія зберігається, а скрапінг далі йде в браузері patchright з цими cookie.
  Якщо AliExpress реагуватиме на різницю браузерів (розлогін/капча) — входьте з `--browser chromium`.
- Сервер без графіки: увійдіть `--headful` на Windows, зробіть `aliweb session export`, перенесіть `.bin`
  на сервер і `aliweb session import`.

## Швидкий старт (CLI)

```bash
aliweb accounts add main --login me@mail.com           # пароль запитає інтерактивно
aliweb login main                                      # безголовий вхід (браузер patchright)
aliweb login main --headful --browser msedge           # з вікном у Edge — див. розділ «Браузери»
aliweb accounts list                                   # * — активний акаунт
aliweb accounts use other                              # перемкнути активний
aliweb accounts rename main shop                       # назви-ідентифікатори
aliweb check main                                      # чи жива збережена сесія
aliweb session export main main.bin                    # без пароля; --include-credentials щоб з паролем
aliweb session export main main.bin --passphrase-env MY_PHRASE   # окрема парольна фраза
aliweb session import main.bin --as copy --overwrite
aliweb logout main
aliweb debug open "https://www.aliexpress.com/..."     # відкрити URL активним акаунтом + дамп
```

## Використання як бібліотеки

```python
import asyncio
from aliweb.bootstrap import build_api
from aliweb.domain.accounts import AccountId
from aliweb.domain.catalog import SearchQuery
from aliweb.infrastructure.config import Settings


async def main() -> None:
    async with build_api(Settings.from_env()) as api:
        await api.auth.login(AccountId.parse("main"))
        async for page in api.catalog.iter_pages(SearchQuery("usb c cable"), max_pages=3):
            for product in page.products:
                print(product.title, product.ref)


asyncio.run(main())  # поки екстрактори — заглушки: підніметься ExtractorNotImplementedError
```

## Діагностика

При помилці (політика `ALIWEB_DUMP_POLICY=on_error`) у `data/diagnostics/<час>_<причина>_<id>/`:
`screenshot.png`, `snapshot.mhtml` (повна копія сторінки з ресурсами — відкривати в Chromium/Chrome/Edge),
`page.html`, `network.jsonl`, `report.json`. Шлях також додається до тексту помилки.
`ALIWEB_DUMP_POLICY=always` додатково дампить кожну контрольну точку потоку входу.

## Розробка

```bash
pytest                 # 170+ тестів, включно з архітектурними інваріантами
ruff check . && ruff format --check .
mypy                   # strict
```
