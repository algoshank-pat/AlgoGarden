import pytest
import chat_api as api
from owner_transcript import provenance
from tests.test_customer_feedback import client,say

@pytest.mark.parametrize('channel',['chat','voice'])
def test_complete_transcript_survives_session_removal(client,channel):
 sid=client.post('/api/sessions',json={'channel':channel}).json()['session_id']
 result=say(client,sid,'What are your opening hours?')
 assert result['response_sources']==['Rule / template']
 client.post(f'/api/sessions/{sid}/stop');client.post(f'/api/sessions/{sid}/stop')
 client.delete(f'/api/sessions/{sid}')
 for _ in range(2):
  row=next(r for r in client.get('/api/owner/dashboard').json()['calls'] if r['id']==sid)
  transcript=row['transcript']
  assert [m['role'] for m in transcript]==['assistant','user','assistant','system']
  assert 'Saffron' in transcript[0]['text']
  assert transcript[1]['text']=='What are your opening hours?'
  assert transcript[2]['text']==result.get('spoken_reply',result['reply'])
  assert len([m for m in transcript if m['kind']=='stopped'])==1

def test_greeting_only_call_visible(client):
 sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id']
 row=next(r for r in client.get('/api/owner/dashboard').json()['calls'] if r['id']==sid)
 assert len(row['transcript'])==1 and row['transcript'][0]['kind']=='greeting'

def test_sources_are_multiple_and_evidence_based():
 assert provenance({'structured':{'calls':[{'component':'search_knowledge'},{'component':'ollama.chat'}]}})==['RAG','LLM']
 assert provenance({},[{'name':'mcp.lookup.timings'}])==['MCP']
 assert provenance({'routing':{'reason':'pending_booking_status'}})==['Memory']
 assert provenance({})==['Not recorded']
 assert provenance({'structured':{'calls':[{'component':'get_location'}]}})==['Rule / template']
