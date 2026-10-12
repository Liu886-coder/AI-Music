import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import perf_counter
from uuid import uuid4
from contextvars import ContextVar
from datetime import datetime, timezone

from app.core.config import get_settings

current_trace = ContextVar('agent_trace', default=None)
logger = logging.getLogger('music_agent.audit')


def sanitize(value):
    settings = get_settings()
    if isinstance(value, dict):
        return {k: sanitize(v) for k, v in value.items() if k.lower() not in
                {'api_key', 'authorization', 'llm_api_key', 'lastfm_api_key'}}
    if isinstance(value, (list, tuple)):
        return [sanitize(v) for v in value]
    if isinstance(value, str):
        for secret in (settings.llm_api_key, settings.lastfm_api_key):
            if secret:
                value = value.replace(secret, '[REDACTED]')
        return value
    return value


class Trace:
    def __init__(self, message, controls):
        self.message = message
        self.request_id = str(uuid4())
        self.started = perf_counter()
        self.events = []
        self.emit('request', request_hash=hashlib.sha256(message.encode()).hexdigest(),
                  controls=controls.model_dump(),
                  **({'request_text': message} if get_settings().agent_log_raw_requests else {}))

    def emit(self, event, **data):
        record = sanitize({'request_id': self.request_id, 'event': event,
                           'timestamp': datetime.now(timezone.utc).isoformat(),
                           'elapsed_ms': round((perf_counter() - self.started) * 1000), **data})
        if not get_settings().agent_log_raw_requests and self.message:
            def redact_echo(value):
                if isinstance(value, dict):
                    return {key: redact_echo(item) for key, item in value.items()}
                if isinstance(value, list):
                    return [redact_echo(item) for item in value]
                return value.replace(self.message, '[REQUEST REDACTED]') if isinstance(value, str) else value
            # Preserve event names and correlation IDs even for very short requests.
            record = {**record, **redact_echo(sanitize(data))}
        self.events.append(record)
        if not get_settings().agent_log_enabled:
            return
        try:
            path = Path(get_settings().agent_log_dir).resolve() / 'agent.jsonl'
            if not any(getattr(h, 'baseFilename', '') == str(path) for h in logger.handlers):
                for old in list(logger.handlers):
                    logger.removeHandler(old)
                    old.close()
                path.parent.mkdir(parents=True, exist_ok=True)
                handler = RotatingFileHandler(path, maxBytes=5_000_000, backupCount=3, encoding='utf-8')
                handler.setFormatter(logging.Formatter('%(message)s'))
                logger.addHandler(handler)
                logger.setLevel(logging.INFO)
                logger.propagate = False
            logger.info(json.dumps(record, ensure_ascii=False))
        except OSError:
            logging.getLogger(__name__).warning('Agent audit log unavailable')


def emit(event, **data):
    trace = current_trace.get()
    if trace:
        trace.emit(event, **data)
