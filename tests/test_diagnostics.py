"""Тести збирача діагностики на фейковій сторінці (реальний браузер не потрібен)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from aliweb.infrastructure.config import DiagnosticsSettings, DumpPolicy
from aliweb.infrastructure.diagnostics.collector import DiagnosticsCollector
from aliweb.infrastructure.redaction import Redactor


class FakeCdp:
    def __init__(self, data: str) -> None:
        self.data = data
        self.detached = False

    async def send(self, method: str, params: dict[str, Any]) -> dict[str, str]:
        assert method == "Page.captureSnapshot" and params == {"format": "mhtml"}
        return {"data": self.data}

    async def detach(self) -> None:
        self.detached = True


class FakeContext:
    def __init__(self, cdp: FakeCdp) -> None:
        self.cdp = cdp

    async def new_cdp_session(self, page: object) -> FakeCdp:
        return self.cdp


class FakePage:
    url = "https://www.aliexpress.com/item/1.html?token=SECRETTOKEN"
    viewport_size: dict[str, int] = {"width": 1366, "height": 768}  # noqa: RUF012

    def __init__(self, *, fail_screenshot: bool = False, secret: str = "PASSWORD-XYZ") -> None:
        self.cdp = FakeCdp(f"MHTML-DATA {secret}")
        self.context = FakeContext(self.cdp)
        self.fail_screenshot = fail_screenshot
        self.secret = secret
        self.masked = False

    async def evaluate(self, js: str) -> int:
        self.masked = "password" in js
        return 1

    async def screenshot(self, *, path: Path, full_page: bool, type: str) -> None:
        if self.fail_screenshot:
            raise RuntimeError("screenshot exploded")
        Path(path).write_bytes(b"\x89PNG")

    async def content(self) -> str:
        return f"<html>{self.secret}</html>"

    async def title(self) -> str:
        return "Title"


def collector(
    tmp_path: Path, *, keep: int = 50, policy: DumpPolicy = DumpPolicy.ON_ERROR
) -> DiagnosticsCollector:
    redactor = Redactor()
    redactor.register("PASSWORD-XYZ")
    return DiagnosticsCollector(DiagnosticsSettings(policy=policy, keep=keep), tmp_path, redactor)


async def test_full_dump_contains_all_artifacts_and_redacts(tmp_path: Path) -> None:
    page = FakePage()
    err = ValueError("bad PASSWORD-XYZ")
    directory = await collector(tmp_path).capture(cast(Any, page), reason="Login Failed!", error=err)
    assert directory is not None
    names = {p.name for p in directory.iterdir()}
    assert {"screenshot.png", "snapshot.mhtml", "page.html", "report.json"} <= names
    assert page.masked and page.cdp.detached
    for file in directory.iterdir():
        if file.suffix in {".html", ".mhtml", ".json"}:
            assert "PASSWORD-XYZ" not in file.read_text(encoding="utf-8")
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    assert report["error"]["type"] == "ValueError" and "SECRETTOKEN" not in report["url"]
    assert all(v == "ok" for v in report["artifacts"].values())
    assert "login-failed" in directory.name


async def test_one_failing_step_does_not_stop_others(tmp_path: Path) -> None:
    directory = await collector(tmp_path).capture(cast(Any, FakePage(fail_screenshot=True)), reason="x")
    assert directory is not None
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    assert report["artifacts"]["screenshot"].startswith("failed")
    assert report["artifacts"]["snapshot_mhtml"] == "ok" and (directory / "page.html").exists()


async def test_policy_gating(tmp_path: Path) -> None:
    page = cast(Any, FakePage())
    never = collector(tmp_path / "n", policy=DumpPolicy.NEVER)
    assert await never.on_error(page, RuntimeError("x"), None) is None
    on_error = collector(tmp_path / "e")
    assert await on_error.checkpoint(page, "step", None) is None  # чекпоінти лише для ALWAYS
    assert await on_error.on_error(page, RuntimeError("x"), None) is not None
    always = collector(tmp_path / "a", policy=DumpPolicy.ALWAYS)
    assert await always.checkpoint(page, "step", None) is not None


async def test_retention_keeps_newest(tmp_path: Path) -> None:
    c = collector(tmp_path, keep=3)
    for i in range(6):
        await c.capture(cast(Any, FakePage()), reason=f"r{i}")
    assert len([p for p in tmp_path.iterdir() if p.is_dir()]) == 3


@pytest.mark.parametrize("reason", ["", "../../etc/passwd", "a" * 200])
async def test_reason_cannot_escape_directory(tmp_path: Path, reason: str) -> None:
    directory = await collector(tmp_path).capture(cast(Any, FakePage()), reason=reason)
    assert directory is not None and directory.parent == tmp_path
