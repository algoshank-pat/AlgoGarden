"""Dialogue controls preserve business state and never authorize a reservation write."""
import re
from routing import Decision

def normalized(text):return ' '.join(re.sub(r'[^a-z0-9\s]',' ',text.lower().replace('’',"'").replace("let's",'let us').replace("'",'')).split())
def clear_assent(text):return normalized(text) in {'yes','yes please','yeah','yep','correct','that is correct','thats correct','yes that is correct','yes thats correct','yes correct','confirm','that works','that works for me'}

def reservation_confirmation(text,action='create'):
 """Complete approval of a pending readback, not a general yes/intent rule.

 Full matching excludes conditions, corrections, negation and extra questions.
 Callers must still check the current workflow/confirmation state.
 """
 if '?' in text:return False
 if clear_assent(text):return True
 lead=r'(?:(?:yes|yeah|yep|okay|ok|sure)\s+)?'
 polite=r'(?:(?:please|go ahead and|please go ahead and)\s+)?'
 if action=='create':
  verb=r'(?:confirm|book|reserve|finalize)'
  target=r'(?:it|that|(?:(?:the|this|that|my|our)\s+)?(?:reservation|booking|table))'
 elif action=='update':
  verb=r'(?:confirm|approve)'
  target=r'(?:it|that|(?:(?:the|this|that|my|our)\s+)?(?:update|changes?|reservation|booking))'
 elif action=='cancel':
  verb=r'(?:confirm|approve)'
  target=r'(?:it|that|(?:(?:the|this|that|my|our)\s+)?cancellation)'
 else:return False
 command=rf'(?:{polite}{verb}|i (?:confirm|approve))(?:\s+{target})?'
 return bool(re.fullmatch(lead+command+r'(?:\s+(?:please|thanks|thank you))?',normalized(text)))

def name_assent(text):
 """Explicit, complete name approval; never general booking authorization."""
 t=normalized(text)
 return bool(re.fullmatch(r'(?:(?:yes|yeah|yep) )?(?:(?:that|the|my) name is (?:correct|right)|(?:that is|thats) (?:the correct name|my name)|you (?:got|have) (?:my|the) name (?:right|correct))(?: (?:thank you|thanks))?',t))

def booking_prompt(bot):
 c=bot.reservation_changes
 if c.active:
  if c.current and c.pending:
   s={**c.current,**c.changes}
   return f"Please confirm: {c.action} the reservation for {s['party_size']} on {s['date']} at {s['time']}. Is that correct?"
  if getattr(c,'offered_alternatives',None):
   from receptionist import alternative_question
   return alternative_question(c.offered_alternatives)
  if not c.current and len(getattr(c,'matches',[]))>1 and getattr(c,'selection_offered',False):
   options='; '.join(f"{i}. {row['date']} at {row['time']} for {row['party_size']} guests" for i,row in enumerate(c.matches,1))
   return f"I found multiple reservations: {options}. Which reservation do you mean? Please choose its number."
  if c.current and c.action=='lookup':return 'Would you like to modify or cancel your reservation?'
  return 'What date, time or party size would you like to change?' if c.current else 'What phone number was used for the reservation?'
 s=bot.slots
 if getattr(bot,'_existing_choice',False):return 'Would you like help with your existing reservation, or a separate booking?'
 if bot.awaiting_confirmation and all(k in s for k in ['name','phone','date','time','party_size']):
  return f"Table for {s['party_size']} on {s['date']} at {s['time']}, under {s['name']}. Is that correct?"
 if getattr(bot,'_offered_alternatives',None):
  from receptionist import alternative_question
  return alternative_question(bot._offered_alternatives)
 questions={'phone':'What phone number can I use for your reservation?','name':'May I have your name for the reservation?','party_size':'How many guests?','date':'What date would you like?','time':'What time would you like? Please include morning or evening.'}
 field=getattr(bot,'_awaiting_slot',None)
 if field not in questions or s.get(field):field=next((key for key in questions if not s.get(key)),None)
 if field:return questions[field]
 # Complete details with no pending approval follow a rejected readback.
 return 'What would you like to change: the name, phone number, number of guests, date or time?'

