"""Repeatable synthetic reservations for every day in October–December 2026."""
import json
import random
import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from math import ceil

from receptionist import HERE, Tools, load_restaurant
from reservation_schedule import shifts_for, minute, clock, peak_load

TAG='baseline:2026-q4:v1:'


def main():
    restaurant=load_restaurant(HERE/'restaurants/demo_restaurant.yaml')
    tools=Tools(restaurant);store=tools.booking_store();capacity=restaurant['seating']['seats_per_slot']
    duration=restaurant['seating']['duration_minutes']
    output=HERE/'results/testing-baseline';output.mkdir(parents=True,exist_ok=True)
    backup=output/('before-baseline-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.sqlite3')
    with sqlite3.connect(store.path) as source,sqlite3.connect(backup) as destination:source.backup(destination)
    rng=random.Random(20261001);start=date(2026,10,1);end=date(2026,12,31)
    from customer_identity import local_now
    from seating_time import has_arrived
    current=local_now(restaurant)
    existing={r.get('request_key'):r for r in store.list(tools.business_id)}
    inserted=0;replayed=0;blocked=0;days=[]
    for offset in range((end-start).days+1):
        day=start+timedelta(days=offset);iso=day.isoformat()
        slots=[clock(t) for shift in shifts_for(restaurant,day).values()
               for t in range(minute(shift['open']),minute(shift['close'])-duration+1,30)]
        if not slots:raise ValueError('Baseline requested for a closed day: '+iso)
        target=rng.randint(12,30)
        candidates=[]
        # Some evenings are deliberately busy enough to exercise alternatives.
        if offset%7==0:candidates.extend([('19:00',4)]*10)
        candidates.extend((rng.choice(slots),rng.choice([2,2,3,4,4])) for _ in range(100))
        accepted=0
        for index,(time,party) in enumerate(candidates):
            if accepted>=target:break
            key=f'{TAG}{iso}:{index:03d}'
            record={'date':iso,'time':time,'party_size':party,'name':f'Baseline test {iso} #{index+1}',
                    'phone':'9999'+str(offset*200+index).zfill(6),'ts':'2026-10-01T00:00:00+00:00',
                    'session_id':TAG+iso,'source':['phone','chat','walk_in'][index%3]}
            if record['source']=='walk_in' and not has_arrived(iso,time,current):
                record['source']=['phone','chat'][index%2]
            # Existing seed rows keep their original evidence on replay. The
            # owner API flags old future walk-in labels as unverified.
            if key in existing:record['source']=existing[key]['source']
            result=store.book(tools.business_id,record,capacity,0,key)
            if result.get('booked'):
                accepted+=1
                if result.get('replayed'):replayed+=1
                else:inserted+=1
            else:blocked+=1
        if not accepted:raise AssertionError('No baseline reservations for '+iso)
        days.append({'date':iso,'baseline_bookings':accepted})
    all_rows=store.list(tools.business_id)
    baseline=[r for r in all_rows if (r.get('request_key') or '').startswith(TAG)]
    active=[r for r in all_rows if r.get('status','active')=='active']
    for item in days:
        day_rows=[r for r in active if r['date']==item['date']]
        seats=[(minute(r['time']),minute(r['time'])+duration,r['party_size']) for r in day_rows]
        tables=[(a,b,ceil(n/restaurant['seating']['seats_per_table'])) for a,b,n in seats]
        item['peak_reserved_seats']=peak_load(seats,0,1440)
        item['peak_reserved_tables']=peak_load(tables,0,1440)
        assert item['peak_reserved_seats']<=capacity
        assert item['peak_reserved_tables']<=restaurant['seating']['table_count']
    report={'restaurant':restaurant['name'],'date_range':['2026-10-01','2026-12-31'],'seed':20261001,
            'baseline_tag':TAG,'backup':str(backup),'new_bookings':inserted,'replayed':replayed,
            'capacity_blocked_attempts':blocked,'baseline_total':len(baseline),
            'months':dict(Counter(r['date'][:7] for r in baseline)),
            'sources':dict(Counter(r['source'] for r in baseline)),'days':days,
            'checks':{'every_day_has_baseline':len(days)==92,'capacity_verified':True,'table_capacity_verified':True}}
    (output/'baseline-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    (output/'baseline-reservations.json').write_text(json.dumps(baseline,indent=2),encoding='utf-8')
    print(json.dumps({key:value for key,value in report.items() if key!='days'},indent=2))


if __name__=='__main__':main()
