"""Public turn contracts for the consolidated engine (not legacy classifier mocks)."""
import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
import receptionist as app
from decision_engine import SCHEMA,FIELD_TYPES,turn_context,decide


def response(relevance='relevant',intent='reservation',**fields):
    return SimpleNamespace(message=SimpleNamespace(content=json.dumps({
        'relevance':relevance,'intent':intent,'confidence':0.95,'reason_summary':'Explicit caller request.',
        'fields':{**dict.fromkeys(FIELD_TYPES),**fields}})))

@pytest.fixture
def bot(monkeypatch,tmp_path):
    monkeypatch.setenv('RECEPTIONIST_DECISION_MODE','unified')
    monkeypatch.setenv('RECEPTIONIST_DECISION_PROTOCOL','baseline')
    monkeypatch.setenv('RECEPTIONIST_DECISION_FASTPATHS','baseline')
    monkeypatch.setattr(app.booking_db,'DB_PATH',tmp_path/'reservations.sqlite3')
    monkeypatch.setattr(app,'BOOKINGS_FILE',tmp_path/'bookings.jsonl')
    return app.Receptionist(app.load_restaurant(app.HERE/'restaurants/demo_restaurant.yaml'),chat=Mock())


def test_create_routes_and_extracts_once_without_second_judge(bot):
    bot.chat.return_value=response(date='tomorrow',time='8 pm',party_size=2)
    reply,tools=bot.turn('Please book a table for two tomorrow at eight in the evening.')
    assert 'phone' in reply.lower() and not bot.ended
    bot.chat.assert_called_once()
    assert bot.chat.call_args.kwargs['format']==SCHEMA
    assert bot.slots['time']=='8 pm' and bot.slots['party_size']==2

@pytest.mark.parametrize('active',[False,True])
@pytest.mark.parametrize('relevance,phrase',[('unrelated',"I can't help you with this question."),('unclear',"I didn't get that. Can you please repeat?")])
def test_scope_uses_one_call_and_preserves_pending_fields(bot,active,relevance,phrase):
    bot.reserving=active;bot.slots={'name':'Alex'};bot.failures=0
    bot.chat.return_value=response(relevance,'other')
    reply,tools=bot.turn('An unresolved freeform question')
    assert reply==phrase and not tools and bot.slots=={'name':'Alex'}
    bot.chat.assert_called_once()


def test_invalid_schema_does_not_retry_or_mutate(bot):
    bot.chat.return_value=SimpleNamespace(message=SimpleNamespace(content='{"intent":"reservation"}'))
    bot.reserving=True;bot.slots={'name':'Alex'}
    reply,tools=bot.turn('Could you help with something?')
    assert reply=="I didn't get that. Can you please repeat?" and not tools
    assert bot.slots=={'name':'Alex'}
    bot.chat.assert_called_once()


def test_decision_reused_within_turn_but_fresh_next_turn(bot):
    bot.chat.return_value=response(time='quarter to nine')
    with turn_context(bot,'first'):
        assert decide(bot,'first')==decide(bot,'same turn')
    assert bot.chat.call_count==1
    with turn_context(bot,'second'):decide(bot,'second')
    assert bot.chat.call_count==2


def test_engine_fields_feed_update_without_another_extraction(bot):
    bot.reservation_changes.begin('update','+919900112233')
    bot.reservation_changes.current={'id':'test','phone':'9900112233','status':'active','version':1,'date':'2026-11-01','time':'20:00','party_size':2}
    bot.chat.return_value=response(time='half past eight',party_size=4)
    bot.tools.prepare_reservation_change=Mock(return_value={'valid':True,'changes':{'date':'2026-11-01','time':'20:30','party_size':4},'reservation':{'date':'2026-11-01','time':'20:30','party_size':4}})
    bot.turn('Make it four people at half past eight please')
    bot.chat.assert_called_once()
    assert bot.tools.prepare_reservation_change.call_args.args[1]['party_size']==4


def test_context_is_isolated_between_concurrent_customers(bot):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    barrier=Barrier(2)
    def run(name):
        other=app.Receptionist(bot.restaurant,chat=Mock())
        def infer(**kwargs):
            barrier.wait(timeout=5);return response(name=name)
        other.chat.side_effect=infer
        with turn_context(other,name):
            first=decide(other,name);second=decide(other,name)
        assert other.chat.call_count==1
        return first['fields']['name'],second['fields']['name']
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(run,['Alex','Sam']))==[('Alex','Alex'),('Sam','Sam')]


def test_second_model_call_is_blocked(bot):
    from observability import traced_chat
    bot.chat.return_value=response()
    with turn_context(bot,'hello'):
        decide(bot,'hello')
        with pytest.raises(RuntimeError,match='budget exceeded'):
            traced_chat(bot.chat,model=bot.model,format=SCHEMA,messages=[])
    bot.chat.assert_called_once()


