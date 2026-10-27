"""API contract and retry tests; no live inference or booking writes."""
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
import chat_api


@pytest.fixture
def client(monkeypatch, tmp_path):
    from concurrency import TurnScheduler
    monkeypatch.setattr(chat_api, 'scheduler', TurnScheduler())
    monkeypatch.setattr(chat_api.engine.booking_db, 'DB_PATH', tmp_path / 'reservations.sqlite3')
    monkeypatch.setattr(chat_api.audit, 'CALLS_FILE', tmp_path / 'calls.jsonl')
    monkeypatch.setattr(chat_api.audit, 'REVIEWS_FILE', tmp_path / 'reviews.jsonl')
    monkeypatch.setattr(chat_api.audit, 'NOTES_FILE', tmp_path / 'notes.jsonl')
    monkeypatch.setattr(chat_api.engine, 'BOOKINGS_FILE', tmp_path / 'bookings.jsonl')
    monkeypatch.setattr(chat_api.engine, 'MESSAGES_FILE', tmp_path / 'messages.jsonl')
    chat_api.sessions.clear()
    monkeypatch.setattr(chat_api, 'new_bot', lambda: SimpleNamespace(turn=Mock(return_value=('Hello!', [])), ended=False))
    with TestClient(chat_api.app) as client:
        yield client
    chat_api.sessions.clear()


def test_sessions_are_separate(client):
    first = client.post('/api/sessions').json()['session_id']
    second = client.post('/api/sessions').json()['session_id']
    assert first != second
    assert chat_api.sessions[first].bot is not chat_api.sessions[second].bot


def test_owner_notes_persist_resolve_and_leave_transcript_unchanged(client):
    sid=client.post('/api/sessions').json()['session_id']
    client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':'Hello'})
    original=chat_api.audit.CALLS_FILE.read_bytes()
    payload={'request_id':str(uuid4()),'text':' Called guest and confirmed the new time. ','resolve':True}
    response=client.post(f'/api/owner/conversations/{sid}/notes',json=payload)
    assert response.status_code==201,response.text
    note=response.json()['note']
    assert note['text']=='Called guest and confirmed the new time.' and note['at'] and note['author']=='Owner'
    assert client.post(f'/api/owner/conversations/{sid}/notes',json=payload).json()['note']['id']==note['id']
    dashboard=client.get('/api/owner/dashboard').json()
    call=next(c for c in dashboard['calls'] if c['id']==sid)
    assert call['review']=='Resolved' and len(call['follow_up_notes'])==1
    assert chat_api.audit.CALLS_FILE.read_bytes()==original
    assert client.post(f'/api/owner/conversations/{sid}/notes',json={**payload,'text':'Changed text'}).status_code==409
    assert client.post(f'/api/owner/conversations/{sid}/notes',json={**payload,'request_id':str(uuid4()),'text':'   '}).status_code==422
    assert client.post(f'/api/owner/conversations/{uuid4()}/notes',json=payload).status_code==404


def test_owner_note_retry_recovers_partial_resolution(client,monkeypatch):
    sid=client.post('/api/sessions').json()['session_id']
    client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':'Hello'})
    payload={'request_id':str(uuid4()),'text':'Follow-up complete.','resolve':True}
    append=chat_api.audit.append
    def fail_review(path,record):
        if path==chat_api.audit.REVIEWS_FILE:raise OSError('Unavailable')
        return append(path,record)
    monkeypatch.setattr(chat_api.audit,'append',fail_review)
    assert client.post(f'/api/owner/conversations/{sid}/notes',json=payload).status_code==503
    monkeypatch.setattr(chat_api.audit,'append',append)
    assert client.post(f'/api/owner/conversations/{sid}/notes',json=payload).status_code==201
    assert len(chat_api.audit.read(chat_api.audit.NOTES_FILE))==1


