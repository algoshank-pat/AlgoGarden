import json
from unittest.mock import Mock
import pytest
import observability as obs
from trace_payloads import payload

def test_structured_payload_redacts_contacts_secrets_and_hidden_reasoning(monkeypatch):
    monkeypatch.setenv('LANGSMITH_API_KEY','secret-test-key')
    result=payload({'name':'Avery','phone':'2025550199','email':'avery@example.com','api_key':'secret-test-key',
        'thinking':'internal thoughts','message':'Call 202-555-0199 or avery@example.com; secret-test-key',
        'content':json.dumps({'phone':'2025550199','name':'Avery','party_size':4,'time':'20:30'})})
    encoded=json.dumps(result)
    assert 'Avery' not in encoded and '2025550199' not in encoded and 'secret-test-key' not in encoded
    assert 'avery@example.com' not in encoded and 'internal thoughts' not in encoded
    assert result['content']['party_size']==4 and result['content']['time']=='20:30'

def test_developer_payloads_export_tool_inputs_and_results(monkeypatch):
    monkeypatch.setenv('LANGSMITH_PAYLOAD_MODE','developer');monkeypatch.setenv('LANGSMITH_TRACING','true');monkeypatch.setenv('LANGSMITH_API_KEY','test-only')
    for key in ['LANGSMITH_AGENT_ID','LANGSMITH_AGENT_ENVIRONMENT','LANGSMITH_PROJECT','LANGCHAIN_PROJECT','LANGCHAIN_SESSION']:monkeypatch.delenv(key,raising=False)
    run=Mock();monkeypatch.setattr(obs,'RunTree',Mock(return_value=run));monkeypatch.setattr(obs,'client',lambda:Mock())
    @obs.observed('check_table','tool')
    def tool(date,party_size):return {'available':True,'date':date,'party_size':party_size}
    tool('2026-11-01',4);obs.drain_exports()
    assert obs.RunTree.call_args.kwargs['inputs']=={'date':'2026-11-01','party_size':4}
    assert run.end.call_args.kwargs['outputs']['result']['party_size']==4

def test_group_update_keeps_transcript_and_finish_keeps_reply(monkeypatch):
    import turn_tracing as tracing
    from langsmith.run_trees import RunTree
    from datetime import datetime,timezone,timedelta
    monkeypatch.setenv('LANGSMITH_PAYLOAD_MODE','developer');monkeypatch.setattr(obs,'export_later',lambda work:None)
    root=RunTree(name='test',inputs={})
    tracing.set_payload(root,inputs={'text':'What are your hours?'},outputs={'reply':'We open at eleven.'})
    tracing.finish(root,channel='chat')
    assert root.inputs['text']=='What are your hours?'
    assert root.outputs['reply']=='We open at eleven.'
    assert root.outputs['channel']=='chat'

def test_updated_root_inputs_are_included_in_sdk_patch(monkeypatch):
    import turn_tracing as tracing
    from langsmith.run_trees import RunTree
    client=Mock()
    monkeypatch.setenv('LANGSMITH_PAYLOAD_MODE','developer')
    monkeypatch.setattr(obs,'export_later',lambda work:work())
    run=RunTree(name='test',inputs={},client=client)
    tracing.set_payload(run,inputs={'text':'What are your opening hours?'})
    assert client.update_run.call_args.kwargs['inputs']=={'text':'What are your opening hours?'}
