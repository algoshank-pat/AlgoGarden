import copy
from unittest.mock import Mock
import pytest
import receptionist as app
from routing import broad_availability_question
from information_requests import late_policy_question

@pytest.fixture
def bot(tmp_path,monkeypatch):
 monkeypatch.setenv('LANGSMITH_TRACING','false')
 monkeypatch.setenv('RECEPTIONIST_DECISION_MODE','unified');monkeypatch.setenv('RECEPTIONIST_DECISION_PROTOCOL','baseline')
 monkeypatch.setattr(app.booking_db,'DB_PATH',tmp_path/'private.sqlite3');monkeypatch.setattr(app,'BOOKINGS_FILE',tmp_path/'bookings.jsonl')
 b=app.Receptionist(app.load_restaurant(app.HERE/'restaurants/demo_restaurant.yaml'),chat=Mock(side_effect=AssertionError('No LLM for these information controls')))
 b.tools.check_table=Mock(return_value={'available':True});b.tools.book_table=Mock(side_effect=AssertionError('Clarification cannot write'))
 return b

@pytest.mark.parametrize('text',['Do you have anything free in November?','Can we come sometime next week?','Any vacancies over the coming weekend?','Could you squeeze the three of us in one evening next week?',"We were hoping to drop in during December.",'Is there space for a couple of us later this month?','Might we dine with you at some point in November?',"I wonder if you've got any room next week?"])
def test_request_is_narrowed_before_phone_or_lookup(bot,text):
 reply,tools=bot.turn(text)
 assert 'date' in reply.lower() and 'phone' not in reply.lower() and not tools
 bot.tools.check_table.assert_not_called();bot.tools.book_table.assert_not_called();bot.chat.assert_not_called()

@pytest.mark.parametrize('question',['Do you have anything free in November?','Can we come sometime next week?'])
def test_answers_original_availability_request_not_a_later_booking(bot,question):
 bot.turn(question)
 reply,tools=bot.turn('November 6, 2026 at eight in the evening')
 assert 'people' in reply and not tools;bot.tools.check_table.assert_not_called()
 reply,tools=bot.turn('2')
 assert 'available' in reply and 'phone' not in reply and tools==['check_table']
 bot.tools.check_table.assert_called_once_with(2,'2026-11-06','20:00')
 assert not bot.reserving and not bot.slots and not bot.awaiting_confirmation
 bot.tools.book_table.assert_not_called();bot.chat.assert_not_called()

@pytest.mark.parametrize('question',["If the train makes us twenty minutes late, will you still hold our table?",'How late can we show up before we lose the table?',"Is there a grace period if traffic holds us up?","Will you keep our seats if we're delayed?",'What happens to the booking if we arrive after the reservation time?'])
def test_policy_question_delivers_grounded_answer_and_preserves_pending(bot,question):
 text='Tables are held for 15 minutes after the reserved time unless staff agree otherwise.'
 bot.reserving=True;bot.awaiting_confirmation=True;bot.slots={'date':'2026-11-06','time':'20:00','party_size':2,'name':'Private','phone':'9900112233'}
 before=copy.deepcopy(bot.slots)
 bot.menu_agent.tools.search_knowledge=Mock(return_value={'relevant':True,'chunks':[{'source':'cancellations.md','text':text}]})
 reply,tools=bot.turn(question)
 assert reply==text and tools==['search_knowledge'] and bot.menu_agent.last_sources==['cancellations.md']
 assert bot.slots==before and bot.awaiting_confirmation
 reply,tools=bot.turn('Yes')
 assert 'correct' in reply and not tools and bot.slots==before
 bot.tools.book_table.assert_not_called();bot.chat.assert_not_called()

@pytest.mark.parametrize('text',['Is parking free in November?','Do you serve gluten free food next week?','What is your cancellation policy in November?','Book a table for two on November 6 at 8 pm','Change my reservation to a later time','How late are you open?','Could you hold a table for Friday?','How long may we sit at our table?'])
def test_negative_questions_are_not_hijacked(text):
 assert not broad_availability_question(text) and not late_policy_question(text)

def test_availability_interruption_preserves_pending_update(bot):
 bot.reservation_changes.begin('update','9900112233');change=bot.reservation_changes
 change.current={'id':'private','date':'2026-11-06','time':'19:00','party_size':2,'version':1};change.changes={'time':'20:00'};change.pending=True
 snapshot=copy.deepcopy({k:v for k,v in change.__dict__.items() if k!='bot'})
 bot.turn('Do you have anything free in November?');bot.turn('November 7, 2026 at eight pm');bot.turn('for 4 people')
 after=copy.deepcopy({k:v for k,v in change.__dict__.items() if k!='bot'})
 assert after==snapshot
 reply,tools=bot.turn('Yes');assert 'confirm' in reply.lower() and not tools
 assert change.pending and change.changes=={'time':'20:00'}

def test_nonrelevant_question_keeps_clarification_state(bot):
 from tests.test_decision_engine import response
 bot.turn('Anything free in November?');bot.chat=Mock(return_value=response('unrelated','other'))
 reply,tools=bot.turn('Who is Albert Einstein?')
 assert reply=="I can't help you with this question." and not tools
 assert bot._availability_enquiry=={} and not bot.slots