def test_owner_create_update_cancel_and_stale_write(client):
    payload={'request_id':str(uuid4()),'name':'Owner test','phone':'9900776655','party_size':2,'date':'2026-12-08','time':'20:00','source':'phone'}
    response=client.post('/api/owner/reservations',json=payload)
    assert response.status_code==201,response.text
    reference=response.json()['id']
    assert client.post('/api/owner/reservations',json=payload).json()['id']==reference
    update={'request_id':str(uuid4()),'action':'update','expected_version':1,'date':'2026-12-08','time':'20:30','party_size':4}
    response=client.post(f'/api/owner/reservations/{reference}/change',json=update)
    assert response.status_code==200,response.text
    assert response.json()['reservation']['version']==2
    assert response.json()['reservation']['party_size']==4
    assert client.post(f'/api/owner/reservations/{reference}/change',json={**update,'request_id':str(uuid4())}).status_code==409
    response=client.post(f'/api/owner/reservations/{reference}/change',json={'request_id':str(uuid4()),'action':'cancel','expected_version':2})
    assert response.status_code==200,response.text
    assert response.json()['reservation']['status']=='cancelled'
    tools=chat_api.engine.Tools(chat_api.restaurant)
    assert len(tools.booking_store().list(tools.business_id))==1


def test_owner_invalid_contact_past_and_closed_time(client):
    payload={'request_id':str(uuid4()),'name':'Owner test','phone':'bad-phone','party_size':2,'date':'2026-12-08','time':'20:00'}
    assert client.post('/api/owner/reservations',json=payload).status_code==422
    assert client.post('/api/owner/reservations',json={**payload,'phone':'9900776655','date':'2020-01-01'}).status_code==422
    assert client.post('/api/owner/reservations',json={**payload,'phone':'9900776655','time':'16:00'}).status_code==409


def test_stop_blocks_future_turns_even_during_processing(client):
    sid=client.post('/api/sessions').json()['session_id']
    session=chat_api.sessions[sid]
    with session.turn_lock:
        response=client.post(f'/api/sessions/{sid}/stop')
        assert response.status_code==200 and response.json()['turn_in_progress']
    response=client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':'Yes, please'})
    assert response.status_code==409
    session.bot.turn.assert_not_called()


@pytest.mark.parametrize('channel',['voice','chat'])
@pytest.mark.parametrize('phrase',['I change my mind','I changed my mind','I wanna change my mind','I would like to change my mind',"I've changed my mind",'Forget what I told you','Forget what I said','Start over'])
def test_restart_requires_confirmation_and_no_preserves_state(client,channel,phrase):
    sid=client.post('/api/sessions',json={'channel':channel}).json()['session_id']
    session=chat_api.sessions[sid];original=session.bot
    original.slots={'name':'Alex','date':'tomorrow'}
    session.awaiting_more_help=True
    def say(text):return client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':text}).json()
    result=say(phrase)
    assert 'start this conversation over' in result['reply']
    assert session.bot is original and original.slots['name']=='Alex'
    result=say('No')
    assert not result['ended'] and session.bot is original and original.slots['date']=='tomorrow'
    original.turn.assert_not_called()
    say(phrase);result=say('Yes, please')
    assert 'start again' in result['reply'] and chat_api.restaurant['name'] in result['reply']
    assert session.bot is not original and not session.awaiting_restart and not session.awaiting_more_help


@pytest.mark.parametrize('stage',['phone','name','readback','update','cancel'])
def test_restart_confirmation_cannot_commit_pending_action(client,monkeypatch,stage):
    bot=chat_api.engine.Receptionist(chat_api.restaurant,chat=Mock(side_effect=AssertionError('No inference for restart')))
    monkeypatch.setattr(chat_api,'new_bot',lambda:bot)
    sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id']
    session=chat_api.sessions[sid]
    bot.slots={'name':'Alex','phone':'9900112233','party_size':2,'date':'tomorrow','time':'8 pm'}
    bot.reserving=True;bot._awaiting_slot=stage
    bot.awaiting_confirmation=stage=='readback'
    if stage in {'update','cancel'}:bot.reservation_changes.begin(stage);bot.reservation_changes.pending=True
    original_book=Mock();original_change=Mock()
    bot.tools.book_table=original_book;bot.tools.change_reservation=original_change
    def say(text):return client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':text}).json()
    say('Forget what I told you')
    monkeypatch.setattr(chat_api,'new_bot',lambda:chat_api.engine.Receptionist(chat_api.restaurant,chat=Mock()))
    result=say('Yes')
    assert 'start again' in result['reply'] and session.bot.slots=={}
    assert not session.bot.reservation_changes.active and not session.bot.awaiting_confirmation
    original_book.assert_not_called();original_change.assert_not_called()


