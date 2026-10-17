from __future__ import annotations

import pytest

from aliweb.cli.main import build_parser, main


def test_parser_covers_commands() -> None:
    parser = build_parser()
    assert parser.parse_args(["login", "main", "--headful"]).headful is True
    assert parser.parse_args(["session", "import", "x.bin", "--as", "new", "--overwrite"]).overwrite
    assert parser.parse_args(["accounts", "add", "a", "--login", "u@m.c"]).cmd == "add"
    with pytest.raises(SystemExit):
        parser.parse_args(["login", "a", "--headful", "--headless"])  # взаємовиключні


def test_secret_generate_prints_key(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["secret", "generate"]) == 0
    assert len(capsys.readouterr().out.strip()) >= 32


def test_missing_secret_is_friendly_error(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
) -> None:
    monkeypatch.delenv("ALIWEB_SESSION_SECRET", raising=False)
    assert main(["--env-file", "/nonexistent/.env", "accounts", "list"]) == 1
    assert "ALIWEB_SESSION_SECRET" in capsys.readouterr().err
