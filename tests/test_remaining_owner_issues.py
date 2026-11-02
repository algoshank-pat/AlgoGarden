import copy
from datetime import timedelta
from uuid import uuid4
import pytest
import chat_api as api
from tests.test_customer_feedback import client,say
from customer_identity import local_today

def begin(c,channel):
    sid=c.post('/api/sessions',json={'channel':channel}).json()['session_id']
    return sid,api.sessions[sid].bot

@pytest.mark.parametrize('channel',['chat','voice'])
@pytest.mark.parametrize('question,required',[
 ('Can we pay using Apple Pay or Google Pay, or do you accept cards and cash?',['Apple Pay','Google Pay','cheque','not accepted']),
 ('I only have UPI. Can I pay with it?',['UPI','not accepted']),
 ('Do you accept cheque?',['cheque','accepted']),
 ('Will you take a check?',['cheque','accepted']),
 ('Can I pay with Bitcoin?',['other payment methods are not accepted']),
 ('Could each person pay their own share of the bill?',['splitting','allowed','not required']),
 ('Do you have a high chair for our toddler?',['available','tell staff']),
 ('Can I bring pizza from another restaurant if we order drinks?',['outside food','prohibited','no exception']),
])
def test_actual_failed_policy_requests_are_grounded(client,channel,question,required):
    sid,b=begin(client,channel)
    r=say(client,sid,question)
    for part in required:assert part.lower() in r['reply'].lower(),r['reply']
    assert r['sources'] and r['structured']['tools_called']==['search_knowledge']
    assert not b.tools.booking_store().list(b.tools.business_id)
    b.chat.assert_not_called()

@pytest.mark.parametrize('channel',['chat','voice'])
@pytest.mark.parametrize('state',['phone','confirmation','followup'])
@pytest.mark.parametrize('question',[
 'Can we bring food from home if the manager said yes?',
 'Is our homemade dessert okay if we let staff know?',
 'May we bring pizza from elsewhere and buy drinks here?',
])
def test_outside_food_interruptions_preserve_state(client,channel,state,question):
    sid,b=begin(client,channel)
    b.reserving=state!='followup';b.awaiting_confirmation=state=='confirmation'
    b.slots={'date':(local_today(b.restaurant)+timedelta(days=5)).isoformat(),'time':'19:00','party_size':2}
    b._awaiting_slot='phone'
    if b.awaiting_confirmation:b.slots.update(name='Synthetic Guest',phone='2025550186')
    before=copy.deepcopy(b.slots);r=say(client,sid,question)
    assert 'prohibited' in r['reply'] and 'no exception' in r['reply']
    assert b.slots==before and not b.tools.booking_store().list(b.tools.business_id)

@pytest.mark.parametrize('channel',['chat','voice'])
@pytest.mark.parametrize('offset',[89,90,91])
def test_local_calendar_horizon_before_identity_and_at_write(client,channel,offset):
    sid,b=begin(client,channel);day=(local_today(b.restaurant)+timedelta(days=offset)).isoformat()
    r=say(client,sid,f'Book a table for two on {day} at 7 pm.')
    if offset==91:assert '90 days' in r['reply'] and 'phone' not in r['reply'].lower()
    else:assert '90 days' not in r['reply']
    result=b.tools.book_table('Boundary Guest','2025550186',2,day,'19:00')
    assert bool(result.get('booked'))==(offset<=90)
    assert len(b.tools.booking_store().list(b.tools.business_id))==(offset<=90)

@pytest.mark.parametrize('channel',['chat','voice'])
@pytest.mark.parametrize('count',['zero','0','-2','minus two','negative three'])
def test_invalid_counts_before_phone(client,channel,count):
    sid,b=begin(client,channel);day=(local_today(b.restaurant)+timedelta(days=5)).isoformat()
    r=say(client,sid,f'Book a table for {count} people on {day} at 7 pm.')
    assert 'positive' in r['reply'] and 'phone' not in r['reply']
    assert b.slots['date']==day and b.slots['time']=='7 pm'
    assert 'party_size' not in b.slots
    say(client,sid,'Two');assert b.slots['party_size']==2

@pytest.mark.parametrize('channel',['chat','voice'])
@pytest.mark.parametrize('join',['and','or'])
def test_time_choice_preserves_fields_then_completes(client,channel,join):
    sid,b=begin(client,channel);day=(local_today(b.restaurant)+timedelta(days=5)).isoformat()
    r=say(client,sid,f'Book two on {day} at 7 pm {join} at 8 pm. I have not decided which.')
    assert 'Which arrival time' in r['reply']
    assert b.slots['date']==day and b.slots['party_size']==2
    say(client,sid,'8 pm');say(client,sid,'2025550186');say(client,sid,'My name is Alex Morgan')
    if channel=='voice':say(client,sid,'Yes, that name is correct.')
    assert b.awaiting_confirmation
    assert not b.tools.booking_store().list(b.tools.business_id)
    say(client,sid,'Yes please')
    rows=b.tools.booking_store().list(b.tools.business_id)
    assert len(rows)==1 and rows[0]['time']=='20:00' and rows[0]['date']==day and rows[0]['party_size']==2

