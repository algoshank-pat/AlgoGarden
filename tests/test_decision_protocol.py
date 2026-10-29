from types import SimpleNamespace
from unittest.mock import Mock
from datetime import datetime,date
import json
import pytest
import decision_protocol as protocol
from decision_evidence import clock_phrases,selection,verify
from decision_fastpaths import fields
from structured_io import validate_json
from tests.test_decision_engine import response

@pytest.fixture
def state():
 return SimpleNamespace(reserving=True,_awaiting_slot='time',restaurant={'timezone':'Asia/Kolkata','seating':{'max_party_size':12}},reservation_changes=SimpleNamespace(active=False,action=None,selection_offered=False))

def value(**data):return {'relevance':'relevant','intent':'reservation','confidence':.9,'fields':dict.fromkeys(protocol.FIELDS)|data}

@pytest.mark.parametrize('text,expected',[('quarter past eight','20:15'),('quarter to nine','20:45'),('half past eight','20:30'),('five in the afternoon','17:00'),("eight o’clock in the morning",'08:00'),('noon','12:00'),('midnight','00:00'),('8:30 pm','20:30')])
def test_explicit_clock_words_win_over_model_rephrasing(text,expected):
 from receptionist import parse_time
 v=verify(value(time='wrong'),text,{})
 assert parse_time(v['fields']['time'])==expected

def test_ambiguous_time_choices_never_get_silently_selected():
 v=verify(value(time='8 pm'),'eight in the evening or nine in the evening',{})
 assert v['relevance']=='unclear' and all(x is None for x in v['fields'].values())

def test_name_noon_is_not_a_clock_correction():
 v=verify(value(name='Noon'),'Put it under Noon',{'expected_field':'name'})
 assert v['fields']['name']=='Noon' and v['fields']['time'] is None

def test_explicit_selection_requires_offered_list():
 assert selection('The second reservation, please',True)==2
 assert selection('Make it four people',True) is None
 assert selection('2',False) is None

@pytest.mark.parametrize('text', ['4 guests','four','There are four of us'])
def test_expected_party_avoids_inference(state,text):
 state._awaiting_slot='party_size';assert fields(state,text)=={'party_size':4}

@pytest.mark.parametrize('text',['Not four, just two','four guests tomorrow','four at eight pm'])
def test_party_fastpath_does_not_drop_corrections_or_other_fields(state,text):
 state._awaiting_slot='party_size';assert fields(state,text) is None

@pytest.mark.parametrize('text',['November 10, 2026','10 November 2026','tomorrow'])
def test_expected_date_avoids_inference(state,text):
 state._awaiting_slot='date';assert fields(state,text)=={'date':text.lower()}

@pytest.mark.parametrize('text',['Will it rain on Sunday?','tomorrow at eight pm','November 31, 2026'])
def test_date_fastpath_requires_a_valid_date_only(state,text):
 state._awaiting_slot='date';assert fields(state,text) is None

def test_every_wire_key_remains_required():
 payload={**dict.fromkeys(protocol.FIELDS),'relevance':'relevant','intent':'reservation','confidence':.9}
 validate_json(payload,protocol.SCHEMA)
 del payload['time']
 with pytest.raises(ValueError):validate_json(payload,protocol.SCHEMA)

def test_protocol_does_not_send_stored_values_as_extraction_evidence():
 req={'messages':[{'role':'system','content':'Trusted server clock: 2026-10-28T02:00:00+05:30; restaurant timezone: Asia/Kolkata;'},{'role':'user','content':json.dumps({'context':{'current_fields':{'phone':'9900112233','date':'2026-11-01','name':'Alex'},'creating':True},'caller':'Tomorrow at eight'})}],'options':{},'format':{}}
 compiled=protocol.compile_request(req)
 assert '9900112233' not in compiled['messages'][-1]['content'] and '2026-11-01' not in compiled['messages'][-1]['content']
 assert 'phone' in compiled['messages'][-1]['content'] and 'trusted_clock' in compiled['messages'][-1]['content']

