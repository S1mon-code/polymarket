"""JSON structured logging for all bots."""

import json
import logging
import sys
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "bot": getattr(record, "bot", "unknown"),
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            log_entry["error"] = self.formatException(record.exc_info)
        extra_keys = set(record.__dict__) - set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)
        for key in extra_keys:
            if key not in ("bot",):
                log_entry[key] = record.__dict__[key]
        return json.dumps(log_entry)


def get_logger(bot_name: str, level: str = "INFO") -> logging.Logger:
    """Create a JSON-structured logger for a specific bot."""
    logger = logging.getLogger(f"trading.{bot_name}")
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)

    # Inject bot name into all records
    old_factory = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)
        record.bot = bot_name
        return record

    logging.setLogRecordFactory(factory)
    return logger
