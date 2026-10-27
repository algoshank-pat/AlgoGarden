"""Repeatable fictional planning data; never written into live reservations."""
import json
import random
from datetime import date, timedelta
from functools import lru_cache
from reservation_schedule import day_plan, shifts_for, minute, clock


@lru_cache(maxsize=4)
def generate(configuration, today=None, current_time=None):
    restaurant=json.loads(configuration)
    today=date.fromisoformat(today) if today else date.today()
    current_time=current_time or '00:00'
    rng=random.Random(20261001)
    rows=[]
    day=date(2026,10,1)
    while day<date(2027,1,1):
        candidates=[clock(t) for shift in shifts_for(restaurant,day).values()
                    for t in range(minute(shift['open']),minute(shift['close'])-89,30)]
        daily=[]
        for i in range(rng.randint(6,14)):
            booking={'id':f'sample-{day.isoformat()}-{i:02d}','date':day.isoformat(),
                     'time':rng.choice(candidates),'party_size':rng.choice([2,2,3,4,4,6]),
                     'name':f'Sample guest {i+1}','phone':'','status':'active','version':1,
                     'source':rng.choice(['phone','chat','walk_in']),'sample':True}
            if booking['source']=='walk_in' and (day.isoformat(),booking['time'])>(today.isoformat(),current_time):
                booking['source']=['phone','chat'][i%2]
            plan=day_plan(restaurant,daily+[booking],day.isoformat())
            if not any(b['needs_assignment'] for b in plan['bookings']) and plan['peak_reserved']<=plan['capacity']:
                daily.append(booking)
        rows.extend(daily)
        day+=timedelta(days=1)
    return rows
