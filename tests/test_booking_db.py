import json
import multiprocessing
import sqlite3
from concurrent.futures import ProcessPoolExecutor

import pytest
from booking_db import BookingStore


def record(**changes):
    return {'date': '2026-12-01', 'time': '20:00', 'party_size': 2,
            'name': 'Test guest', 'phone': '9900112233', 'ts': '2026-10-25T12:00:00', **changes}


_barrier = None


def test_existing_schema_migrates_without_losing_reservations(tmp_path):
    path = tmp_path / 'old.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE reservations (
            id TEXT PRIMARY KEY, business_id TEXT NOT NULL,
            date TEXT NOT NULL, time TEXT NOT NULL, party_size INTEGER NOT NULL,
            name TEXT NOT NULL, phone TEXT NOT NULL, ts TEXT NOT NULL,
            session_id TEXT, request_key TEXT, UNIQUE(business_id, request_key))''')
        db.execute('INSERT INTO reservations VALUES(?,?,?,?,?,?,?,?,?,?)',
                   ('12345678-abcd', 'restaurant', '2026-12-01', '20:00', 2,
                    'Test guest', '9900112233', '2026-10-25', None, 'old-key'))
    store = BookingStore(path)
    rows = store.list('restaurant')
    assert len(rows) == 1
    assert rows[0]['id'] == '12345678-abcd'
    assert rows[0]['status'] == 'active'
    assert rows[0]['version'] == 1
    assert store.seats('restaurant', '2026-12-01', '20:00') == 2
    assert BookingStore(path).list('restaurant') == rows


def initialize_worker(barrier):
    global _barrier
    _barrier = barrier


def competing_booking(args):
    path, key = args
    store = BookingStore(path)
    _barrier.wait(timeout=30)
    return store.book('restaurant', record(), capacity=4, baseline=2, request_key=key)


def competing_duplicate(args):
    path, index = args
    store = BookingStore(path)
    _barrier.wait(timeout=30)
    phone = '+91 99001 12233' if index % 2 else '9900112233'
    return store.book('restaurant', record(phone=phone), capacity=40, baseline=0, request_key=f'new-caller-{index}')


def competing_update(args):
    path,booking_id,phone,key=args
    store=BookingStore(path)
    _barrier.wait(timeout=30)
    return store.change('restaurant',booking_id,phone,'update',1,{'date':'2026-12-03','time':'20:00'},2,0,key)


def test_concurrent_updates_cannot_claim_same_last_seats(tmp_path):
    path=tmp_path/'db.sqlite3'
    store=BookingStore(path)
    first=store.book('restaurant',record(),2,0,'one')
    second=store.book('restaurant',record(date='2026-12-02',phone='9880055667'),2,0,'two')
    context=multiprocessing.get_context('spawn')
    with ProcessPoolExecutor(max_workers=2,mp_context=context,initializer=initialize_worker,initargs=(context.Barrier(2),)) as pool:
        results=list(pool.map(competing_update,[(path,first['id'],'9900112233','change-one'),(path,second['id'],'9880055667','change-two')]))
    assert sum(result['changed'] for result in results)==1
    assert store.seats('restaurant','2026-12-03','20:00')==2
    assert sum(row['party_size'] for row in store.list('restaurant'))==4


def test_customer_duplicate_race_with_ample_capacity(tmp_path):
    path = tmp_path / 'reservations.sqlite3'
    store = BookingStore(path)
    context = multiprocessing.get_context('spawn')
    barrier = context.Barrier(6)
    with ProcessPoolExecutor(max_workers=6, mp_context=context,
                             initializer=initialize_worker, initargs=(barrier,)) as pool:
        results = list(pool.map(competing_duplicate, [(path, i) for i in range(6)]))
    assert sum(r['booked'] for r in results) == 1
    assert sum(r.get('duplicate', False) for r in results) == 5
    assert len(store.list('restaurant')) == 1


@pytest.mark.parametrize('same_key', [False, True])
def test_separate_processes_cannot_overbook_last_seats(tmp_path, same_key):
    path = tmp_path / 'reservations.sqlite3'
    store = BookingStore(path)
    context = multiprocessing.get_context('spawn')
    barrier = context.Barrier(6)
    with ProcessPoolExecutor(max_workers=6, mp_context=context,
                             initializer=initialize_worker, initargs=(barrier,)) as pool:
        results = list(pool.map(competing_booking, [(path, 'same-key' if same_key else str(i)) for i in range(6)]))
    assert store.seats('restaurant', '2026-12-01', '20:00') == 2
    assert len(store.list('restaurant')) == 1
    if same_key:
        assert all(r['booked'] for r in results)
        assert len({r['id'] for r in results}) == 1
    else:
        assert sum(r['booked'] for r in results) == 1
    with store.connection() as db:
        assert db.execute('SELECT count(*) FROM booking_events').fetchone()[0] == 1


def test_request_replay_and_conflict(tmp_path):
    store = BookingStore(tmp_path / 'db.sqlite3')
    first = store.book('a', record(), 2, 0, 'key')
    second = store.book('a', record(), 2, 0, 'key')
    assert first['id'] == second['id'] and second['replayed']
    with pytest.raises(ValueError):
        store.book('a', record(party_size=1), 2, 0, 'key')
    assert len(store.list('a')) == 1


def test_businesses_do_not_share_capacity(tmp_path):
    store = BookingStore(tmp_path / 'db.sqlite3')
    assert store.book('a', record(), 2, 0, 'same')['booked']
    assert store.book('b', record(), 2, 0, 'same')['booked']
    assert store.seats('a', '2026-12-01', '20:00') == 2


def test_failed_transaction_rolls_back_booking_and_event(tmp_path, monkeypatch):
    store = BookingStore(tmp_path / 'db.sqlite3')
    original = store._insert
    def fail(db, row, kind):
        original(db, row, kind)
        raise RuntimeError('simulated failure before commit')
    monkeypatch.setattr(store, '_insert', fail)
    with pytest.raises(RuntimeError):
        store.book('a', record(), 2, 0, 'key')
    assert store.list('a') == []
    with store.connection() as db:
        assert db.execute('SELECT count(*) FROM booking_events').fetchone()[0] == 0


def test_legacy_import_once_preserves_file_and_duplicate_rows(tmp_path):
    source = tmp_path / 'bookings.jsonl'
    original = (json.dumps(record())+'\n')*2
    source.write_text(original)
    store = BookingStore(tmp_path / 'db.sqlite3')
    assert store.migrate_jsonl(source, 'a') == 2
    assert store.migrate_jsonl(source, 'a') == 0
    assert store.seats('a', '2026-12-01', '20:00') == 4
    assert source.read_text() == original


def test_bad_legacy_record_rolls_back_whole_import(tmp_path):
    source = tmp_path / 'bookings.jsonl'
    source.write_text(json.dumps(record())+'\n'+json.dumps(record(party_size=-1)))
    store = BookingStore(tmp_path / 'db.sqlite3')
    with pytest.raises(ValueError):
        store.migrate_jsonl(source, 'a')
    assert store.list('a') == []


def test_capacity_rechecked_after_earlier_availability(tmp_path):
    store = BookingStore(tmp_path / 'db.sqlite3')
    assert store.seats('a', '2026-12-01', '20:00') == 0
    assert store.book('a', record(), 2, 0, 'first')['booked']
    assert not store.book('a', record(), 2, 0, 'second')['booked']
