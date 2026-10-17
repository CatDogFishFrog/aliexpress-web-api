"""Налаштування логування: консоль + (опційно) файл із ротацією, текст або JSON, маскування секретів."""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from datetime import UTC, datetime
from pathlib import Path

from aliweb.infrastructure.config import LoggingSettings
from aliweb.infrastructure.redaction import Redactor

ROOT_LOGGER_NAME = "aliweb"
_STD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


def _extra_fields(record: logging.LogRecord) -> dict[str, object]:
    return {k: v for k, v in record.__dict__.items() if k not in _STD_ATTRS}


class RedactingFilter(logging.Filter):
    """Маскує секрети в повідомленні, аргументах, extra та тексті винятку."""

    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self._redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redactor.redact(record.getMessage())
        record.args = None
        for key, value in _extra_fields(record).items():
            if isinstance(value, str):
                setattr(record, key, self._redactor.redact(value))
        if record.exc_info and not record.exc_text:
            record.exc_text = self._redactor.redact(logging.Formatter().formatException(record.exc_info))
        return True


class TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        extra = _extra_fields(record)
        if extra:
            text += " | " + " ".join(f"{k}={v}" for k, v in extra.items())
        return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **_extra_fields(record),
        }
        if record.exc_info:
            payload["exception"] = record.exc_text or self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(settings: LoggingSettings, *, logs_dir: Path, redactor: Redactor) -> logging.Logger:
    """Конфігурує логер ``aliweb`` (ідемпотентно). Повертає кореневий логер проєкту."""
    logger = logging.getLogger(ROOT_LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    level = getattr(logging, settings.level, None)
    if not isinstance(level, int):
        raise ValueError(f"Невідомий рівень логування: {settings.level!r}")
    logger.setLevel(level)
    logger.propagate = False

    formatter: logging.Formatter = JsonFormatter() if settings.json else TextFormatter()
    redacting = RedactingFilter(redactor)

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if settings.to_file:
        logs_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                logs_dir / "aliweb.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
            )
        )
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(redacting)
        logger.addHandler(handler)
    return logger
