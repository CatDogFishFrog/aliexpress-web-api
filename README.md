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
aliweb accounts add manual-only                        # акаунт БЕЗ облікових даних (для ручного входу)
aliweb accounts update main --login new@mail.com       # змінити логін/пароль існуючого акаунта
aliweb accounts list                                   # * — активний акаунт
aliweb accounts use other                              # перемкнути активний
aliweb accounts rename main shop                       # назви-ідентифікатори
aliweb check main                                      # чи жива збережена сесія
aliweb session export main main.bin                    # без пароля; --include-credentials щоб з паролем
aliweb session export main main.bin --passphrase-env MY_PHRASE   # окрема парольна фраза
aliweb session import main.bin --as copy --overwrite
aliweb logout main
```

### Вхід в акаунт

```bash
aliweb login main                                  # автоматично, безголовий, браузер patchright
aliweb login main --headful                        # автоматично, з вікном
aliweb login main --headful --browser msedge       # автоматично, у Microsoft Edge
aliweb login main --headful --manual               # РУЧНИЙ: ви входите самі, програма чекає
aliweb login main --headful --browser chrome --manual --manual-timeout 900
aliweb login main --fresh                          # ігнорувати збережену сесію
```

- Автоматичний вхід **людиноподібний**: набір по символах, рухи миші по кривих, природні кліки й скрол
  (`ALIWEB_HUMAN_INPUT`, `ALIWEB_HUMAN_SPEED` у `.env`).
- Якщо акаунт уже авторизований, команда скаже про це й нічого не робитиме (`--fresh` — примусовий новий вхід).
- Якщо AliExpress показує слайдер-капчу, програма проходить її сама (кілька спроб). Якщо автоматика не допомагає,
  використайте `--manual`.
- `--manual`: автоматизацію вимкнено. Програма відкриває сторінку входу й чекає, поки ви увійдете (капча, пасскі, QR, соцмережі — що завгодно).
  Коли вхід підтверджено, вона забирає сесію й **закриває браузер**. Потрібен `--headful`; пароль у акаунті не обов'язковий.
  Закрили вікно раніше, ніж увійшли — сесію не збережено.

### Профіль середовища акаунта (відбиток браузера)

Кожен акаунт має **постійне браузерне середовище**: User-Agent, відбиток (navigator, екран, відеокарта, ядра, пам'ять…), мова, часовий пояс, розмір вікна.
Воно створюється один раз (при першому вході чи використанні акаунта) і зберігається разом із сесією в зашифрованому файлі, тож наступного дня акаунт
працює в тому самому «середовищі». Дані реалістичні: генератор відкидає неправдоподібні комбінації, а версія Chrome в UA завжди відповідає реальному рушію.

```bash
aliweb profile show main                 # що саме «заявляє» акаунт: ОС, екран, GPU, мова…
aliweb profile regenerate main           # створити НОВЕ середовище (потім: aliweb login main --fresh)
aliweb accounts list                     # у кожного акаунта видно його профіль (напр. «windows 1920x1080»)
```

Параметри створення профілю — у `.env`: `ALIWEB_FINGERPRINT_OS` (за замовч. ОС вашого комп'ютера), `ALIWEB_LOCALE`, `ALIWEB_TIMEZONE`.
Вони діють лише для НОВИХ профілів.

### Експорт та імпорт сесій

```bash
aliweb session export main main.bin                       # сесія + відбиток браузера (без логіну/пароля)
aliweb session export main main.bin --include-credentials # + логін і пароль акаунта
aliweb session export main main.bin --no-fingerprint      # без відбитка (при імпорті створиться новий)
aliweb session export main main.bin --passphrase          # окрема фраза, вводиться в терміналі (приховано)
aliweb session import main.bin --as copy --passphrase     # та сама фраза при імпорті
```

- **`--include-credentials`**: у пакет потрапляє логін і пароль. Тоді імпортований акаунт після `logout` зможе увійти автоматично (`aliweb login`).
  Без нього сесія працює, доки не спливе, а повторний вхід вимагатиме задати облікові дані (`aliweb accounts update`) або ручного входу (`--manual`).
- **Відбиток** (середовище) їде з сесією за замовчуванням, щоб cookie й надалі працювали в тому самому «браузері». `--no-fingerprint` вимикає це.
- **`--passphrase` / `--passphrase-env`**: пакет шифрується цією фразою **замість** `ALIWEB_SESSION_SECRET`. Потрібно, коли пакет їде на комп'ютер з іншим
  `.env`: той самий пакет там відкриється лише зі згаданою фразою. `--passphrase` просить ввести її в терміналі (приховано, при експорті з підтвердженням);
  `--passphrase-env ІМ'Я_ЗМІННОЇ` бере її зі змінної середовища (передавати **ім'я** змінної, а не саму фразу). Мінімум 16 символів. Значення фрази
  в аргументі командного рядка навмисно не приймається: воно залишається в історії команд.

### Дебаг сторінок

```bash
aliweb debug open "https://www.aliexpress.com/"             # як є: з сесією активного акаунта, якщо вона є, інакше анонімно
aliweb debug open "https://www.aliexpress.com/" --anonymous # без cookie
aliweb debug open "https://www.aliexpress.com/" --account second --headful
```

`debug open` **не входить** в акаунт, не перевіряє сесію й не змінює її. Це швидкий огляд того, що зараз бачить скрапер.

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
