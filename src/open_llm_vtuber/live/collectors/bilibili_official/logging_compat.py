"""Small loguru adapter for the ported Bilibili client.

The upstream client used a project-specific logger facade with an ``exc``
keyword. Keeping this adapter local avoids coupling the live collector to
Amaidesu's logging package.
"""

from __future__ import annotations

from typing import Any

from loguru import logger


class _CompatLogger:
    def __init__(self, name: str) -> None:
        self._logger = logger.bind(component=name)

    def _write(self, level: str, message: str, *args: Any, exc: Any = None, **kwargs: Any) -> None:
        target = self._logger.opt(exception=exc) if exc is not None else self._logger
        target.log(level, message, *args, **kwargs)

    def debug(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._write("DEBUG", message, *args, **kwargs)

    def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._write("INFO", message, *args, **kwargs)

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._write("WARNING", message, *args, **kwargs)

    def error(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._write("ERROR", message, *args, **kwargs)

    def exception(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._write("ERROR", message, *args, exc=True, **kwargs)


def get_logger(name: str) -> _CompatLogger:
    return _CompatLogger(name)


__all__ = ["get_logger"]
