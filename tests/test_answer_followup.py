"""Answer -> more questions -> pending work or optional survey, on both channels."""
import copy
from datetime import timedelta
from uuid import uuid4
from unittest.mock import Mock
import pytest
import chat_api as api
from customer_identity import local_today
from customer_feedback import OFFER,RATING
from conversation_followup import QUESTION,INVITE,no_more_questions
from tests.test_customer_feedback import client,say
from tests.test_decision_engine import response

@pytest.fixture(autouse=True)
def isolated(monkeypatch,tmp_path):
    monkeypatch.setenv('RECEPTIONIST_FIXTURE_DIR',str(tmp_path))
    monkeypatch.setenv('RECEPTIONIST_DECISION_MODE','unified')
    monkeypatch.setenv('LANGSMITH_TRACING','false')

def begin(client,channel,phase='idle'):
    sid=client.post('/api/sessions',json={'channel':channel}).json()['session_id']
    s=api.sessions[sid];b=s.bot
    day=(local_today(b.restaurant)+timedelta(days=5)).isoformat()
    if phase=='gate':s.pending_reservation_request=f'Book a table for two on {day} at 7 pm.'
    elif phase in {'phone','confirmation','completed'}:
        b.reserving=True;b._awaiting_slot='phone'
        b.slots={'date':day,'time':'19:00','party_size':2}
        if phase in {'confirmation','completed'}:
            b.slots.update(name='Private Guest',phone='2025550186');b.awaiting_confirmation=True
        if phase=='completed':
            r=say(client,sid,'Yes')
            assert r['reply'].endswith(QUESTION)
            assert len(b.tools.booking_store().list(b.tools.business_id))==1
    return sid,s,b

@pytest.mark.parametrize('channel',['chat','voice'])
@pytest.mark.parametrize('phase',['idle','gate','phone','confirmation','completed'])
def test_answers_yes_more_questions_then_no_resumes_or_surveys(client,channel,phase):
    sid,s,b=begin(client,channel,phase)
    slots=copy.deepcopy(b.slots);pending=s.pending_reservation_request
    rows=copy.deepcopy(b.tools.booking_store().list(b.tools.business_id))
    for text in ['Where can I park?','Can I bring my dog?','Do you have gluten-free food?']:
        r=say(client,sid,text)
        assert r['reply'].endswith(QUESTION) and r['reply'].count(QUESTION)==1
        assert r['sources'] or set(r['structured']['tools_called'])&{'get_location','check_dish'}
        assert s.awaiting_more_help
        assert b.slots==slots and s.pending_reservation_request==pending
        assert b.tools.booking_store().list(b.tools.business_id)==rows
        assert r['conversation_wait'] is None if phase=='completed' else r['conversation_wait']['delay_ms']==15000
        yes=say(client,sid,'Yes')
        assert yes['reply']==INVITE and not yes['structured']['tools_called']
        assert b.slots==slots and not yes['ended']
    r=say(client,sid,'No, I do not have any more questions.')
    if phase in {'idle','completed'}:
        assert r['reply']==OFFER and r['survey']['status']=='offered'
        assert r['conversation_wait'] is None
        assert say(client,sid,'Yes')['reply']==RATING
        assert say(client,sid,'good')['survey']['rating']=='good'
        assert s.bot.ended
    else:
        assert r['survey']['status']=='not_offered' and not r['structured']['tools_called']
        if phase=='gate':assert 'Do you already have a reservation' in r['reply'] and s.pending_reservation_request==pending
        elif phase=='phone':assert 'phone' in r['reply'].lower() and b.slots==slots
        else:
            assert 'Is that correct?' in r['reply'] and b.slots==slots
            assert b.tools.booking_store().list(b.tools.business_id)==rows
            confirmed=say(client,sid,'Yes')
            assert confirmed['reply'].endswith(QUESTION)
            assert len(b.tools.booking_store().list(b.tools.business_id))==len(rows)+1
            assert say(client,sid,'No')['survey']['status']=='offered'
            assert say(client,sid,'No thanks')['survey']['status']=='declined'
    if phase!='confirmation':assert b.tools.booking_store().list(b.tools.business_id)==rows
    b.chat.assert_not_called()

@pytest.mark.parametrize('text',['No','No thanks','No more questions','I have no additional questions',"I don't have any more questions",'No, I do not have any more questions.','Nothing else, thank you.','No questions',"I'm good, thanks","No, I'm all set",'That answers my questions'])
def test_natural_no_variants(text):assert no_more_questions(text)

@pytest.mark.parametrize('text',['No, where can I park?','No, change it to eight pm','No, book for three instead','I have more questions','I have no reservation','No gluten, please'])
def test_no_does_not_swallow_a_real_request(text):assert not no_more_questions(text)

