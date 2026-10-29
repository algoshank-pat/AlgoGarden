from uuid import uuid4
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
import chat_api as api
from customer_feedback import OFFER,RATING

@pytest.fixture
def client(monkeypatch,tmp_path):
 from concurrency import TurnScheduler
 monkeypatch.setattr(api,'scheduler',TurnScheduler())
 monkeypatch.setattr(api.engine.booking_db,'DB_PATH',tmp_path/'reservations.sqlite3')
 for name in ['CALLS_FILE','REVIEWS_FILE','NOTES_FILE']:monkeypatch.setattr(api.audit,name,tmp_path/(name+'.jsonl'))
 for name in ['BOOKINGS_FILE','MESSAGES_FILE']:monkeypatch.setattr(api.engine,name,tmp_path/(name+'.jsonl'))
 def bot():
  b=api.engine.Receptionist(api.restaurant,chat=Mock(side_effect=AssertionError('Survey needs no inference')))
  b.tools.transfer_to_human=Mock()
  return b
 monkeypatch.setattr(api,'new_bot',bot);api.sessions.clear()
 with TestClient(api.app) as c:yield c
 api.sessions.clear()

def say(c,sid,text,**extra):
 r=c.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':text,**extra});assert r.status_code==200
 return r.json()

def begin(c,channel='voice'):
 sid=c.post('/api/sessions',json={'channel':channel}).json()['session_id']
 assert say(c,sid,'What are your opening hours?')['reply'].endswith('Is there anything else I can help you with?')
 v=say(c,sid,'No, thank you. That is all.')
 assert v['reply']==OFFER and not v['ended'] and v['survey']['status']=='offered'
 return sid

@pytest.mark.parametrize('channel',['voice','chat'])
def test_consent_rating_persisted_by_call_after_session_deleted(client,channel):
 sid=begin(client,channel)
 assert say(client,sid,'good')['survey']['rating'] is None
 assert say(client,sid,'Yes, please')['reply']==RATING
 payload={'request_id':str(uuid4()),'text':'It was good'}
 a=client.post(f'/api/sessions/{sid}/turns',json=payload).json()
 b=client.post(f'/api/sessions/{sid}/turns',json=payload).json()
 assert a['structured']['output']['survey']==a['survey']
 assert a==b and a['survey']['rating']=='good' and a['ended'] and not a['handoff']
 assert not any(c['component']=='ollama.chat' for c in a['structured']['calls'])
 client.delete(f'/api/sessions/{sid}')
 row=next(c for c in client.get('/api/owner/dashboard').json()['calls'] if c['id']==sid)
 assert row['feedback']['rating']=='good' and row['intent']=='hours' and row['outcome']=='Answered'
 assert sum(e['name']=='feedback.rated' for e in row['events'])==1

@pytest.mark.parametrize('channel',['voice','chat'])
@pytest.mark.parametrize('rating',['0','5','4.5','good or bad','not good','excellent',''])
def test_invalid_score_is_not_saved(client,rating,channel):
 sid=begin(client,channel);say(client,sid,'Yes')
 v=say(client,sid,rating or ('[No intelligible speech detected]' if channel=='voice' else '...'),**({'input_kind':'unrecognized_audio'} if not rating and channel=='voice' else {}))
 assert not v['ended'] and v['survey']['rating'] is None and not v['handoff']
 assert say(client,sid,'Skip')['ended']

@pytest.mark.parametrize('channel',['voice','chat'])
@pytest.mark.parametrize('rating',['good','bad'])
def test_good_or_bad_response(client,rating,channel):
 sid=begin(client,channel);say(client,sid,'Sure')
 v=say(client,sid,str(rating));assert v['ended'] and v['survey']['rating']==rating

@pytest.mark.parametrize('channel',['voice','chat'])
def test_declining_does_not_record_rating(client,channel):
 sid=begin(client,channel);v=say(client,sid,'No thanks')
 assert v['ended'] and v['survey']['status']=='declined' and v['survey']['rating'] is None

def test_staff_request_during_survey_is_not_blocked(client):
 sid=begin(client);v=say(client,sid,'Can I speak to a staff member?')
 assert v['ended'] and v['handoff'] and v['survey']['rating'] is None

def test_customer_can_resume_business_instead_of_survey(client):
 sid=begin(client);v=say(client,sid,'What are your opening hours?')
 assert 'Our hours' in v['reply'] and not v['ended'] and v['survey']['status']=='skipped'

def test_hard_stop_does_not_offer_survey(client):
 sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id']
 say(client,sid,'What are your opening hours?')
 v=say(client,sid,'Stop the call')
 assert v['ended'] and v['survey']['status']=='not_offered'


def test_natural_rating_can_mention_booking_experience(client):
 sid=begin(client);say(client,sid,'Yes')
 v=say(client,sid,'My booking experience was good')
 assert v['ended'] and v['survey']['rating']=='good'


def test_staff_handoff_also_offers_optional_survey(client):
 sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id']
 first=say(client,sid,'I would like to speak to a person')
 assert first['handoff'] and not first['ended'] and first['survey']['status']=='offered'
 assert 'optional survey' in first['reply']
 last=say(client,sid,'No thanks')
 assert last['ended'] and last['survey']['status']=='declined'
 assert 'Thank you' in last['reply']