def test_ambiguous_or_rejected_dates_never_reuse_previous_enquiry_date(bot):
 bot.turn('Anything free in November?');bot.turn('November 6, 2026')
 bot.turn('Friday or Saturday instead?')
 bot.turn('eight pm')
 bot.tools.check_table.assert_not_called()
 assert 'date' not in bot._availability_enquiry

def test_explicit_active_correction_is_not_hijacked_by_availability(bot):
 from information_requests import information_turn
 bot.reserving=True
 assert information_turn(bot,'Make it four people, and could we come at half past eight instead?') is None

@pytest.mark.parametrize('correction',['Make it four people at eight pm instead','Four people at eight pm'])
def test_correction_after_policy_never_restores_a_stale_readback(bot,correction):
 from tests.test_decision_engine import response
 bot.reserving=bot.awaiting_confirmation=True
 bot.slots={'date':'2026-11-06','time':'19:00','party_size':2,'name':'Private','phone':'9900112233'}
 text='Tables are held for 15 minutes after the reserved time unless staff agree otherwise.'
 bot.menu_agent.tools.search_knowledge=Mock(return_value={'relevant':True,'chunks':[{'source':'cancellations.md','text':text}]})
 bot.turn('How late can I arrive for my reservation?')
 bot.chat=Mock(return_value=response(party_size=4,time='8 pm'))
 bot.tools.check_table.return_value={'available':True,'date':'2026-11-06','time':'20:00'}
 reply,_=bot.turn(correction)
 assert 'Table for 4' in reply and '20:00' in reply
 bot.tools.book_table.assert_not_called()
 bot.tools.book_table=Mock(return_value={'booked':True,'date':'2026-11-06','time':'20:00'})
 reply,tools=bot.turn('Yes');assert tools==['book_table'] and 'booked' in reply
 assert bot.tools.book_table.call_args.kwargs['party_size']==4

@pytest.mark.parametrize('control',['Yes','No','Correct',"That's correct",'No, thank you','Confirm'])
def test_information_consent_never_confirms_old_booking(bot,control):
 bot.reserving=bot.awaiting_confirmation=True
 bot.slots={'date':'2026-11-06','time':'19:00','party_size':2,'name':'Private','phone':'9900112233'}
 text='Tables are held for 15 minutes after the reserved time unless staff agree otherwise.'
 bot.menu_agent.tools.search_knowledge=Mock(return_value={'relevant':True,'chunks':[{'source':'cancellations.md','text':text}]})
 before=copy.deepcopy(bot.slots)
 bot.turn('How late can I arrive for my reservation?');reply,tools=bot.turn(control)
 assert 'correct' in reply and not tools and bot.slots==before and bot.awaiting_confirmation
 bot.tools.book_table.assert_not_called()

def test_availability_retry_counter_resets_without_losing_request(bot):
 bot.turn('Anything free in November?')
 for _ in range(3):assert bot.turn('uh um')[0]=="I didn't get that. Can you please repeat?"
 assert bot.failures==3
 bot.turn('November 6, 2026');assert bot.failures==0 and 'date' in bot._availability_enquiry
 for _ in range(3):bot.turn('uh um')
 reply,tools=bot.turn('uh um')
 assert tools==['transfer_to_human'] and bot.ended
 bot.tools.check_table.assert_not_called();bot.tools.book_table.assert_not_called()

def test_negated_guest_count_does_not_check_wrong_party(bot):
 bot.turn('Anything free in November?');bot.turn('November 6, 2026 at eight pm')
 reply,tools=bot.turn('Not four people, just two of us')
 assert tools==['check_table']
 bot.tools.check_table.assert_called_once_with(2,'2026-11-06','20:00')

@pytest.mark.parametrize('interruption',['policy','availability'])
def test_voice_preliminary_booking_survives_information_interruption(bot,tmp_path,monkeypatch,interruption):
 import chat_api as api
 from fastapi.testclient import TestClient
 from uuid import uuid4
 from tests.test_decision_engine import response
 monkeypatch.setattr(api,'new_bot',lambda:bot)
 monkeypatch.setattr(api.audit,'CALLS_FILE',tmp_path/'calls.jsonl')
 text='Tables are held for 15 minutes after the reserved time unless staff agree otherwise.'
 bot.menu_agent.tools.search_knowledge=Mock(return_value={'relevant':True,'chunks':[{'source':'cancellations.md','text':text}]})
 with TestClient(api.app) as client:
  sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id']
  def say(t):return client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':t}).json()
  original='Please book a table for two on November 6, 2026 at eight pm.'
  assert say(original)['reply']=='Do you have a reservation with us?'
  if interruption=='policy':v=say('How late can I arrive for my reservation?')
  else:
   say('Anything free in November?');say('November 7, 2026 at eight pm');v=say('for two people')
  session=api.sessions[next(k for k in api.sessions if str(k)==sid)]
  assert session.pending_reservation_request==original and not bot.slots and not bot.reserving
  assert 'Returning to your original request' in v['reply'] and not session.awaiting_more_help
  bot.chat.assert_not_called()
  bot.chat=Mock(return_value=response(party_size=2,date='November 6, 2026',time='eight pm'))
  assert 'phone' in say('No, I do not.')['reply']
  assert bot.slots['date']=='November 6, 2026'
  bot.tools.book_table.assert_not_called()
  client.delete('/api/sessions/'+sid)
