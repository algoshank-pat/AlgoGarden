import json
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
import receptionist as app
import observability as obs
from local_audit import capture


@pytest.mark.parametrize('text',['What are your hours?','Where can I park?','What is your address?'])
def test_common_information_skips_llm(text):
    chat=Mock(side_effect=AssertionError('No LLM on template route'))
    bot=app.Receptionist(app.load_restaurant(app.HERE/'restaurants/demo_restaurant.yaml'),chat=chat)
    reply,tools=bot.turn(text)
    assert reply and tools
    chat.assert_not_called()


def test_dessert_category_is_checked_without_llm_guessing():
    bot=app.Receptionist(app.load_restaurant(app.HERE/'restaurants/demo_restaurant.yaml'),chat=Mock(side_effect=AssertionError('No LLM')))
    bot.menu_agent.chat=bot.chat
    reply,tools=bot.turn('What desserts do you have?')
    assert tools==['get_menu'] and 'Mysore Pak' in reply and 'Gobi Manchurian' not in reply
    bot.chat.assert_not_called()


def test_ambiguous_reservation_classifies_and_extracts_in_one_call(tmp_path,monkeypatch):
    monkeypatch.setattr(app.booking_db,'DB_PATH',tmp_path/'db.sqlite3')
    chat=Mock(return_value=SimpleNamespace(message=SimpleNamespace(content=json.dumps({
        'intent':'reservation','confidence':.9,'fields':{'party_size':2,'date':'tomorrow','time':'half past eight'}}))))
    bot=app.Receptionist(app.load_restaurant(app.HERE/'restaurants/demo_restaurant.yaml'),chat=chat)
    reply,_=bot.turn('We would like to come in tomorrow evening.')
    assert 'phone number' in reply
    assert bot.slots['party_size']==2 and bot.slots['date']=='tomorrow'
    chat.assert_called_once()
    assert 'reasoning' not in chat.call_args.kwargs['format']['properties']


def test_ambiguous_menu_does_not_generate_twice():
    chat=Mock(return_value=SimpleNamespace(message=SimpleNamespace(content=json.dumps({
        'intent':'menu','confidence':.9,'fields':{}}))))
    bot=app.Receptionist(app.load_restaurant(app.HERE/'restaurants/demo_restaurant.yaml'),chat=chat)
    reply,tools=bot.turn('What can I eat?')
    assert tools==['get_menu'] and 'Rs' in reply
    chat.assert_called_once()


def test_common_speech_is_cached():
    from voice.adapters import PiperSpeaker
    speaker=object.__new__(PiperSpeaker);speaker._cache=OrderedDict()
    speaker.voice=SimpleNamespace(synthesize=Mock(return_value=iter([b'pcm'])))
    text='Do you have a reservation with us?'
    assert list(speaker.chunks(text))==list(speaker.chunks(text))==[b'pcm']
    speaker.voice.synthesize.assert_called_once_with(text)


def test_generation_limits_are_logged_as_actual_inputs():
    events=[]
    chat=Mock(return_value=SimpleNamespace(message=SimpleNamespace(content='{"answer":"ok"}',tool_calls=[])))
    with capture(events):
        obs.traced_chat(chat,model='test',format={'type':'object','properties':{'answer':{'type':'string'}},'required':['answer']},messages=[])
    options=chat.call_args.kwargs
    assert options['keep_alive']=='30m' and options['options']['num_ctx']==2048 and options['options']['num_predict']==192
    prepared=next(e for e in events if e['name']=='ollama.request_prepared')
    assert prepared['details']['inputs']['kwargs']['options']==options['options']


def test_slow_trace_network_does_not_block_business(monkeypatch):
    import threading
    release=threading.Event();finished=threading.Event()
    root=Mock();root.id='id'
    def slow_post():release.wait(2);finished.set()
    root.post.side_effect=slow_post
    monkeypatch.setenv('LANGSMITH_TRACING','true');monkeypatch.setenv('LANGSMITH_API_KEY','test')
    monkeypatch.setattr(obs,'client',lambda:Mock());monkeypatch.setattr(obs,'RunTree',Mock(return_value=root))
    try:
        assert obs.observed('test')(lambda:'completed')()=='completed'
        assert not finished.is_set()
    finally:release.set();obs.drain_exports()