def test_summary_is_code_owned_and_public_fields_are_complete():
 raw={**dict.fromkeys(protocol.FIELDS),'relevance':'relevant','intent':'reservation','confidence':.9,'time':'8 pm'}
 v=protocol.normalize(raw)
 assert set(v['fields'])==set(protocol.FIELDS) and v['reason_summary_source']=='code'


@pytest.mark.parametrize('text,name',[("Actually, put it under Morgan Lee.",'Morgan Lee'),("My name is Anne-Marie O'Neil","Anne-Marie O'Neil"),("put  it under  McDonald",'McDonald')])
def test_expected_name_keeps_literal_spelling(state,text,name):
 state._awaiting_slot='name';assert fields(state,text)=={'name':name}

@pytest.mark.parametrize('text',["Put it under Morgan and make it four people","My name is Morgan at eight pm"])
def test_name_fastpath_never_drops_extra_fields(state,text):
 state._awaiting_slot='name';assert fields(state,text) is None


def wire(relevance='relevant',intent='reservation',**fields):
 return SimpleNamespace(message=SimpleNamespace(content=json.dumps({**dict.fromkeys(protocol.FIELDS),'relevance':relevance,'intent':intent,'confidence':.9,**fields})))

@pytest.fixture
def compact_bot(monkeypatch,tmp_path):
 import receptionist as app
 monkeypatch.setenv('RECEPTIONIST_DECISION_MODE','unified')
 monkeypatch.setenv('RECEPTIONIST_DECISION_PROTOCOL','compact')
 monkeypatch.setenv('RECEPTIONIST_DECISION_FASTPATHS','safe')
 monkeypatch.setattr(app.booking_db,'DB_PATH',tmp_path/'fixture.sqlite3')
 monkeypatch.setattr(app,'BOOKINGS_FILE',tmp_path/'bookings.jsonl')
 return app.Receptionist(app.load_restaurant(app.HERE/'restaurants/demo_restaurant.yaml'),chat=Mock())

def test_private_public_turn_uses_one_flat_generation(compact_bot):
 b=compact_bot;b.chat.return_value=wire(party_size=4,date='tomorrow',time='eight thirty')
 reply,tools=b.turn('Please book a table for four tomorrow at quarter past eight.')
 assert 'phone' in reply.lower() and b.slots['time']=='quarter past eight' and b.slots['party_size']==4
 b.chat.assert_called_once()
 assert b.chat.call_args.kwargs['format']==protocol.SCHEMA

def test_private_correction_keeps_original_until_confirmed(compact_bot):
 b=compact_bot;b.reservation_changes.begin('update','+919900112233')
 original={'id':'fixture','date':'2026-11-01','time':'20:00','party_size':2,'status':'active','version':1}
 b.reservation_changes.current=dict(original)
 b.tools.prepare_reservation_change=Mock(return_value={'valid':True,'changes':{'date':'2026-11-01','time':'20:15','party_size':4}})
 b.tools.change_reservation=Mock()
 b.chat.return_value=wire(party_size=4,time='eight thirty')
 reply,_=b.turn('Make it four guests at quarter past eight instead')
 assert 'correct' in reply and b.tools.prepare_reservation_change.call_args.args[1]['time']=='quarter past eight'
 b.turn('No')
 assert b.reservation_changes.current==original and not b.reservation_changes.pending
 b.tools.change_reservation.assert_not_called();b.chat.assert_called_once()

