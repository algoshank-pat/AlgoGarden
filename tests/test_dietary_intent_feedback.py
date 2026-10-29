import io,wave,copy
from uuid import uuid4
from unittest.mock import Mock
import pytest
import chat_api as api
from tests.test_customer_feedback import client,say
from customer_intent import declined_booking

@pytest.mark.parametrize('text',["I do not wanna book it", "I don't want to book a table",'We would rather not reserve today',"No, that’s it. Since you could not tell me if these are gluten-free options, I do not want to book a reservation today."])
def test_decline_intent(text):assert declined_booking(text)

@pytest.mark.parametrize('text',["I don't want to cancel my reservation",'I do not want gluten in my food','Not today, tomorrow please','Can I book without gluten?','I want to book a table'])
def test_negation_scope(text):assert not declined_booking(text)

@pytest.mark.parametrize('channel',['chat','voice'])
def test_exact_failure_and_feedback(client,channel):
 sid=client.post('/api/sessions',json={'channel':channel}).json()['session_id']
 b=api.sessions[sid].bot
 b.tools.book_table=Mock(side_effect=AssertionError('No booking writes allowed'))
 first=say(client,sid,'Before the reservation, can you tell me if your restaurant has gluten fillery options?')
 assert 'gluten-free' in first['reply'] and 'Gobi' not in first['reply'] and 'phone' not in first['reply'].lower()
 answer=say(client,sid,'Do you have gluten-free options?')
 assert 'no gluten listed' in answer['reply'] and 'cross-contact' in answer['reply'] and 'Gobi' not in answer['reply'] and 'Prawns' not in answer['reply']
 assert 'reservation?' in answer['reply']
 add=say(client,sid,'Add these gluten free options.')
 assert 'have not added' in add['reply']
 end=say(client,sid,"No, that's it. Since you could not tell me if these are gluten-free options, I do not want to book a reservation today.")
 assert 'sorry' in end['reply'] and 'voice note' in end['reply'] and 'phone' not in end['reply'].lower()
 assert not b.reserving and not b.slots and api.sessions[sid].pending_reservation_request is None
 say(client,sid,'Yes')
 c=say(client,sid,'You did not answer my gluten-free booking question.')
 assert c['survey']['comment']=='You did not answer my gluten-free booking question.'
 assert c['survey']['status']=='awaiting_rating'
 assert say(client,sid,'six')['survey']['rating'] is None
 last=say(client,sid,'four')
 assert last['survey']['rating']==4 and last['ended']
 records=api.audit.read(api.audit.CALLS_FILE)
 assert records[-1]['session_id']==sid and records[-1]['result']['survey']['comment']==c['survey']['comment']
 b.tools.book_table.assert_not_called();b.chat.assert_not_called()

@pytest.mark.parametrize('channel',['chat','voice'])
def test_dietary_interrupt_preserves_draft(client,channel):
 sid=client.post('/api/sessions',json={'channel':channel}).json()['session_id'];session=api.sessions[sid];b=session.bot
 b.reserving=True;b.awaiting_confirmation=True;b.slots={'name':'Private User','phone':'+15555550100','date':'2026-11-07','time':'20:30','party_size':2}
 b._last_agent_reply='Please confirm your table for two. Is that correct?'
 state=copy.deepcopy(b.slots)
 b.tools.book_table=Mock(side_effect=AssertionError('Information cannot commit a booking'))
 say(client,sid,'Before we book, do you have gluten-free dishes?')
 assert b.slots==state and b.awaiting_confirmation
 repeat=say(client,sid,'Yes')
 assert 'confirm' in repeat['reply'] and b.slots==state
 b.tools.book_table.assert_not_called();b.chat.assert_not_called()


def wave_data():
 stream=io.BytesIO()
 with wave.open(stream,'wb') as w:w.setparams((1,2,16000,0,'NONE','not compressed'));w.writeframes(b'\x00\x00'*1600)
 return stream.getvalue()