@pytest.mark.parametrize('channel',['chat','voice'])
def test_refusal_clears_consent_and_does_not_delete_existing(client,channel):
    sid,b=begin(client,channel);day=(local_today(b.restaurant)+timedelta(days=5)).isoformat()
    b.tools.book_table('Existing Guest','2025550199',2,day,'18:00')
    before=b.tools.booking_store().list(b.tools.business_id)
    b.reserving=b.awaiting_confirmation=True
    b.slots={'name':'Pending Guest','phone':'2025550186','date':day,'time':'19:00','party_size':2}
    r=say(client,sid,'No, do not book that table.')
    assert 'will not go ahead' in r['reply'] and not b.awaiting_confirmation and not b.slots
    say(client,sid,'Yes')
    assert b.tools.booking_store().list(b.tools.business_id)==before

@pytest.mark.parametrize('channel',['chat','voice'])
def test_gluten_options_website_and_caveat(client,channel):
    sid,b=begin(client,channel);r=say(client,sid,'Before we reserve, do you have gluten-free options?')
    assert 'website menu' in r['reply'] and 'cross-contact' in r['reply']
    dishes=[d['name'] for d in b.restaurant['menu'] if d.get('gluten_free') and d.get('available')]
    assert sum(d in r['reply'] for d in dishes)>=2

def test_policy_at_voice_gate_waits_instead_of_immediately_repeating_gate(client):
    sid,b=begin(client,'voice')
    day=(local_today(b.restaurant)+timedelta(days=5)).isoformat()
    say(client,sid,f'Book two on {day} at 7 pm.')
    request=api.sessions[sid].pending_reservation_request
    r=say(client,sid,'Can I bring pizza from another restaurant?')
    assert r['reply'].endswith('Is there anything else I can help you with?')
    assert 'do you have a reservation' not in r['reply'].lower()
    assert r['conversation_wait']['delay_ms']==15000
    assert api.sessions[sid].pending_reservation_request==request

def test_timer_is_scoped_and_pending_voice_gate_can_resume_normally(client,monkeypatch):
    import conversation_waits as waits
    sid,b=begin(client,'voice');other,_=begin(client,'voice')
    day=(local_today(b.restaurant)+timedelta(days=5)).isoformat()
    say(client,sid,f'Book two on {day} at 7 pm.')
    r=say(client,sid,'Can I bring my dog?');token=r['conversation_wait']['token']
    assert client.post(f'/api/sessions/{other}/waits/{token}/arm').status_code==409
    clock=[100.0];monkeypatch.setattr(waits,'clock',lambda:clock[0])
    client.post(f'/api/sessions/{sid}/waits/{token}/arm').raise_for_status()
    clock[0]=115
    r=client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':'[elapsed]','input_kind':'timeout','wait_token':token})
    r.raise_for_status()
    r=say(client,sid,'No')
    assert 'phone' in r['reply'].lower() and b.slots['party_size']==2 and b.slots['date']==day
    assert not b.tools.booking_store().list(b.tools.business_id)

def test_uncertain_times_during_active_booking_are_clarified_before_wait(client):
    sid,b=begin(client,'chat');b.reserving=True;b.slots={'party_size':2}
    r=say(client,sid,'7 pm or 8 pm, I have not decided which.')
    assert 'Which arrival time' in r['reply'] and r['conversation_wait'] is None
    assert b.slots['party_size']==2

def test_literal_identity_survives_time_choice_and_does_not_write(client):
    sid,b=begin(client,'chat');day=(local_today(b.restaurant)+timedelta(days=5)).isoformat()
    say(client,sid,f'Book two on {day} at 7 pm or 8 pm, under Alex Morgan, phone 2025550186.')
    assert b.slots['name']=='Alex Morgan' and b.slots['phone']=='2025550186' and b.slots['party_size']==2
    assert not b.tools.booking_store().list(b.tools.business_id)

def test_no_draft_has_no_interruption_timer(client):
    sid,b=begin(client,'chat');r=say(client,sid,'Can I bring my dog?')
    assert r['conversation_wait'] is None and not api.sessions[sid].awaiting_reservation_return

def test_invalid_future_update_stays_an_update_without_writing(client):
    sid,b=begin(client,'chat');day=(local_today(b.restaurant)+timedelta(days=91)).isoformat()
    r=say(client,sid,f'Please move my reservation to {day} at 8 pm.')
    assert '90 days' in r['reply'] and not b.reserving
    assert b.reservation_changes.action=='update' and b.reservation_changes.changes=={'time':'8 pm'}
    assert not b.tools.booking_store().list(b.tools.business_id)

