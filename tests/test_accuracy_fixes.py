"""Customer-request accuracy, scope and preserved booking state; private fixtures only."""
import copy
from unittest.mock import Mock
import pytest
import chat_api as api
from tests.test_customer_feedback import client,say

@pytest.mark.parametrize('channel',['chat','voice'])
@pytest.mark.parametrize('question,expected',[
 ('How much is Masala Dosa?',['$12']),
 ('What would Filter Coffee set me back?',['$4']),
 ('Tell me the price of Bisi Bele Bath.',['$13']),
 ('Does Masala Dosa contain dairy?',['contains dairy']),
 ('Is there milk in Masala Dosa?',['contains dairy']),
 ('What allergens are in Bisi Bele Bath?',['dairy','peanuts']),
 ('Could you list every allergen for Bisi Bele Bath?',['dairy','peanuts']),
 ('When does dinner service start?',['18:00']),
 ('Until when is lunch served?',['15:00']),
 ('From when can we eat dinner?',['18:00']),
 ('Is a well-behaved terrier allowed in the dining room?',['not permitted']),
 ('Would my little dachshund be welcome inside?',['not permitted']),
])
def test_facts_during_phone_collection(client,channel,question,expected):
 sid=client.post('/api/sessions',json={'channel':channel}).json()['session_id'];b=api.sessions[sid].bot
 b.reserving=True;b._awaiting_slot='phone';b.slots={'date':'2027-02-17','time':'19:00','party_size':4}
 before=copy.deepcopy(b.slots);b.tools.book_table=Mock(side_effect=AssertionError('Information cannot book'))
 r=say(client,sid,question)
 assert all(w.lower() in r['reply'].lower() for w in expected)
 assert b.slots==before and b._awaiting_slot=='phone'
 assert not set(r['structured']['tools_called'])&{'recognize_customer','book_table','lookup_reservations_by_phone'}
 b.chat.assert_not_called()

def test_unknown_allergen_is_not_a_safety_promise(client):
 sid=client.post('/api/sessions',json={'channel':'chat'}).json()['session_id'];b=api.sessions[sid].bot
 b.restaurant=copy.deepcopy(b.restaurant);b.tools.r=b.restaurant
 next(d for d in b.restaurant['menu'] if d['name']=='Masala Dosa').pop('allergens')
 r=say(client,sid,'Does Masala Dosa contain dairy?')
 assert 'not recorded' in r['reply'].lower() or 'cannot confirm' in r['reply'].lower()
 assert 'does not contain' not in r['reply'].lower()

def test_menu_config_change_is_immediate_and_business_local(client):
 ids=[client.post('/api/sessions',json={'channel':'chat'}).json()['session_id'] for _ in range(2)]
 b=api.sessions[ids[0]].bot;b.restaurant=copy.deepcopy(b.restaurant);b.tools.r=b.restaurant
 assert '$12' in say(client,ids[0],'How much is Masala Dosa?')['reply']
 next(d for d in b.restaurant['menu'] if d['name']=='Masala Dosa')['price_usd']=99
 assert '$99' in say(client,ids[0],'How much is Masala Dosa?')['reply']
 assert '$12' in say(client,ids[1],'How much is Masala Dosa?')['reply']

@pytest.mark.parametrize('text',[
 'What tables do you have for dinner tomorrow?',
 'Book dinner for four tomorrow at seven.',
 'Can we come at lunch next week?',
])
def test_service_time_detector_does_not_swallow_booking_or_availability(text):
 from information_requests import readonly_info_intent
 assert readonly_info_intent(text) is None

def test_explicit_evidence_recovers_missing_fields_without_copying_context():
 from booking_evidence import explicit_fields
 assert explicit_fields('Book for four on February 17, 2027 at half past eight in the evening.')=={'party_size':4,'date':'February 17, 2027','time':'half past eight in the evening'}
 assert explicit_fields('My phone number is 2025550173.')=={}
 assert explicit_fields('Change the time to nine in the evening instead.')=={'time':'nine in the evening'}
 assert explicit_fields('Book for November.')=={}
 assert explicit_fields('Book for 2 November at seven pm.')=={'date':'2 November','time':'seven pm'}
 assert 'party_size' not in explicit_fields('Book for two or four people.')

