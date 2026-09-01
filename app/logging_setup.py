"""FreeBatch Studio - logging setup.

Rotating file logs plus a secret-redacting filter. Never log API keys,
Authorization headers, or base64 bodies.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

_MASK_PATTERNS: list[str] = []


def register_secret(secret: str) -> None:
    """Register a live secret value so it is redacted from all logs."""
    secret = (secret or "").strip()
    if len(secret) >= 4 and secret not in _MASK_PATTERNS:
        _MASK_PATTERNS.append(secret)


def register_secrets_from_env() -> None:
    for var in ("AGNES_API_KEY", "AI_HORDE_API_KEY"):
        val = os.environ.get(var, "").strip()
        if val:
            register_secret(val)


class SecretRedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        redacted = msg
        for secret in _MASK_PATTERNS:
            if secret in redacted:
                redacted = redacted.replace(secret, "***MASKED***")
        for header in ("authorization", "apikey", "x-api-key"):
            lowered = redacted.lower()
            idx = lowered.find(header)
            while idx != -1:
                line_end = redacted.find("\n", idx)
                end = line_end if line_end != -1 else len(redacted)
                if ":" in redacted[idx:end]:
                    redacted = redacted[:idx] + header + ": ***MASKED***" + redacted[end:]
                lowered = redacted.lower()
                idx = lowered.find(header, end)
        record.msg = redacted
        record.args = ()
        return True


def setup_logging(log_dir: str, level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("freebatch")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    _redact_filter = SecretRedactingFilter()

    file_handler = RotatingFileHandler(
        os.path.join(log_dir, "app.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(_redact_filter)
    logger.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(_redact_filter)
    logger.addHandler(console)

    register_secrets_from_env()
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger("freebatch")
