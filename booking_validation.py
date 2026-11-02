"""Literal validation before identity collection; shared with write boundaries."""
import re
from datetime import timedelta

HORIZON_DAYS=90

def date_error(restaurant, day, clock=None):
    from customer_identity import local_today,local_now
    today=local_today(restaurant)
    if day < today:return "That date is in the past. What future date works for you?"
    if day > today+timedelta(days=HORIZON_DAYS):
        return f'We accept reservations up to 90 days ahead, through {(today+timedelta(days=90)).isoformat()}. Which date within that range works for you?'
    if clock and day==today and clock<=local_now(restaurant).strftime('%H:%M'):
        return 'That time has already passed today. What later time or future date works for you?'
    return None

def validate_turn(bot,text):
    from information_requests import availability_question
    from policy_requests import policy_question
    from booking_evidence import explicit_fields
    from decision_evidence import clock_phrases
    from receptionist import parse_date,parse_time
    from customer_identity import local_today
    from routing import Decision
    from reservation_changes import change_intent
    if change_intent(text) in {'cancel','lookup'}:return None
    if bot.reservation_changes.active and bot.reservation_changes.action in {'cancel','lookup'}:return None
    # Availability clarification owns its separate state. Rejecting a date
    # there must not start a booking or overwrite an interrupted draft.
    if availability_question(text) or getattr(bot,'_availability_enquiry',None) is not None:return None
    active=bot.reserving or bot.reservation_changes.active
    booking=bool(re.search(r'\b(book|reserve|reservation|table for)\b',text,re.I))
    if policy_question(text) or not (active or booking or availability_question(text)):return None
    fields=explicit_fields(text)
    # Retain other explicit identity fields when a clock/date needs clarification.
    from customer_identity import normalize_phone
    phones=[m[1] for m in re.finditer(r'(?<!\d)(\+?\d[\d ()-]{7,}\d)(?!\d)',text) if normalize_phone(m[1],bot.restaurant.get('phone_region','IN'))]
    if len(phones)==1:fields['phone']=phones[0]
    name=re.search(r'\b(?:my name is|under the name|under)\s+([A-Za-z]+(?:[ \-][A-Za-z]+){0,3}?)(?=\s+(?:and|on|at|for|phone|my number)\b|[,.!?]|$)',text,re.I)
    if name:fields['name']=name[1].strip()

    # Explicit corrections select one clock; alternatives need a customer choice.
    clocks=clock_phrases(text)
    conflict=len(clocks)>1 and not re.search(r'\b(instead|change|correct|rather than|not at)\b',text,re.I)
    invalid=re.search(r'\b(?:for|party of)\s+(?:minus\s+\w+|negative\s+\w+|zero|0|-[0-9]+)\b|\b(?:minus\s+\w+|negative\s+\w+|zero|0|-[0-9]+)\s+(?:guests?|people)\b',text,re.I)
    if getattr(bot,'_awaiting_slot',None)=='party_size' and re.fullmatch(r'\s*(?:zero|0|-\d+|minus \w+|negative \w+)[.!]?\s*',text,re.I):invalid=True
    error=None;field=None
    if fields.get('date'):
        day=parse_date(fields['date'],today=local_today(bot.restaurant))
        if day:error=date_error(bot.restaurant,day);field='date' if error else None
    if not error and fields.get('time'):
        current=bot.reservation_changes
        stored={**(current.current or {}),**current.changes} if current.active else bot.slots
        day_text=fields.get('date') or stored.get('date')
        day=parse_date(day_text,today=local_today(bot.restaurant)) if day_text else None
        clock=parse_time(fields['time'])
        if day and clock:error=date_error(bot.restaurant,day,clock);field='time' if error else None
    if not error and invalid:error='How many guests? Please give a positive number.';field='party_size'
    if not error and conflict:error='Which arrival time would you like: '+ ' or '.join(c[2] for c in clocks)+'?';field='time'
    if not error:return None
    if bot.reservation_changes.active or change_intent(text)=='update':
        # Preserve the update workflow, never turn an invalid update into creation.
        if not bot.reservation_changes.active:bot.reservation_changes.begin('update')
        bot.reservation_changes.pending=False
        bot.reservation_changes.changes.pop(field,None)
        for key,value in fields.items():
            if key in {'date','time','party_size'} and key!=field:bot.reservation_changes.changes[key]=value
    else:
        bot.reserving=True;bot.awaiting_confirmation=False
        for key,value in fields.items():
            if key!=field:
                if key=='name' and getattr(bot.tools,'booking_source','chat')=='phone':
                    bot._name_candidate=value;bot._name_readback=f'I heard {value}. Is that name correct?'
                else:bot.slots[key]=value
        bot.slots.pop(field,None);bot._awaiting_slot=field
    bot._information_readback=None;bot._information_readback_state=None
    bot.last_decision=Decision('reservation','rule','booking_validation','Clarify literal invalid or conflicting input before collecting identity.',fields=fields)
    return error,[]