def test_field_recovery_preserves_name_and_phone(client):
 from tests.test_decision_engine import response
 sid=client.post('/api/sessions',json={'channel':'chat'}).json()['session_id'];b=api.sessions[sid].bot
 b.chat=Mock(return_value=response())
 r=say(client,sid,'Please book for four on February 17, 2027 at seven in the evening.')
 assert b.slots=={'date':'February 17, 2027','time':'seven in the evening','party_size':4}
 assert 'phone' in r['reply'].lower();b.chat.assert_called_once()
 say(client,sid,'My phone number is 2025550173.');say(client,sid,'My name is Sam Miller.')
 assert b.awaiting_confirmation and b.slots['party_size']==4 and b.slots['date']=='February 17, 2027'
 assert 'Sam Miller' in b._last_agent_reply

@pytest.mark.parametrize('query,source,condition',[
 ('Can I bring my own birthday cake?','Birthday cakes from outside are welcome if staff are informed in advance.','in advance'),
 ('How long will you hold my table if I am late?','Tables are held for 15 minutes after the reserved time unless staff agree otherwise.','unless staff agree otherwise'),
 ('How much cancellation notice do you need?','Please tell staff about a cancellation at least 2 hours before the reservation.','at least 2 hours'),
 ('Do you accept credit cards?','Cash, UPI, debit cards and credit cards are accepted.','credit cards'),
])
def test_source_faq_conditions_and_versioned_invalidation(query,source,condition):
 from policy_faq import lookup
 from policy_requests import select_quotes
 docs={'policy.md':source}
 r=lookup('private-a',query,docs)
 assert r and condition in ' '.join(c['text'] for c in select_quotes(query,[{'text':s,'source':c['source']} for c in r['chunks'] for s in c['sentences']]))
 changed=lookup('private-a',query,{'policy.md':source+' Additional source revision.'})
 assert changed['source_digest']!=r['source_digest']
 other=lookup('private-b',query,docs)
 assert all(c['business']=='private-b' for c in other['chunks'])
 r['chunks'][0]['sentences'].clear()
 assert lookup('private-a',query,docs)['chunks'][0]['sentences']

@pytest.mark.parametrize('query',['Can I bring my terrier onto your patio?','Do high chairs have seatbelts?','Do you accept Bitcoin?','What pet vaccination certificates do I need?'])
def test_cache_declines_uncovered_facets(query):
 from policy_faq import lookup
 assert lookup('private',query,{'policy.md':'Pets are not permitted indoors. High chairs are available. Cash and cards are accepted.'}) is None

def test_unfamiliar_policy_cannot_select_dress_by_generic_dining_room_words():
 from policy_requests import select_quotes
 assert select_quotes('Is a well-behaved unknown creature allowed in the dining room?',[{'source':'dress.md','text':'Casual clothing is welcome; shirts and footwear are required in the dining room.'}])==[]

@pytest.mark.parametrize('channel',['chat','voice'])
def test_spoken_time_correction_after_no_requires_fresh_confirmation(client,channel):
 from tests.test_decision_engine import response
 sid=client.post('/api/sessions',json={'channel':channel}).json()['session_id'];b=api.sessions[sid].bot
 b.reserving=b.awaiting_confirmation=True
 b.slots={'date':'2027-02-17','time':'19:00','party_size':4,'name':'Sam Miller','phone':'2025550173'}
 b._recognized_phone='+12025550173';b.chat=Mock(return_value=response(time='nine in the evening'))
 b.tools.book_table=Mock(return_value={'booked':True,'date':'2027-02-17','time':'21:00'})
 r=say(client,sid,'No, change the time to nine in the evening instead.')
 from receptionist import parse_time
 assert b.slots['date']=='2027-02-17' and b.slots['party_size']==4 and parse_time(b.slots['time'])=='21:00' and b.awaiting_confirmation
 assert 'correct' in r['reply'].lower();b.tools.book_table.assert_not_called()
 say(client,sid,'Yes please');b.tools.book_table.assert_called_once()

@pytest.mark.parametrize('name',['Noon','Tomorrow'])
def test_name_words_are_not_booking_date_or_time_evidence(client,name):
 sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id'];b=api.sessions[sid].bot
 b.reserving=True;b._awaiting_slot='name'
 b.slots={'date':'2027-02-17','time':'19:00','party_size':4,'phone':'2025550173'};b._recognized_phone='+12025550173'
 r=say(client,sid,'My name is '+name+'.')
 assert b.slots['name']==name and b.slots['date']=='2027-02-17' and b.slots['time']=='19:00' and b.awaiting_confirmation
 assert name in r['reply']
