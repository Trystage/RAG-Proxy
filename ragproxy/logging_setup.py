"""日志配置：文件 + 控制台；API key 脱敏；用户输入输出转义换行。"""
from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_SENSITIVE_PATTERNS = [
    re.compile(r"(sk-[A-Za-z0-9_-]{6,})"),
    re.compile(r"(api[_-]?key[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9._-]{6,})", re.I),
    re.compile(r"(bearer\s+[A-Za-z0-9._-]{6,})", re.I),
]

_ESCAPE_RE = re.compile(r"[\r\n]+")


class SensitiveFilter(logging.Filter):
    """脱敏常见密钥形态，并把用户内容里的换行转义为 \\n。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            for pat in _SENSITIVE_PATTERNS:
                msg = pat.sub(lambda m: m.group(1)[:3] + "****", msg)
            if "\n" in msg or "\r" in msg:
                msg = _ESCAPE_RE.sub(" \\n ", msg)
            record.msg = msg
            record.args = ()
        except Exception:  # noqa: BLE001 - 日志本身不能抛异常
            pass
        return True


def setup_logging(data_dir: Path, level: str = "INFO") -> None:
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    for h in list(root.handlers):
        root.removeHandler(h)

    file_handler = RotatingFileHandler(
        log_dir / "rag-proxy.log",
        maxBytes=20 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(SensitiveFilter())

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(SensitiveFilter())

    root.addHandler(file_handler)
    root.addHandler(console)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
