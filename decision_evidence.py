"""Extract clock/selection evidence without choosing an ambiguous alternative."""
import re
HOUR=r'(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)'
PERIOD=r'(?:(?:in the|at)\s+)?(?:morning|afternoon|evening|night)|a\.?m\.?|p\.?m\.?'
PATTERNS=[
 r'\b(?:a\s+)?(?:quarter|half|\d{1,2}|five|ten|fifteen|twenty(?: five)?)\s+(?:minutes?\s+)?(?:past|to|before|after)\s+'+HOUR+r'(?:\s+(?:'+PERIOD+r'))?\b',
 r'\b\d{1,2}:\d{2}\s*(?:a\.?m\.?|p\.?m\.?)?\b',
 r'\b'+HOUR+r'(?:\s+(?:fifteen|thirty|forty[ -]?five))?\s+(?:o[’\x27 ]?clock)(?:\s+(?:'+PERIOD+r'))?\b',
 r'\b'+HOUR+r'(?:\s+(?:fifteen|thirty|forty[ -]?five))?\s*(?:'+PERIOD+r')\b',
 r'\b'+HOUR+r'\s+(?:fifteen|thirty|forty[ -]?five)\b',
 r'\b(?:midnight|noon|midday)\b']

def negative_prefix(prefix):
    return bool(re.search(r"\b(?:not|never|except|anything but|other than|(?:do not|don't|cannot|can't)(?:\s+(?:want|book|make|reserve|change|it|the|my|booking|reservation|to)){0,6})(?:\s+(?:at|for|around|on))?\s*$",prefix,re.I))


def clock_phrases(text):
    candidates=[]
    for pattern in PATTERNS:
        candidates.extend(re.finditer(pattern,text,re.I))
    chosen=[]
    for match in sorted(candidates,key=lambda m:-(m.end()-m.start())):
        if not any(match.start()<b and match.end()>a for a,b,_ in chosen):chosen.append((match.start(),match.end(),match[0].strip()))
    return sorted(chosen)


def selection(text,offered):
    if not offered:return None
    match=re.search(r'\b(first|second|third|1st|2nd|3rd|\d+(?:st|nd|rd|th)?)\s+(?:reservation|booking|option|one|suggestion)\b',text,re.I)
    if not match and re.fullmatch(r'\s*\d+\s*[.!]?\s*',text):return int(re.search(r'\d+',text)[0])
    if not match:return None
    word=match[1].lower()
    return {'first':1,'second':2,'third':3,'1st':1,'2nd':2,'3rd':3}.get(word) or int(re.search(r'\d+',word)[0])


def date_phrases(text):
    from decision_fastpaths import DATE_ONLY
    return [(m.start(),m.end(),m[0]) for m in re.finditer(r'\b'+DATE_ONLY+r'\b',text,re.I)
            if not re.search(r'\b(last|previous|past)\s*$',text[:m.start()],re.I)]


def verify(value,text,context):
    from receptionist import parse_time
    fields=value['fields']
    if value['relevance']!='relevant':
        value['fields']={k:None for k in fields};return value
    if value['intent']=='reservation':
        # Date resolution belongs to the trusted restaurant clock in business
        # validation. Preserve an extracted calendar expression instead of an
        # invented ISO conversion. Do not introduce a date the model omitted.
        dates=date_phrases(text)
        if context.get('expected_field') not in {'name','phone'}:
            if len(dates)==1 and fields.get('date') is not None:
                fields['date']=None if negative_prefix(text[:dates[0][0]]) else dates[0][2]
            elif len(dates)>1 and re.search(r'\bor\b',text[dates[0][1]:dates[-1][0]],re.I):
                value.update(relevance='unclear',intent='other')
                value['fields']={k:None for k in fields};return value
        choice=selection(text,context.get('selection_offered',False))
        if choice is not None:fields['reservation_choice']=choice;fields['reference']=None
        if context.get('expected_field')!='name':
            phrases=clock_phrases(text)
            if len(phrases)==1 and parse_time(phrases[0][2]) is not None:
                phrase=phrases[0][2]
                prefix=text[:phrases[0][0]]
                negated=negative_prefix(prefix)
                named_clock=fields.get('name') and phrase.lower() in fields['name'].lower() and re.search(r'\b(name is|call me|put it under)\b',text,re.I)
                if negated:fields['time']=None
                elif not named_clock:fields['time']=re.sub(r'([ap])\.?m\.?$',r'\1m',phrase,flags=re.I)
            elif len(phrases)>1 and re.search(r'\bor\b',text[phrases[0][1]:phrases[-1][0]],re.I):
                value.update(relevance='unclear',intent='other',reason_summary='Multiple clock choices need clarification.')
                value['fields']={k:None for k in fields}
    return value
