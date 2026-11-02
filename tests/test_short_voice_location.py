import copy
import pytest
import chat_api as api
from tests.test_customer_feedback import client,say

def test_address_is_short_and_parking_is_not_added(client):
 sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id']
 r=say(client,sid,'What is your address?')
 assert r['reply'].startswith('Demo address: 123 Demo Garden Lane, Springfield, Illinois.')
 assert 'United States' not in r['reply'] and 'valet' not in r['reply'] and 'fictional demo address for the course' not in r['reply']
 assert r['structured']['tools_called']==['get_location']
 api.sessions[sid].bot.chat.assert_not_called()

def test_parking_only_does_not_read_the_address(client):
 sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id']
 r=say(client,sid,'Where can I park?')
 assert 'parking in front' in r['reply'] and 'Friday to Sunday dinner' in r['reply']
 assert '123 Demo' not in r['reply']

def test_combined_request_answers_both_and_preserves_draft(client):
 sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id'];b=api.sessions[sid].bot
 b.reserving=b.awaiting_confirmation=True;b.slots={'date':'2026-11-09','time':'20:30','party_size':2,'name':'Private Guest','phone':'2025550173'}
 before=copy.deepcopy(b.slots)
 r=say(client,sid,'What is your address, and do you have parking?')
 assert 'Demo address:' in r['reply'] and 'Friday to Sunday dinner' in r['reply']
 assert b.slots==before and not b.tools.booking_store().list(b.tools.business_id)

def test_chat_retains_full_location_details(client):
 sid=client.post('/api/sessions',json={'channel':'chat'}).json()['session_id']
 r=say(client,sid,'What is your address?')
 assert 'fictional demo address for the course' in r['reply'] and 'parking in front' in r['reply']
