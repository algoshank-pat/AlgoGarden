"""Staff-help consent and question navigation cannot become policy queries/writes."""
import copy
from unittest.mock import Mock
import pytest
import chat_api as api
from customer_intent import ADDITIONAL_QUESTIONS_REPLY
from knowledge import FALLBACK
from information_requests import pending_state
from tests.test_customer_feedback import client,say
from tests.test_answer_followup import begin

@pytest.fixture(autouse=True)
def private(monkeypatch,tmp_path):
    monkeypatch.setenv('RECEPTIONIST_FIXTURE_DIR',str(tmp_path))
    monkeypatch.setenv('LANGSMITH_TRACING','false')

def offer(client,sid,bot):
    original=bot.tools.search_knowledge
    bot.tools.search_knowledge=Mock(return_value={'relevant':False,'chunks':[]})
    try:
        result=say(client,sid,'What is your restaurant policy about storing bicycles?')
        assert result['reply']==FALLBACK and api.sessions[sid].awaiting_staff_offer
    finally:bot.tools.search_knowledge=original

@pytest.mark.parametrize('channel',['chat','voice'])
@pytest.mark.parametrize('phase',['phone','confirmation','completed'])
@pytest.mark.parametrize('choice',['No.','No thanks.','Not right now.','No, I have a question.','No, but could I ask another question?'])
def test_declining_staff_invites_actual_question_and_preserves_work(client,channel,phase,choice):
    sid,s,b=begin(client,channel,phase)
    before=pending_state(b);rows=copy.deepcopy(b.tools.booking_store().list(b.tools.business_id))
    offer(client,sid,b)
    response=say(client,sid,choice)
    assert response['reply']==ADDITIONAL_QUESTIONS_REPLY and not response['handoff']
    assert not response['structured']['calls'] and not response['structured']['tools_called']
    assert not s.awaiting_staff_offer and pending_state(b)==before
    for phrase in ['No, I have a question.','I still have another question.']:
        assert say(client,sid,phrase)['reply']==ADDITIONAL_QUESTIONS_REPLY
    answered=say(client,sid,'Do you have high chairs?')
    assert 'high chairs are available' in answered['reply'].lower()
    assert answered['sources'] and answered['structured']['tools_called']==['search_knowledge']
    assert b.tools.booking_store().list(b.tools.business_id)==rows and pending_state(b)==before
    b.chat.assert_not_called()

@pytest.mark.parametrize('channel',['chat','voice'])
def test_accepting_staff_is_handoff_not_reservation_confirmation(client,channel):
    sid,s,b=begin(client,channel,'confirmation');before=pending_state(b)
    offer(client,sid,b)
    response=say(client,sid,'Yes, please.')
    assert response['handoff'] and response['structured']['tools_called']==['transfer_to_human']
    assert pending_state(b)==before and not b.tools.booking_store().list(b.tools.business_id)
    b.tools.transfer_to_human.assert_called_once();b.chat.assert_not_called()

@pytest.mark.parametrize('channel',['chat','voice'])
def test_more_complete_question_is_answered_not_swallowed_by_no(client,channel):
    sid,s,b=begin(client,channel,'completed');rows=b.tools.booking_store().list(b.tools.business_id)
    offer(client,sid,b)
    reply=say(client,sid,'No, I have a question. Where can I park?')
    assert 'get_location' in reply['structured']['tools_called'] and 'parking' in reply['reply']
    assert not s.awaiting_staff_offer and b.tools.booking_store().list(b.tools.business_id)==rows
    assert say(client,sid,'No more questions')['survey']['status']=='offered'

@pytest.mark.parametrize('channel',['chat','voice'])
def test_done_after_staff_offer_reaches_survey(client,channel):
    sid,s,b=begin(client,channel,'completed');offer(client,sid,b)
    r=say(client,sid,'No more questions, thank you.')
    assert r['survey']['status']=='offered' and not s.awaiting_staff_offer
    assert say(client,sid,'No thanks')['ended']

def test_unclear_audio_keeps_offer_and_other_session_is_independent(client):
    a,sa,ba=begin(client,'voice','confirmation');offer(client,a,ba)
    b,sb,bb=begin(client,'voice','completed')
    r=say(client,a,'[No intelligible speech detected]',input_kind='unrecognized_audio')
    assert sa.awaiting_staff_offer and not r['ended']
    assert say(client,b,'No more questions')['survey']['status']=='offered'
    assert sa.awaiting_staff_offer and not sb.awaiting_staff_offer
    assert say(client,a,'No')['reply']==ADDITIONAL_QUESTIONS_REPLY
    assert ba.awaiting_confirmation and not ba.tools.booking_store().list(ba.tools.business_id)[0]['session_id']==a

@pytest.mark.parametrize('channel',['chat','voice'])
@pytest.mark.parametrize('text',["Yeah, but where's your restaurant?",'Yes, but where can I park?','Yeah, can I bring my dog inside?'])
def test_real_question_prefixed_yes_answers_before_booking(client,channel,text):
    sid,s,b=begin(client,channel,'confirmation');before=pending_state(b)
    reply=say(client,sid,text)
    assert set(reply['structured']['tools_called']) & {'get_location','search_knowledge'}
    assert pending_state(b)==before and not b.tools.booking_store().list(b.tools.business_id)
    returned=say(client,sid,'No more questions')
    assert 'Is that correct?' in returned['reply'] and not b.tools.booking_store().list(b.tools.business_id)
    assert say(client,sid,'Yes, please confirm the reservation')['structured']['tools_called']==['book_table']
    assert len(b.tools.booking_store().list(b.tools.business_id))==1
