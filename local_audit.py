"""Local audit events and append-only owner records, independent of LangSmith."""
import json
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from threading import RLock
from inspect import signature
from uuid import uuid4

HERE = Path(__file__).parent
DATA_ROOT=Path(os.environ['RECEPTIONIST_FIXTURE_DIR']) if os.getenv('RECEPTIONIST_FIXTURE_DIR') else HERE
CALLS_FILE = DATA_ROOT / 'calls.jsonl'
REVIEWS_FILE = DATA_ROOT / 'owner_reviews.jsonl'
NOTES_FILE = DATA_ROOT / 'owner_follow_up_notes.jsonl'
_events = ContextVar('local_audit_events', default=None)
_active_call = ContextVar('local_audit_call', default=None)
_capture_start = ContextVar('local_audit_capture_start', default=None)
io_lock = RLock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def append(path: Path, record: dict):
    with io_lock:
        with path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n')


def read(path: Path) -> list[dict]:
    with io_lock:
        return _read(path)


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if line.strip():
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError('Expected object')
                records.append(value)
            except ValueError as error:
                raise ValueError(f'Invalid JSONL record at line {number} in {path.name}') from error
    return records


@contextmanager
def capture(events: list):
    token = _events.set(events)
    clock_token = _capture_start.set(time.perf_counter())
    try:
        yield
    finally:
        _events.reset(token)
        _capture_start.reset(clock_token)


def event(name: str, details: dict | None = None, duration_ms: float | None = None):
    events = _events.get()
    if events is not None:
        started=_capture_start.get()
        record={'name': name, 'at': now(), 'details': details or {},
                'offset_ms':round((time.perf_counter()-started)*1000,3) if started is not None else None,
                'parent_call_id':_active_call.get()}
        if duration_ms is not None:record['duration_ms']=duration_ms
        events.append(record)


def audited(name: str):
    def decorate(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            from observability import summary
            from structured_io import CallPayload,json_value
            call_id=str(uuid4())
            bound=signature(fn).bind_partial(*args,**kwargs)
            inputs={k:v for k,v in bound.arguments.items() if k not in {'self','cls','chat'} and not callable(v)}
            event(name + '.started',CallPayload(call_id=call_id,component=name,inputs=json_value(inputs)).model_dump(mode='json'))
            started=time.perf_counter()
            token=_active_call.set(call_id)
            try:
                value = fn(*args, **kwargs)
            except Exception as error:
                event(name + '.failed',CallPayload(call_id=call_id,component=name,error={'type':type(error).__name__,'code':'call_failed'}).model_dump(mode='json'),duration_ms=round((time.perf_counter()-started)*1000,3))
                raise
            finally:
                _active_call.reset(token)
            details=summary(value)
            details.update(CallPayload(call_id=call_id,component=name,output=json_value(value)).model_dump(mode='json'))
            event(name + '.completed', details,duration_ms=round((time.perf_counter()-started)*1000,3))
            return value
        return wrapper
    return decorate
