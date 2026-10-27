from datetime import datetime,date
from zoneinfo import ZoneInfo
from unittest.mock import Mock
from types import SimpleNamespace
import json
import customer_identity as clock
import receptionist as app


def test_restaurant_timezone_is_independent_of_host_timezone(monkeypatch):
    class Frozen:
        @classmethod
        def now(cls,tz):
            return datetime(2026,10,26,23,30,tzinfo=ZoneInfo('America/Chicago')).astimezone(tz)
    monkeypatch.setattr(clock,'datetime',Frozen)
    r={'timezone':'Asia/Kolkata'}
    assert clock.local_today(r)==date(2026,10,27)
    assert clock.local_now(r).strftime('%H:%M')=='10:00'
    assert '2026-10-27T10:00:00+05:30' in clock.clock_context(r)


def test_booking_extraction_receives_clock_without_caller_providing_it(tmp_path,monkeypatch):
    monkeypatch.setattr(app.booking_db,'DB_PATH',tmp_path/'bookings.sqlite3')
    monkeypatch.setattr(clock,'local_now',lambda r:datetime(2026,10,27,23,59,tzinfo=ZoneInfo('Asia/Kolkata')))
    r=app.load_restaurant(app.HERE/'restaurants/demo_restaurant.yaml')
    chat=Mock(return_value=SimpleNamespace(message=SimpleNamespace(content=json.dumps({'date':'tomorrow','party_size':2,'time':'8 pm'}))))
    bot=app.Receptionist(r,chat=chat)
    reply,_=bot.turn('Book a table for two tomorrow at eight.')
    system=chat.call_args.kwargs['messages'][0]['content']
    assert '2026-10-27T23:59:00+05:30' in system and 'Asia/Kolkata' in system
    assert bot.slots['date']=='tomorrow'
    assert app.parse_date(bot.slots['date'],today=clock.local_today(r))==date(2026,10,28)
    assert 'phone' in reply.lower()
    monkeypatch.setattr(clock,'local_now',lambda r:datetime(2026,10,28,0,1,tzinfo=ZoneInfo('Asia/Kolkata')))
    assert '2026-10-28T00:01:00+05:30' in clock.clock_context(r)