def test_multifield_correction_is_not_overwritten_by_time_fast_path(bot):
    bot.reserving=True;bot._awaiting_slot='time'
    bot.slots={'name':'Alex','phone':'9900112233','date':'tomorrow','party_size':2}
    bot.chat.return_value=response(party_size=4,date='tomorrow',time='quarter past eight')
    bot.tools.recognize_customer=Mock(return_value={'has_reservation':False,'returning':False})
    bot.tools.check_table=Mock(return_value={'available':False,'reason':'Unavailable','alternatives':[]})
    bot.turn('Make it four guests tomorrow at quarter past eight')
    bot.chat.assert_called_once()
    bot.tools.check_table.assert_called_once_with(4,'tomorrow','quarter past eight')


def test_menu_allergen_fact_needs_no_generation(bot):
    bot.chat.return_value=response(intent='menu',dish='Masala Dosa')
    reply,tools=bot.turn('Does Masala Dosa contain allergens?')
    assert tools==['check_dish'] and 'dairy' in reply
    bot.chat.assert_not_called()


def test_explicit_reservation_selection_needs_no_inference(bot):
    from decision_engine import rule_fields
    bot.reservation_changes.begin('update','+919900112233')
    bot.reservation_changes.selection_offered=True
    assert rule_fields(bot,'The first one, please, is')=={'reservation_choice':1}
    bot.chat.assert_not_called()

@pytest.mark.parametrize('channel',['chat','voice'])
def test_api_retries_reuse_unified_decision(bot,monkeypatch,tmp_path,channel):
    import chat_api
    from fastapi.testclient import TestClient
    from uuid import uuid4
    from concurrency import TurnScheduler
    monkeypatch.setattr(chat_api,'new_bot',lambda:bot)
    monkeypatch.setattr(chat_api,'scheduler',TurnScheduler())
    monkeypatch.setattr(chat_api.audit,'CALLS_FILE',tmp_path/'calls.jsonl')
    bot.chat.return_value=response('unrelated','other')
    with TestClient(chat_api.app) as client:
        sid=client.post('/api/sessions',json={'channel':channel}).json()['session_id']
        payload={'request_id':str(uuid4()),'text':'Who is Albert Einstein?'}
        first=client.post(f'/api/sessions/{sid}/turns',json=payload).json()
        second=client.post(f'/api/sessions/{sid}/turns',json=payload).json()
        assert first==second and first['reply']=="I can't help you with this question."
        assert first['routing']['source']=='unified_llm'
        bot.chat.assert_called_once()
        client.delete(f'/api/sessions/{sid}')


def test_policy_answer_uses_grounded_sources_without_second_generation(bot):
    bot.chat.return_value=response(intent='policy')
    bot.menu_agent.chat=Mock(side_effect=AssertionError('No answer-generation call after decision'))
    bot.menu_agent.tools.search_knowledge=Mock(return_value={'relevant':True,'chunks':[{'source':'vouchers.md','text':'Gift vouchers require advance arrangements.'}]})
    reply,tools=bot.turn('Do you offer gift vouchers?')
    assert reply=='Gift vouchers require advance arrangements.' and tools==['search_knowledge']
    assert bot.menu_agent.last_sources==['vouchers.md']
    bot.chat.assert_called_once()
    bot.menu_agent.chat.assert_not_called()


def test_time_phrase_cannot_overwrite_existing_date(bot):
    bot.chat.return_value=response(date='half past eight',time='half past eight',party_size=4)
    with turn_context(bot,'Make it four people at half past eight instead'):
        value=decide(bot,'Make it four people at half past eight instead')
    assert value['fields']['date'] is None and value['fields']['time']=='half past eight'
    bot.chat.assert_called_once()


def test_invalid_explicit_date_is_not_silently_dropped(bot):
    bot.chat.return_value=response(date='2026-99-99',time='8 pm')
    with turn_context(bot,'2026-99-99 at 8 pm'):
        value=decide(bot,'2026-99-99 at 8 pm')
    assert value['fields']['date']=='2026-99-99'  # business validation must reject this proposal

@pytest.mark.parametrize('raw_intent',['reservation','policy','menu','hours'])
def test_unrelated_scope_refusal_overrides_contradictory_intent(bot,raw_intent):
    import copy
    bot.reserving=True;bot._awaiting_slot='phone';bot.slots={'date':'2027-01-12','time':'19:00','party_size':2}
    original=copy.deepcopy(bot.slots)
    bot.chat.return_value=response(relevance='unrelated',intent=raw_intent)
    bot.tools.book_table=Mock(side_effect=AssertionError('Unrelated input cannot write'))
    reply,tools=bot.turn('Who is Albert Einstein?')
    assert reply=="I can't help you with this question." and tools==[]
    assert bot.last_decision.intent=='other' and bot.last_decision.reason=='unrelated'
    assert bot.slots==original and bot.reserving and not bot.ended
    bot.chat.assert_called_once();bot.tools.book_table.assert_not_called()
