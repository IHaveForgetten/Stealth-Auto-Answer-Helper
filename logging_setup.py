"""统一日志：文件 + 控制台。API Key 等敏感字段在落盘前打码。"""

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_DIR = Path(__file__).resolve().parent / "logs"
_SECRET_PATTERN = re.compile(r"(sk-[A-Za-z0-9_\-]{4})[A-Za-z0-9_\-]+")


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _SECRET_PATTERN.sub(r"\1***", record.msg)
        if record.args:
            record.args = tuple(
                _SECRET_PATTERN.sub(r"\1***", a) if isinstance(a, str) else a
                for a in record.args
            )
        return True


def get_logger() -> logging.Logger:
    logger = logging.getLogger("autodati")
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    logger.addFilter(_RedactFilter())

    _LOG_DIR.mkdir(exist_ok=True)
    fh = RotatingFileHandler(
        _LOG_DIR / "autodati.log", maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(threadName)s %(message)s")
    )
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
    logger.addHandler(ch)

    return logger


def log_dir() -> Path:
    _LOG_DIR.mkdir(exist_ok=True)
    return _LOG_DIR
