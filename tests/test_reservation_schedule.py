from datetime import date
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from reservation_schedule import day_plan, shifts_for, peak_load
from booking_db import BookingStore
from receptionist import Tools, load_restaurant, HERE


@pytest.fixture
def restaurant():
    return load_restaurant(HERE/'restaurants/demo_restaurant.yaml')


def booking(id='one',time='20:00',size=2,status='active'):
    return {'id':id,'date':'2026-12-01','time':time,'party_size':size,'status':status,
            'name':'Synthetic guest','phone':'9900112233','ts':'2026-10-26'}


def test_daily_hours_and_weekend_brunch(restaurant):
    weekday=day_plan(restaurant,[],'2026-12-01')
    weekend=day_plan(restaurant,[],'2026-12-05')
    monday=day_plan(restaurant,[],'2026-11-30')
    assert [s['name'] for s in weekday['services']]==['lunch','dinner']
    assert [len(s['windows']) for s in weekday['services']]==[8,10]
    assert [s['name'] for s in weekend['services']]==['brunch','lunch','dinner']
    assert len(weekend['services'][0]['windows'])==6
    assert len(monday['services'])==2
    tools=Tools(restaurant)
    assert not tools._in_shift('08:00',date(2026,12,1))
    assert tools._in_shift('08:00',date(2026,12,5))=='brunch'
    assert tools._in_shift('21:30',date(2026,12,1))=='dinner'
    assert not tools._in_shift('22:00',date(2026,12,1))


def test_daily_utilization_counts_exact_seat_hours(restaurant):
    restaurant['seating']['existing_bookings']={}
    plan=day_plan(restaurant,[booking(time='12:15',size=4),booking('cancelled',size=12,status='cancelled')],'2026-12-01')
    assert plan['utilization']=={'percent':1.7,'reserved_seat_hours':6.0,'available_seat_hours':360.0}
    assert plan['peak_percent']==10.0
    assert plan['services'][0]['utilization']['percent']==3.8
    assert plan['services'][1]['utilization']['percent']==0


def test_utilization_clips_closing_and_includes_weekend_brunch(restaurant):
    restaurant['seating']['existing_bookings']={}
    row=booking(time='22:30',size=4)
    plan=day_plan(restaurant,[row],'2026-12-01')
    assert plan['utilization']['reserved_seat_hours']==2.0
    assert day_plan(restaurant,[],'2026-12-05')['utilization']['available_seat_hours']==480.0
    restaurant['hours']['weekly_off']='tuesday'
    assert day_plan(restaurant,[row],'2026-12-01')['utilization']=={'percent':0,'reserved_seat_hours':0.0,'available_seat_hours':0.0}


def test_map_overlap_cancellation_release_and_off_grid_start(restaurant):
    rows=[booking(),booking('two','20:30',5),booking('cancelled','20:00',8,'cancelled')]
    plan=day_plan(restaurant,rows,'2026-12-01')
    slots={w['start']:w for s in plan['services'] for w in s['windows']}
    assert slots['20:00']['reserved_seats']==2
    assert slots['20:30']['reserved_seats']==7
    assert slots['21:00']['reserved_seats']==7
    assert slots['21:30']['reserved_seats']==5
    assert slots['22:00']['reserved_seats']==0
    assert plan['guest_count']==7 and plan['reservation_count']==2
    assert plan['bookings'][0]['tables']==['T01']
    assert plan['bookings'][1]['tables']==['T02','T03']
    assert len(slots['20:30']['tables'])==10
    assert sum(t['reserved'] for t in slots['20:30']['tables'])==3
    off_grid=day_plan(restaurant,[booking(time='20:15')],'2026-12-01')
    window=next(w for s in off_grid['services'] for w in s['windows'] if w['start']=='20:00')
    assert window['reserved_seats']==2


def test_peak_counts_concurrency_not_sum_of_distinct_stays():
    assert peak_load([(660,750,4),(750,840,4)],660,840)==4


def test_duration_capacity_and_table_capacity_are_transactional(tmp_path):
    store=BookingStore(tmp_path/'db.sqlite3',duration_minutes=90,table_count=1,seats_per_table=4)
    first=store.book('r',booking(size=2),40,0,'one')
    assert first['booked']
    assert not store.book('r',{**booking('two','20:30',1),'phone':'9880055667'},40,0,'two')['booked']
    assert not store.available('r',booking(time='21:00',size=1),40)
    assert store.book('r',{**booking('three','21:30',4),'phone':'9880055667'},40,0,'three')['booked']
    # Updating excludes itself but still respects later overlapping stays.
    assert not store.change('r',first['id'],'9900112233','update',1,{'time':'20:30'},40,0,'move')['changed']
    assert store.change('r',first['id'],'9900112233','cancel',1,{},40,0,'cancel')['changed']
    assert store.seats('r','2026-12-01','20:00')==0


def test_overlapping_seats_without_table_limit(tmp_path):
    store=BookingStore(tmp_path/'db.sqlite3',duration_minutes=90)
    store.book('r',booking(size=4),4,0,'one')
    assert not store.book('r',{**booking('two','20:30',1),'phone':'9880055667'},4,0,'two')['booked']
    assert store.book('r',{**booking('three','21:30',4),'phone':'9880055667'},4,0,'three')['booked']


def test_cancelled_bookings_do_not_take_suggested_tables(restaurant):
    plan=day_plan(restaurant,[booking(status='cancelled')],'2026-12-01')
    assert plan['guest_count']==0 and len(plan['cancelled'])==1
    assert not any(t['reserved'] for s in plan['services'] for w in s['windows'] for t in w['tables'])


def test_different_start_times_compete_for_same_table(tmp_path):
    path=tmp_path/'db.sqlite3'
    BookingStore(path)
    barrier=Barrier(2)
    def attempt(index):
        store=BookingStore(path,duration_minutes=90,table_count=1,seats_per_table=4)
        row={**booking(time='20:00' if index==0 else '20:30'),
             'phone':'9900112233' if index==0 else '9880055667'}
        barrier.wait(timeout=10)
        return store.book('r',row,40,0,str(index))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(attempt,[0,1]))
    assert sum(r['booked'] for r in results)==1
    assert len(BookingStore(path).list('r'))==1


def test_source_breakdown_and_table_sources(restaurant):
    records=[{**booking(id='phone'),'source':'phone'},
             {**booking(id='chat'),'source':'chat'},
             {**booking(id='walkin'),'source':'walk_in'},
             {**booking(id='old'),'source':'unknown'}]
    plan=day_plan(restaurant,records,'2026-12-01')
    window=next(w for s in plan['services'] for w in s['windows'] if w['start']=='20:00')
    assert window['source_counts']=={'phone':2,'chat':2,'walk_in':2,'unknown':2}
    assert {t['source'] for t in window['tables'] if t['reserved']}=={'phone','chat','walk_in','unknown'}
    assert sum(window['source_counts'].values())==window['reserved_seats']
