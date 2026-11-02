import copy,json,base64,io,wave
from unittest.mock import Mock
import pytest
import chat_api as api
from tests.test_customer_feedback import client,say

def draft(client):
 sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id'];b=api.sessions[sid].bot
 b.reserving=True;b.slots={'date':'2027-02-17','time':'19:00','party_size':5,'phone':'2025550173'};b._recognized_phone='+12025550173'
 return sid,b

def test_spell_separate_parts_verify_name_then_verify_booking(client):
 sid,b=draft(client);b._awaiting_slot='name'
 assert 'last name' in say(client,sid,'First name T E S T.')['reply']
 r=say(client,sid,'Last name G U E S T.')
 assert 'Test Guest' in r['reply'] and 'name correct' in r['reply'] and 'name' not in b.slots
 b.tools.book_table=Mock(return_value={'booked':True,'date':'2027-02-17','time':'19:00'})
 r=say(client,sid,'Yes please');assert b.slots['name']=='Test Guest' and b.awaiting_confirmation
 b.tools.book_table.assert_not_called()
 assert 'correct' in r['reply'].lower()
 say(client,sid,'Yes please');b.tools.book_table.assert_called_once();b.chat.assert_not_called()
 assert b._name_parts=={} and b._verified_name is None and b._name_candidate is None

def test_repeat_never_becomes_name_and_correction_is_verified(client):
 sid,b=draft(client);b._awaiting_slot='name';b._last_agent_reply='Please spell your name.'
 assert say(client,sid,'Say it.')['reply']=='Please spell your name.'
 assert 'name' not in b.slots
 say(client,sid,'My name is Sam Miller.')
 assert b._name_candidate=='Sam Miller' and 'name' not in b.slots
 say(client,sid,'No')
 r=say(client,sid,'First name T as in Tiger E as in Echo S as in Sierra T as in Tiger. Last name G U E S T.')
 assert 'Test Guest' in r['reply'];say(client,sid,'Yes');assert b.slots['name']=='Test Guest'

def test_misheard_phonetic_words_are_not_used_as_a_person_name(client):
 sid,b=draft(client);b._awaiting_slot='name'
 r=say(client,sid,'First name Tango Echo C era Tango.')
 assert 'say your name again' in r['reply'] and not getattr(b,'_name_candidate',None) and 'name' not in b.slots
 say(client,sid,'Last name Golf Uniform Echo Sierra Tango.')
 r=say(client,sid,'First name T as in Tiger E as in Echo S as in Sun T as in Tiger.')
 assert b._name_candidate=='Test Guest' and 'name' not in b.slots

def test_spelling_failure_counter_resets_and_handoffs_after_four_failures(client):
 sid,b=draft(client);b._awaiting_slot='name'
 bad='First name Tango Echo C era Tango.'
 for _ in range(3):
  r=say(client,sid,bad);assert not r['handoff'] and 'repeat' in r['reply'].lower()
 assert b.failures==3
 say(client,sid,'First name T E S T.');assert b.failures==0
 for i in range(4):
  r=say(client,sid,'Last name Golf Uniform unknown Sierra Tango.')
  assert r['handoff']==(i==3)
 assert 'name' not in b.slots

@pytest.mark.parametrize('resume',["Let's complete the reservation.",'Continue.',"Yeah, I would like to do that reservation. Table for five."])
def test_pause_and_resume_preserve_confirmation_without_write(client,resume):
 sid,b=draft(client);b.slots['name']='Private Guest';b.awaiting_confirmation=True
 original=copy.deepcopy(b.slots);b.tools.book_table=Mock(return_value={'booked':True,'date':'2027-02-17','time':'19:00'})
 assert 'Take your time' in say(client,sid,'Yes! Wait, two minutes.')['reply']
 r=say(client,sid,resume);assert 'Private Guest' in r['reply'] and 'correct' in r['reply'].lower()
 assert b.slots==original;b.tools.book_table.assert_not_called();b.chat.assert_not_called()
 say(client,sid,'Yes please');b.tools.book_table.assert_called_once()

def test_confused_assent_cannot_select_alternative(client):
 sid,b=draft(client);b.slots['name']='Private Guest';b.slots.pop('time');b._awaiting_slot='time'
 b._offered_alternatives=[{'date':'2027-02-17','time':'18:00','party_size':5}]
 before=copy.deepcopy(b.slots)
 r=say(client,sid,"Yes. What? We're skipping how we")
 assert 'suggested time' in r['reply'] and b.slots==before and not b.awaiting_confirmation
 assert not r['structured']['tools_called'];b.chat.assert_not_called()

