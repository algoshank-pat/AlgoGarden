import json
from unittest.mock import Mock
from uuid import uuid4
import pytest
import observability as obs
import turn_tracing as tracing
import local_audit as audit

@pytest.fixture
def setup(monkeypatch,tmp_path):
    tracing._runs.clear()
    monkeypatch.setattr(audit,'DATA_ROOT',tmp_path)
    monkeypatch.setenv('LANGSMITH_TRACING','true')
    monkeypatch.setenv('LANGSMITH_API_KEY','test-only')
    from datetime import datetime,timezone
    run=Mock();run.id=uuid4();run.start_time=datetime.now(timezone.utc);run.extra={};run.tags=[]
    def child(*args,**kwargs):
        value=Mock();value.start_time=kwargs.get('start_time',run.start_time);value.end_time=value.start_time;value.extra=kwargs.get('extra',{});value.tags=[]
        return value
    run.create_child.side_effect=child
    constructor=Mock(return_value=run)
    monkeypatch.setattr(obs,'RunTree',constructor)
    monkeypatch.setattr(obs,'client',lambda:Mock())
    yield run,constructor,tmp_path
    obs.drain_exports();tracing._runs.clear()

def test_speech_backend_and_playback_share_one_root(setup):
    run,constructor,path=setup
    sid,rid=uuid4(),uuid4()
    for name in ['speech.to_text','backend.total','speech.synthesis_compute']:
        with tracing.stage(sid,rid,name):
            assert obs._parent.get() is tracing.group_for(run,tracing.group_name(name))
    tracing.record(sid,rid,'browser.reply_start',1250,run)
    obs.drain_exports()
    assert constructor.call_count==1
    assert [c.args[0] for c in run.create_child.call_args_list]==['Input','Processing','Response']
    rows=[json.loads(line) for line in (path/'turn_timings.jsonl').read_text().splitlines()]
    assert all(row['request_id']==str(rid) for row in rows)
    assert rows[-1]['measurement']=='browser_reported'
    assert obs._parent.get() is None

def test_failure_recorded_without_raw_error(setup):
    run,_,path=setup
    with pytest.raises(ValueError):
        with tracing.stage(uuid4(),uuid4(),'speech.to_text'):raise ValueError('private phone number')
    obs.drain_exports()
    assert 'private phone' not in str(run.mock_calls)
    assert json.loads((path/'turn_timings.jsonl').read_text())['status']=='failed'

def test_disabled_still_records_locally(setup,monkeypatch):
    _,constructor,path=setup
    monkeypatch.setenv('LANGSMITH_TRACING','false')
    with tracing.stage(uuid4(),uuid4(),'backend.total') as run:assert run is None
    constructor.assert_not_called()
    assert (path/'turn_timings.jsonl').exists()

def test_export_problem_does_not_break_stage(setup):
    run,_,_=setup
    run.create_child.side_effect=RuntimeError('unavailable')
    called=[]
    with tracing.stage(uuid4(),uuid4(),'backend.total'):called.append(True)
    assert called==[True]

def test_conversations_have_different_roots(setup):
    _,constructor,_=setup
    rid=uuid4()
    tracing.root_for(uuid4(),rid,'chat');tracing.root_for(uuid4(),rid,'voice')
    assert constructor.call_count==2
    assert constructor.call_args_list[0].kwargs['id']!=constructor.call_args_list[1].kwargs['id']

def test_streaming_synthesis_correlates_timings(setup,monkeypatch):
    import browser_voice as voice
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    run,constructor,path=setup
    monkeypatch.setattr(voice,'speaker',SimpleNamespace(voice=SimpleNamespace(config=SimpleNamespace(sample_rate=22050)),chunks=lambda text:[SimpleNamespace(audio_int16_bytes=b'\0\0'*100)]))
    app=FastAPI();app.include_router(voice.router)
    sid,rid=uuid4(),uuid4()
    with TestClient(app) as api:
        response=api.post('/api/voice/speak/stream',json={'text':'Private customer reply','session_id':str(sid),'request_id':str(rid)})
    assert response.status_code==200
    rows=[json.loads(line) for line in (path/'turn_timings.jsonl').read_text().splitlines()]
    assert {r['stage'] for r in rows}=={'speech.time_to_first_audio','speech.synthesis_compute','speech.stream_lifetime'}
    assert all(r['duration_ms']>=0 for r in rows)
    obs.drain_exports()
    assert 'Private customer reply' not in str(run.mock_calls)

