from __future__ import annotations

import pytest

from aliweb.cli.main import build_parser, login_mode_from_args, main
from aliweb.domain.browser import BrowserChoice, BrowserKind, LoginMode
from aliweb.domain.errors import InvalidLoginModeError


def test_parser_covers_commands() -> None:
    parser = build_parser()
    assert parser.parse_args(["login", "main", "--headful"]).headful is True
    assert parser.parse_args(["session", "import", "x.bin", "--as", "new", "--overwrite"]).overwrite
    assert parser.parse_args(["accounts", "add", "a", "--login", "u@m.c"]).cmd == "add"
    with pytest.raises(SystemExit):
        parser.parse_args(["login", "a", "--browser", "chrome", "--browser-path", "/x"])  # взаємовиключні
    with pytest.raises(SystemExit):
        parser.parse_args(["login", "a", "--browser", "firefox"])  # лише chromium-сімейство


def test_secret_generate_prints_key(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["secret", "generate"]) == 0
    assert len(capsys.readouterr().out.strip()) >= 32


def test_missing_secret_is_friendly_error(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
) -> None:
    monkeypatch.delenv("ALIWEB_SESSION_SECRET", raising=False)
    assert main(["--env-file", "/nonexistent/.env", "accounts", "list"]) == 1
    assert "ALIWEB_SESSION_SECRET" in capsys.readouterr().err


def mode_for(*argv: str) -> LoginMode:
    return login_mode_from_args(build_parser().parse_args(["login", "main", *argv]))


class TestLoginModeFromArgs:
    def test_no_flags_is_headless_patchright(self) -> None:
        assert mode_for() == LoginMode(headless=True)

    def test_headful_defaults_to_patchright_browser(self) -> None:
        assert mode_for("--headful") == LoginMode(headless=False)
        assert mode_for("--headful", "--browser", "chromium") == LoginMode(headless=False)

    @pytest.mark.parametrize("name", ["chrome", "msedge"])
    def test_headful_real_browser(self, name: str) -> None:
        mode = mode_for("--headful", "--browser", name)
        assert mode == LoginMode(headless=False, browser=BrowserChoice(BrowserKind(name)))

    def test_custom_path(self) -> None:
        mode = mode_for("--headful", "--browser-path", "C:/Brave/brave.exe")
        assert mode.browser == BrowserChoice.custom("C:/Brave/brave.exe")

    @pytest.mark.parametrize("extra", [["--browser", "msedge"], ["--browser-path", "/x"]])
    def test_real_browser_without_headful_is_rejected(self, extra: list[str]) -> None:
        with pytest.raises(InvalidLoginModeError, match="--headful"):
            mode_for(*extra)

    def test_rejection_is_friendly_cli_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["login", "main", "--browser", "msedge"]) == 1
        assert "--headful" in capsys.readouterr().err
