from unittest.mock import Mock
from types import SimpleNamespace
import json
import pytest
import receptionist as engine


def bot():
    b=engine.Receptionist(engine.load_restaurant(engine.HERE/'restaurants/demo_restaurant.yaml'),chat=Mock())
    b.reserving=b.awaiting_confirmation=True
    b.slots={'name':'A B','phone':'9900112233','party_size':4,'date':'2026-11-01','time':'8 pm'}
    return b


@pytest.mark.parametrize('phrase',['No, I would like to change the time.','I want to change my time','Please change the time'])
def test_rejected_time_removed_and_new_time_requested(phrase):
    b=bot();b.chat=Mock(side_effect=AssertionError('No inference for asking for a new time'))
    reply,called=b.turn(phrase)
    assert reply=='What time would you like instead?' and not called
    assert 'time' not in b.slots and b.slots['date']=='2026-11-01'
    assert not b.awaiting_confirmation
    b.tools.check_table=Mock(return_value={'available':True,'date':'2026-11-01','time':'20:30'})
    b._recognized_phone=engine.normalize_phone(b.slots['phone'])
    b.chat=Mock(return_value=SimpleNamespace(message=SimpleNamespace(content=json.dumps({'time':'half past eight'}))))
    reply,called=b.turn('Half past eight instead')
    assert '20:30' in reply and 'Is that correct?' in reply
    assert 'book_table' not in called


def test_unrecognized_non_does_not_repeat_readback_or_book():
    b=bot();b.chat.return_value=SimpleNamespace(message=SimpleNamespace(content='{}'))
    reply,called=b.turn('Non')
    assert reply=="I didn't get that. Can you please repeat?" and not called
    assert b.awaiting_confirmation and b.slots['time']=='8 pm'


@pytest.mark.parametrize('phrase',['Hi. I would like to','I would like to','We want to','Hello, I need to',"I'd like to",'Could you','I was wondering if you','Can I book a table for','I want to make a','I would like a table at'])
def test_partial_request_never_guesses_handoff(phrase):
    b=bot();original=dict(b.slots)
    b.chat=Mock(side_effect=AssertionError('Do not classify incomplete utterances with the LLM'))
    for _ in range(3):
        reply,called=b.turn(phrase)
        assert reply=="I didn't get that. Can you please repeat?" and not called
        assert not b.ended and b.slots==original
    b.chat.assert_not_called()


@pytest.mark.parametrize('phrase',['Yeah.','Whatever you suggested.','The time you suggested.'])
def test_single_alternative_natural_acceptance_requires_readback(phrase):
    b=bot();b.awaiting_confirmation=False;b.slots.pop('time')
    b._offered_alternatives=[{'date':'2026-11-03','time':'18:30','party_size':4}]
    b._recognized_phone=engine.normalize_phone(b.slots['phone'])
    b.tools.check_table=Mock(return_value={'available':True,'date':'2026-11-03','time':'18:30'})
    b.chat=Mock(side_effect=AssertionError('Single-option acceptance needs no inference'))
    reply,called=b.turn(phrase)
    assert '2026-11-03' in reply and '18:30' in reply and 'Is that correct?' in reply
    assert 'book_table' not in called


@pytest.mark.parametrize('phrase',['Can I book a table?','Two people','Tomorrow','No','My name is An','Can I book a table for 4?','Can I book a table at 20:30?',"I want to speak to the manager"])
def test_complete_requests_and_short_slot_answers_are_not_fragments(phrase):
    from routing import incomplete_request
    assert not incomplete_request(phrase)


import pytest as _pytest
@_pytest.fixture(autouse=True)
def legacy_component_contract(monkeypatch):
    """These mocks target the legacy individual component schemas; unified contracts have their own tests."""
    monkeypatch.setenv('RECEPTIONIST_DECISION_MODE','legacy')
