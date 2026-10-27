"""Information requests stay separate from booking mutations and identity collection."""
import re

TOPICS=r'\b(parking|park|menu|food|gluten|vegan|vegetarian|dish|coffee|dosa|drinks?|dessert|pets?|dogs?|cats?|kids?|children|corkage|dress|refund|weather)\b'
MONTH=r'jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?'
BOOK_COMMAND=r'\b(book|reserve|reschedule|cancel|update|modify|change|move)\b'

def late_policy_question(text):
 t=text.lower().replace('’',"'")
 if re.search(BOOK_COMMAND,t) or re.search(r'\b(open|closing|close|hours|latest)\b',t):return False
 if re.search(r"\b(food|order|delivery|payment|refund|corkage|dress|kitchen)\b|\b(?:not|never)\s+(?:be\s+)?late\b|\b(?:won't|will not)\s+be\s+late\b",t):return False
 question='?' in t or bool(re.search(r'\b(what if|how long|how late|will you|would you|can i|can we|do you|is it|grace|policy|allowed)\b',t))
 late=bool(re.search(r'\b(late|delayed|delay|running behind|behind schedule|past (?:my|our|the) (?:booking|reservation)|grace)\b|\bafter\s+(?:(?:my|our|the|scheduled|reserved|booked)\s+)*(?:reservation|booking|time)\b',t))
 holding=bool(re.search(r'\b(hold|held|keep|lose|give away|give up|wait)\b.*\b(tables?|bookings?|reservations?|seats?)|\b(tables?|bookings?|reservations?|seats?)\b.*\b(hold|held|wait)\b',t))
 arrival=bool(re.search(r'\b(arriv\w*|get there|show up|running|train|traffic|tables?|bookings?|reservations?|seats?)\b',t))
 return question and (bool(re.search(r'\bgrace period\b',t)) or (late and arrival) or (holding and bool(re.search(r'\b(how long|after|past|grace)\b',t))))

def availability_question(text):
 t=text.lower()
 if re.search(TOPICS,t) or re.search(r'\b(policy|late|delayed|opening hours|business hours)\b',t):return False
 supply=bool(re.search(r'\b(openings?|availability|vacanc\w*|slots?)\b|\banything\s+(?:free|available|open)|\b(?:any|enough)\s+(?:room|space|seats?|tables?|spots?)\b|\b(?:is|are) there\s+(?:any\s+)?(?:room|space|seats?|tables?|spots?)\b|\bchance of\b.*\b(?:room|space|seats?|tables?|spots?)\b|\b(?:tables?|seats?|space|room)\b.*\b(?:free|available|open)\b|\b(?:fit|squeeze)\b.*\b(?:us|me|people|guests)\b',t))
 visit=bool(re.search(r'\b(?:can|could|may|might|hoping|like|want)\b.*\b(?:come|visit|drop in|stop by|dine)\b',t))
 return (supply or visit) and not re.search(BOOK_COMMAND,t)

def broad_availability(text):
 from decision_evidence import date_phrases
 t=text.lower()
 if re.search(TOPICS,t) or re.search(r'\b(policies|policy|cancellations?|payment|corkage|dress|hold|late|delayed)\b',t) or late_policy_question(text):return False
 demand=availability_question(text) or bool(re.search(r'\b(tables?|reservations?|book(?:ing)?)\b',t))
 window=re.sub(r'\bmay\s+(?:i|we|you)\b','',t)
 wide=bool(re.search(r'\b(?:'+MONTH+r'|weeks?|weekends?|month|sometime|anytime|any time|whenever|coming days|next few days)\b',window))
 return demand and wide and not date_phrases(text)

def wants_information(bot,text):
 from routing import required_handoff
 if required_handoff(text):return False
 return bool(late_policy_question(text) or broad_availability(text) or availability_question(text) or getattr(bot,'_availability_enquiry',None) is not None or getattr(bot,'_information_readback',None))

def remember_readback(bot):
 if getattr(bot,'_information_readback',None):return
 change=bot.reservation_changes
 if bot.awaiting_confirmation or (change.active and change.pending):
  previous=getattr(bot,'_last_agent_reply','')
  if previous and re.search(r'correct|confirm',previous,re.I):bot._information_readback=previous
  elif change.active:
   row={**change.current,**change.changes}
   bot._information_readback=f"Please confirm: {change.action} the reservation for {row['party_size']} guests on {row['date']} at {row['time']}. Is that correct?"
  else:
   s=bot.slots;bot._information_readback=f"Please confirm your original request: table for {s['party_size']} on {s['date']} at {s['time']}, under {s['name']}. Is that correct?"

