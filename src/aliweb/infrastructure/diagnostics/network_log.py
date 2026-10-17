"""Легкий журнал мережевих запитів сторінки для діагностики (без тіл і заголовків)."""

from __future__ import annotations

import json
from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from patchright.async_api import BrowserContext, Request, Response

from aliweb.infrastructure.redaction import Redactor


@dataclass(slots=True)
class NetworkEntry:
    ts: str
    method: str
    url: str
    resource_type: str
    status: int | None = None
    failure: str | None = None


class NetworkRecorder:
    """Кільцевий буфер останніх N запитів/відповідей контексту.

    Навмисно НЕ зберігає заголовки й тіла (там cookie/токени); лише метадані. URL
    проходять через :class:`Redactor`.
    """

    def __init__(self, redactor: Redactor, *, capacity: int = 500) -> None:
        self._redactor = redactor
        self._entries: deque[NetworkEntry] = deque(maxlen=capacity)
        self._by_request: dict[int, NetworkEntry] = {}

    def attach(self, context: BrowserContext) -> None:
        context.on("request", self._on_request)
        context.on("response", self._on_response)
        context.on("requestfailed", self._on_failed)

    def _on_request(self, request: Request) -> None:
        entry = NetworkEntry(
            ts=datetime.now(UTC).isoformat(timespec="milliseconds"),
            method=request.method,
            url=self._redactor.redact(request.url),
            resource_type=request.resource_type,
        )
        self._entries.append(entry)
        self._by_request[id(request)] = entry

    def _on_response(self, response: Response) -> None:
        entry = self._by_request.pop(id(response.request), None)
        if entry is not None:
            entry.status = response.status

    def _on_failed(self, request: Request) -> None:
        entry = self._by_request.pop(id(request), None)
        if entry is not None:
            entry.failure = self._redactor.redact(request.failure or "failed")

    def __len__(self) -> int:
        return len(self._entries)

    def to_jsonl(self) -> str:
        return "\n".join(json.dumps(asdict(e), ensure_ascii=False) for e in self._entries)
