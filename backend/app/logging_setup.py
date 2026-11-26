import logging
import sys
from pathlib import Path

import structlog

from app.config import PROJECT_ROOT

_configured = False


def setup_logging(level: str = "INFO") -> None:
    global _configured

    log_level = getattr(logging, level.upper(), logging.INFO)
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(log_level)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    stream.setLevel(log_level)
    root.addHandler(stream)

    log_path: str | None = None
    try:
        log_dir_path = PROJECT_ROOT / "data" / "logs"
        log_dir_path.mkdir(parents=True, exist_ok=True)
        file_path = log_dir_path / "chronicum.log"
        fh = logging.FileHandler(file_path, encoding="utf-8")
        fh.setFormatter(formatter)
        fh.setLevel(log_level)
        root.addHandler(fh)
        log_path = str(file_path)
    except OSError:
        log_path = None

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    _configured = True
    get_logger(__name__).info("logging_ready", level=level.upper(), file=log_path)


def get_logger(name: str | None = None):
    return structlog.get_logger(name)


def log_dir() -> Path:
    return PROJECT_ROOT / "data" / "logs"