def information_turn(bot,text):
 from routing import Decision,required_handoff,keyword_intent,narrow_availability
 if required_handoff(text):return None
 if late_policy_question(text):
  remember_readback(bot);bot.failures=0
  bot.last_decision=Decision('policy','rule','late_arrival_question','Answer the restaurant late-arrival policy; preserve pending booking details.')
  query=text+' Late arrival grace period: how long tables are held after the reserved time.'
  return bot.menu_agent.turn(query,policy=True,direct=True)
 broad=broad_availability(text);explicit=availability_question(text)
 state=getattr(bot,'_availability_enquiry',None)
 if broad or explicit:
  remember_readback(bot)
  # A broad explicit booking still narrows before entering the existing booking flow.
  if re.search(BOOK_COMMAND,text,re.I):
   bot._availability_enquiry=None
   return narrow_availability(bot)
  if state is None:state={};bot._availability_enquiry=state
 if state is not None:
  # Other explicit tasks interrupt this clarification; never turn a policy/menu
  # question or cancellation into a fabricated availability field.
  if re.search(BOOK_COMMAND,text,re.I) and not broad:
   bot._availability_enquiry=None;return None
  if not broad and not explicit and (keyword_intent(text) in {'menu','policy','hours','location_parking'} or re.search(r'\b(who|weather|python|einstein)\b',text,re.I)):
   return None
  from decision_evidence import date_phrases,clock_phrases,negative_prefix
  from receptionist import parse_date,parse_time
  from customer_identity import local_today
  days=date_phrases(text);clocks=clock_phrases(text)
  if len(days)==1 and not negative_prefix(text[:days[0][0]]):state['date']=days[0][2]
  if len(clocks)==1 and not negative_prefix(text[:clocks[0][0]]):state['time']=clocks[0][2]
  counts=dict(zip('one two three four five six seven eight nine ten eleven twelve'.split(),range(1,13)))
  match=re.search(r'\b(\d{1,2}|'+ '|'.join(counts)+r')\s+(?:people|guests?|of us)\b|\b(?:for|party of)\s+(\d{1,2}|'+ '|'.join(counts)+r')\b',text,re.I)
  if match:
   raw=(match[1] or match[2]).lower();state['party_size']=int(raw) if raw.isdigit() else counts[raw]
  expected=getattr(bot,'_availability_expected',None)
  scalar=text.lower().strip().rstrip('.!?')
  if not clocks:
   at=re.search(r'\b(?:at|around)\s+(\d{1,2}|'+ '|'.join(counts)+r')\b(?!\s+(?:people|guests))',text,re.I)
   if at:state['time']=str(counts.get(at[1].lower(),at[1]))
   elif expected=='time' and (scalar.isdigit() or scalar in counts):state['time']=str(counts.get(scalar,scalar))
  if expected=='party_size' and (scalar.isdigit() or scalar in counts):state['party_size']=int(scalar) if scalar.isdigit() else counts[scalar]
  bot.failures=0;bot.last_decision=Decision('reservation','rule','availability_enquiry','Answer availability, not a booking or identity request.',fields=dict(state))
  if len(days)>1 or len(clocks)>1:return 'Please choose one date and one arrival time so I can check availability.',[]
  for field,prompt in [('date','Which specific date would you prefer?'),('time','Around what arrival time?'),('party_size','For how many people?')]:
   if field not in state:
    bot._availability_expected=field
    return (('Which date or short date range would you prefer, and around what time?' if broad and not state else prompt),[])
  day=parse_date(state['date'],today=local_today(bot.restaurant));clock=parse_time(state['time'])
  if day is None or clock is None:
   state.pop('date' if day is None else 'time',None)
   return 'Please give a valid specific date and arrival time.',[]
  if not 1<=state['party_size']<=bot.restaurant['seating']['max_party_size']:
   state.pop('party_size');bot._availability_expected='party_size'
   return 'Please give a party size within our reservation limit.',[]
  remember_readback(bot)
  result=bot.tools.check_table(state['party_size'],day.isoformat(),clock)
  bot._availability_enquiry=None;bot._availability_expected=None
  if result.get('available'):
   return f"Yes, a table for {state['party_size']} is available on {day.isoformat()} at {clock}. No reservation has been made.",['check_table']
  from receptionist import alternative_question
  return 'That requested time is unavailable. '+alternative_question(result.get('alternatives',[])),['check_table']
 readback=getattr(bot,'_information_readback',None)
 if readback:
  bot._information_readback=None
  if re.fullmatch(r'(?:yes(?: please)?|no(?: thanks)?|correct|confirm)[.!]?',text.strip(),re.I):
   bot.last_decision=Decision('reservation','rule','resume_readback','Repeat the original readback after an information interruption before any write.')
   return readback,[]
 return None
