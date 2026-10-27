from datetime import date
from unittest.mock import Mock
import pytest
import receptionist as app
from routing import Decision

@pytest.fixture
def bot(monkeypatch,tmp_path):
 monkeypatch.setenv('RECEPTIONIST_DECISION_MODE','unified')
 monkeypatch.setenv('RECEPTIONIST_DECISION_PROTOCOL','baseline')
 monkeypatch.setenv('RECEPTIONIST_DECISION_FASTPATHS','baseline')
 monkeypatch.setattr(app.booking_db,'DB_PATH',tmp_path/'db.sqlite3')
 monkeypatch.setattr(app,'BOOKINGS_FILE',tmp_path/'bookings.jsonl')
 b=app.Receptionist(app.load_restaurant(app.HERE/'restaurants/demo_restaurant.yaml'),chat=Mock(side_effect=AssertionError('Contextual suggestions need no model')))
 b.reserving=True;b.slots={'date':'2026-11-01','party_size':5,'name':'Alex','phone':'9900112233'}
 b._availability_context={'requested':{'date':'2026-11-01','time':'17:00','party_size':5},'reason':'Outside booking hours'}
 b.last_decision=Decision('reservation','rule')
 b._last_agent_reply='Outside booking hours. What other date or time would work for you?'
 b.tools.reservation_alternatives=Mock(return_value=[{'date':'2026-11-01','time':'20:30','party_size':5}])
 return b

@pytest.mark.parametrize('text',['Do you have any recommendations?','What would you suggest?','Any alternatives?','What options do I have?'])
def test_recommendations_resolve_to_booking_context(bot,text):
 before=dict(bot.slots)
 reply,tools=bot.turn(text)
 assert '8:30 PM' in reply and tools==['reservation_alternatives']
 assert bot.slots==before and not bot.awaiting_confirmation
 assert bot.last_decision.intent=='reservation' and bot._last_topic=='reservation'
 bot.chat.assert_not_called()
 bot.tools.reservation_alternatives.assert_called_once_with(date(2026,11,1),'17:00',5,current=None)

def test_explicit_food_request_changes_topic_and_next_followup_uses_menu(bot):
 from tests.test_decision_engine import response
 bot.chat=Mock(return_value=response(intent='menu'))
 bot.menu_agent.turn=Mock(return_value=('Menu answer', ['get_menu']))
 bot.turn('Do you have any food recommendations?')
 bot.turn('Any recommendations?')
 assert bot.menu_agent.turn.call_count==2 and bot.chat.call_count==2
 bot.tools.reservation_alternatives.assert_not_called()
 assert bot._last_topic=='menu'

def test_update_context_preserves_original_and_excludes_it_from_capacity(bot):
 bot.reserving=False;bot.reservation_changes.begin('update','+919900112233')
 original={'id':'original','date':'2026-11-01','time':'20:00','party_size':2,'status':'active','version':1}
 bot.reservation_changes.current=original.copy()
 bot.turn('Do you have any recommendations?')
 assert bot.reservation_changes.current==original and not bot.reservation_changes.pending
 assert bot.tools.reservation_alternatives.call_args.kwargs['current']==original
 assert bot.reservation_changes.offered_alternatives

def test_bounded_fallback_finds_open_times_when_one_hour_choices_are_full(monkeypatch,tmp_path):
 monkeypatch.setattr(app.booking_db,'DB_PATH',tmp_path/'db.sqlite3')
 t=app.Tools(app.load_restaurant(app.HERE/'restaurants/demo_restaurant.yaml'))
 t._slot_available=Mock(side_effect=lambda day,time,party:time=='20:30')
 options=t.reservation_alternatives(date(2026,11,1),'17:00',5)
 assert len(options)==3 and all(x['time']=='20:30' for x in options)
 assert {x['date'] for x in options}=={'2026-11-01','2026-11-02','2026-11-03'}
 assert t._slot_available.call_count<=60


@pytest.mark.parametrize('text',['Book a table for five on 1 November, 2026 at five in the afternoon','Book a table on May 15 at 8 pm','Book a table on 15 May at 8 pm','Book for December 1 at seven'])
def test_specific_spoken_dates_are_not_mistaken_for_month_wide_requests(text):
 from routing import broad_availability_question
 assert not broad_availability_question(text)