@pytest.mark.parametrize('hour,closing',[(5,'Good night!'),(6,'Have a good day!'),(17,'Have a good day!'),(18,'Good night!'),(23,'Good night!')])
def test_voice_greeting_more_help_and_polite_goodbye(client,monkeypatch,hour,closing):
    from datetime import datetime
    monkeypatch.setattr(chat_api,'restaurant_now',lambda:datetime(2026,10,26,hour,0))
    opened=client.post('/api/sessions',json={'channel':'voice'}).json()
    assert opened['greeting'].startswith('Thank you for calling')
    sid=opened['session_id'];bot=chat_api.sessions[sid].bot
    bot.turn.return_value=('Your reservation is updated to 4 guests on 2026-12-02 at 20:30.',['change_reservation'])
    result=client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':'Yes, please.'}).json()
    assert result['reply'].endswith('Is there anything else I can help you with?') and not result['ended']
    # Closing follows arrival time even if the call crosses the day/night boundary.
    monkeypatch.setattr(chat_api,'restaurant_now',lambda:datetime(2026,10,27,12,0))
    result=client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':"No, thank you. That's all."}).json()
    assert not result['ended'] and result['survey']['status']=='offered'
    result=client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':'No thanks'}).json()
    assert result['ended'] and result['reply']=='Thank you so much for calling. '+closing
    assert bot.turn.call_count==1


def test_no_during_pending_voice_confirmation_does_not_end_call(client):
    sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id']
    bot=chat_api.sessions[sid].bot
    bot.turn.return_value=('No changes were made to your reservation.',[])
    result=client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':'No.'}).json()
    assert not result['ended']
    bot.turn.assert_called_once_with('No.')


@pytest.mark.parametrize('answer',['No.','No, I don’t.','No, I do not have a reservation.'])
def test_voice_new_branch_preserves_initial_booking_details(client,monkeypatch,answer):
    import json
    bot=chat_api.engine.Receptionist(chat_api.restaurant,chat=Mock())
    bot.chat.side_effect=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({'date':'next Saturday','time':'half past eight','party_size':2})))]
    monkeypatch.setattr(chat_api,'new_bot',lambda:bot)
    sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id']
    def say(text):return client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':text}).json()
    assert say('Can I book a table for two next Saturday at half past eight?')['reply']=='Do you have a reservation with us?'
    bot.chat.assert_not_called()
    assert say(answer)['reply']=='Lemme help you with the reservation. What phone number can I use for your reservation?'
    assert bot.slots=={'date':'next Saturday','time':'half past eight','party_size':2}
    assert bot.tools.booking_store().list(bot.tools.business_id)==[]


def test_voice_yes_branch_looks_up_existing_by_phone(client,monkeypatch):
    import json
    bot=chat_api.engine.Receptionist(chat_api.restaurant,chat=Mock())
    bot.tools.book_table('Alex','9900112233',2,'2026-12-01','20:00',request_key='lookup-fixture')
    bot.chat.return_value=SimpleNamespace(message=SimpleNamespace(content=json.dumps({'phone':'9900112233','reference':'9900112233'})))
    monkeypatch.setattr(chat_api,'new_bot',lambda:bot)
    sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id']
    def say(text):return client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':text}).json()
    assert say('Can I book a table?')['reply']=='Do you have a reservation with us?'
    assert 'phone number' in say('Yes.')['reply']
    reply=say('My number is 9900112233')['reply']
    assert '2026-12-01' in reply and 'modify or cancel' in reply
    assert len(bot.tools.booking_store().list(bot.tools.business_id))==1


def test_new_voice_booking_explicitly_asks_phone_then_name(client,monkeypatch):
    import json
    def response(fields):return SimpleNamespace(message=SimpleNamespace(content=json.dumps(fields)))
    bot=chat_api.engine.Receptionist(chat_api.restaurant,chat=Mock())
    bot.chat.side_effect=[response({'party_size':2,'date':'2026-12-01','time':'half past eight'})]
    monkeypatch.setattr(chat_api,'new_bot',lambda:bot)
    sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id']
    def say(text):return client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':text}).json()
    assert say('Can I book a table for two on December 1 at half past eight?')['reply']=='Do you have a reservation with us?'
    assert say('No.')['reply']=='Lemme help you with the reservation. What phone number can I use for your reservation?'
    assert say('9900112233')['reply']=='May I have your name for the reservation?'
    assert bot.tools.booking_store().list(bot.tools.business_id)==[]
    reply=say('Alex.')['reply']
    assert 'under Alex' in reply and '20:30' in reply and 'correct' in reply.lower()
    assert bot.tools.booking_store().list(bot.tools.business_id)==[]
    say('Yes, please.')
    rows=bot.tools.booking_store().list(bot.tools.business_id)
    assert len(rows)==1 and rows[0]['name']=='Alex'