def test_private_policy_remains_grounded_with_one_generation(compact_bot):
 b=compact_bot;b.chat.return_value=wire(intent='policy')
 b.menu_agent.chat=Mock(side_effect=AssertionError('No second model call'))
 b.menu_agent.tools.search_knowledge=Mock(return_value={'relevant':True,'chunks':[{'source':'policy.md','text':'Gift vouchers require advance arrangements.'}]})
 reply,tools=b.turn('Do you offer gift vouchers?')
 assert reply=='Gift vouchers require advance arrangements.' and tools==['search_knowledge']
 assert b.menu_agent.last_sources==['policy.md'];b.chat.assert_called_once();b.menu_agent.chat.assert_not_called()

def test_invalid_wire_is_not_retried_or_written(compact_bot):
 b=compact_bot;b.reserving=True;b.slots={'name':'Alex'}
 raw={**dict.fromkeys(protocol.FIELDS),'relevance':'relevant','intent':'reservation','confidence':.9};del raw['time']
 b.chat.return_value=SimpleNamespace(message=SimpleNamespace(content=json.dumps(raw)))
 reply,tools=b.turn('Could you help us make a reservation?')
 assert reply=="I didn't get that. Can you please repeat?" and not tools and b.slots=={'name':'Alex'}
 b.chat.assert_called_once()

def test_compact_profile_session_isolation_and_budget(compact_bot):
 from decision_engine import turn_context,decide
 from observability import traced_chat
 from concurrent.futures import ThreadPoolExecutor
 from threading import Barrier
 import receptionist as app
 barrier=Barrier(2)
 def run(name):
  b=app.Receptionist(compact_bot.restaurant,chat=Mock())
  def infer(**kwargs):barrier.wait(timeout=5);return wire(name=name)
  b.chat.side_effect=infer
  with turn_context(b,name):
   one=decide(b,name);two=decide(b,name)
   with pytest.raises(RuntimeError,match='budget exceeded'):traced_chat(b.chat,model=b.model,format=protocol.SCHEMA,messages=[])
  assert b.chat.call_count==1
  return one['fields']['name'],two['fields']['name']
 with ThreadPoolExecutor(max_workers=2) as pool:
  assert list(pool.map(run,['Alex','Sam']))==[('Alex','Alex'),('Sam','Sam')]


def test_explicit_name_is_not_overwritten_by_raw_prefix(compact_bot):
 b=compact_bot;b.reserving=True;b._awaiting_slot='name'
 b.chat=Mock(side_effect=AssertionError('Name-only correction is a safe rule'))
 reply,tools=b.turn('Put it under McDonald')
 assert b.slots['name']=='McDonald' and 'phone' in reply.lower()
 b.chat.assert_not_called()


def test_selection_with_correction_needs_joint_extraction(state):
 state.reservation_changes.active=True
 state.reservation_changes.selection_offered=True
 assert fields(state,'The second booking please')=={'reservation_choice':2}
 assert fields(state,'The second booking, and change it to four people tomorrow') is None

def test_native_selection_fallback_cannot_drop_mixed_fields(compact_bot):
 from decision_engine import rule_fields
 b=compact_bot;b.reservation_changes.begin('update','9900112233')
 b.reservation_changes.selection_offered=True
 assert rule_fields(b,'The second one at nine pm') is None
 assert rule_fields(b,'The second one please')=={'reservation_choice':2}
 assert rule_fields(b,'The first one, please, do')=={'reservation_choice':1}

def test_expected_date_correction_politeness_is_not_a_missing_field(state):
 state._awaiting_slot='date'
 assert fields(state,'Wednesday instead, please.')=={'date':'wednesday'}
 assert fields(state,'Wednesday instead, please, at nine') is None
 state._awaiting_slot='time'
 assert fields(state,'Wednesday instead, please.')=={'date':'wednesday'}
 state._awaiting_slot='name'
 assert fields(state,'Wednesday') is None

def test_summary_matches_validated_fields():
 raw={**dict.fromkeys(protocol.FIELDS),'relevance':'unclear','intent':'reservation','confidence':.5,'time':'quarter to nine'}
 req={'messages':[{'content':json.dumps({'caller':'quarter to nine','context':{}})}]}
 result=protocol.normalize(raw,req)
 assert 'no explicit fields' in result['reason_summary'] and result['fields']['time'] is None

