import copy
from unittest.mock import Mock
import pytest
import chat_api as api
from tests.test_customer_feedback import client,say
from decision_evidence import date_phrases
from receptionist import parse_date

@pytest.mark.parametrize('wording',[
 'the ninth of November, 2026','November ninth, 2026','on the ninth of November twenty twenty six',
 'November twenty-first, twenty twenty-six','on 2026-11-09',
])
def test_natural_date_evidence_keeps_the_entire_calendar_date(wording):
 found=date_phrases(wording);assert len(found)==1
 assert parse_date(found[0][2]) is not None
 assert parse_date(found[0][2]).year==2026


@pytest.mark.parametrize('negative',['No.','No, no.','No, I do not have a reservation.'])
def test_offer_survives_gate_name_correction_and_policy_until_final_confirmation(client,negative):
 sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id'];b=api.sessions[sid].bot
 r=say(client,sid,'Do you have a table for two on the ninth of November, 2026 at seven in the evening?')
 assert r['structured']['tools_called']==['check_table']
 assert not b.slots and not b.reserving
 r=say(client,sid,'I would like to book that table.')
 assert 'have a reservation' in r['reply']
 r=say(client,sid,negative)
 assert b.slots=={'party_size':2,'date':'2026-11-09','time':'19:00'}
 assert 'phone' in r['reply'].lower()
 say(client,sid,'9900881234');say(client,sid,'My name is Alex Morgan.')
 r=say(client,sid,'Yes please.')
 assert b.awaiting_confirmation and b.slots['party_size']==2 and 'How many' not in r['reply']
 say(client,sid,'Actually, change the time to half past eight in the evening.')
 draft=copy.deepcopy(b.slots)
 say(client,sid,'Is a birthday cake from outside okay?')
 assert b.slots==draft
 r=say(client,sid,"Let's return to my reservation.")
 assert b.slots==draft and not b.tools.booking_store().list(b.tools.business_id)
 say(client,sid,'Yes, please book it.')
 rows=b.tools.booking_store().list(b.tools.business_id);assert len(rows)==1
 assert {k:rows[0][k] for k in ['date','time','party_size','name']}=={'date':'2026-11-09','time':'20:30','party_size':2,'name':'Alex Morgan'}
 r=say(client,sid,'Is my seat reserved?');assert 'confirmed' in r['reply']
 say(client,sid,'Sure');say(client,sid,'Yes')
 assert b.tools.booking_store().list(b.tools.business_id)==rows
 assert say(client,sid,'Please end the call.')['ended']
 b.chat.assert_not_called()


def test_offer_is_session_scoped_and_cannot_override_explicit_new_details(client):
 a=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id']
 b=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id']
 say(client,a,'Any tables available for two on November 9, 2026 at 7 pm?')
 from booking_context import selected_booking_request
 assert selected_booking_request(api.sessions[b].bot,'Book that table.') is None
 assert selected_booking_request(api.sessions[a].bot,'Book that table for four instead.') is None
 say(client,a,'Book a table for four on November 10, 2026 at 8 pm.');say(client,a,'No')
 assert api.sessions[a].bot.slots['party_size']==4
 assert api.sessions[a].bot.slots['date']=='November 10, 2026'


def test_ambiguous_gate_answer_does_not_consume_offer_or_enter_lookup(client):
 sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id'];s=api.sessions[sid]
 say(client,sid,'Any tables available for two on November 9, 2026 at 7 pm?');say(client,sid,'Book that table.')
 saved=s.pending_reservation_request
 r=say(client,sid,'No, maybe yes, I am not sure.')
 assert s.pending_reservation_request==saved and not s.bot.reservation_changes.active
 assert not s.bot.tools.booking_store().list(s.bot.tools.business_id)
 assert r['reply']=='Okay, I can wait for 20 seconds.'
 assert r['conversation_wait']['delay_ms']==20000


def test_incomplete_month_after_broad_booking_stays_in_date_clarification(client):
 sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id'];b=api.sessions[sid].bot
 say(client,sid,'I would like to book a reservation for the weekend.')
 r=say(client,sid,'October uh')
 assert 'repeat' in r['reply'].lower() and 'phone' not in r['reply'].lower()
 assert b._availability_enquiry is not None and not b.reserving and not b.slots
 b.chat.assert_not_called()