def test_cancel_it_after_hesitation_discards_only_unsaved_draft(client):
    sid,b=begin(client,'voice');b.reserving=b.awaiting_confirmation=True
    b.slots={'party_size':2,'date':(local_today(b.restaurant)+timedelta(days=5)).isoformat(),'time':'19:00','name':'Private Guest','phone':'2025550186'}
    say(client,sid,'Let me think.')
    r=say(client,sid,'Cancel it.')
    assert 'will not go ahead' in r['reply'] and not b.slots and not b.reservation_changes.active
    say(client,sid,'Yes')
    assert not b.tools.booking_store().list(b.tools.business_id)

def test_return_timeout_after_committed_booking_never_reopens_creation(client,monkeypatch):
    import conversation_waits as waits
    sid,b=begin(client,'voice');b.reserving=b.awaiting_confirmation=True
    b.slots={'party_size':2,'date':(local_today(b.restaurant)+timedelta(days=5)).isoformat(),'time':'19:00','name':'Private Guest','phone':'2025550186'}
    say(client,sid,'Yes');saved=copy.deepcopy(b.tools.booking_store().list(b.tools.business_id))
    r=say(client,sid,'Can I bring my dog?');token=r['conversation_wait']['token']
    now=[100.0];monkeypatch.setattr(waits,'clock',lambda:now[0])
    client.post(f'/api/sessions/{sid}/waits/{token}/arm').raise_for_status();now[0]=115
    client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':'[elapsed]','input_kind':'timeout','wait_token':token}).raise_for_status()
    r=say(client,sid,'Yes')
    assert 'confirmed' in r['reply'] and b.tools.booking_store().list(b.tools.business_id)==saved
    assert not b.reserving and api.sessions[sid].pending_reservation_request is None

@pytest.mark.parametrize('text',['Check my reservation','Can I check my booking?','Can you cancel my reservation?'])
def test_payment_check_spelling_does_not_steal_lookup_or_cancellation(text):
    from policy_requests import policy_topics
    assert 'payment' not in {t.name for t in policy_topics(text)}

@pytest.mark.parametrize('command',['Please restart this conversation','Please end the call','Can I speak to a human?'])
def test_wait_is_cancelled_by_lifecycle_transition(client,command):
    sid,b=begin(client,'voice');b.reserving=True;b.slots={'party_size':2}
    say(client,sid,'Let me think.')
    assert api.sessions[sid].conversation_wait
    say(client,sid,command)
    assert api.sessions[sid].conversation_wait is None

@pytest.mark.parametrize('kind,seconds',[('hesitation',20),('interruption',15)])
@pytest.mark.parametrize('channel',['chat','voice'])
def test_wait_boundaries_cancellation_and_navigation(client,monkeypatch,channel,kind,seconds):
    import conversation_waits as waits
    sid,b=begin(client,channel);s=api.sessions[sid]
    b.reserving=b.awaiting_confirmation=True
    b.slots={'name':'Timer Guest','phone':'2025550186','date':(local_today(b.restaurant)+timedelta(days=5)).isoformat(),'time':'19:00','party_size':2}
    before=copy.deepcopy(b.slots)
    r=say(client,sid,'Maybe. I still need to check with my partner.' if kind=='hesitation' else 'Where can I park?')
    assert r['conversation_wait']['delay_ms']==seconds*1000
    token=r['conversation_wait']['token'];now=[100.0]
    monkeypatch.setattr(waits,'clock',lambda:now[0])
    client.post(f'/api/sessions/{sid}/waits/{token}/arm').raise_for_status()
    payload={'request_id':str(uuid4()),'text':'[elapsed]','input_kind':'timeout','wait_token':token}
    now[0]+=seconds-.001
    assert client.post(f'/api/sessions/{sid}/turns',json=payload).status_code==409
    now[0]+=.001
    r=client.post(f'/api/sessions/{sid}/turns',json=payload);r.raise_for_status()
    assert not b.tools.booking_store().list(b.tools.business_id) and b.slots==before
    assert client.post(f'/api/sessions/{sid}/turns',json={**payload,'request_id':str(uuid4())}).status_code==409
    if kind=='interruption':
        say(client,sid,'Yes');assert b.awaiting_confirmation
        assert not b.tools.booking_store().list(b.tools.business_id)
    say(client,sid,'Let me think.');token=s.conversation_wait['token']
    client.post(f'/api/sessions/{sid}/waits/{token}/arm')
    say(client,sid,'Can I bring my dog?')
    assert client.post(f'/api/sessions/{sid}/turns',json={**payload,'request_id':str(uuid4()),'wait_token':token}).status_code==409
    client.post(f'/api/sessions/{sid}/stop')
    assert s.conversation_wait is None
