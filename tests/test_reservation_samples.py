import json
from collections import Counter
from receptionist import HERE, load_restaurant
from reservation_samples import generate
from reservation_schedule import day_plan


def test_sample_months_cover_every_day_and_fit_tables():
    restaurant=load_restaurant(HERE/'restaurants/demo_restaurant.yaml')
    rows=generate(json.dumps(restaurant,sort_keys=True))
    counts=Counter(b['date'] for b in rows)
    assert len(counts)==92 and min(counts.values())>=1
    assert min(counts)=='2026-10-01' and max(counts)=='2026-12-31'
    assert {b['source'] for b in rows}=={'phone','chat','walk_in'}
    assert all(b['sample'] and b['id'].startswith('sample-') for b in rows)
    for day in counts:
        plan=day_plan(restaurant,rows,day)
        assert plan['peak_reserved']<=plan['capacity']
        assert not any(b['needs_assignment'] for b in plan['bookings'])
        assert all(w['accepts_arrival'] for s in plan['services'] for w in s['windows'] if any(b['time']==w['start'] for b in plan['bookings']))
