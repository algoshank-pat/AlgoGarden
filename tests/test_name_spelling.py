from unittest.mock import Mock
import pytest
import chat_api as api
from name_spelling import spelled_name
from tests.test_customer_feedback import client,say

@pytest.mark.parametrize('text,expected',[
 ('Shishank, S-H-A-S-H-A-N-K.','Shashank'),
 ('No, let me spell my name. First name T E S T. Last name G U E S T.','Test Guest'),
 ('My name is spelled T-E-S-T space G-U-E-S-T.','Test Guest'),
 ('Let me spell my name. First name Tango Echo Sierra Tango. Last name Golf Uniform Echo Sierra Tango.','Test Guest'),
 ('No, my first name is spelled T as in Tiger, E as in Echo, S as in Sierra, T as in Tiger.','Test Guest'),
 ('My last name is spelled M I L L E R.','Tast Miller'),
 ('Let me spell it. My first name is T as in Tiger, E as in Echo, S as in Sierra, T as in Tiger, and my last name is G U E S T.','Test Guest'),
])
def test_spelling(text,expected):assert spelled_name(text,'Tast Guest').name==expected

@pytest.mark.parametrize('text',['My name is Charlie Mike.','Can I bring my dog?','Change my time to eight.','My name is Sam Miller.'])
def test_ordinary_names_and_unrelated_text(text):assert spelled_name(text) is None

@pytest.mark.parametrize('text',['Let me spell my name.','My name is spelled T E something.','My first name is spelled D as in Tiger E S T.'])
def test_unclear_spelling_is_not_guessed(text):assert spelled_name(text,'Tast Guest').name is None

@pytest.mark.parametrize('channel',['voice','chat'])
def test_corrected_readback_requires_new_confirmation(client,channel):
 sid=client.post('/api/sessions',json={'channel':channel}).json()['session_id'];b=api.sessions[sid].bot
 b.reserving=b.awaiting_confirmation=True
 b.slots={'name':'Tast Guest','phone':'2025550173','party_size':2,'date':'2027-02-17','time':'19:00'}
 b._recognized_phone='+12025550173';b.tools.check_table=Mock(return_value={'available':True,'date':'2027-02-17','time':'19:00'})
 b.tools.book_table=Mock(return_value={'booked':True,'date':'2027-02-17','time':'19:00','id':'private'})
 before=dict(b.slots)
 r=say(client,sid,'No, let me spell my name. First name T E S T. Last name G U E S T.')
 assert b.slots=={**before,'name':'Test Guest'} and b.awaiting_confirmation
 assert 'Test Guest' in r['reply'] and 'correct' in r['reply'].lower() and 'Tast' not in r['reply']
 b.tools.book_table.assert_not_called();b.chat.assert_not_called()
 again=say(client,sid,'My name is spelled T E S T space G U E S T.')
 assert b.awaiting_confirmation and 'Test Guest' in again['reply'];b.tools.book_table.assert_not_called()
 say(client,sid,'Yes please');b.tools.book_table.assert_called_once_with(**{**before,'name':'Test Guest'})

def test_unclear_correction_cannot_confirm_old_name(client):
 sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id'];b=api.sessions[sid].bot
 b.reserving=b.awaiting_confirmation=True;b.slots={'name':'Tast Guest','phone':'2025550173','party_size':2,'date':'2027-02-17','time':'19:00'}
 b.tools.book_table=Mock(side_effect=AssertionError('Must not book unclear name'))
 r=say(client,sid,'No, let me spell my name.')
 assert 'spell your first name' in r['reply'] and not b.awaiting_confirmation and 'name' not in b.slots
 say(client,sid,'Yes');b.tools.book_table.assert_not_called()

def test_mixed_changes_do_not_take_name_only_fastpath():
 from decision_fastpaths import fields
 b=Mock();b.reserving=True;b.slots={'name':'Tast Guest'}
 text='My name is spelled T E S T space G U E S T, and change the time to 8 pm.'
 assert not spelled_name(text,b.slots['name']).pure
 assert fields(b,text) is None

def test_mixed_spelling_and_time_preserves_both_with_one_generation(client):
 from tests.test_decision_engine import response
 sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id'];b=api.sessions[sid].bot
 b.reserving=b.awaiting_confirmation=True
 b.slots={'name':'Tast Guest','phone':'2025550173','party_size':2,'date':'2027-02-17','time':'19:00'}
 b._recognized_phone='+12025550173'
 b.chat=Mock(return_value=response(name='Wrong Again',time='8 pm'))
 b.tools.check_table=Mock(return_value={'available':True,'date':'2027-02-17','time':'20:00'})
 b.tools.book_table=Mock(side_effect=AssertionError('Correction cannot book'))
 r=say(client,sid,'No, my name is spelled T E S T space G U E S T, and change the time to 8 pm.')
 assert b.slots['name']=='Test Guest' and b.slots['time']=='8 pm' and b.awaiting_confirmation
 assert 'Test Guest' in r['reply'];b.chat.assert_called_once();b.tools.book_table.assert_not_called()

def test_spelling_is_session_local(client):
 ids=[client.post('/api/sessions',json={'channel':'voice'}).json()['session_id'] for _ in range(2)]
 for sid in ids:
  b=api.sessions[sid].bot;b.reserving=b.awaiting_confirmation=True
  b.slots={'name':'Tast Guest','phone':'2025550173','party_size':2,'date':'2027-02-17','time':'19:00'}
  b._recognized_phone='+12025550173';b.tools.check_table=Mock(return_value={'available':True,'date':'2027-02-17','time':'19:00'})
 untouched=dict(api.sessions[ids[1]].bot.slots)
 say(client,ids[0],'No, my name is spelled T E S T space G U E S T.')
 assert api.sessions[ids[0]].bot.slots['name']=='Test Guest'
 assert api.sessions[ids[1]].bot.slots==untouched and api.sessions[ids[1]].bot.awaiting_confirmation
