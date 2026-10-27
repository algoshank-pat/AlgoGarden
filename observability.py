"""Optional, payload-minimizing LangSmith traces. Never replay business operations."""
import atexit
import logging
import os
import queue
import threading
import time
from contextvars import ContextVar
from functools import wraps

from langsmith import Client
from langsmith.run_trees import RunTree

_parent = ContextVar('receptionist_trace_parent', default=None)
_client = None
PROMPT_VERSION = 'router-v2-confidence'
_exports=queue.Queue(maxsize=256)
_export_worker=None
_export_lock=threading.Lock()


def _export_loop():
    while True:
        work=_exports.get()
        try:work()
        except Exception:logging.getLogger(__name__).warning('Background trace export failed.')
        finally:_exports.task_done()


def export_later(work):
    global _export_worker
    with _export_lock:
        if _export_worker is None:
            _export_worker=threading.Thread(target=_export_loop,name='trace-export',daemon=True)
            _export_worker.start()
    try:_exports.put_nowait(work)
    except queue.Full:logging.getLogger(__name__).warning('Trace export queue full; span dropped.')


def drain_exports(timeout=2):
    deadline=time.monotonic()+timeout
    while _exports.unfinished_tasks and time.monotonic()<deadline:time.sleep(.005)
    return not _exports.unfinished_tasks


def enabled() -> bool:
    return (os.getenv('LANGSMITH_TRACING', '').lower() == 'true'
            and bool(os.getenv('LANGSMITH_API_KEY')))


def client():
    global _client
    if _client is None:
        _client = Client(timeout_ms=1000, auto_batch_tracing=True)
    return _client


def summary(value) -> dict:
    """Only export known categorical/numeric fields, never arbitrary strings or reprs."""
    from routing import Decision, INTENTS
    from ollama import ChatResponse
    if isinstance(value, ChatResponse):
        return {key: getattr(value, key) for key in ('prompt_eval_count', 'eval_count', 'total_duration', 'load_duration')
                if type(getattr(value, key, None)) is int}
    if isinstance(value, Decision):
        sources = {'llm', 'rule', 'keyword_fallback', 'active_workflow', 'unified_llm'}
        return {'intent': value.intent if value.intent in INTENTS else 'other',
                'source': value.source if value.source in sources else 'unknown',
                'confidence': value.confidence,
                'decision_summary': f'Route selected: {value.intent}' if value.intent in INTENTS else 'Route unavailable'}
    if isinstance(value, dict):
        return {key: value[key] for key in ('booked', 'available', 'saved', 'transferring', 'on_menu', 'open', 'returning', 'has_reservation', 'duplicate', 'relevant', 'found', 'changed')
                if key in value and type(value[key]) is bool}
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], list):
        allowed = {'get_hours', 'get_location', 'get_menu', 'check_dish', 'check_table', 'book_table', 'take_message', 'transfer_to_human', 'recognize_customer', 'search_knowledge', 'get_specials','lookup_reservation','change_reservation','reservation_alternatives'}
        return {'tools_called': [tool for tool in value[1] if isinstance(tool, str) and tool in allowed]}
    return {'completed': True}


def observed(name: str, run_type: str = 'chain'):
    def decorate(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if name == 'receptionist.turn' and args:
                args[0].last_trace_id = None
            if not enabled():
                return fn(*args, **kwargs)
            run, token = None, None
            owner = args[0] if args else None
            try:
                parent = _parent.get()
                metadata = {'prompt_version': PROMPT_VERSION, 'payload_policy': 'no_raw_text'}
                model = kwargs.get('model', getattr(owner, 'model', None))
                if model is not None:
                    metadata['model'] = model if model in {'gemma4:e2b-it-qat', 'gemma4', 'gemma4:latest', 'qwen3.6', 'qwen3.6:latest'} else 'custom_model'
                session_id = getattr(owner, 'trace_session_id', None)
                if isinstance(session_id, str):
                    # Only application-generated UUIDs, not names or user-chosen identifiers.
                    from uuid import UUID
                    metadata['session_id'] = str(UUID(session_id))
                if parent:
                    run = parent.create_child(name, run_type=run_type, inputs={}, extra={'metadata': metadata})
                else:
                    run = RunTree(name=name, run_type=run_type, inputs={},
                                  project_name=os.getenv('LANGSMITH_PROJECT', 'restaurant-receptionist'),
                                  extra={'metadata': metadata}, client=client())
                export_later(run.post)
                token = _parent.set(run)
                if name == 'receptionist.turn':
                    owner.last_trace_id = str(run.id)
            except Exception:
                # Instrumentation setup failure must never block or repeat a booking.
                run = None
                logging.getLogger(__name__).warning('Tracing unavailable; continuing without this span.')
            try:
                result = fn(*args, **kwargs)
            except BaseException:
                if run is not None:
                    try:
                        run.end(error='Operation failed; raw error omitted for privacy.')
                        export_later(run.patch)
                    except Exception:
                        pass
                raise
            else:
                if run is not None:
                    try:
                        outputs = summary(result)
                        decision = getattr(owner, 'last_decision', None)
                        if name == 'receptionist.turn' and decision is not None:
                            outputs['routing'] = summary(decision)
                        run.end(outputs=outputs)
                        export_later(run.patch)
                    except Exception:
                        logging.getLogger(__name__).warning('Trace export failed; operation already completed.')
                return result
            finally:
                if token is not None:
                    _parent.reset(token)
        from local_audit import audited
        return audited(name)(wrapper)
    return decorate


@observed('ollama.chat', 'llm')
def traced_chat(chat, **kwargs):
    from decision_engine import consume_call
    consume_call()
    strict_required=kwargs.pop('_strict_required',False)
    from structured_io import validate_llm_response
    from structured_io import json_value
    from local_audit import event
    kwargs.setdefault('keep_alive',os.getenv('OLLAMA_KEEP_ALIVE','30m'))
    kwargs['options']={'num_ctx':int(os.getenv('OLLAMA_NUM_CTX','2048')),
                       'num_predict':int(os.getenv('OLLAMA_NUM_PREDICT','192')),**kwargs.get('options',{})}
    from local_audit import _active_call
    event('ollama.request_prepared',{'schema_version':'1.0','call_id':_active_call.get(),'component':'ollama.chat','inputs':{'kwargs':json_value(kwargs)}})
    response=chat(**kwargs)
    event('ollama.response_received',{'schema_version':'1.0','output':json_value(response)})
    if strict_required:
        import json
        from structured_io import validate_json
        validate_json(json.loads(response.message.content),kwargs['format'])
    return validate_llm_response(response,kwargs)


def flush():
    drain_exports()
    if _client is not None:
        try:
            _client.flush()
        except Exception:
            pass


atexit.register(flush)
