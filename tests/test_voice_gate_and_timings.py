from unittest.mock import Mock
import pytest
import chat_api as api
from tests.test_customer_feedback import client,say

@pytest.mark.parametrize('original',['I would like to do a reservation tomorrow.','Yeah, I would like to make a reservation tomorrow.','Could I make a booking next Saturday?'])
@pytest.mark.parametrize('answer',['No.','No, no.',"No, I do not have a reservation.",'No thanks.'])
def test_scoped_new_selection_preserves_date_without_generation(client,original,answer):
 sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id'];s=api.sessions[sid];b=s.bot;s.pending_reservation_request=original
 r=say(client,sid,answer)
 assert 'phone' in r['reply'].lower() and b.slots.get('date') in {'tomorrow','next Saturday'}
 assert b.reserving and not b.awaiting_confirmation
 assert not r['structured']['tools_called'];b.chat.assert_not_called()


@pytest.mark.parametrize('stage',['browser.endpoint_detected','browser.recognition_request','browser.recognition_complete','browser.backend_request','browser.backend_reply','browser.speech_request','browser.first_audio_received','browser.playback_started','browser.reply_start','browser.tts_to_first_playback'])
def test_real_timing_endpoint_resolves_uuid_to_string_registry_key(client,stage):
 sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id']
 result=say(client,sid,'What are your opening hours?');rid=result['request_id']
 response=client.post(f'/api/sessions/{sid}/timings',json={'request_id':rid,'stage':stage,'duration_ms':100})
 assert response.status_code==200 and response.json()['saved']

def test_invalid_or_uncompleted_timing_cannot_cross_sessions(client):
 from uuid import uuid4
 sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id']
 result=say(client,sid,'What are your opening hours?')
 other=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id']
 payload={'request_id':result['request_id'],'stage':'browser.reply_start','duration_ms':10}
 assert client.post(f'/api/sessions/{other}/timings',json=payload).status_code==409
 assert client.post(f'/api/sessions/{uuid4()}/timings',json=payload).status_code==404

@pytest.mark.parametrize('answer',["No, I'm not sure.",'No, maybe I do.','No, actually yes.','Yes, no, I cannot remember.'])
def test_ambiguous_gate_answer_does_not_choose_new_or_erase_request(client,answer):
 sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id'];s=api.sessions[sid];b=s.bot
 s.pending_reservation_request='I would like to do a reservation tomorrow.'
 result=say(client,sid,answer)
 assert s.pending_reservation_request
 if answer in {"No, I'm not sure.",'No, maybe I do.'}:
  # Existing uncertainty behavior intentionally offers the 20-second wait.
  assert result['reply']=='Okay, I can wait for 20 seconds.'
  assert result['conversation_wait']['delay_ms']==20000
 else:assert 'yes or no' in result['reply']
 assert not b.reserving and not b.slots and not b.reservation_changes.active
 b.chat.assert_not_called()

def test_no_without_the_existing_new_gate_cannot_start_a_new_booking(client):
 sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id'];s=api.sessions[sid]
 s.bot.turn=Mock(return_value=('Could you repeat that?',[]))
 reply,tools=api.voice_reservation_turn(s,'No.')
 assert reply=='Could you repeat that?' and not s.bot.reserving and not s.pending_reservation_request
 s.bot.turn.assert_called_once_with('No.')

def test_failed_timing_storage_is_not_reported_as_saved(client,monkeypatch):
 import turn_tracing
 sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id']
 result=say(client,sid,'What are your opening hours?')
 monkeypatch.setattr(turn_tracing,'record',lambda *args,**kwargs:False)
 response=client.post(f'/api/sessions/{sid}/timings',json={'request_id':result['request_id'],'stage':'browser.reply_start','duration_ms':10})
 assert response.status_code==503


@pytest.mark.parametrize('answer',["No, I don't have a reservation.","No, I don’t have a reservation.",'No.','No, I do not have a reservation.'])
def test_polite_full_request_keeps_all_fields_without_inference_or_write(client,answer):
 sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id'];s=api.sessions[sid];b=s.bot
 say(client,sid,"Thanks. I'd like to book a table for two on November 10th, 2026, at 7 in the evening.")
 reply=say(client,sid,answer)
 assert 'phone' in reply['reply'].lower()
 assert b.slots=={'party_size':2,'date':'November 10th, 2026','time':'7 in the evening'}
 assert not b.tools.booking_store().list(b.tools.business_id) and not b.awaiting_confirmation
 b.chat.assert_not_called()