def test_voice_note_consent_session_and_persistence(client,monkeypatch,tmp_path):
 monkeypatch.setattr(api.audit,'DATA_ROOT',tmp_path)
 a=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id']
 b=client.post('/api/sessions',json={'channel':'chat'}).json()['session_id']
 note=str(uuid4());url=f'/api/sessions/{a}/feedback/voice-notes/{note}'
 assert client.post(url,content=wave_data()).status_code==409
 say(client,a,"I don't want to book it");say(client,a,'Yes')
 assert client.post(url,content=b'invalid').status_code==422
 assert client.post(url,content=wave_data()).status_code==200
 assert client.post(url,content=wave_data()).status_code==200
 assert client.post(f'/api/sessions/{b}/feedback/voice-notes/{uuid4()}',content=wave_data()).status_code==409
 assert client.get(f'/api/sessions/{b}/feedback/voice-notes/{note}').status_code==404
 comment=say(client,a,'I wanted dietary information, not to book.')
 assert comment['survey']['voice_notes'][0]['url']==url
 assert 'voice_notes' not in api.sessions[b].survey
 say(client,a,'5');api.sessions.pop(a)
 assert client.get(url).content==wave_data()
 assert api.audit.read(tmp_path/'feedback_notes.jsonl')[0]['session_id']==a


def test_skip_and_decline_optional_feedback(client):
 sid=client.post('/api/sessions').json()['session_id']
 say(client,sid,"I do not want to book it")
 assert say(client,sid,"No thanks, I don't want a booking")['ended']
 sid=client.post('/api/sessions').json()['session_id']
 say(client,sid,"I do not want to book it");say(client,sid,'Yes')
 assert say(client,sid,'skip')['survey']['status']=='awaiting_rating'
 assert say(client,sid,'skip')['ended']

@pytest.mark.parametrize('query,expected,excluded',[
 ('Is Gobi Manchurian gluten-free?','lists gluten','Yes,'),
 ('Is Mushroom Pepper Fry gluten free?','does not certify','Yes,'),
 ('I have coeliac disease. Are these dishes safe?','cannot confirm','Yes,'),
])
def test_specific_dietary_question(client,query,expected,excluded):
 sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id']
 answer=say(client,sid,query)
 assert expected in answer['reply'] and excluded not in answer['reply']
 api.sessions[sid].bot.chat.assert_not_called()

@pytest.mark.parametrize('channel',['chat','voice'])
def test_yes_to_dietary_choice_is_not_existing_reservation_consent(client,channel):
 sid=client.post('/api/sessions',json={'channel':channel}).json()['session_id']
 session=api.sessions[sid]
 if channel=='voice':session.pending_reservation_request='Book a table for two next Saturday at half past eight'
 say(client,sid,'Do you have gluten-free dishes?')
 answer=say(client,sid,'Yes please')
 assert 'dietary information' in answer['reply'] and 'phone' not in answer['reply'].lower()
 assert not session.bot.reservation_changes.active


@pytest.mark.parametrize('pending',['voice_gate','readback'])
def test_declining_clears_only_unsaved_work(client,pending):
 sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id']
 session=api.sessions[sid];b=session.bot
 if pending=='voice_gate':session.pending_reservation_request='Book a table for two'
 else:b.reserving=True;b.awaiting_confirmation=True;b.slots={'date':'2026-11-07','time':'20:30'};b._offered_alternatives=[{'time':'19:30'}]
 b.tools.book_table=Mock(side_effect=AssertionError('Decline cannot write'))
 result=say(client,sid,"I don't wanna book it")
 assert result['survey']['status']=='offered' and 'phone' not in result['reply'].lower()
 assert not b.reserving and not b.awaiting_confirmation and not b.slots and not b._offered_alternatives
 assert session.pending_reservation_request is None
 say(client,sid,'Yes');assert not b.reservation_changes.active
 b.chat.assert_not_called();b.tools.book_table.assert_not_called()


def test_asr_dietary_clarification_yes_answers_the_actual_question(client):
 sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id']
 say(client,sid,'Does your restaurant have gluten fillery options?')
 answer=say(client,sid,'Yes')
 assert 'no gluten listed' in answer['reply'] and 'cross-contact' in answer['reply']
 assert not api.sessions[sid].bot.reservation_changes.active
