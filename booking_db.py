"""SQLite reservation storage. Capacity checks and inserts share one write transaction."""
from __future__ import annotations
import hashlib
import os
import json
import re
import sqlite3
from math import ceil
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4
from customer_identity import normalize_phone
from reservation_schedule import intervals, minute, peak_load

DB_PATH = (Path(os.environ['RECEPTIONIST_FIXTURE_DIR']) if os.getenv('RECEPTIONIST_FIXTURE_DIR') else Path(__file__).parent) / 'reservations.sqlite3'


class BookingStore:
    def __init__(self, path: Path, phone_region: str = 'IN', duration_minutes: int = 0, baseline_slots=None, table_count=0, seats_per_table=4):
        self.path = path
        self.phone_region = phone_region
        self.duration_minutes = duration_minutes
        self.baseline_slots = baseline_slots or {}
        self.table_count = table_count
        self.seats_per_table = seats_per_table
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS reservations (
                    id TEXT PRIMARY KEY, business_id TEXT NOT NULL,
                    date TEXT NOT NULL, time TEXT NOT NULL,
                    party_size INTEGER NOT NULL CHECK(party_size > 0),
                    name TEXT NOT NULL, phone TEXT NOT NULL, ts TEXT NOT NULL,
                    session_id TEXT, request_key TEXT,
                    UNIQUE(business_id, request_key)
                );
                CREATE INDEX IF NOT EXISTS reservation_slot
                    ON reservations(business_id, date, time);
                CREATE TABLE IF NOT EXISTS booking_events (
                    id INTEGER PRIMARY KEY, booking_id TEXT NOT NULL,
                    kind TEXT NOT NULL, at TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS migrations (
                    source TEXT NOT NULL, business_id TEXT NOT NULL,
                    imported_count INTEGER NOT NULL, at TEXT NOT NULL,
                    PRIMARY KEY(source, business_id)
                );
                CREATE TABLE IF NOT EXISTS customer_visits (
                    business_id TEXT NOT NULL, phone_key TEXT NOT NULL,
                    local_date TEXT NOT NULL, session_id TEXT NOT NULL,
                    at TEXT NOT NULL,
                    PRIMARY KEY(business_id, phone_key, local_date, session_id)
                );
                CREATE TABLE IF NOT EXISTS reservation_operations (
                    business_id TEXT NOT NULL, request_key TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, result TEXT NOT NULL,
                    PRIMARY KEY(business_id, request_key)
                );
            ''')
        with self.transaction() as db:
            columns = {row['name'] for row in db.execute('PRAGMA table_info(reservations)')}
            if 'status' not in columns:
                db.execute("ALTER TABLE reservations ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
            if 'version' not in columns:
                db.execute('ALTER TABLE reservations ADD COLUMN version INTEGER NOT NULL DEFAULT 1')
            if 'source' not in columns:
                db.execute("ALTER TABLE reservations ADD COLUMN source TEXT NOT NULL DEFAULT 'unknown'")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=5000')
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                db.execute('COMMIT')
            except BaseException:
                db.execute('ROLLBACK')
                raise

    def migrate_jsonl(self, source: Path, business_id: str) -> int:
        """Import the legacy file once, atomically, leaving its bytes untouched."""
        key = str(source.resolve())
        with self.transaction() as db:
            if db.execute('SELECT 1 FROM migrations WHERE source=? AND business_id=?', (key, business_id)).fetchone():
                return 0
            records = []
            if source.exists():
                for line in source.read_text(encoding='utf-8').splitlines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    date.fromisoformat(row['date'])
                    datetime.strptime(row['time'], '%H:%M')
                    if type(row['party_size']) is not int or row['party_size'] < 1:
                        raise ValueError('Invalid party size in legacy bookings')
                    for field in ('name', 'phone', 'ts'):
                        if not isinstance(row[field], str):
                            raise ValueError('Invalid legacy booking field')
                    records.append(row)
            for index, row in enumerate(records):
                booking = {**row, 'id': str(uuid4()), 'business_id': business_id,
                           'request_key': f'legacy:{hashlib.sha256(key.encode()).hexdigest()}:{index}',
                           'session_id': row.get('session_id')}
                self._insert(db, booking, 'imported')
            db.execute('INSERT INTO migrations VALUES(?,?,?,?)',
                       (key, business_id, len(records), datetime.now(timezone.utc).isoformat()))
            return len(records)

    @staticmethod
    def _insert(db, booking: dict, kind: str):
        columns = ('id', 'business_id', 'date', 'time', 'party_size', 'name', 'phone', 'ts', 'session_id', 'request_key','source')
        values={**booking,'source':booking.get('source','unknown')}
        db.execute('INSERT INTO reservations('+','.join(columns)+') VALUES(?,?,?,?,?,?,?,?,?,?,?)', tuple(values.get(c) for c in columns))
        db.execute('INSERT INTO booking_events(booking_id,kind,at,payload) VALUES(?,?,?,?)',
                   (booking['id'], kind, booking['ts'], json.dumps(booking)))

    def seats(self, business_id: str, date: str, time: str) -> int:
        with self.connection() as db:
            return self._load(db, business_id, date, time)

    def _load(self, db, business_id, day, time, exclude_id='', baseline=0):
        if not self.duration_minutes:
            return baseline + db.execute("SELECT COALESCE(SUM(party_size),0) FROM reservations WHERE business_id=? AND date=? AND time=? AND status='active' AND id<>?",
                                         (business_id,day,time,exclude_id)).fetchone()[0]
        rows = db.execute("SELECT * FROM reservations WHERE business_id=? AND date=? AND status='active' AND id<>?",
                          (business_id,day,exclude_id)).fetchall()
        loads = intervals([dict(row) for row in rows],day,self.duration_minutes,self.baseline_slots)
        return peak_load(loads,minute(time),minute(time)+self.duration_minutes)

    def _fits(self,db,business_id,record,capacity,baseline=0,exclude_id=''):
        if self._load(db,business_id,record['date'],record['time'],exclude_id,baseline)+record['party_size']>capacity:
            return False
        if not self.table_count or not self.duration_minutes:
            return True
        rows=db.execute("SELECT * FROM reservations WHERE business_id=? AND date=? AND status='active' AND id<>?",
                        (business_id,record['date'],exclude_id)).fetchall()
        loads=intervals([dict(row) for row in rows],record['date'],self.duration_minutes,self.baseline_slots)
        tables=[(a,b,ceil(n/self.seats_per_table)) for a,b,n in loads]
        return peak_load(tables,minute(record['time']),minute(record['time'])+self.duration_minutes)+ceil(record['party_size']/self.seats_per_table)<=self.table_count

    def available(self,business_id,record,capacity,baseline=0):
        with self.connection() as db:
            return self._fits(db,business_id,record,capacity,baseline)

    def book(self, business_id: str, record: dict, capacity: int, baseline: int, request_key: str) -> dict:
        with self.transaction() as db:
            old = db.execute('SELECT * FROM reservations WHERE business_id=? AND request_key=?',
                             (business_id, request_key)).fetchone()
            if old:
                for key in ('date', 'time', 'party_size', 'name', 'phone'):
                    if old[key] != record[key]:
                        raise ValueError('Booking request key reused with different details')
                if old['source']!=record.get('source','unknown'):
                    raise ValueError('Booking request key reused with different source')
                return {'booked': old['status']=='active', **dict(old), 'replayed': True}
            phone_key = normalize_phone(record['phone'], self.phone_region)
            if phone_key:
                # Under the same write transaction as capacity and insertion: two
                # new sessions cannot race around customer-level duplicate checks.
                candidates = db.execute("SELECT * FROM reservations WHERE business_id=? AND date=? AND time=? AND status='active'",
                                        (business_id, record['date'], record['time'])).fetchall()
                for candidate in candidates:
                    if normalize_phone(candidate['phone'], self.phone_region) == phone_key:
                        return {'booked': False, 'duplicate': True,
                                'reason': 'An existing reservation already uses this phone, date and time.',
                                'existing_id': candidate['id']}
            if not self._fits(db,business_id,record,capacity,baseline):
                return {'booked': False, 'reason': 'That time is fully booked.'}
            booking = {**record, 'id': str(uuid4()), 'business_id': business_id, 'request_key': request_key}
            self._insert(db, booking, 'created')
            return {'booked': True, **booking, 'status':'active','version':1, 'replayed': False}

    def list(self, business_id: str) -> list[dict]:
        with self.connection() as db:
            return [dict(row) for row in db.execute('SELECT * FROM reservations WHERE business_id=? ORDER BY date,time,ts', (business_id,))]

    def lookup_by_phone(self, business_id: str, phone: str, today: str) -> dict:
        key=normalize_phone(phone,self.phone_region)
        if not key:raise ValueError('A valid phone number is required')
        with self.connection() as db:
            rows=db.execute("SELECT * FROM reservations WHERE business_id=? AND date>=? AND status='active' ORDER BY date,time,id",
                            (business_id,today)).fetchall()
        matches=[dict(row) for row in rows if normalize_phone(row['phone'],self.phone_region)==key]
        return {'found':bool(matches),'reservations':matches}

    def recognize(self, business_id: str, phone: str, today: str, session_id: str) -> dict:
        phone_key = normalize_phone(phone, self.phone_region)
        if not phone_key:
            raise ValueError('A valid phone number is needed for recognition')
        with self.transaction() as db:
            seen = db.execute('SELECT 1 FROM customer_visits WHERE business_id=? AND phone_key=? AND local_date=? AND session_id<>? LIMIT 1',
                              (business_id, phone_key, today, session_id)).fetchone() is not None
            db.execute('INSERT OR IGNORE INTO customer_visits VALUES(?,?,?,?,?)',
                       (business_id, phone_key, today, session_id, datetime.now(timezone.utc).isoformat()))
            candidates = db.execute("SELECT * FROM reservations WHERE business_id=? AND date>=? AND status='active' ORDER BY date,time",
                                    (business_id, today)).fetchall()
            matches = [dict(row) for row in candidates if normalize_phone(row['phone'], self.phone_region) == phone_key]
            return {'returning': seen or bool(matches), 'has_reservation': bool(matches), 'reservations': matches}

    def _matching(self, db, business_id: str, reference: str, phone: str):
        reference = re.sub(r'[\s-]', '', reference).lower()
        key = normalize_phone(phone, self.phone_region)
        if not key or not re.fullmatch(r'[a-f0-9]{8,32}',reference):
            return None
        rows = db.execute("SELECT * FROM reservations WHERE business_id=? AND replace(id,'-','') LIKE ?",
                          (business_id, reference+'%')).fetchall()
        matches = [dict(row) for row in rows if normalize_phone(row['phone'],self.phone_region)==key]
        return matches[0] if len(matches)==1 else None

    def lookup(self, business_id: str, reference: str, phone: str) -> dict:
        with self.connection() as db:
            record = self._matching(db,business_id,reference,phone)
            return {'found':bool(record),'reservation':record} if record else {'found':False}

    def preview_change(self,business_id: str,current_id: str,target: dict,capacity: int,baseline: int) -> dict:
        with self.connection() as db:
            other=db.execute("SELECT * FROM reservations WHERE business_id=? AND date=? AND time=? AND status='active' AND id<>?",
                (business_id,target['date'],target['time'],current_id)).fetchall()
            phone=normalize_phone(target['phone'],self.phone_region)
            if any(normalize_phone(row['phone'],self.phone_region)==phone for row in other):
                return {'available':False,'reason':'Another reservation for this phone already exists at that time.'}
            available=self._fits(db,business_id,target,capacity,baseline,current_id)
            return {'available':available,'reason':'' if available else 'The requested time is fully booked; the original reservation is unchanged.'}

    def change(self, business_id: str, reference: str, phone: str, action: str, expected_version: int,
               changes: dict, capacity: int, baseline: int, request_key: str, session_id: str | None=None) -> dict:
        if action not in {'update','cancel'} or set(changes)-{'date','time','party_size'} or (action=='cancel' and changes):
            raise ValueError('Invalid reservation operation')
        fingerprint = json.dumps({'reference':reference,'phone':normalize_phone(phone,self.phone_region),
            'action':action,'expected_version':expected_version,'changes':changes},sort_keys=True)
        with self.transaction() as db:
            receipt=db.execute('SELECT * FROM reservation_operations WHERE business_id=? AND request_key=?',
                               (business_id,request_key)).fetchone()
            if receipt:
                if receipt['fingerprint']!=fingerprint:
                    raise ValueError('Operation key reused for a different change')
                return {**json.loads(receipt['result']),'replayed':True}
            before=self._matching(db,business_id,reference,phone)
            if not before:
                return {'changed':False,'reason':'No unique reservation matches that reference and phone.'}
            if before['status']!='active':
                return {'changed':False,'reason':'That reservation is already cancelled.'}
            if before['version']!=expected_version:
                return {'changed':False,'conflict':True,'reason':'The reservation changed since the read-back; please look it up again.'}
            after={**before,**changes,'status':'cancelled' if action=='cancel' else 'active','version':expected_version+1}
            if type(after['party_size']) is not int or after['party_size']<1:
                raise ValueError('Invalid party size')
            if action=='update':
                date.fromisoformat(after['date'])
                datetime.strptime(after['time'],'%H:%M')
                other=db.execute("SELECT * FROM reservations WHERE business_id=? AND date=? AND time=? AND status='active' AND id<>?",
                                 (business_id,after['date'],after['time'],before['id'])).fetchall()
                key=normalize_phone(phone,self.phone_region)
                if any(normalize_phone(row['phone'],self.phone_region)==key for row in other):
                    return {'changed':False,'duplicate':True,'reason':'Another reservation for this phone already exists at that time.'}
                if not self._fits(db,business_id,after,capacity,baseline,before['id']):
                    return {'changed':False,'reason':'The requested time is fully booked; the original reservation is unchanged.'}
            db.execute('UPDATE reservations SET date=?,time=?,party_size=?,status=?,version=? WHERE id=? AND business_id=?',
                       (after['date'],after['time'],after['party_size'],after['status'],after['version'],before['id'],business_id))
            at=datetime.now(timezone.utc).isoformat()
            db.execute('INSERT INTO booking_events(booking_id,kind,at,payload) VALUES(?,?,?,?)',
                (before['id'],'cancelled' if action=='cancel' else 'updated',at,
                 json.dumps({'before':before,'after':after,'session_id':session_id})))
            result={'changed':True,'action':action,'reservation':after,'replayed':False}
            db.execute('INSERT INTO reservation_operations VALUES(?,?,?,?)',
                       (business_id,request_key,fingerprint,json.dumps(result)))
            return result

    def history(self,business_id: str) -> list[dict]:
        with self.connection() as db:
            return [{**dict(row),'payload':json.loads(row['payload'])} for row in db.execute(
                'SELECT e.* FROM booking_events e JOIN reservations r ON e.booking_id=r.id WHERE r.business_id=? ORDER BY e.id', (business_id,))]
