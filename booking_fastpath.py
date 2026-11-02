"""Closed-grammar booking extraction. Unmatched or mixed intents use the unchanged model."""
import re

def fields(bot,text):
 from decision_fastpaths import NUMBERS,DATE_ONLY
 from decision_evidence import PATTERNS
 from customer_identity import phone_from_utterance,local_today
 from receptionist import parse_date,parse_time
 from routing import required_handoff
 if required_handoff(text):return None
 t=text.strip().replace('’',"'")
 # A voice gate appends only an explicit existing/new answer to its saved request.
 if getattr(bot,'reserving',False):
  t=re.sub(r'\n\s*(?:no(?:[, ]+no)*|nope|no[, ]+i(?: am|\x27m) not|no[, ]+i (?:do not|don\x27t) (?:have (?:a |an existing )?reservation|have one)|no[, ]+i (?:do not|don\x27t))\s*[.!]*$','',t,flags=re.I)
 t=t.strip().rstrip('.!?')
 if re.search(r'\b(not|never|unless|if|either|or|maybe|perhaps|ignore|pretend|policy|permission|price|cost|menu|allergen|parking|outside)\b',t,re.I):return None
 active=bool(getattr(bot,'reserving',False))
 change=bot.reservation_changes
 n=r'(?:\d{1,2}|'+ '|'.join(NUMBERS)+r')'
 def party(raw):return NUMBERS[raw.lower()] if raw.lower() in NUMBERS else int(raw)
 def valid(values):
  if 'party_size' in values and not 1<=values['party_size']<=bot.restaurant['seating']['max_party_size']:return None
  if 'date' in values and parse_date(values['date'],today=local_today(bot.restaurant)) is None:return None
  if 'date' in values:values['date']=re.sub(r'^on\s+','',values['date'],flags=re.I)
  if 'time' in values and parse_time(values['time']) is None:return None
  return values
 tail=r',?\s+(?:and\s+)?(?:actually\s+)?make\s+(?:it|that)\s+(?P<party>'+n+r')\s+(?:people|guests)(?:\s+instead)?'
 if active:
  m=re.fullmatch(r'(?:my (?:phone )?number is|my phone is|you can reach me at)\s+(?P<phone>[^,]+?)'+tail,t,re.I)
  if m:
   spoken=r'(?:zero|oh|one|two|three|four|five|six|seven|eight|nine|plus|double|triple)'
   if not re.fullmatch(r'(?:[0-9\s()+.\-]+|'+spoken+r'(?:[\s-]+'+spoken+r')*)',m['phone'],re.I):return None
   phone=phone_from_utterance(m['phone'],bot.restaurant.get('phone_region','IN'))
   if phone:return valid({'phone':phone,'party_size':party(m['party'])})
  m=re.fullmatch(r"(?:put it under|my name is|call me|it should be)\s+(?P<name>[a-z]+(?:[ '-][a-z]+){0,3})"+tail,t,re.I)
  if m:
   if re.search(r'\b(cancel|book|reservation|date|time|phone|and|tomorrow|today|people|guests)\b',m['name'],re.I):return None
   return valid({'name':m['name'],'party_size':party(m['party'])})
 clock=r'(?:'+'|'.join(PATTERNS)+r')'
 # Only courtesy words at the beginning and ordinary would-contractions.
 # Full matching below still rejects corrections, conditions and mixed tasks;
 # never discard an arbitrary sentence to make a request fit the fast path.
 opening=r'(?:(?:thanks|thank you|okay|ok|hi|hello)[,.!]*\s+)*'
 lead=opening+r'(?:(?:(?:please|can i|could i|can we|could we|i want to|we want to|(?:i|we)(?: would|\x27d) like to)\s+)?(?:book|reserve)(?:\s+(?:a|us a|me a))?(?:\s+table)?|(?:we|i) would love (?:dinner|lunch|brunch|a table))'
 if active and not change.active and re.fullmatch(lead,t,re.I):return {}
 # The existing/new gate has already established booking intent. A closed
 # date-only request needs no generation when the customer explicitly chooses
 # new. Do not use this for questions, conditions, corrections or mixed tasks.
 partial_lead=r'(?:(?:i|we) would like to (?:do|make)|(?:can|could) (?:i|we) (?:do|make)|please (?:do|make))\s+(?:a\s+)?(?:reservation|booking)'
 m=re.fullmatch(r'(?:(?:yeah|okay|ok|hi|hello)[, ]+)?(?:'+partial_lead+'|'+lead+r')\s+(?:for\s+)?(?P<date>'+DATE_ONLY+r')',t,re.I)
 if active and m and not change.active:
  return valid({'date':m['date']})
 m=re.fullmatch(lead+r'\s+(?:for\s+)?(?P<party>'+n+r')(?:\s+(?:people|guests|of us))?[,]?\s+(?P<date>'+DATE_ONLY+r')[,]?\s+(?:(?:at|around)\s+)?(?P<time>'+clock+r')',t,re.I)
 if m and not change.active:
  return valid({'party_size':party(m['party']),'date':m['date'],'time':m['time']})
 if change.active and change.action=='update':
  m=re.fullmatch(r'(?:no[, ]+)?(?:please\s+)?change\s+(?:the\s+)?time\s+to\s+(?P<time>'+clock+r')(?:\s+instead)?',t,re.I)
  if m:return valid({'time':m['time']})
  m=re.fullmatch(r'(?:please\s+)?(?:move|change|reschedule|update)\s+(?:(?:my|our|the|this)\s+)?(?:reservation|booking|table)\s+to\s+(?P<date>'+DATE_ONLY+r')\s+(?:at\s+)?(?P<time>'+clock+r')(?:,?\s+for\s+(?P<party>'+n+r')(?:\s+(?:people|guests))?)?',t,re.I)
  if m:
   result={'date':m['date'],'time':m['time']}
   if m['party']:result['party_size']=party(m['party'])
   return valid(result)
 return None
