"""Session-local navigation and committed booking receipts; never authorize writes."""
import re
from conversation_controls import normalized,booking_prompt
from routing import Decision


def acknowledgement(text):
    # A short /uh/ filler is sometimes transcribed as the article "a". This
    # normalization is only used for navigation, never write confirmation.
    t=re.sub(r'^(?:(?:uh|um|ah|well|a)\s+)+','',normalized(text))
    return bool(re.fullmatch(r'(?:(?:yes|yeah|yep|sure|okay|ok|alright|of course)(?:\s+|$))+(?:please|thanks|thank you)?',t))


def end_request(text):
    t=normalized(text)
    lead=r'(?:(?:okay|ok|alright|please|thanks|thank you|yes|yeah|sure)\s+)*'
    command=r'(?:end|stop|finish)\s+(?:(?:this|the)\s+)?(?:call|conversation|chat)|hang up|goodbye|bye'
    return bool(re.fullmatch(lead+r'(?:'+command+r')(?:\s+(?:please|thanks|thank you))?',t)
                or re.fullmatch(r'(?:no more questions\s+)?let us (?:be done|finish|end the call)',t))


def status_question(text):
    t=normalized(text)
    from policy_requests import policy_question
    from customer_intent import dietary_request
    if policy_question(text) or dietary_request(text):return False
    # Status inquiries must not swallow new bookings, corrections or commands.
    if re.search(r'\b(?:another|new|different|instead)\b|\b(?:and|but|also)\s+(?:please\s+)?(?:cancel|change|update|move|book|reserve)\b',t):return False
    noun=r'(?:table|seat|reservation|booking)'
    return bool(re.search(r'\b(?:did|have) you (?:already )?(?:book|booked|reserve|reserved|confirm|confirmed|save|saved)\b',t)
                or re.search(r'\b(?:did|have) you (?:make|made|got)\b.*\b'+noun+r'\b',t)
                or re.fullmatch(r'(?:(?:okay|ok|sure|please)\s+)*(?:are we (?:booked|all set)|am i booked|is (?:my|our|the) '+noun+r' (?:booked|reserved|confirmed|all set|saved|active)|did (?:it|the booking|the reservation) go through|what did we book|what time (?:am i|are we) booked(?: for)?|(?:can|could) you confirm (?:my|our|the) '+noun+r'|do (?:i|we) have (?:a|the) '+noun+r'(?: booked| reserved)?)',t)
                or (re.match(r'^(?:what|whats|which|how many)\b',t) and re.search(r'\b(?:my|our|i|we)\b',t) and re.search(r'\b(?:booked|reserved|reservation|booking)\b',t)))


def completed_booking(session):
    bot=session.bot
    return bool(any(r['business_id']==bot.tools.business_id for r in session.reservation_receipts)
                and not bot.reserving and not bot.reservation_changes.active
                and session.pending_reservation_request is None)


def repeated_confirmation(text):
    """A closed approval of the existing request cannot create a new booking."""
    t=normalized(text)
    lead=r'(?:(?:yes|yeah|yep|okay|ok|sure|please|go ahead|and|then)\s+)*'
    lead+=r'(?:(?:can you|could you|would you|i would like to|id like to|i want to)\s+)?'
    approve=r'(?:book|reserve|confirm|save|finalize)(?:\s+(?:it|that|the table|that table|the reservation|my reservation|the booking|my booking))?'
    settled=r'(?:i|we|you)\s+(?:(?:have|had)\s+)?(?:already|just)\s+(?:confirmed|booked|reserved)(?:\s+(?:it|that|the table|my reservation|the reservation|the booking))?'
    assent=r'(?:that is|thats|that looks|it is|its) (?:correct|right|fine)|confirmed|i confirm'
    return bool(re.fullmatch(lead+r'(?:'+approve+'|'+settled+'|'+assent+r')(?:\s+(?:please|thanks|thank you|now))*',t))


def follow_up_question(session):
    if completed_booking(session):return ' Do you have any other questions?'
    if getattr(session.bot,'_dietary_followup',False):return ' Would you like more dietary information, or help with a reservation?'
    return ' Is there anything else I can help you with?'