def handle(bot,text):
 t=normalized(text);active=bot.reserving or bot.reservation_changes.active
 if not active and t in {'hi','hello','hey','good morning','good afternoon','good evening'}:
  bot.last_decision=Decision('other','rule','greeting','Answer a simple greeting directly without inference.')
  return f"Thanks for calling {bot.restaurant['name']}; how can I help?",[]
 name_yes=bool(getattr(bot,'_name_candidate',None) and name_assent(text))
 repeat=bool(re.fullmatch(r'(?:please )?(?:say it|say that|repeat(?: that| it| the question)?|say (?:that|it) again|could you repeat(?: that)?|can you repeat(?: that)?|what did you say)(?: please)?',t))
 pause=bool(re.fullmatch(r'(?:(?:yes|yeah|okay|ok)\s+)?(?:wait(?: a moment| a minute| two minutes)?|hold on(?: a moment| a minute)?|give me (?:a moment|a minute|two minutes)|one moment)(?: please)?',t))
 from information_requests import resume_reservation
 resume=resume_reservation(text) or t in {'continue','resume','im back','i am back','okay continue','go ahead continue'}
 if bot.awaiting_confirmation and re.fullmatch(r'(?:(?:yeah|yes|okay) )?(?:i would like to|i want to|let us) (?:do|complete|finish|proceed with) (?:(?:that|the|my) )?(?:reservation|booking)(?: table for (?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve))?',t):
  from booking_evidence import explicit_fields
  values=explicit_fields(text)
  resume=all(bot.slots.get(k)==v for k,v in values.items())
 ambiguous_assent=bool((bot.awaiting_confirmation or getattr(bot,'_offered_alternatives',None)) and re.match(r'^(yes|yeah|yep)\b',t) and not clear_assent(text) and not name_yes)
 change=bot.reservation_changes
 pending_change=change.active and change.pending
 pending_create=bot.reserving and bot.awaiting_confirmation and not change.active and not getattr(bot,'_name_candidate',None)
 confirmation_alias=(pending_create or pending_change) and reservation_confirmation(text,change.action if pending_change else 'create')
 if active and getattr(bot,'_awaiting_slot',None)=='name':
  from name_spelling import spelled_components,spelled_name
  spelling=spelled_name(text,bot.slots.get('name'))
  parts=spelled_components(text) if spelling and spelling.pure else None
  if not parts and re.search(r'\b(first name|last name|surname)\b',text,re.I):
   from name_spelling import NATO,ALIASES
   words=re.findall(r'\b[a-z]+\b',text.lower())
   letter_evidence=sum(w in NATO or w in ALIASES or len(w)==1 for w in words)
   if letter_evidence>=2 or re.search(r'\bas in\b|\bspell',text,re.I):
    bot._name_candidate=None;bot._name_readback=None
    component='last' if re.search(r'last name|surname',text,re.I) else 'first'
    from routing import repeat_or_handoff
    reply,tools=repeat_or_handoff(bot)
    if tools:return reply,tools
    return reply+' Could you say your name again?',[]
  if parts:
   bot._paused=False
   known=getattr(bot,'_name_parts',None) or {};known.update(parts);bot._name_parts=known
   if not all(k in known for k in ['first','last']):return 'Thank you. What is your '+('last' if 'first' in known else 'first')+' name?',[]
   candidate=known['first']+' '+known['last'];bot._name_candidate=candidate
   bot._name_readback=f"I heard {candidate}. Is that name correct?"
   bot.last_decision=Decision('reservation','rule','verify_spelled_name','Read back explicitly spelled name and wait before using it.')
   return bot._name_readback,[]
 if not (repeat or pause or resume or ambiguous_assent or confirmation_alias or getattr(bot,'_name_candidate',None) or getattr(bot,'_paused',False)):return None
 if not active and not repeat:return None
 if repeat:
  bot._name_needs_readback=False
  reply=getattr(bot,'_name_readback',None) or (booking_prompt(bot) if getattr(bot,'_paused',False) else getattr(bot,'_last_agent_reply',None)) or booking_prompt(bot)
  bot.last_decision=Decision('reservation','rule','repeat_prompt','Repeat the current question without extracting identity or changing the draft.')
  return reply,[]
 if pause:
  bot._paused=True;bot.last_decision=Decision('reservation','rule','pause_conversation','Keep draft and confirmation state; wait for an explicit continuation.')
  return 'Of course. Take your time. Say "continue" when you are ready.',[]
 if ambiguous_assent and not resume and not confirmation_alias:
  # An information question prefixed with yes is not booking permission.
  # The read-only path answers it and saves the readback for later consent.
  from information_requests import readonly_info_intent
  from policy_requests import policy_question
  from customer_intent import dietary_request
  if readonly_info_intent(text) or policy_question(text) or dietary_request(text):return None
  bot.last_decision=Decision('reservation','rule','clarify_assent','A question or interruption after yes cannot accept an option or authorize a write.')
  if bot.awaiting_confirmation:return 'I have not booked it yet. '+booking_prompt(bot),[]
  return 'Would you like the suggested time? Please say yes, or tell me a different time.',[]
 if resume:
  bot._name_needs_readback=False
  bot._paused=False;bot.last_decision=Decision('reservation','rule','resume_conversation','Restore the current question or readback; do not commit on a resume request.')
  from information_requests import pending_state
  saved=getattr(bot,'_information_readback',None)
  if getattr(bot,'_information_readback_state',None)!=pending_state(bot):saved=None
  bot._information_readback=None;bot._information_readback_state=None;bot._dietary_followup=False
  return getattr(bot,'_name_readback',None) or saved or booking_prompt(bot),[]
 if getattr(bot,'_paused',False) and (clear_assent(text) or name_yes or confirmation_alias):
  bot._paused=False
  bot._name_needs_readback=False
  return getattr(bot,'_name_readback',None) or booking_prompt(bot),[]
 candidate=getattr(bot,'_name_candidate',None)
 if candidate and (clear_assent(text) or name_yes):
  if getattr(bot,'_name_needs_readback',False):
   bot._name_needs_readback=False
   return bot._name_readback,[]
  bot._name_candidate=None;bot._name_readback=None;bot._verified_name=candidate;bot._routed_fields={'name':candidate}
  bot.last_decision=Decision('reservation','rule','name_verified','Customer confirmed the decoded name; continue collecting the booking.')
  return bot._reservation('Name verified')
 if candidate and t in {'no','no thanks','not correct','thats wrong','that is wrong'}:
  bot._name_candidate=None;bot._name_readback=None;bot._name_parts={};bot._awaiting_slot='name'
  return 'Could you say your name again?',[]
 if confirmation_alias:
  if getattr(bot,'_information_readback',None):
   reply=booking_prompt(bot);bot._information_readback=None;bot._information_readback_state=None;bot._dietary_followup=False
   return reply,[]
  bot.last_trace_id=None
  bot.last_decision=Decision('reservation','rule','explicit_confirmation','Complete explicit approval of the current readback; preserve the scoped write boundary.')
  return change.turn('Yes') if pending_change else bot._reservation('Yes')
 return None
