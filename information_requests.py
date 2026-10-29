"""Information requests stay separate from booking mutations and identity collection."""
import re
import copy

TOPICS=r'\b(parking|park|menu|food|gluten|vegan|vegetarian|dish|coffee|dosa|drinks?|dessert|pets?|dogs?|cats?|kids?|children|corkage|dress|refund|weather)\b'
MONTH=r'jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?'
BOOK_COMMAND=r'\b(book|reserve|reschedule|cancel|update|modify|change|move)\b'
VISIT=r'\b(?:can|could|may|might|hoping|like|want)\b.*\b(?:come|visit|drop in|stop by|dine)\b'

def confirmation_control(text):
 return re.sub(r'[^a-z0-9\s]','',text.lower()).strip() in {'yes','yes please','no','no thanks','no thank you','correct','thats correct','yes that is correct','yes thats correct','yes correct','that is correct','confirm'}

def resume_reservation(text):
 t=text.lower().replace('’',"'").replace("let's",'let us').replace("i'd",'i would')
 t=' '.join(re.sub(r'[^a-z0-9\s]',' ',t).split())
 return bool(re.fullmatch(r'(?:(?:ok|okay|alright|yes|please)\s+)*(?:(?:let us|can we|could we|i want to|i would like to|id like to)\s+)?(?:go back to|get back to|return to|back to|resume|continue(?: with)?|finish)\s+(?:(?:my|our|the|that)\s+)?(?:reservation|booking)(?:\s+(?:now|please))*',t))

def readonly_info_intent(text):
 from routing import required_handoff
 if required_handoff(text) or re.search(BOOK_COMMAND,text,re.I):return None
 question=bool('?' in text or re.search(r'\b(what|where|when|how|can|could|may|do|does|is|are|tell)\b',text,re.I))
 if question and re.search(r'\b(parking|park|valet|address|directions|location|landmark)\b',text,re.I):return 'location_parking'
 if question and re.search(r'\b(hours|opening time|closing time)\b',text,re.I):return 'hours'
 if (question and re.search(r'\b(brunch|lunch|dinner)\b',text,re.I)
     and not re.search(r'\b(tables?|seats?|availability|available|free|room|space)\b',text,re.I)
     and re.search(r'\b(what time|when|start\w*|begin\w*|finish\w*|end\w*|serv(?:e|ed|ing|ice))\b',text,re.I)):
  return 'hours'
 return None

def pending_state(bot):
 c=bot.reservation_changes
 return copy.deepcopy((bot.reserving,bot.awaiting_confirmation,bot.slots,c.action,getattr(c,'pending',False),getattr(c,'current',None),getattr(c,'changes',None)))

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
 visit=bool(re.search(VISIT,t) and ('?' in t or re.search(r'\b(can|could|may|might)\b',t)))
 return (supply or visit) and not re.search(BOOK_COMMAND,t)

def broad_availability(text):
 from decision_evidence import date_phrases
 t=text.lower()
 if re.search(TOPICS,t) or re.search(r'\b(policies|policy|cancellations?|payment|corkage|dress|hold|late|delayed)\b',t) or late_policy_question(text):return False
 demand=availability_question(text) or bool(re.search(VISIT,t)) or bool(re.search(r'\b(tables?|reservations?|book(?:ing)?)\b',t))
 window=re.sub(r'\bmay\s+(?:i|we|you)\b','',t)
 wide=bool(re.search(r'\b(?:'+MONTH+r'|weeks?|weekends?|month|sometime|anytime|any time|whenever|coming days|next few days)\b',window))
 return demand and wide and not date_phrases(text)

def wants_information(bot,text):
 from policy_requests import policy_question
 if policy_question(text):return True
 if readonly_info_intent(text):return True
 from customer_intent import dietary_request
 if dietary_request(text):return True
 from routing import required_handoff
 if required_handoff(text):return False
 return bool(getattr(bot,'_dietary_followup',False) or late_policy_question(text) or broad_availability(text) or availability_question(text) or getattr(bot,'_availability_enquiry',None) is not None or getattr(bot,'_information_readback',None))

def remember_readback(bot):
 signature=pending_state(bot)
 if getattr(bot,'_information_readback',None) and getattr(bot,'_information_readback_state',None)==signature:return
 bot._information_readback=None
 change=bot.reservation_changes
 if bot.awaiting_confirmation or (change.active and change.pending):
  previous=getattr(bot,'_last_agent_reply','')
  if previous and re.search(r'correct|confirm',previous,re.I):bot._information_readback=previous
  elif change.active:
   row={**change.current,**change.changes}
   bot._information_readback=f"Please confirm: {change.action} the reservation for {row['party_size']} guests on {row['date']} at {row['time']}. Is that correct?"
  else:
   s=bot.slots;bot._information_readback=f"Please confirm your original request: table for {s['party_size']} on {s['date']} at {s['time']}, under {s['name']}. Is that correct?"
  bot._information_readback_state=signature

