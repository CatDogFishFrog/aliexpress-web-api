# aliweb — неофіційний AliExpress Web API (каркас)

Скрапінг AliExpress через [patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python). Python **>= 3.14**.

## Встановлення

```bash
uv venv --python 3.14 && source .venv/bin/activate     # або python3.14 -m venv .venv
uv pip install -e ".[dev]"
patchright install chromium                            # або встановіть Google Chrome (channel=chrome)
cp .env.example .env
aliweb secret generate                                 # вставте результат у ALIWEB_SESSION_SECRET
```

> Збережіть `ALIWEB_SESSION_SECRET` окремо: без нього акаунти та `.bin`-файли не розшифрувати.

## Швидкий старт (CLI)

```bash
aliweb accounts add main --login me@mail.com           # пароль запитає інтерактивно
aliweb login main                                      # headless за замовчуванням
aliweb login main --headful                            # якщо headless не пускає — з вікном
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