@pytest.mark.parametrize('text,field,expected',[
 ('Wednesday instead, please.','date','wednesday'),
 ('Could we push it to a quarter to nine?','time','20:45')])
def test_contextual_single_field_corrections_use_no_generation(compact_bot,text,field,expected):
 b=compact_bot;b.reserving=True;b._awaiting_slot='time'
 b.slots={'party_size':2,'date':'2026-11-01','name':'Alex','phone':'9900112233'}
 b.chat=Mock(side_effect=AssertionError('Unambiguous correction must not generate'))
 b.turn(text)
 assert b.slots[field]==expected and b.slots['name']=='Alex' and b.slots['phone']=='9900112233'
 b.chat.assert_not_called()

def test_owner_totals_are_blocked_before_model_in_active_workflow(compact_bot):
 b=compact_bot;b.reserving=True;b.slots={'party_size':2,'date':'2026-11-01'}
 b.chat=Mock(side_effect=AssertionError('Owner totals must not reach inference'))
 reply,tools=b.turn('How many customers have already reserved for today?')
 assert reply=="I can't help you with this question." and not tools
 assert b.slots=={'party_size':2,'date':'2026-11-01'}
 b.chat.assert_not_called()

def test_negative_clock_is_not_added_as_a_proposed_time(compact_bot):
 b=compact_bot;b.chat.return_value=wire(date='tomorrow')
 b.turn('Please book a table tomorrow, but not at eight pm.')
 assert b.slots['date']=='tomorrow' and not b.slots.get('time')
 b.chat.assert_called_once()

def test_negated_guest_count_does_not_negate_later_clock():
 v=verify(value(time='wrong'),'Not four guests, just two tomorrow at eight in the evening',{})
 assert v['fields']['time']=='eight in the evening'

@pytest.mark.parametrize('phrase',['next Saturday','tomorrow','November 22, 2026','22 November 2026','2026-11-22','November 31, 2026'])
def test_calendar_words_are_not_replaced_by_invented_model_dates(phrase):
 v=verify(value(date='2026-11-02'),f'Please book for two on {phrase} at eight pm',{})
 assert v['fields']['date'].removeprefix('on ')==phrase

def test_date_validation_does_not_invent_omitted_or_past_dates():
 assert verify(value(date=None),'Please call me tomorrow',{})['fields']['date'] is None
 assert verify(value(date='2026-10-24'),'Change last Saturday',{})['fields']['date']=='2026-10-24'
 assert verify(value(date='November 3'),'Move November 1 to November 3',{})['fields']['date']=='November 3'
 assert verify(value(date='Wednesday'),'Not on Wednesday',{})['fields']['date'] is None

def test_two_date_options_require_clarification():
 v=verify(value(date='Friday'),'Could we do Friday or Saturday at eight pm?',{})
 assert v['relevance']=='unclear' and all(x is None for x in v['fields'].values())

def test_voice_no_merged_request_preserves_explicit_day(compact_bot):
 b=compact_bot;b.chat.return_value=wire(party_size=2,date='2026-11-02',time='08:30')
 reply,_=b.turn('Can I book a table for two next Saturday at half past eight?\nNo, I do not.')
 assert 'phone' in reply.lower() and b.slots['date']=='next Saturday' and b.slots['time']=='half past eight'
 b.chat.assert_called_once()

@pytest.mark.parametrize('text',['Please cancel my reservation','Could you check my booking please','I would like to reschedule my reservation'])
def test_complete_initial_lookup_commands_need_no_model(compact_bot,text):
 b=compact_bot;b.chat=Mock(side_effect=AssertionError('Complete initial action needs no inference'))
 reply,tools=b.turn(text)
 assert 'phone number' in reply.lower() and not b.ended
 b.chat.assert_not_called()
