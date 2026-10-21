# aliweb — unofficial AliExpress Web API (scaffold)

Scrapes AliExpress with [patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python). Python **>= 3.14**.

## Installation

```bash
uv venv --python 3.14 && source .venv/bin/activate     # or python3.14 -m venv .venv
uv pip install -e ".[dev]"
patchright install chromium                            # required by the scraper
cp .env.example .env
aliweb secret generate                                 # put the result in ALIWEB_SESSION_SECRET
```

> Store `ALIWEB_SESSION_SECRET` separately. Without it, saved accounts and `.bin` files cannot be decrypted.

## Browsers

| Use | Browser | Configuration |
|---|---|---|
| **Scraper** (API operations) | **Always patchright** (`patchright install chromium`) | Headless/headful mode: `ALIWEB_HEADLESS` in `.env` |
| **Account login** | Choose when starting the login command | `aliweb login` options |

Account login (`aliweb login <id> ...`):

```bash
aliweb login main                                  # headless, patchright (for terminal-only servers)
aliweb login main --headful                        # show the patchright browser window
aliweb login main --headful --browser chrome       # installed Google Chrome
aliweb login main --headful --browser msedge       # installed Microsoft Edge
aliweb login main --headful --browser-path "C:\Program Files\Brave\brave.exe"   # custom Chromium-compatible browser
```

- Headless login only supports patchright; `--browser` and `--browser-path` require `--headful`.
- Chrome, Edge, and custom browsers must already be installed; patchright does not download them.
- After login, the session is saved. Scraping continues in patchright using those cookies. If AliExpress reacts to the browser difference (logout or captcha), log in with `--browser chromium`.
- On a server without a graphical desktop, log in with `--headful` on Windows, run `aliweb session export`, and transfer the `.bin` file to the server with `aliweb session import`.

## Quick Start (CLI)

```bash
aliweb accounts add main --login me@mail.com           # prompts for password
aliweb accounts add manual-only                        # account without credentials (for manual login)
aliweb accounts update main --login new@mail.com       # update an existing account's login/password
aliweb accounts list                                   # * marks the active account
aliweb accounts use other                              # switch the active account
aliweb accounts rename main shop                       # rename an account identifier
aliweb check main                                      # check the saved session
aliweb session export main main.bin                    # without a password; use --include-credentials to include it
aliweb session export main main.bin --passphrase-env MY_PHRASE
aliweb session import main.bin --as copy --overwrite
aliweb logout main
```

### Logging In

```bash
aliweb login main                                  # automatic, headless, patchright
aliweb login main --headful                        # automatic, with a browser window
aliweb login main --headful --browser msedge       # automatic, in Microsoft Edge
aliweb login main --headful --manual               # manual: you log in while the program waits
aliweb login main --headful --browser chrome --manual --manual-timeout 900
aliweb login main --fresh                          # ignore the saved session
```

- Automatic login uses human-like typing, mouse movement, clicks, and scrolling (`ALIWEB_HUMAN_INPUT`, `ALIWEB_HUMAN_SPEED` in `.env`).
- If the account is already authenticated, the command reports it and does nothing (`--fresh` starts a new login).
- If AliExpress shows a slider captcha, the program attempts to solve it. If automation does not work, use `--manual`.
- `--manual` disables automation. The program opens the login page and waits for you to sign in by any available method (captcha, passkey, QR code, social login, etc.). Once login is confirmed, it captures the session and closes the browser. Requires `--headful`; account credentials are optional.
- If you close the window before signing in, the session is not saved.

### Account Browser Profile

Each account has a **persistent browser environment**: User-Agent, fingerprint (navigator, screen, GPU, cores, memory, etc.), locale, timezone, and window size. It is created once (on first login or use) and saved with the session in an encrypted file, so the account uses the same environment on subsequent days. The generator rejects unrealistic combinations and matches the Chrome version in the User-Agent to the actual engine.

```bash
aliweb profile show main                 # OS, screen, GPU, locale, and other profile details
aliweb profile regenerate main           # create a NEW profile (then run: aliweb login main --fresh)
aliweb accounts list                     # each account shows its profile, e.g. "windows 1920x1080"
```

Profile creation settings are in `.env`: `ALIWEB_FINGERPRINT_OS` (defaults to the host OS), `ALIWEB_LOCALE`, and `ALIWEB_TIMEZONE`. These settings apply only to NEW profiles.

### Exporting and Importing Sessions

```bash
aliweb session export main main.bin                       # session + browser profile, no login/password
aliweb session export main main.bin --include-credentials # also include account login and password
aliweb session export main main.bin --no-fingerprint      # omit profile; a new one is created on import
aliweb session export main main.bin --passphrase          # hidden passphrase prompt in the terminal
aliweb session import main.bin --as copy --passphrase     # enter the same passphrase during import
```

- **`--include-credentials`** adds the login and password to the bundle. After import, the account can log in automatically with `aliweb login` after logout. Without it, the session works until it expires; to log in again, update account credentials (`aliweb accounts update`) or use manual login (`--manual`).
- The browser profile is included by default so the session cookies continue to work in the same environment. `--no-fingerprint` omits it.
- **`--passphrase` / `--passphrase-env`** encrypts the bundle with this passphrase instead of `ALIWEB_SESSION_SECRET`. Use it to transfer a bundle to a computer with a different `.env`; the bundle can only be opened there with the same passphrase. `--passphrase` prompts in the terminal (hidden input, with confirmation on export). `--passphrase-env VARIABLE_NAME` reads the value from an environment variable; pass the variable **name**, not the passphrase. Minimum length: 16 characters. Passing the passphrase as a command-line argument is deliberately unsupported because it would remain in shell history.

### Page Diagnostics

```bash
aliweb debug open "https://www.aliexpress.com/"             # use the active account session if available; otherwise anonymous
aliweb debug open "https://www.aliexpress.com/" --anonymous # no cookies
aliweb debug open "https://www.aliexpress.com/" --account second --headful
```

`debug open` does **not** log in, verify the session, or modify it. It quickly shows what the scraper currently sees.

## Using as a Library

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


asyncio.run(main())  # raises ExtractorNotImplementedError until extractors are implemented
```

## Diagnostics

When an error occurs (`ALIWEB_DUMP_POLICY=on_error`), artifacts are saved to `data/diagnostics/<time>_<reason>_<id>/`: `screenshot.png`, `snapshot.mhtml` (complete page copy with resources; open in Chromium, Chrome, or Edge), `page.html`, `network.jsonl`, and `report.json`. The path is also added to the error message. `ALIWEB_DUMP_POLICY=always` additionally captures each login flow checkpoint.

## Development

```bash
pytest                 # 170+ tests, including architecture invariants
ruff check . && ruff format --check .
mypy                   # strict
```