def information_turn(bot,text):
 from routing import Decision,required_handoff,keyword_intent,narrow_availability,repeat_or_handoff
 from policy_requests import policy_question
 if policy_question(text):
  remember_readback(bot);bot.failures=0
  bot.last_decision=Decision('policy','rule','policy_enquiry','Answer a restaurant policy question before collecting booking fields; preserve the draft.')
  return bot.menu_agent.turn(text,policy=True,direct=True)
 if required_handoff(text):return None
 info=readonly_info_intent(text)
 if info:
  remember_readback(bot);bot.failures=0
  return bot._dispatch(text,Decision(info,'rule','restaurant_information','Answer an explicit restaurant information question without changing the pending reservation.'),3)
 if getattr(bot,'_dietary_followup',False) and not getattr(bot,'_information_readback',None):
  if confirmation_control(text):
   if bot._dietary_followup=='confirm_gluten':
    choice=re.sub(r'[^a-z\s]','',text.lower()).strip()
    if choice in {'yes','yes please','correct','thats correct','yes that is correct','yes thats correct','yes correct','that is correct','confirm'}:
     from customer_intent import dietary_turn
     return dietary_turn(bot,'Do you have gluten-free options?')
    bot._dietary_followup=False
    return 'What dietary requirement would you like me to check?',[]
   bot.last_decision=Decision('menu','rule','clarify_dietary_followup','A yes/no answer to a choice of intents is ambiguous; do not start identity collection.')
   return 'Would you like more dietary information, help with a reservation, or to finish the conversation?',[]
  bot._dietary_followup=False
 if late_policy_question(text):
  remember_readback(bot);bot.failures=0
  bot.last_decision=Decision('policy','rule','late_arrival_question','Answer the restaurant late-arrival policy; preserve pending booking details.')
  query=text+' Late arrival grace period: how long tables are held after the reserved time.'
  return bot.menu_agent.turn(query,policy=True,direct=True)
 broad=broad_availability(text);explicit=availability_question(text)
 state=getattr(bot,'_availability_enquiry',None)
 if state is None and not confirmation_control(text) and (bot.reserving or bot.reservation_changes.active) and not broad and re.search(r'\b(instead|rather|make it|make that|change|correct|move|update|reschedule)\b',text,re.I):
  bot._availability_enquiry=None
  bot._information_readback=None
  bot._information_readback_state=None
  return None
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
   bot._availability_enquiry=None;bot._information_readback=None;return None
  if not broad and not explicit and (keyword_intent(text) in {'menu','policy','hours','location_parking'} or re.search(r'\b(who|weather|python|einstein)\b',text,re.I)):
   return None
  from decision_evidence import date_phrases,clock_phrases,negative_prefix
  from receptionist import parse_date,parse_time
  from customer_identity import local_today
  days=date_phrases(text);clocks=clock_phrases(text)
  if len(days)>1 or (len(days)==1 and negative_prefix(text[:days[0][0]])):state.pop('date',None)
  elif len(days)==1:state['date']=days[0][2]
  if len(clocks)>1 or (len(clocks)==1 and negative_prefix(text[:clocks[0][0]])):state.pop('time',None)
  elif len(clocks)==1:state['time']=clocks[0][2]
  counts=dict(zip('one two three four five six seven eight nine ten eleven twelve'.split(),range(1,13)))
  matches=list(re.finditer(r'\b(\d{1,2}|'+ '|'.join(counts)+r')\s+(?:people|guests?|of us)\b|\b(?:for|party of)\s+(\d{1,2}|'+ '|'.join(counts)+r')\b',text,re.I))
  positive=[match for match in matches if not negative_prefix(text[:match.start()])]
  if matches and len(positive)!=1:state.pop('party_size',None)
  if len(positive)==1:
   match=positive[0]
   raw=(match[1] or match[2]).lower();state['party_size']=int(raw) if raw.isdigit() else counts[raw]
  expected=getattr(bot,'_availability_expected',None)
  scalar=text.lower().strip().rstrip('.!?')
  recognized=bool(broad or explicit or days or clocks or matches)
  if not clocks:
   at=re.search(r'\b(?:at|around)\s+(\d{1,2}|'+ '|'.join(counts)+r')\b(?!\s+(?:people|guests))',text,re.I)
   if at:
    recognized=True
    if negative_prefix(text[:at.start()]):state.pop('time',None)
    else:state['time']=str(counts.get(at[1].lower(),at[1]))
   elif expected=='time' and (re.fullmatch(r'\d{1,4}',scalar) or scalar in counts):state['time']=str(counts.get(scalar,scalar));recognized=True
  if expected=='party_size' and (re.fullmatch(r'\d{1,2}',scalar) or scalar in counts):state['party_size']=int(scalar) if scalar.isdigit() else counts[scalar];recognized=True
  if not recognized:
   bot.last_decision=Decision('other','rule','unclear_availability_reply','Ask for repetition without altering pending booking details.')
   return repeat_or_handoff(bot)
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
   return f"Yes, a table for {state['party_size']} is available on {day.isoformat()} at {clock}.",['check_table']
  from receptionist import alternative_question
  return 'That requested time is unavailable. '+alternative_question(result.get('alternatives',[])),['check_table']
 readback=getattr(bot,'_information_readback',None)
 if readback:
  if getattr(bot,'_information_readback_state',None)!=pending_state(bot):
   bot._information_readback=None;bot._information_readback_state=None
   return None
  if confirmation_control(text) or resume_reservation(text):
   bot._dietary_followup=False
   bot._information_readback=None;bot._information_readback_state=None
   bot.last_decision=Decision('reservation','rule','resume_readback','Repeat the original readback after an information interruption before any write.')
   return readback,[]
 return None