@pytest.mark.parametrize('yes',['Yeah.','Yep.','That works for me.','I confirm this reservation.','Yes please book it.'])
def test_clear_confirmation_aliases_commit_once(client,yes):
 sid,b=draft(client);b.slots['name']='Private Guest';b.awaiting_confirmation=True
 b.tools.book_table=Mock(return_value={'booked':True,'date':'2027-02-17','time':'19:00'})
 say(client,sid,yes);b.tools.book_table.assert_called_once();b.chat.assert_not_called()

def test_declarative_highchair_request_reaches_source(client):
 sid,b=draft(client);before=copy.deepcopy(b.slots)
 r=say(client,sid,"Uh sure I have a child. I'm looking for a high chair.")
 assert 'high chairs are available on request' in r['reply'] and r['sources']==['kids_pets.md']
 assert b.slots==before;b.chat.assert_not_called()
 assert r['voice_input']['expected_field']==getattr(b,'_awaiting_slot',None)

def test_policy_interruption_repeats_name_check_before_accepting_yes(client):
 sid,b=draft(client);b._awaiting_slot='name'
 say(client,sid,'My name is Sam Miller.');say(client,sid,'Can I bring my dog?')
 r=say(client,sid,'Yes please')
 assert 'Sam Miller' in r['reply'] and 'name correct' in r['reply'] and 'name' not in b.slots
 say(client,sid,'Yes please');assert b.slots['name']=='Sam Miller' and b.awaiting_confirmation

def test_stream_packets_are_valid_wav_and_speech_lock_does_not_block_asr(client,monkeypatch):
 import browser_voice as voice
 from types import SimpleNamespace
 def chunks(text):
  assert voice.audio_lock.acquire(blocking=False)
  voice.audio_lock.release()
  for _ in range(2):yield SimpleNamespace(audio_int16_bytes=b'\0\0'*160)
 monkeypatch.setattr(voice,'speaker',SimpleNamespace(chunks=chunks,voice=SimpleNamespace(config=SimpleNamespace(sample_rate=16000))))
 r=client.post('/api/voice/speak/stream',json={'text':'First sentence. Second sentence.'})
 assert r.status_code==200
 packets=[json.loads(s) for s in r.text.splitlines()];assert [p['index'] for p in packets]==[0,1]
 for packet in packets:
  with wave.open(io.BytesIO(base64.b64decode(packet['audio']))) as w:assert w.getnframes()==160 and w.getframerate()==16000
 assert not voice.speech_lock.locked()

def test_disconnected_stream_closes_synthesis_and_releases_lock(monkeypatch):
 import browser_voice as voice
 import asyncio
 from types import SimpleNamespace
 count=[]
 def chunks(text):
  for i in range(5):count.append(i);yield SimpleNamespace(audio_int16_bytes=b'\0\0'*160)
 monkeypatch.setattr(voice,'speaker',SimpleNamespace(chunks=chunks,voice=SimpleNamespace(config=SimpleNamespace(sample_rate=16000))))
 class Probe:
  calls=0
  async def is_disconnected(self):self.calls+=1;return self.calls>1
 async def consume():
  stream=voice.speak_stream(voice.SpeechRequest(text='One sentence.'),Probe())
  return [packet async for packet in stream.body_iterator]
 assert len(asyncio.run(consume()))==1 and count==[0] and not voice.speech_lock.locked()

def test_streaming_keeps_all_words_dates_and_policy_conditions(client,monkeypatch):
 import browser_voice as voice
 from voice.speech_text import natural_speech
 from types import SimpleNamespace
 spoken=[]
 def chunks(text):spoken.append(text);yield SimpleNamespace(audio_int16_bytes=b'\0\0'*160)
 monkeypatch.setattr(voice,'speaker',SimpleNamespace(chunks=chunks,voice=SimpleNamespace(config=SimpleNamespace(sample_rate=16000))))
 text='Table for 5 on 2027-02-17 at 18:00, under Private Guest, phone 2 0 2 5 5 5 0 1 7 3. Please say yes to confirm; no reservation has been made yet.'
 r=client.post('/api/voice/speak/stream',json={'text':text})
 assert r.status_code==200 and len(spoken)>1
 assert ' '.join(' '.join(spoken).split())==' '.join(natural_speech(text).split())
