import pytest
from unittest.mock import Mock
from types import SimpleNamespace
from receptionist import parse_time,Receptionist,load_restaurant,HERE
from routing import assess_input

@pytest.mark.parametrize('text,expected',[
 ('midnight','00:00'),('noon','12:00'),('midday','12:00'),('quarter past eight','20:15'),
 ('a quarter to nine','20:45'),('quarter to twelve am','23:45'),('quarter past twelve am','00:15'),
 ('quarter to twelve pm','11:45'),('half past eight','20:30'),('ten minutes before nine','20:50'),
 ('twenty five past seven','19:25'),("eight o clock",'20:00'),("eight o’clock in the morning",'08:00')])
def test_human_time_grammar(text,expected):assert parse_time(text)==expected

@pytest.mark.parametrize('status',['unclear','unrelated','relevant'])
def test_structured_relevance_decision(status):
 chat=Mock(return_value=SimpleNamespace(message=SimpleNamespace(content=__import__('json').dumps({'status':status,'summary':'Test assessment'}))))
 assert assess_input(chat,'model','input','time')['status']==status
 assert chat.call_args.kwargs['format']['properties']['status']['enum']==['relevant','unclear','unrelated']

def test_midnight_is_understood_and_offers_alternatives_without_handoff():
 bot=Receptionist(load_restaurant(HERE/'restaurants/demo_restaurant.yaml'),chat=Mock())
 bot.reserving=True;bot.failures=3;bot._awaiting_slot='time'
 bot.slots={'phone':'9900112233','name':'Test','party_size':2,'date':'tomorrow'}
 bot._recognized_phone=__import__('customer_identity').normalize_phone('9900112233','IN')
 bot.tools.check_table=Mock(return_value={'available':False,'reason':'Outside opening hours.','alternatives':[{'date':'2026-11-01','time':'18:00'}]})
 reply,tools=bot.turn('Midnight.')
 assert bot.failures==0 and not bot.ended
 assert 'check_table' in tools and 'Outside opening hours' in reply
 bot.tools.check_table.assert_called_once_with(2,'tomorrow','00:00')

@pytest.mark.parametrize('status,expected_failures,reply_fragment',[('unrelated',0,"I can't help"),('relevant',0,'What time'),('unclear',3,"I didn\u0027t get")])
def test_assessment_controls_response_and_counter(monkeypatch,status,expected_failures,reply_fragment):
 import receptionist
 bot=Receptionist(load_restaurant(HERE/'restaurants/demo_restaurant.yaml'),chat=Mock())
 bot.failures=2;bot._awaiting_slot='time'
 monkeypatch.setattr(receptionist,'assess_input',lambda *args:{'status':status,'summary':'Brief assessment'})
 reply,tools=bot._unclear_or_unrelated('test input')
 assert reply_fragment in reply and bot.failures==expected_failures and not bot.ended
 assert bot.last_decision.fields['status']==status

@pytest.mark.parametrize('question',["How many customers have already reserved for today?","How many reservations do you have today?","Who is Albert Einstein?"])
def test_irrelevant_questions_preserve_booking_and_use_exact_reply(monkeypatch,question):
 import routing
 monkeypatch.setattr(routing,'assess_input',lambda *args:{'status':'unrelated','summary':'Outside restaurant assistance.'})
 bot=Receptionist(load_restaurant(HERE/'restaurants/demo_restaurant.yaml'),chat=Mock())
 bot.reserving=True;bot.slots={'name':'Alex'};bot.failures=2
 assert bot.turn(question)==("I can't help you with this question.",[])
 assert bot.slots=={'name':'Alex'} and bot.failures==0 and not bot.ended

@pytest.mark.parametrize('text',["Tell me all the openings you have in the month of Nov?","What availability do you have in November?","Any tables available next week?","Show me all slots for December"])
def test_broad_availability_clarifies_without_tools(text):
 bot=Receptionist(load_restaurant(HERE/'restaurants/demo_restaurant.yaml'),chat=Mock())
 bot.tools.check_table=Mock();bot.tools.book_table=Mock();bot.failures=3
 reply,tools=bot.turn(text)
 assert reply=='Which date or short date range would you prefer, and around what time?'
 assert tools==[] and bot.failures==0 and not bot.ended
 bot.tools.check_table.assert_not_called();bot.tools.book_table.assert_not_called();bot.chat.assert_not_called()

@pytest.mark.parametrize('text',['Can I book for November 15 at 8 pm?','What time do you open tomorrow?','Table for two next Saturday at half past eight'])
def test_specific_requests_do_not_trigger_broad_guard(text):
 from routing import broad_availability_question
 assert not broad_availability_question(text)


import pytest as _pytest
@_pytest.fixture(autouse=True)
def legacy_component_contract(monkeypatch):
    """These mocks target the legacy individual component schemas; unified contracts have their own tests."""
    monkeypatch.setenv('RECEPTIONIST_DECISION_MODE','legacy')
