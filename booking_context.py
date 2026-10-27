"""Resolve short follow-ups against the most recent booking availability question."""
import re


def suggest_booking(bot,text):
    context=getattr(bot,'_availability_context',None)
    if not context or getattr(getattr(bot,'last_decision',None),'intent',None)!='reservation':return None
    if not (bot.reserving or bot.reservation_changes.action=='update'):return None
    if re.search(r'\b(first|second|third|1st|2nd|3rd|\d+)\s+(?:option|one|suggestion)\b',text,re.I):return None
    if not re.search(r'\b(recommend(?:ation)?s?|suggest(?:ion)?s?|options?|alternatives?)\b|\bwhat else (?:is available|would work)\b',text,re.I):return None
    # Explicit food/topic changes must still be routed normally.
    from routing import keyword_intent,Decision
    if keyword_intent(text) in {'menu','policy','hours','location_parking','order','human'} or re.search(r'\b(food|eat|dishes?|drinks?|desserts?)\b',text,re.I):return None
    lower=text.lower()
    if any(d['name'].lower() in lower for d in bot.restaurant.get('menu',[])):return None
    from receptionist import parse_date,parse_time,alternative_question
    from customer_identity import local_today
    target=context['requested']
    day=parse_date(target['date'],today=local_today(bot.restaurant));time=parse_time(target['time'])
    if not day or not time:return None
    change=bot.reservation_changes
    current=change.current if change.active else None
    options=bot.tools.reservation_alternatives(day,time,target['party_size'],current=current)
    if change.active:change.offered_alternatives=options
    else:bot._offered_alternatives=options
    bot.failures=0
    bot.last_decision=Decision('reservation','rule','contextual_booking_suggestions','Recommendations refer to the pending unavailable booking.',fields={'requested':target,'alternatives':options})
    if options:return alternative_question(options),['reservation_alternatives']
    return 'I couldn’t find an available nearby time over the next two days. Would a different date or party size work for you?',['reservation_alternatives']
