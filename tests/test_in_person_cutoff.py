from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo
import json
import pytest
from fastapi.testclient import TestClient
import chat_api as api
from reservation_schedule import day_plan

@pytest.fixture
def client(monkeypatch,tmp_path):
 monkeypatch.setattr(api.engine.booking_db,'DB_PATH',tmp_path/'db.sqlite3')
 monkeypatch.setattr(api.engine,'BOOKINGS_FILE',tmp_path/'bookings.jsonl')
 monkeypatch.setattr(api.audit,'CALLS_FILE',tmp_path/'calls.jsonl')
 monkeypatch.setattr(api,'restaurant_now',lambda:datetime(2026,10,27,13,15,tzinfo=ZoneInfo(api.restaurant['timezone'])))
 with TestClient(api.app) as c:yield c

@pytest.mark.parametrize('day,time,status',[('2026-10-26','12:00',201),('2026-10-27','13:00',201),('2026-10-27','13:15',201),('2026-10-27','13:16',422),('2026-10-28','11:00',422)])
def test_arrival_cutoff(client,day,time,status):
 r=client.post('/api/owner/reservations/in-person',json={'request_id':str(uuid4()),'name':'Walk-in test','party_size':2,'date':day,'time':time})
 assert r.status_code==status,r.text
 rows=api.engine.Tools(api.restaurant).booking_store().list(api.engine.Tools(api.restaurant).business_id)
 assert len(rows)==(1 if status==201 else 0)

def test_future_legacy_label_is_unverified_without_losing_capacity(client):
 tools=api.engine.Tools(api.restaurant)
 tools.booking_store().book(tools.business_id,{'date':'2026-10-28','time':'18:00','party_size':2,'name':'Legacy','phone':'','ts':'test','source':'walk_in'},40,0,'legacy')
 data=client.get('/api/owner/reservations/day?day=2026-10-28').json()
 assert data['guest_count']==2 and data['peak_reserved']==2
 assert data['bookings'][0]['source']=='unknown' and data['bookings'][0]['recorded_source']=='walk_in'
 assert all(w['source_counts']['walk_in']==0 for s in data['services'] for w in s['windows'])
 assert tools.booking_store().list(tools.business_id)[0]['source']=='walk_in'

def test_today_projection_does_not_claim_future_actual_occupancy(client):
 now=api.restaurant_now()
 records=[{'id':'walkin','date':'2026-10-27','time':'13:00','party_size':2,'source':'walk_in','status':'active'},
          {'id':'future-phone','date':'2026-10-27','time':'13:30','party_size':2,'source':'phone','status':'active'},
          {'id':'future-chat','date':'2026-10-27','time':'13:30','party_size':2,'source':'chat','status':'active'}]
 data=day_plan(api.restaurant,records,'2026-10-27',now=now)
 windows={w['start']:w for s in data['services'] for w in s['windows']}
 assert windows['13:00']['source_counts']['walk_in']==2
 assert windows['13:30']['source_counts']['walk_in']==0 and windows['13:30']['reserved_seats']==6
 assert windows['13:30']['source_counts']['phone']==2 and windows['13:30']['source_counts']['chat']==2
 assert all(t['source']!='walk_in' for t in windows['13:30']['tables'])
 past=day_plan(api.restaurant,[{**records[0],'date':'2026-10-26'}],'2026-10-26',now=now)
 assert any(w['source_counts']['walk_in']==2 for s in past['services'] for w in s['windows'])

def test_preview_respects_time_as_well_as_date(client):
 from reservation_samples import generate
 rows=generate(json.dumps(api.restaurant,sort_keys=True),'2026-10-27','13:15')
 assert any(r['source']=='walk_in' for r in rows if r['date']<'2026-10-27')
 assert all(r['source']!='walk_in' for r in rows if (r['date'],r['time'])>('2026-10-27','13:15'))
