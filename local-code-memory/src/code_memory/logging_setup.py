"""Structured logging with correlation IDs.

Every log record can carry ``scan_id`` / ``request_id`` / ``task_id`` which are
threaded through via a contextvar so call sites do not have to pass them
explicitly. Output is either JSON lines (structured=true) or a compact human
format. Secrets are never logged by this project; callers must not pass them.
"""

from __future__ import annotations

import contextvars
import json
import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any

_CORRELATION: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "code_memory_correlation", default={}
)

_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


def bind(**ids: str) -> contextvars.Token:
    """Bind correlation ids for the current context. Returns a reset token."""
    current = dict(_CORRELATION.get())
    current.update({k: v for k, v in ids.items() if v is not None})
    return _CORRELATION.set(current)


def unbind(token: contextvars.Token) -> None:
    _CORRELATION.reset(token)


class _CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in _CORRELATION.get().items():
            setattr(record, key, value)
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Promote any extra=... fields and bound correlation ids.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class _TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {
            k: v for k, v in record.__dict__.items()
            if k not in _RESERVED and not k.startswith("_") and k != "taskName"
        }
        if extras:
            base += " " + " ".join(f"{k}={v}" for k, v in extras.items())
        return base


def configure_logging(cfg: dict[str, Any] | None) -> None:
    """Configure the root 'code_memory' logger from a logging config dict."""
    cfg = cfg or {}
    root = logging.getLogger("code_memory")
    root.handlers.clear()
    root.setLevel(getattr(logging, str(cfg.get("level", "INFO")).upper(), logging.INFO))
    root.propagate = False

    structured = bool(cfg.get("structured", True))
    corr_filter = _CorrelationFilter()

    if cfg.get("console", True):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            _JsonFormatter() if structured
            else _TextFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        handler.addFilter(corr_filter)
        root.addHandler(handler)

    file_cfg = cfg.get("file", {}) or {}
    if file_cfg.get("enabled"):
        path = Path(file_cfg.get("path", "./logs/code-memory.log"))
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            path, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(_JsonFormatter() if structured else _TextFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"))
        fh.addFilter(corr_filter)
        root.addHandler(fh)

    for name, level in (cfg.get("levels", {}) or {}).items():
        logging.getLogger(f"code_memory.{name}").setLevel(
            getattr(logging, str(level).upper(), logging.INFO)
        )


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the 'code_memory' namespace."""
    return logging.getLogger(name if name.startswith("code_memory") else f"code_memory.{name}")