def test_owner_calendar_day_contract(client):
    result=client.get('/api/owner/reservations/day',params={'day':'2026-12-05'})
    assert result.status_code==200
    plan=result.json()
    assert plan['duration_minutes']==90 and plan['table_count']==10
    assert [s['name'] for s in plan['services']]==['brunch','lunch','dinner']
    assert client.get('/api/owner/reservations/day',params={'day':'not-a-date'}).status_code==422
    assert client.get('/api/owner/reservations/day',params={'day':'2026-02-30'}).status_code==422
    settings=client.get('/api/owner/dashboard').json()['reservation_settings']
    assert settings['timezone']=='America/Chicago'
    assert settings['hours']['weekly_off'] is None


@pytest.mark.parametrize('channel,source',[('chat','chat'),('voice','phone')])
def test_reservation_source_is_saved_at_creation(client,monkeypatch,channel,source):
    bot=chat_api.engine.Receptionist(chat_api.restaurant,chat=Mock())
    monkeypatch.setattr(chat_api,'new_bot',lambda:bot)
    session=client.post('/api/sessions',json={'channel':channel}).json()['session_id']
    result=chat_api.sessions[session].bot.tools.book_table('Source test','9900112233',2,'2026-12-01','20:00')
    assert result['booked']
    stored=bot.tools.booking_store().list(bot.tools.business_id)[0]
    assert stored['source']==source and stored['session_id']==session


def test_in_person_seating_capacity_retries_and_source(client,monkeypatch):
    from datetime import date,datetime
    monkeypatch.setattr(chat_api,'local_today',lambda r:date(2026,12,1))
    monkeypatch.setattr(chat_api.engine,'local_today',lambda r:date(2026,12,1))
    monkeypatch.setattr(chat_api,'restaurant_now',lambda:datetime(2026,12,1,12,0))
    payload={'request_id':str(uuid4()),'name':'Walk-in test','party_size':3,'date':'2026-12-01','time':'11:30'}
    first=client.post('/api/owner/reservations/in-person',json=payload)
    assert first.status_code==201
    retry=client.post('/api/owner/reservations/in-person',json=payload)
    assert retry.status_code==201 and retry.json()['id']==first.json()['id']
    plan=client.get('/api/owner/reservations/day',params={'day':'2026-12-01'}).json()
    window=next(w for s in plan['services'] for w in s['windows'] if w['start']=='11:30')
    assert window['source_counts']['walk_in']==3
    assert window['tables'][0]['source']=='walk_in'
    assert plan['reservation_count']==1
    changed=client.post(f"/api/owner/reservations/{first.json()['id']}/change",json={
        'request_id':str(uuid4()),'action':'update','expected_version':1,
        'date':'2026-12-02','time':'11:30','party_size':3})
    assert changed.status_code==422
    assert 'cannot be rescheduled' in changed.json()['detail']
    assert client.post('/api/owner/reservations/in-person',json={**payload,'request_id':str(uuid4()),'time':'12:30'}).status_code==422
    assert client.post('/api/owner/reservations/in-person',json={**payload,'date':'2026-12-02'}).status_code==422


def test_legacy_sources_need_recorded_evidence(client,monkeypatch):
    monkeypatch.setattr(chat_api.audit,'read',lambda path:[{'session_id':'known','channel':'voice'}])
    rows=chat_api.reservation_sources([{'session_id':'known','source':'unknown'},
        {'session_id':'missing','source':'unknown'},{'session_id':'known','source':'chat'}])
    assert [row['source'] for row in rows]==['phone','unknown','chat']


def test_voice_channel_is_recorded_and_invalid_channel_rejected(client):
    session = client.post('/api/sessions', json={'channel':'voice'}).json()['session_id']
    assert client.post(f'/api/sessions/{session}/turns',json={'request_id':str(uuid4()),'text':'Opening hours'}).status_code == 200
    assert client.get('/api/owner/dashboard').json()['calls'][0]['channel'] == 'voice'
    assert client.post('/api/sessions',json={'channel':'unknown'}).status_code == 422