def test_browser_reports_only_completed_known_turns(setup,monkeypatch):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    import chat_api
    _,_,path=setup
    sid,rid=uuid4(),uuid4()
    # Match the real registry; UUID-keyed mocks hid the live 404 regression.
    monkeypatch.setattr(chat_api,'sessions',{str(sid):SimpleNamespace(replies={str(rid):None},channel='voice')})
    with TestClient(chat_api.app) as api:
        url=f'/api/sessions/{sid}/timings'
        assert api.post(url,json={'request_id':str(rid),'stage':'browser.reply_start','duration_ms':1234}).status_code==200
        assert api.post(url,json={'request_id':str(uuid4()),'stage':'browser.reply_start','duration_ms':1234}).status_code==409
        assert api.post(url,json={'request_id':str(rid),'stage':'browser.reply_start','duration_ms':-1}).status_code==422
        assert api.post(url,json={'request_id':str(rid),'stage':'private text','duration_ms':1}).status_code==422

@pytest.mark.parametrize('stage,group',[('speech.to_text','Input'),('speech.endpoint_delay','Input'),('queue.wait','Processing'),('backend.total','Processing'),('speech.synthesis_compute','Response'),('browser.reply_start','Response')])
def test_stage_group_mapping(stage,group):
    assert tracing.group_name(stage)==group

@pytest.mark.parametrize('action,intent,expected',[(None,'reservation','booking'),('update','reservation','update'),('cancel','reservation','cancellation'),('lookup','reservation','lookup'),('update','menu','menu'),(None,'policy','policy')])
def test_use_case_labels_preserve_workflow(setup,action,intent,expected):
    from types import SimpleNamespace
    run,_,_=setup
    bot=SimpleNamespace(reserving=False,reservation_changes=SimpleNamespace(action=action))
    tracing.label_turn(run,bot,'Yes please',{'routing':{'intent':intent}})
    assert run.extra['metadata']['use_case']==expected
    assert 'use_case:'+expected in run.tags
    assert 'Yes please' not in str(run.extra)

def test_finished_cancellation_keeps_prior_label(setup):
    from types import SimpleNamespace
    run,_,_=setup
    bot=SimpleNamespace(reserving=False,reservation_changes=SimpleNamespace(action=None))
    tracing.label_turn(run,bot,'Yes',{'routing':{'intent':'reservation'}},prior='cancellation')
    assert run.extra['metadata']['use_case']=='cancellation'

def test_real_run_tree_nests_instrumented_work(setup,monkeypatch):
    from langsmith.run_trees import RunTree
    _,_,_=setup
    monkeypatch.setattr(obs,'RunTree',RunTree)
    monkeypatch.setattr(obs,'export_later',lambda work:None)
    sid,rid=uuid4(),uuid4()
    with tracing.stage(sid,rid,'backend.total','chat') as root:
        parent=obs._parent.get()
        assert parent.name=='Processing'
        parent.create_child('ollama.chat',run_type='llm',inputs={})
    tracing.record(sid,rid,'speech.to_text',10,root)
    tracing.record(sid,rid,'speech.synthesis_compute',20,root)
    groups={child.name:child for child in root.child_runs}
    assert set(groups)=={'Input','Processing','Response'}
    assert 'ollama.chat' in {child.name for child in groups['Processing'].child_runs}
    assert [child.name for child in groups['Input'].child_runs]==['speech.to_text']
    assert [child.name for child in groups['Response'].child_runs]==['speech.synthesis_compute']
