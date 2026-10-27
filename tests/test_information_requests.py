import copy
from unittest.mock import Mock
import pytest
import receptionist as app
from routing import broad_availability_question
from information_requests import late_policy_question

@pytest.fixture
def bot(tmp_path,monkeypatch):
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