def post_confirmation_request(session,text):
    """Completed is a dialogue phase: a model cannot silently reopen creation."""
    if session.bot.ended or not completed_booking(session):return None
    from reservation_changes import change_intent
    from routing import keyword_intent
    t=normalized(text)
    pending=session.separate_booking_request
    if pending:
        session.separate_booking_request=None
        if acknowledgement(text) or re.fullmatch(r'(?:a |the )?(?:new|another|separate|additional) (?:one|booking|reservation|table)',t):
            session.bot.reserving=True
            session.awaiting_more_help=False
            return session.bot.turn(pending)
        # An interruption or refusal cannot approve the pending new request.
    if change_intent(text) or status_question(text) or repeated_confirmation(text):return None
    if keyword_intent(text)!='reservation':return None
    if re.search(r'\b(?:new|another|separate|additional|second|extra)\s+(?:table|reservation|booking)\b',t):return None
    if not re.search(r'\b(?:book|reserve|reservation|booking)\b',t):return None
    result=reservation_status(session,'Is my reservation confirmed?')
    if result is None:return None
    reply,tools=result
    reply=reply.removesuffix(' Is there anything else I can help you with?')
    creation=bool(re.match(r'(?:(?:yes|yeah|please)\s+)*(?:(?:(?:can|could|would) (?:i|we|you)|(?:i|we) (?:would like|want|need) to|id like to)\s+)?(?:book|reserve|make (?:a |the )?(?:reservation|booking))\b',t))
    if not creation:return reply+' Do you have any other questions?',tools
    session.separate_booking_request=text
    return reply+' Is this a separate booking?',tools


def remember_committed(session,calls):
    """Only successful tool receipts can establish completed-booking memory."""
    business=session.bot.tools.business_id
    for call in calls:
        output=call.get('output')
        if not isinstance(output,dict):continue
        if call.get('component')=='book_table' and output.get('booked') is True:
            row=output
        elif call.get('component')=='change_reservation' and output.get('changed') is True:
            row=output.get('reservation') or {}
        else:continue
        if not row.get('id') or not row.get('phone') or row.get('business_id')!=business:continue
        receipt={k:row[k] for k in ['id','phone','business_id']}
        session.reservation_receipts=[r for r in session.reservation_receipts if r['id']!=receipt['id']]
        session.reservation_receipts.append(receipt)
        session.reservation_receipts=session.reservation_receipts[-8:]
        session.bot._has_saved_reservation=True
        if not session.bot.reserving and not session.bot.reservation_changes.active:
            session.awaiting_reservation_return=False
            session.bot._information_readback=None;session.bot._information_readback_state=None
            session.bot._dietary_followup=False


def reservation_status(session,text):
    bot=session.bot
    if bot.ended:return None
    if bot.reservation_changes.active and bot.reservation_changes.action=='lookup':return None
    from information_requests import resume_reservation
    completed_return=bool(session.reservation_receipts and not (bot.reserving or bot.reservation_changes.active) and resume_reservation(text))
    repeated=completed_booking(session) and repeated_confirmation(text) and not status_question(text)
    if not (status_question(text) or completed_return or repeated):return None
    if bot.reserving or bot.reservation_changes.active:
        from information_requests import remember_readback
        remember_readback(bot)
        bot.menu_agent.last_sources=[]
        # The response below itself presents the current question/readback.
        # Do not keep an older policy-interruption guard that repeats it again.
        bot._information_readback=None;bot._information_readback_state=None
        bot._dietary_followup=False;bot._name_needs_readback=False
        bot.last_decision=Decision('reservation','rule','pending_booking_status','Describe the saved draft without committing it or restarting identity collection.')
        session.awaiting_reservation_return=False
        prefix='Your requested changes have not been saved yet. ' if bot.reservation_changes.active else 'Nothing has been saved for this request yet. '
        return prefix+(getattr(bot,'_name_readback',None) or booking_prompt(bot)),[]
    receipts=[r for r in session.reservation_receipts if r['business_id']==bot.tools.business_id]
    if not receipts:return None
    bot.last_trace_id=None
    bot.menu_agent.last_sources=[]
    bot.last_decision=Decision('reservation','rule','current_call_booking_status','Read current scoped records using successful receipts from this call; never create a second booking.')
    records=[]
    for receipt in receipts:
        result=bot.tools.lookup_reservation(receipt['id'],receipt['phone'])
        if result.get('found'):records.append(result['reservation'])
    session.awaiting_more_help=True
    session.awaiting_reservation_return=False
    if not records:return "I couldn't verify the reservation saved in this call. I haven't changed or booked anything else.",['lookup_reservation']
    if len(records)>1:
        options='; '.join(f"{i+1}: {r['party_size']} guests on {r['date']} at {r['time']} ({r['status']})" for i,r in enumerate(records))
        return 'These are the saved reservations from this call: '+options+'. Is there anything else I can help you with?',['lookup_reservation']
    row=records[0]
    detail=f"table for {row['party_size']} on {row['date']} at {row['time']}, under {row['name']}"
    if row['status']!='active':return 'That reservation is cancelled: '+detail+'. No new booking has been made.',['lookup_reservation']
    if repeated:return 'Your reservation is already confirmed. Do you have any other questions?',['lookup_reservation']
    return 'Your '+detail+' is confirmed. Is there anything else I can help you with?',['lookup_reservation']
