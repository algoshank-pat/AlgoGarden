"""Restaurant-local service windows and reservation planning (not live check-in)."""
from datetime import date, timedelta
from math import ceil

WEEKDAYS = ['monday','tuesday','wednesday','thursday','friday','saturday','sunday']


def minute(value):
    hour, mins = map(int, value.split(':'))
    return hour * 60 + mins


def clock(value):
    return f'{value // 60:02d}:{value % 60:02d}'


def shifts_for(restaurant, day):
    hours = restaurant['hours']
    if WEEKDAYS[day.weekday()] == hours.get('weekly_off'):
        return {}
    return {name: shift for name, shift in hours['shifts'].items()
            if not shift.get('days') or WEEKDAYS[day.weekday()] in shift['days']}


def intervals(bookings, day, duration, baseline_slots=None):
    result = [(minute(b['time']), minute(b['time']) + duration, b['party_size'])
              for b in bookings if b['date'] == day and b.get('status', 'active') == 'active']
    weekday = WEEKDAYS[date.fromisoformat(day).weekday()]
    for key, count in (baseline_slots or {}).items():
        wd, time = key.split()
        if wd == weekday and count:
            result.append((minute(time), minute(time) + duration, count))
    return result


def peak_load(entries, start, end):
    points = {start} | {a for a,b,n in entries if start <= a < end}
    return max((sum(n for a,b,n in entries if a <= point < b) for point in points), default=0)


def utilization(entries, services, capacity):
    """Integrate exact reserved minutes, clipped to each open service interval."""
    available=sum(minute(s['close'])-minute(s['open']) for s in services)*capacity
    reserved=sum(max(0,min(end,minute(s['close']))-max(start,minute(s['open'])))*seats
                 for start,end,seats in entries for s in services)
    return {'percent':round(reserved/available*100,1) if available else 0,
            'reserved_seat_hours':round(reserved/60,2),'available_seat_hours':round(available/60,2)}


def day_plan(restaurant, bookings, day, now=None):
    if now is not None:
        from seating_time import observed_sources
        bookings=observed_sources(bookings,now)
    day_date = date.fromisoformat(day)
    seating = restaurant['seating']
    duration = seating.get('duration_minutes', 90)
    table_count = seating.get('table_count', 10)
    table_seats = seating.get('seats_per_table', 4)
    capacity = seating['seats_per_slot']
    active = sorted((b for b in bookings if b['date'] == day and b.get('status', 'active') == 'active'),
                    key=lambda b:(b['time'], b['id']))
    # Suggested table allocation remains stable across windows for this snapshot.
    # No physical seating/check-in or persisted table assignment is implied.
    releases = [0] * table_count
    planned = []
    for booking in active:
        start = minute(booking['time'])
        needed = ceil(booking['party_size'] / table_seats)
        available = [i for i,end in enumerate(releases) if end <= start]
        assigned = available[:needed] if len(available) >= needed else []
        for i in assigned:
            releases[i] = start + duration
        planned.append({**booking, 'end_time': clock(start+duration),
                        'tables': [f'T{i+1:02d}' for i in assigned], 'needs_assignment':not assigned})
    loads = intervals(active, day, duration, seating.get('existing_bookings'))
    services = []
    for name,shift in shifts_for(restaurant, day_date).items():
        windows = []
        for start in range(minute(shift['open']), minute(shift['close']), 30):
            end = start + 30
            overlapping = [b for b in planned if minute(b['time']) < end and minute(b['end_time']) > start]
            if now is not None and (day,clock(start))>(now.date().isoformat(),now.strftime('%H:%M')):
                overlapping=[{**b,'source':'unknown','recorded_source':'walk_in','source_note':'Projected table use, not observed in-person occupancy.'} if b.get('source')=='walk_in' else b for b in overlapping]
            reserved = peak_load(loads, start, end)
            tables = []
            for i in range(table_count):
                table_id = f'T{i+1:02d}'
                guests = [b for b in overlapping if table_id in b['tables']]
                sources=sorted({b.get('source','unknown') for b in guests})
                tables.append({'id':table_id,'seats':table_seats,'reserved':bool(guests),
                               'sources':sources,'source':sources[0] if len(sources)==1 else 'mixed' if sources else 'free',
                               'references':[b['id'][:8] for b in guests]})
            points={start}|{minute(b['time']) for b in overlapping if start<=minute(b['time'])<end}
            peak_point=max(points,key=lambda p:sum(b['party_size'] for b in overlapping if minute(b['time'])<=p<minute(b['end_time'])))
            source_counts={key:sum(b['party_size'] for b in overlapping if b.get('source','unknown')==key and minute(b['time'])<=peak_point<minute(b['end_time'])) for key in ['phone','chat','walk_in','unknown']}
            windows.append({'start':clock(start),'end':clock(end),'reserved_seats':reserved,
                            'source_counts':source_counts,
                            'free_seats':max(0,capacity-reserved),'reservations':overlapping,
                            'tables':tables,'legacy_hold_seats':peak_load(intervals([],day,duration,seating.get('existing_bookings')),start,end),
                            'accepts_arrival':start+duration <= minute(shift['close'])})
        services.append({'name':name, **shift, 'windows':windows,
                         'utilization':utilization(loads,[shift],capacity)})
    peak=max((w['reserved_seats'] for s in services for w in s['windows']),default=0)
    return {'date':day,'capacity':capacity,'duration_minutes':duration,'table_count':table_count,
            'seats_per_table':table_seats,'services':services,'bookings':planned,
            'cancelled':[b for b in bookings if b['date']==day and b.get('status')=='cancelled'],
            'reservation_count':len(active),'guest_count':sum(b['party_size'] for b in active),
            'peak_reserved':peak,'peak_percent':round(peak/capacity*100,1) if capacity else 0,
            'utilization':utilization(loads,services,capacity)}