@pytest.mark.parametrize('channel',['chat','voice'])
@pytest.mark.parametrize('phase',['idle','completed','confirmation'])
def test_unrelated_refusal_also_offers_a_way_forward(client,channel,phase):
    sid,s,b=begin(client,channel,phase);before=copy.deepcopy(b.slots)
    b.chat=Mock(return_value=response('unrelated','other'))
    r=say(client,sid,'Who is Albert Einstein?')
    assert r['reply']=="I can't help you with this question. "+QUESTION
    assert len([c for c in r['structured']['calls'] if c['component']=='ollama.chat'])<=1
    r=say(client,sid,'No')
    if phase=='confirmation':assert 'Is that correct?' in r['reply'] and b.slots==before
    else:assert r['survey']['status']=='offered'

@pytest.mark.parametrize('channel',['chat','voice'])
def test_real_question_after_no_is_answered_not_discarded(client,channel):
    sid,s,b=begin(client,channel,'completed');rows=b.tools.booking_store().list(b.tools.business_id)
    say(client,sid,'Can I bring my dog?')
    r=say(client,sid,'No, where can I park?')
    assert 'get_location' in r['structured']['tools_called'] and r['reply'].endswith(QUESTION)
    assert r['survey']['status']=='not_offered' and b.tools.booking_store().list(b.tools.business_id)==rows

@pytest.mark.parametrize('channel',['chat','voice'])
def test_survey_session_isolation_and_retry_idempotency(client,channel):
    a,sa,ba=begin(client,channel);b,sb,bb=begin(client,channel)
    for sid in [a,b]:say(client,sid,'Can I bring my dog?');say(client,sid,'No')
    assert say(client,a,'Yes')['reply']==RATING
    assert say(client,a,'5')['survey']['rating'] is None
    payload={'request_id':str(uuid4()),'text':'good'}
    one=client.post(f'/api/sessions/{a}/turns',json=payload).json()
    assert client.post(f'/api/sessions/{a}/turns',json=payload).json()==one
    assert sa.survey['rating']=='good' and sb.survey['rating'] is None
    assert say(client,b,'No thanks')['survey']['status']=='declined'

@pytest.mark.parametrize('channel',['chat','voice'])
@pytest.mark.parametrize('phase',['phone','confirmation','gate'])
def test_interruption_timeout_resumes_current_step_without_write(client,monkeypatch,channel,phase):
    import conversation_waits as waits
    sid,s,b=begin(client,channel,phase);slots=copy.deepcopy(b.slots)
    r=say(client,sid,'Can I bring my dog?');token=r['conversation_wait']['token']
    now=[100.];monkeypatch.setattr(waits,'clock',lambda:now[0])
    client.post(f'/api/sessions/{sid}/waits/{token}/arm').raise_for_status();now[0]=115.
    r=client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':'[elapsed]','input_kind':'timeout','wait_token':token}).json()
    if phase=='gate':assert 'Do you already have a reservation' in r['reply']
    elif phase=='phone':assert 'phone' in r['reply'].lower()
    else:assert 'Is that correct?' in r['reply']
    assert b.slots==slots and not b.tools.booking_store().list(b.tools.business_id)

def test_do_not_offer_survey_at_confirmation_or_date_clarification(client):
    sid,s,b=begin(client,'voice','confirmation')
    r=say(client,sid,'No')
    assert r['survey']['status']=='not_offered' and not b.tools.booking_store().list(b.tools.business_id)
    other,_,_=begin(client,'voice')
    r=say(client,other,'Do you have anything free in November?')
    assert not r['reply'].endswith(QUESTION) and r['survey']['status']=='not_offered'

@pytest.mark.parametrize('channel',['chat','voice'])
@pytest.mark.parametrize('action',['update','cancel'])
def test_interrupted_changes_still_require_final_confirmation(client,channel,action):
    sid,s,b=begin(client,channel,'completed')
    original=b.tools.booking_store().list(b.tools.business_id)
    change=b.reservation_changes;change.begin(action,original[0]['phone'])
    change.current=original[0];change.changes={'time':'20:00'} if action=='update' else {}
    change.pending=True;change.request_key=str(uuid4())
    assert say(client,sid,'Can I bring my dog?')['reply'].endswith(QUESTION)
    assert say(client,sid,'Yes')['reply']==INVITE
    r=say(client,sid,'No more questions')
    assert action in r['reply'] and 'Is that correct?' in r['reply']
    assert b.tools.booking_store().list(b.tools.business_id)==original
    r=say(client,sid,'Yes')
    assert r['reply'].endswith(QUESTION) and r['structured']['tools_called']==['change_reservation']
    rows=b.tools.booking_store().list(b.tools.business_id)
    assert len(rows)==1 and rows[0]['version']==original[0]['version']+1
    assert rows[0]['time']=='20:00' if action=='update' else rows[0]['status']=='cancelled'
    assert say(client,sid,'No')['survey']['status']=='offered'
