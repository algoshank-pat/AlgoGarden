"""Deterministic spoken dates/times; never change stored reservation fields."""
import re
from datetime import date, datetime

SMALL=['zero','one','two','three','four','five','six','seven','eight','nine','ten','eleven','twelve','thirteen','fourteen','fifteen','sixteen','seventeen','eighteen','nineteen']
ORDINAL=['','first','second','third','fourth','fifth','sixth','seventh','eighth','ninth','tenth','eleventh','twelfth','thirteenth','fourteenth','fifteenth','sixteenth','seventeenth','eighteenth','nineteenth','twentieth','twenty-first','twenty-second','twenty-third','twenty-fourth','twenty-fifth','twenty-sixth','twenty-seventh','twenty-eighth','twenty-ninth','thirtieth','thirty-first']


def number(n):
    if n<20:return SMALL[n]
    return ['','','twenty','thirty','forty','fifty','sixty','seventy','eighty','ninety'][n//10]+('-'+SMALL[n%10] if n%10 else '')


def spoken_date(value):
    d=date.fromisoformat(value)
    year=('twenty '+number(d.year%100)) if 2020<=d.year<=2099 else str(d.year)
    return f"the {ORDINAL[d.day]} of {d:%B}, {year}"


def spoken_time(hour,minute):
    period='in the morning' if hour<12 else 'in the afternoon' if hour<17 else 'in the evening' if hour<22 else 'at night'
    if hour==0 and minute==0:return 'midnight'
    if hour==12 and minute==0:return 'noon'
    h=number(hour%12 or 12)
    value=h if minute==0 else 'half past '+h if minute==30 else h+' '+('oh '+number(minute) if minute<10 else number(minute))
    return value+' '+period


def natural_speech(text):
    def iso(match):
        try:return spoken_date(match[0])
        except ValueError:return match[0]
    text=re.sub(r'\b\d{4}-\d{2}-\d{2}\b',iso,text)
    def named(match):
        try:return spoken_date(datetime.strptime(match[0],'%B %d, %Y').date().isoformat())
        except ValueError:return match[0]
    text=re.sub(r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}\b',named,text,flags=re.I)
    def time(match):
        hour,minute=int(match[1]),int(match[2]);period=match[3]
        if hour>23 or minute>59:return match[0]
        if period:
            if not 1<=hour<=12:return match[0]
            hour=hour%12+(12 if period.lower()=='pm' else 0)
        return spoken_time(hour,minute)
    return re.sub(r'(?<![\d:])(\d{1,2}):(\d{2})(?:\s*(AM|PM))?\b',time,text,flags=re.I)
