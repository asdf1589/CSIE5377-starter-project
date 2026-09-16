"""
Structured (JSON-lines) logging.

Why JSON instead of plain text: at even moderate concurrency, log lines from
different URLs interleave. Free-text logs become unparseable; JSON lines can
be piped into `jq`, Loki, Elasticsearch, or just `grep` for a field. This is
the cheapest form of observability you can add to a system and it should be
there from day one, not bolted on after the first production incident.
"""
import json
import logging
import sys
import time

# Fields we allow callers to attach via `logger.info(msg, extra={...})`.
_EXTRA_FIELDS = (
    "url", "domain", "status", "elapsed_ms", "attempt",
    "worker_id", "queue_depth", "seen", "inflight",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in _EXTRA_FIELDS:
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [handler]
    # aiohttp's own access logger is noisy at INFO; keep it at WARNING.
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