def test_voice_silence_handoff_is_logged_as_status_not_invented_speech(client):
    session = client.post('/api/sessions',json={'channel':'voice'}).json()['session_id']
    bot = chat_api.sessions[session].bot
    bot.tools = SimpleNamespace(transfer_to_human=Mock())
    for index in range(4):
        result = client.post(f'/api/sessions/{session}/turns',json={
            'request_id':str(uuid4()),'text':'[No intelligible speech detected]',
            'input_kind':'unrecognized_audio'}).json()
        assert result['ended'] == (index == 3)
    bot.turn.assert_not_called()
    bot.tools.transfer_to_human.assert_called_once_with(reason='repeated_failed_understandings')
    call = client.get('/api/owner/dashboard').json()['calls'][0]
    assert call['turns'][0]['input_kind'] == 'unrecognized_audio'


@pytest.mark.parametrize('audio_first', [True,False])
def test_mixed_silence_and_unrecognized_text_count_as_four_failures(client,monkeypatch,audio_first):
    bot = chat_api.engine.Receptionist(chat_api.restaurant,chat=Mock(return_value=SimpleNamespace(message=SimpleNamespace(
        content='{"intent":"other","reasoning":"Unclear request","confidence":0.2}'))))
    monkeypatch.setattr(chat_api,'new_bot',lambda:bot)
    session = client.post('/api/sessions',json={'channel':'voice'}).json()['session_id']
    kinds = (['unrecognized_audio','text'] if audio_first else ['text','unrecognized_audio'])*2
    for kind in kinds:
        response = client.post(f'/api/sessions/{session}/turns',json={
            'request_id':str(uuid4()),'text':'[No intelligible speech detected]' if kind=='unrecognized_audio' else '???',
            'input_kind':kind})
    assert response.json()['ended'] and response.json()['handoff']


