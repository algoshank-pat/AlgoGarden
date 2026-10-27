"""Single background acceptance-suite runner with pollable scenario results."""
import json
from copy import deepcopy
from pathlib import Path
from threading import Lock, Thread
from fastapi import APIRouter, HTTPException

ROOT=Path(__file__).parent
router=APIRouter(prefix='/api/voice/tests')
lock=Lock()
from voice_test_harness import CASES, execute_suite, pending_scenarios
state={'status':'idle','scenarios':pending_scenarios()}
try:
    previous=json.loads((ROOT/'results/voice-suite/latest.json').read_text(encoding='utf-8'))
    if previous.get('status') in {'passed','failed'} and [r['name'] for r in previous['scenarios']]==[r[0] for r in CASES]:
        state=previous
except (OSError,ValueError,KeyError,TypeError):
    pass


def run():
    def update(summary):
        with lock:
            state.clear()
            state.update(summary)
    execute_suite(update)


@router.get('')
def status():
    with lock:return deepcopy(state)


@router.post('',status_code=202)
def start():
    with lock:
        if state['status']=='running':raise HTTPException(409,'A test run is already in progress.')
        state.clear()
        state.update(status='running',scenarios=pending_scenarios())
    Thread(target=run,daemon=True,name='voice-acceptance').start()
    return status()
