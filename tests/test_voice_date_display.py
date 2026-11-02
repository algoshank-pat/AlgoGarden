"""Latest live-call regressions; fixed restaurant clock and private databases."""
from datetime import date, datetime
from zoneinfo import ZoneInfo
from unittest.mock import Mock
import pytest
import chat_api as api
import customer_identity
from tests.test_customer_feedback import client, say
from receptionist import parse_date
from decision_evidence import date_phrases
from voice.phrases import SHORTEN, spoken_text, speech_segments

@pytest.fixture(autouse=True)
def clock(monkeypatch, tmp_path):
    now=datetime(2026,11,1,17,0,tzinfo=ZoneInfo('America/Chicago'))
    monkeypatch.setattr(customer_identity,'local_now',lambda restaurant:now)
    monkeypatch.setattr(api.engine,'local_now',lambda restaurant:now)
    monkeypatch.setenv('LANGSMITH_TRACING','false')
    monkeypatch.setenv('RECEPTIONIST_DECISION_MODE','unified')
    monkeypatch.setenv('RECEPTIONIST_DECISION_PROTOCOL','baseline')
    monkeypatch.setenv('RECEPTIONIST_FIXTURE_DIR',str(tmp_path))

@pytest.mark.parametrize('text,expected',[
    ('October thirty first',date(2026,10,31)),
    ('the thirty-first of October',date(2026,10,31)),
    ('October 31 this year',date(2026,10,31)),
    ('October 31 next year',date(2027,10,31)),
    ('October 31, 2027',date(2027,10,31)),
    ('October 31 last year',date(2025,10,31)),
    ('November 9',date(2026,11,9)),
    ('January 1',date(2026,1,1)),
    ('yesterday',date(2026,10,31)),
    ('day before yesterday',date(2026,10,30)),
    ('tomorrow',date(2026,11,2)),
    ('next Saturday',date(2026,11,7)),
])
def test_year_and_relative_dates(text,expected):
    assert parse_date(text,today=date(2026,11,1))==expected
    phrases=date_phrases('A table for two on '+text+' at seven pm')
    assert len(phrases)==1
    assert parse_date(phrases[0][2],today=date(2026,11,1))==expected

def begin(client,channel):
    sid=client.post('/api/sessions',json={'channel':channel}).json()['session_id']
    return sid,api.sessions[sid].bot

@pytest.mark.parametrize('channel',['chat','voice'])
@pytest.mark.parametrize('day',['October thirty first.','the thirty-first of October.','yesterday.'])
def test_live_availability_rejects_past_day_before_collecting_time(client,channel,day):
    sid,b=begin(client,channel)
    b.tools.check_table=Mock(return_value={'available':True})
    say(client,sid,'Hi, I would like to reserve a booking for uh October.')
    r=say(client,sid,day)
    assert 'past' in r['reply'] and 'future date' in r['reply']
    assert not r['structured']['tools_called'] and not b.reserving and not b.slots
    assert b._availability_expected=='date' and 'date' not in b._availability_enquiry
    b.tools.check_table.assert_not_called()
    say(client,sid,'November second at seven pm')
    r=say(client,sid,'for five people')
    assert 'available' in r['reply']
    b.tools.check_table.assert_called_once_with(5,'2026-11-02','19:00')
    assert not b.tools.booking_store().list(b.tools.business_id)
    b.chat.assert_not_called()

@pytest.mark.parametrize('channel',['chat','voice'])
def test_availability_does_not_alter_interrupted_draft(client,channel):
    sid,b=begin(client,channel)
    b.reserving=b.awaiting_confirmation=True
    b.slots={'date':'2026-11-09','time':'19:00','party_size':2,'name':'Private Guest','phone':'2025550186'}
    original=dict(b.slots)
    r=say(client,sid,'Do you have a table for five on October 31 at seven pm?')
    assert 'past' in r['reply'] and not r['structured']['tools_called']
    assert b.slots==original and b.awaiting_confirmation
    assert b._availability_enquiry=={'time':'seven pm','party_size':5}
    assert not b.tools.booking_store().list(b.tools.business_id)

@pytest.mark.parametrize('channel',['chat','voice'])
def test_past_create_then_valid_date_keeps_details_and_confirms_once(client,channel):
    sid,b=begin(client,channel)
    r=say(client,sid,'Book a table for two on October 31 at seven pm')
    assert 'past' in r['reply'] and 'phone' not in r['reply'].lower()
    assert b.slots=={'party_size':2,'time':'seven pm'}
    for text in ['November ninth','2025550186','Alex Morgan','Yes, that name is correct.']:
        r=say(client,sid,text)
        assert not b.tools.booking_store().list(b.tools.business_id)
        if b.awaiting_confirmation:break
    assert b.awaiting_confirmation and '2026-11-09' in r['reply'] and '19:00' in r['reply']
    r=say(client,sid,'Yes, please.')
    rows=b.tools.booking_store().list(b.tools.business_id)
    assert len(rows)==1 and rows[0]['date']=='2026-11-09' and rows[0]['time']=='19:00' and rows[0]['party_size']==2
    assert 'confirmed' in r['reply'] or 'booked' in r['reply']
    say(client,sid,'Is my reservation confirmed?')
    assert len(b.tools.booking_store().list(b.tools.business_id))==1
    b.chat.assert_not_called()

@pytest.mark.parametrize('channel',['chat','voice'])
@pytest.mark.parametrize('prompt',[
    'Do you have a table for two today at noon?',
    'Book a table for two today at noon',
])
def test_elapsed_time_today_is_rejected_before_any_tool(client,channel,prompt):
    sid,b=begin(client,channel)
    r=say(client,sid,prompt)
    assert 'already passed today' in r['reply'] and not r['structured']['tools_called']
    assert not b.tools.booking_store().list(b.tools.business_id)
    b.chat.assert_not_called()

def test_current_clock_rechecked_at_confirmation_and_update_boundary(client):
    sid,b=begin(client,'voice')
    assert 'already passed' in b.tools.book_table('Private','2025550186',2,'today','12:00')['reason']
    result=b.tools.prepare_reservation_change({'date':'2026-11-02','time':'19:00','party_size':2},{'date':'today','time':'12:00'})
    assert not result['valid'] and 'already passed' in result['reason']
    assert not b.tools.booking_store().list(b.tools.business_id)

@pytest.mark.parametrize('source',list(SHORTEN))
def test_stock_speech_has_no_encoding_damage(source):
    spoken=spoken_text(source)
    assert not any(marker in spoken for marker in ['\u00c3','\u00e2','\ufffd'])
    assert ' '.join(speech_segments(spoken))==spoken

def test_unavailable_speech_exact():
    assert spoken_text('That requested time is unavailable.')=="That time isn't available."