def test_retry_is_idempotent_and_conflicting_payload_rejected(client):
    session = client.post('/api/sessions').json()['session_id']
    payload = {'request_id': str(uuid4()), 'text': 'yes'}
    path = f'/api/sessions/{session}/turns'
    first = client.post(path, json=payload)
    second = client.post(path, json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    chat_api.sessions[session].bot.turn.assert_called_once()
    assert client.post(path, json={**payload, 'text': 'different'}).status_code == 409


def test_validation_expiry_and_deletion(client):
    session = client.post('/api/sessions').json()['session_id']
    path = f'/api/sessions/{session}/turns'
    for text in ['', '   ', 'x'*2001]:
        assert client.post(path, json={'request_id': str(uuid4()), 'text': text}).status_code == 422
    chat_api.sessions[session].touched -= chat_api.TTL_SECONDS + 1
    assert client.post(path, json={'request_id': str(uuid4()), 'text': 'Hello'}).status_code == 404
    assert client.delete(f'/api/sessions/{session}').status_code == 204


def test_handoff_has_contact_not_fake_phone_transfer(client):
    session = client.post('/api/sessions').json()['session_id']
    bot = chat_api.sessions[session].bot
    bot.turn.return_value = ('Connecting you now', ['transfer_to_human'])
    bot.ended = True
    result = client.post(f'/api/sessions/{session}/turns', json={'request_id': str(uuid4()), 'text': 'Order please'}).json()
    assert result['handoff'] and not result['ended']
    assert result['survey']['status']=='offered'
    assert result['staff_phone'] == chat_api.restaurant['phone']
    assert 'connect' not in result['reply'].lower()


def test_uncertain_failure_is_terminal_and_not_reexecuted(client):
    session = client.post('/api/sessions').json()['session_id']
    bot = chat_api.sessions[session].bot
    bot.turn.side_effect = RuntimeError('simulated failure after possible write')
    payload = {'request_id': str(uuid4()), 'text': 'yes'}
    for _ in range(2):
        result = client.post(f'/api/sessions/{session}/turns', json=payload).json()
        assert result['ended']
        assert 'check whether' in result['reply']
    bot.turn.assert_called_once()


def test_live_dashboard_persists_real_turns_without_retry_duplicates(client):
    session = client.post('/api/sessions').json()['session_id']
    payload = {'request_id': str(uuid4()), 'text': 'Where can I park?'}
    for _ in range(2):
        assert client.post(f'/api/sessions/{session}/turns', json=payload).status_code == 200
    chat_api.sessions.clear()  # Simulate losing in-memory conversations on restart.
    result = client.get('/api/owner/dashboard').json()
    assert len(result['calls']) == 1
    assert len(result['calls'][0]['turns']) == 1
    assert result['calls'][0]['turns'][0]['caller_text'] == payload['text']
    assert result['bookings'] == result['messages'] == []


def test_review_is_separate_from_immutable_call_history(client):
    session = client.post('/api/sessions').json()['session_id']
    client.post(f'/api/sessions/{session}/turns', json={'request_id': str(uuid4()), 'text': 'Hello'})
    original = chat_api.audit.CALLS_FILE.read_text()
    assert client.post('/api/owner/reviews', json={'target': 'call:'+session, 'status': 'Reviewed'}).status_code == 200
    assert chat_api.audit.CALLS_FILE.read_text() == original
    assert client.get('/api/owner/dashboard').json()['calls'][0]['review'] == 'Reviewed'


def test_actual_audit_records_confirmation_before_book(client, monkeypatch):
    from routing import Decision
    bot = chat_api.engine.Receptionist(chat_api.restaurant, chat=Mock())
    bot.reserving = bot.awaiting_confirmation = True
    bot.slots = {'party_size': 2, 'date': 'tomorrow', 'time': '8 pm', 'name': 'Alex', 'phone': '9900112233'}
    monkeypatch.setattr(bot.tools, 'book_table', chat_api.audit.audited('book_table')(lambda **kw: {'booked': True, 'date': '2026-12-01', 'time': '20:00'}))
    monkeypatch.setattr(chat_api, 'new_bot', lambda: bot)
    session = client.post('/api/sessions').json()['session_id']
    client.post(f'/api/sessions/{session}/turns', json={'request_id': str(uuid4()), 'text': 'yes'})
    call = client.get('/api/owner/dashboard').json()['calls'][0]
    names = [e['name'] for e in call['events']]
    assert names.index('reservation.confirmed') < names.index('book_table.completed')
    assert call['outcome'] == 'Booked'


def test_audit_failure_does_not_reexecute_turn(client, monkeypatch):
    session = client.post('/api/sessions').json()['session_id']
    monkeypatch.setattr(chat_api.audit, 'append', Mock(side_effect=OSError('disk full')))
    payload = {'request_id': str(uuid4()), 'text': 'yes'}
    for _ in range(2):
        response = client.post(f'/api/sessions/{session}/turns', json=payload).json()
        assert 'audit_warning' in response
    chat_api.sessions[session].bot.turn.assert_called_once()


def test_corrupt_audit_file_is_visible_error_not_silent_omission(client):
    chat_api.audit.CALLS_FILE.write_text('broken json\n')
    assert client.get('/api/owner/dashboard').status_code == 503


def test_dashboard_reads_sqlite_reservation_not_jsonl(client):
    tools = chat_api.engine.Tools(chat_api.restaurant)
    result = tools.book_table('Database test', '9900112233', 2, '2026-12-01', '8 pm', request_key='dashboard-test')
    assert result['booked']
    assert not chat_api.engine.BOOKINGS_FILE.exists()
    bookings = client.get('/api/owner/dashboard').json()['bookings']
    assert len(bookings) == 1
    assert bookings[0]['id'] == result['id']


def test_customer_cancel_flows_through_api_and_owner_history(client,monkeypatch):
    tools=chat_api.engine.Tools(chat_api.restaurant)
    booking=tools.book_table('API change test','9900112233',2,'2026-12-01','8 pm',request_key='api-change')
    bot=chat_api.engine.Receptionist(chat_api.restaurant,chat=Mock(return_value=SimpleNamespace(message=SimpleNamespace(
        content=__import__('json').dumps({'reference':booking['id'][:8],'phone':'9900112233'})))))
    monkeypatch.setattr(chat_api,'new_bot',lambda:bot)
    session=client.post('/api/sessions').json()['session_id']
    path=f'/api/sessions/{session}/turns'
    readback=client.post(path,json={'request_id':str(uuid4()),'text':'Cancel my reservation'}).json()
    assert 'say yes' in readback['reply']
    assert tools.booking_store().list(tools.business_id)[0]['status']=='active'
    payload={'request_id':str(uuid4()),'text':'yes'}
    for _ in range(2):
        assert 'cancelled' in client.post(path,json=payload).json()['reply']
    dashboard=client.get('/api/owner/dashboard').json()
    assert dashboard['bookings'][0]['status']=='cancelled'
    assert dashboard['calls'][0]['outcome']=='Cancelled'
    assert [event['kind'] for event in dashboard['booking_events']]==['created','cancelled']


def test_independent_conversations_can_overlap_and_dashboard_stays_responsive(client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from concurrency import TurnScheduler
    monkeypatch.setattr(chat_api, 'scheduler', TurnScheduler(workers=2))
    first, second = [client.post('/api/sessions').json()['session_id'] for _ in range(2)]
    entered = [Event(), Event()]
    release = Event()
    def slow(index):
        entered[index].set()
        assert release.wait(5)
        chat_api.audit.event('session-marker', {'session': index})
        return ('Done', [])
    for index, session in enumerate([first, second]):
        chat_api.sessions[session].bot.turn.side_effect = lambda text, index=index: slow(index)
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = [pool.submit(client.post, f'/api/sessions/{session}/turns', json={'request_id': str(uuid4()), 'text': 'Hello'}) for session in [first, second]]
        try:
            assert all(event.wait(3) for event in entered)
            snapshot = pool.submit(client.get, '/api/owner/dashboard').result(timeout=2)
            assert snapshot.status_code == 200
            assert snapshot.json()['queue']['active'] == 2
        finally:
            release.set()
        assert all(job.result(timeout=3).status_code == 200 for job in jobs)
    calls = client.get('/api/owner/dashboard').json()['calls']
    by_id = {call['id']: call for call in calls}
    for index, session in enumerate([first, second]):
        assert by_id[session]['events'][0]['details'] == {'session': index}


def test_same_session_inflight_retry_and_delete_are_rejected_safely(client):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    session = client.post('/api/sessions').json()['session_id']
    entered, release = Event(), Event()
    bot = chat_api.sessions[session].bot
    def slow(text):
        entered.set()
        assert release.wait(5)
        return ('Done', [])
    bot.turn.side_effect = slow
    payload = {'request_id': str(uuid4()), 'text': 'yes'}
    path = f'/api/sessions/{session}/turns'
    with ThreadPoolExecutor(max_workers=2) as pool:
        job = pool.submit(client.post, path, json=payload)
        try:
            assert entered.wait(3)
            assert client.post(path, json=payload).status_code == 409
            assert client.delete(f'/api/sessions/{session}').status_code == 409
            chat_api.sessions[session].touched -= chat_api.TTL_SECONDS + 1
            assert client.post('/api/sessions').status_code == 201
            assert session in chat_api.sessions
        finally:
            release.set()
        assert job.result(timeout=3).status_code == 200
    assert client.post(path, json=payload).status_code == 200
    bot.turn.assert_called_once()
    assert len(chat_api.audit.read(chat_api.audit.CALLS_FILE)) == 1


def test_full_queue_does_not_mutate_or_cache_rejected_turn(client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from concurrency import TurnScheduler
    monkeypatch.setattr(chat_api, 'scheduler', TurnScheduler(workers=1, waiting=0))
    first, second = [client.post('/api/sessions').json()['session_id'] for _ in range(2)]
    entered, release = Event(), Event()
    def slow(text):
        entered.set()
        assert release.wait(5)
        return ('Done', [])
    chat_api.sessions[first].bot.turn.side_effect = slow
    payload = {'request_id': str(uuid4()), 'text': 'Hello'}
    with ThreadPoolExecutor(max_workers=2) as pool:
        job = pool.submit(client.post, f'/api/sessions/{first}/turns', json=payload)
        try:
            assert entered.wait(3)
            response = client.post(f'/api/sessions/{second}/turns', json=payload)
            assert response.status_code == 503
            assert response.headers['retry-after'] == '2'
            chat_api.sessions[second].bot.turn.assert_not_called()
            assert not chat_api.sessions[second].replies
        finally:
            release.set()
        assert job.result(timeout=3).status_code == 200
    assert client.post(f'/api/sessions/{second}/turns', json=payload).status_code == 200
    assert chat_api.scheduler.status()['active'] == 0


def test_queue_wait_timeout_is_retryable(client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from concurrency import TurnScheduler
    monkeypatch.setattr(chat_api, 'scheduler', TurnScheduler(workers=1, waiting=1, wait_seconds=.02))
    first, second = [client.post('/api/sessions').json()['session_id'] for _ in range(2)]
    entered, release = Event(), Event()
    def slow(text):
        entered.set()
        assert release.wait(5)
        return ('Done', [])
    chat_api.sessions[first].bot.turn.side_effect = slow
    with ThreadPoolExecutor(max_workers=2) as pool:
        job = pool.submit(client.post, f'/api/sessions/{first}/turns', json={'request_id': str(uuid4()), 'text': 'Hello'})
        try:
            assert entered.wait(3)
            response = client.post(f'/api/sessions/{second}/turns', json={'request_id': str(uuid4()), 'text': 'Hello'})
            assert response.status_code == 503
            chat_api.sessions[second].bot.turn.assert_not_called()
            assert chat_api.scheduler.status()['waiting'] == 0
        finally:
            release.set()
        assert job.result(timeout=3).status_code == 200


def test_okay_stop_ends_live_call_without_booking(client):
    sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id']
    session=chat_api.sessions[sid]
    result=client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':'Okay, stop.'}).json()
    assert result['ended'] and result['reply'].startswith('Thank you so much for calling')
    session.bot.turn.assert_not_called()


@pytest.mark.parametrize('channel',['voice','chat'])
def test_incomplete_sentence_precedes_booking_and_restart_questions(client,channel):
    sid=client.post('/api/sessions',json={'channel':channel}).json()['session_id']
    session=chat_api.sessions[sid]
    session.pending_reservation_request='Book a table tomorrow'
    session.awaiting_restart=True
    for text in ['Hi. I would like to','Could you','Can I book a table for']:
        r=client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':text}).json()
        assert r['reply']=="I didn't get that. Can you please repeat?" and not r['ended'] and not r['handoff']
        assert session.pending_reservation_request=='Book a table tomorrow' and session.awaiting_restart
    session.bot.turn.assert_not_called()


@pytest.mark.parametrize('channel',['voice','chat'])
def test_three_repeat_prompts_then_handoff_and_retry_is_not_extra_failure(client,channel):
    sid=client.post('/api/sessions',json={'channel':channel}).json()['session_id']
    bot=chat_api.sessions[sid].bot;bot.tools=SimpleNamespace(transfer_to_human=Mock())
    for index in range(4):
        payload={'request_id':str(uuid4()),'text':'I would like to'}
        response=client.post(f'/api/sessions/{sid}/turns',json=payload).json()
        assert response['ended']==(index==3)
        assert response['handoff']==(index==3)
        if index<3:assert response['reply']=="I didn't get that. Can you please repeat?"
        repeated=client.post(f'/api/sessions/{sid}/turns',json=payload).json()
        assert repeated==response and bot.failures==index+1
    bot.tools.transfer_to_human.assert_called_once_with(reason='repeated_failed_understandings')


def test_understood_turn_resets_retry_budget(client,monkeypatch):
    bot=chat_api.engine.Receptionist(chat_api.restaurant,chat=Mock())
    bot.tools.transfer_to_human=Mock()
    monkeypatch.setattr(chat_api,'new_bot',lambda:bot)
    sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id']
    def say(text):return client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':text}).json()
    for _ in range(3):assert not say('I would like to')['ended']
    assert bot.failures==3
    assert 'hours' in say('What are your opening hours?')['reply'] and bot.failures==0
    for _ in range(3):assert not say('I would like to')['ended']
    assert say('I would like to')['handoff']


import pytest as _pytest
@_pytest.fixture(autouse=True)
def legacy_component_contract(monkeypatch):
    """These mocks target the legacy individual component schemas; unified contracts have their own tests."""
    monkeypatch.setenv('RECEPTIONIST_DECISION_MODE','legacy')


def test_sample_seating_never_occurs_in_future():
    import json
    from reservation_samples import generate
    rows=generate(json.dumps(chat_api.restaurant,sort_keys=True),'2026-10-27')
    assert any(b['source']=='walk_in' for b in rows if b['date']<='2026-10-27')
    assert all(b['source']!='walk_in' for b in rows if b['date']>'2026-10-27')


def test_public_restaurant_menu_uses_usd(client):
    info=client.get('/api/restaurant').json()
    assert info['currency']=='USD'
    assert info['menu'] and info['specials']
    assert all('price_usd' in dish and 'price_inr' not in dish for dish in info['menu'])
    assert all('$' in special and 'Rs ' not in special for special in info['specials'])
