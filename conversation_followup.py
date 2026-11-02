"""Code-owned navigation after an answer; never authorization for a write."""
import re
from conversation_controls import normalized

QUESTION='Do you have any other questions?'
INVITE='Go ahead. What else would you like to ask?'
BOOKING_OFFER='Would you like help with a reservation?'


def booking_offer_turn(session,text):
    """Consent to an offered task starts navigation, never a reservation write."""
    if not session.awaiting_booking_offer:return None
    from conversation_memory import acknowledgement
    accepted=acknowledgement(text)
    declined=no_more_questions(text)
    session.awaiting_booking_offer=False
    if not accepted and not declined:return None
    session.awaiting_more_help=session.awaiting_additional_question=False
    session.conversation_wait=None
    if declined:
        from customer_feedback import offer
        return offer(session)
    if pending_work(session):return resume_pending(session)
    from conversation_memory import completed_booking,reservation_status
    if completed_booking(session):return reservation_status(session,'Is my reservation confirmed?')
    from booking_context import selected_booking_request
    selected=selected_booking_request(session.bot,'Book that table')
    if session.channel=='voice':
        from chat_api import voice_reservation_turn
        return voice_reservation_turn(session,selected or 'I would like to book a table.')
    session.bot.reserving=True
    return session.bot.turn(selected or 'Continue.')

def staff_offer_turn(session,text):
    """Resolve consent to the preceding staff offer, not a policy question."""
    if not session.awaiting_staff_offer:return None
    from conversation_memory import acknowledgement
    from customer_intent import ADDITIONAL_QUESTIONS_REPLY
    from routing import Decision
    declined=bool(re.fullmatch(r'(?:no|nope|nah|not now|not right now|rather not|no staff(?: help)?)(?: (?:please|thanks|thank you))?',normalized(text)))
    accepted=acknowledgement(text)
    if not declined and no_more_questions(text):
        session.awaiting_staff_offer=False
        if pending_work(session):return resume_pending(session)
        from customer_feedback import offer
        session.bot.last_trace_id=None;session.bot.menu_agent.last_sources=[]
        session.bot.last_decision=Decision('other','rule','customer_feedback','Caller finished questions after declining staff help; offer optional feedback.')
        return offer(session)
    if not declined and not accepted:return None
    session.awaiting_staff_offer=False
    session.awaiting_more_help=session.awaiting_reservation_return=False
    session.awaiting_additional_question=declined
    session.wait_return_gate=False;session.conversation_wait=None
    b=session.bot;b.failures=session.voice_failures=0
    b.last_trace_id=None;b.menu_agent.last_sources=[]
    b.last_decision=Decision('other' if declined else 'human','rule','staff_offer_declined' if declined else 'staff_offer_accepted','Respond to staff-help consent without re-running policy retrieval or changing reservations.')
    if declined:return ADDITIONAL_QUESTIONS_REPLY,[]
    b.tools.transfer_to_human(reason='Customer accepted staff help for an unanswered policy question.')
    return 'Please contact the restaurant directly for staff help.',['transfer_to_human']


def no_more_questions(text):
    t=normalized(text)
    return bool(re.fullmatch(
        r'(?:(?:no|nope|nah)(?: thanks| thank you)?(?: that is all| thats all| thats it| nothing else)?'
        r'|(?:no (?:(?:more|other|further|additional) )?questions|nothing else|thats all|that is all|thats it|im all set|thanks|thank you)'
        r'|(?:no )?(?:im|i am|were|we are) (?:good|all set)|that answers my questions'
        r'|(?:no )?(?:i|we) (?:have no |dont have (?:any )?|do not have (?:any )?)(?:more |other |further |additional )?questions)'
        r'(?: (?:thanks|thank you|for now))?',t))

def pending_work(session):
    b=session.bot
    return bool(b.reserving or b.reservation_changes.active or session.pending_reservation_request is not None
                or getattr(b,'_availability_enquiry',None) is not None)

def resume_pending(session):
    """Repeat the appropriate question/readback, without consuming consent."""
    from routing import Decision
    session.awaiting_more_help=session.awaiting_additional_question=session.awaiting_reservation_return=False
    session.wait_return_gate=False
    session.conversation_wait=None
    b=session.bot
    b._dietary_followup=False
    availability=getattr(b,'_availability_enquiry',None)
    if availability is not None:
        # Availability is an unfinished read-only request, not consent to book.
        # Return its missing field without checking capacity or collecting ID.
        for field,prompt in [('date','Which specific date would you prefer?'),('time','Around what arrival time?'),('party_size','For how many people?')]:
            if not availability.get(field):
                b._availability_expected=field
                b.last_trace_id=None
                b.last_decision=Decision('reservation','rule','resume_availability','Resume the missing availability detail without lookup, inference or a reservation write.')
                return prompt,[]
        return 'Which date or time would you like me to check?',[]
    if session.pending_reservation_request is not None:
        b.last_trace_id=None
        b.last_decision=Decision('reservation','rule','resume_existing_new_gate','Resume the saved request; an answer about further questions is not an existing-reservation answer.')
        return 'Returning to your reservation. Do you already have a reservation with us?',[]
    if b.reserving or b.reservation_changes.active:
        return b.turn('Continue.')
    return None

def after_answer(session,reply,tools):
    """One follow-up for completed answers, none for missing-field questions."""
    from routing import IRRELEVANT_REPLY
    from conversation_waits import schedule
    b=session.bot
    # A lookup can return a cancellation readback ending in a period. It is
    # still a confirmation question, not a completed information answer.
    # Do not append a second question that steals the caller's next "yes".
    change=b.reservation_changes
    if (b.awaiting_confirmation or getattr(change,'pending',False)) and (
            'Please say yes to confirm.' in reply or reply.endswith('Is that correct?')):
        session.awaiting_more_help=session.awaiting_additional_question=session.awaiting_reservation_return=False
        session.wait_return_gate=False
        session.conversation_wait=None
        return reply
    if b.ended or session.awaiting_restart or session.survey['status'] in {'offered','awaiting_comment','awaiting_rating'} or 'transfer_to_human' in tools:
        return reply
    completed=reply.startswith(('Your reservation is confirmed.','Your table is booked','Your reservation is updated','Your reservation has been cancelled'))
    answered=bool(set(tools)&{'get_hours','get_location','get_menu','check_dish','get_specials','search_knowledge','check_table','lookup_reservation'})
    refusal=reply==IRRELEVANT_REPLY
    dietary=getattr(b,'_dietary_followup',False) is True
    if not (completed or answered or refusal or dietary):return reply
    # Existing question suffixes are navigation, not part of the grounded facts.
    suffixes=(QUESTION,'Do you have any questions?','Is there anything else I can help you with?',
              'Would you like more dietary information, or help with a reservation?',
              'Any other questions, or shall we return to your reservation?')
    while True:
        suffix=next((s for s in suffixes if reply.endswith(s)),None)
        if not suffix:break
        reply=reply[:-len(suffix)].rstrip()
    # Keep explicit readbacks, slot alternatives and genuine clarifications.
    if reply.endswith('?'):return reply
    session.awaiting_more_help=True
    session.awaiting_additional_question=False
    session.awaiting_reservation_return=pending_work(session)
    session.wait_return_gate=False
    b._dietary_followup=False
    if session.awaiting_reservation_return:
        schedule(session,'interruption')
    elif not session.reservation_receipts and not session.booking_offer_made:
        # A questions-only caller has no draft to resume. Offer a reservation
        # after silence instead of waiting forever or inventing booking intent.
        schedule(session,'reservation_offer')
    else:
        session.conversation_wait=None
    return reply+' '+QUESTION
