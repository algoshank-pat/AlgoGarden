"""Session-local wait capabilities. Timeouts navigate; they never authorize writes."""
import re,time
from uuid import uuid4
from routing import Decision

WAIT='Okay, I can wait for 20 seconds.'
FOLLOWUP='Is there anything else I can help you with?'
clock=time.monotonic

def schedule(session,kind):
    session.conversation_wait={'token':str(uuid4()),'kind':kind,'delay_ms':20000 if kind=='hesitation' else 15000,'deadline':None}

def control(session,text):
    bot=session.bot
    from customer_intent import abandon_draft
    from conversation_controls import clear_assent
    from customer_intent import declined_booking
    pending_cancel=bool((bot.reserving or session.pending_reservation_request is not None) and not bot.reservation_changes.active and re.fullmatch(r'\s*(?:please )?cancel (?:it|that|that table|this booking|this reservation)[.!]?\s*',text,re.I))
    if pending_cancel or declined_booking(text) or re.search(r"\b(?:do not|don't|dont|not to)\s+(?:go ahead with\s+)?(?:book|reserve)\b",text,re.I):
        abandon_draft(bot);session.pending_reservation_request=None
        session.awaiting_reservation_return=session.awaiting_more_help=False
        session.wait_return_gate=False
        return 'Alright. I will not go ahead with the reservation.',[]
    from conversation_followup import pending_work
    active=pending_work(session)
    # Explicit instructions to disable guards are data, never assent.
    if re.search(r'\b(?:system override|ignore (?:all |the |your |previous )*(?:rules|instructions)|skip (?:the )?confirmation|bypass (?:the )?(?:confirmation|rules)|pretend (?:i|we) (?:confirmed|said yes))\b',text,re.I):
        bot.last_decision=Decision('other','rule','confirmation_guard','Customer text cannot override policy or confirmation requirements.')
        return 'I need your explicit confirmation of the reservation details before making a booking.',[]
    if active and (re.fullmatch(r'\s*(?:wait|one moment|take your time)[.!]?\s*',text,re.I) or re.search(r'\b(?:maybe|not (?:yet|sure|decided)|need (?:some |more )?time|need to (?:check|decide|think)|check with (?:my|our)|hold on|give me (?:a |one )?(?:moment|minute)|let me (?:think|decide|check)|wait (?:a |one )?(?:moment|minute))\b',text,re.I)):
        schedule(session,'hesitation')
        bot.last_decision=Decision('reservation','rule','wait_for_customer','Wait without confirming or cancelling the draft.')
        return WAIT,[]
    # Finishing side questions is navigation even if the last answer was a
    # readback. It never means confirm/cancel or an unrelated policy request.
    from conversation_followup import no_more_questions,resume_pending
    if (active and not session.awaiting_restart and not bot.ended
            and session.survey['status'] not in {'offered','awaiting_comment','awaiting_rating'}
            and re.search(r'\bquestions\b',text,re.I) and no_more_questions(text)):
        return resume_pending(session)
    if session.wait_return_gate:
        session.wait_return_gate=False
        if clear_assent(text) or re.fullmatch(r'\s*(?:no|no thanks|no thank you)[.!]?\s*',text,re.I):
            session.awaiting_reservation_return=False
            if session.pending_reservation_request is not None:return None
            if bot.reserving or bot.reservation_changes.active:
                # Returning acknowledgement is navigation, not final consent.
                return bot.turn('Continue.')
            if session.reservation_receipts:
                from conversation_memory import reservation_status
                return reservation_status(session,'Is my reservation confirmed?')
            return 'Do you have a reservation with us?',[]
    return None

def expire(session,token,now=None):
    wait=session.conversation_wait
    if not wait or wait['token']!=token:raise ValueError('This wait is no longer active.')
    if wait['deadline'] is None or (clock() if now is None else now)<wait['deadline']:
        raise ValueError('This wait has not reached its deadline.')
    session.conversation_wait=None
    session.bot.last_trace_id=None
    session.bot.last_decision=Decision('reservation','rule','wait_elapsed','Timeout is navigation only; no business tools or reservation write.')
    if wait['kind']=='hesitation':
        # Keep the confirmation boundary; silence takes no action.
        return 'Can you please confirm the reservation, or should I cancel it?',[]
    if wait['kind']=='reservation_offer':
        from conversation_followup import pending_work,BOOKING_OFFER
        if not pending_work(session) and not session.reservation_receipts:
            session.awaiting_more_help=session.awaiting_additional_question=False
            session.awaiting_booking_offer=session.booking_offer_made=True
            return BOOKING_OFFER,[]
    from conversation_followup import resume_pending,QUESTION
    resumed=resume_pending(session)
    if resumed is not None:return resumed
    session.awaiting_more_help=True
    return QUESTION,[]
