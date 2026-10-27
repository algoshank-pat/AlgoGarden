"""Conservative expected-field fast paths; complete requests fall through to the joint model."""
import re
NUMBERS=dict(zip('one two three four five six seven eight nine ten eleven twelve'.split(),range(1,13)))
MONTH=r'(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)'
WEEK=r'(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)'
DATE_ONLY=r'(?:on\s+)?(?:today|tomorrow|tonight|(?:(?:next|this)\s+)?'+WEEK+r'|\d{4}-\d{2}-\d{2}|'+MONTH+r'\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?|(?:the\s+)?\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?'+MONTH+r'(?:,?\s+\d{4})?)'

def selection_only(text):
    t=' '.join(text.lower().strip().rstrip('.!?').replace(',','').split())
    return bool(re.fullmatch(r"(?:(?:the|please|i choose|i want|i'd like)\s+)*(?:first|second|third|\d+(?:st|nd|rd|th)?)(?:\s+(?:reservation|booking|option|one|suggestion))?(?:\s+(?:please|do|thanks|thank you))*",t))


def fields(bot,text):
    active=bot.reserving or bot.reservation_changes.active
    expected=getattr(bot,'_awaiting_slot',None)
    t=' '.join(text.lower().strip().rstrip('.!?').split())
    if bot.reservation_changes.active and getattr(bot.reservation_changes,'selection_offered',False):
        from decision_evidence import selection
        choice=selection(text,True)
        # Mixed selection/correction requests still need joint field extraction.
        if choice is not None and selection_only(text):
            return {'reservation_choice':choice}
    if active and expected=='name':
        match=re.fullmatch(r"(?:actually[, ]+)?(?:my\s+name\s+is|call\s+me|put\s+it\s+under|use\s+the\s+name|change\s+(?:my\s+|the\s+)?name\s+to)\s+([a-z]+(?:[ '-][a-z]+){0,3})",text.strip().rstrip('.!?'),re.I)
        if match and not re.search(r'\b(guests?|people|party|date|time|phone|and|at|on)\b',match[1],re.I):
            return {'name':match[1]}
    if active and expected=='party_size':
        match=re.fullmatch(r'(?:(?:just|for|party of|there are|we are)\s+)?(\d{1,2}|'+ '|'.join(NUMBERS)+r')(?:\s+(?:guests?|people|of us))?',t)
        if match:
            n=NUMBERS.get(match[1],int(match[1]) if match[1].isdigit() else None)
            if n and 1<=n<=bot.restaurant['seating']['max_party_size']:return {'party_size':n}
    date_reply=re.fullmatch(r'('+DATE_ONLY+r')(?:\s+instead)?(?:,?\s+please)?',t)
    if active and expected not in {'name','phone'} and date_reply:
        from receptionist import parse_date
        from customer_identity import local_today
        candidate=date_reply[1]
        if parse_date(candidate,today=local_today(bot.restaurant)) is not None:return {'date':candidate}
    if not active and re.fullmatch(r'(?:(?:(?:can|could|would) (?:i|we|you)|(?:i|we) (?:want|need|would like) to|please)\s+)?(?:cancel|update|modify|change|reschedule|check|find|look up)\s+(?:my|our|the|an existing)\s+(?:table\s+)?(?:reservation|booking)(?:\s+please)?',t):
        return {}
    return None
