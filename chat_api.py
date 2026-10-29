"""Local, single-worker API for the customer chat channel."""
import logging
import asyncio
import sqlite3
import os
import time
import re
from dataclasses import asdict
from dataclasses import dataclass, field
from threading import Lock
from uuid import UUID, uuid4
from typing import Literal

import ollama
import local_audit as audit
import receptionist as engine
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from concurrency import QueueBusy, configured_scheduler
from reservation_schedule import day_plan
from customer_identity import local_today, local_now
from routing import Decision, keyword_intent, required_handoff, incomplete_request, INCOMPLETE_REPLY, repeat_or_handoff, irrelevant_question, IRRELEVANT_REPLY, broad_availability_question, narrow_availability
from reservation_changes import change_intent

from receptionist import DEFAULT_MODEL, HERE, Receptionist, load_restaurant

from contextlib import asynccontextmanager
from browser_voice import router as browser_voice_router, close_audio

@asynccontextmanager
async def lifespan(app):
    try:
        yield
    finally:
        try:close_audio()
        finally:
            from reservation_mcp_client import close_mcp_connections
            await asyncio.to_thread(close_mcp_connections)

app = FastAPI(title='Restaurant customer chat', lifespan=lifespan)
app.include_router(browser_voice_router)
from voice_test_jobs import router as voice_test_router
app.include_router(voice_test_router)
restaurant = load_restaurant(HERE / 'restaurants' / 'demo_restaurant.yaml')
MODEL = os.getenv('RECEPTIONIST_MODEL', DEFAULT_MODEL)
TTL_SECONDS = 3600
MAX_SESSIONS = 100
MAX_TURNS = 100
lock = Lock()  # Session registry only; never held during model work.
scheduler = configured_scheduler()


@dataclass
class Session:
    bot: Receptionist
    touched: float = field(default_factory=time.monotonic)
    replies: dict = field(default_factory=dict)
    turn_lock: object = field(default_factory=Lock)
    channel: str = 'chat'
    voice_failures: int = 0
    awaiting_more_help: bool = False
    farewell: str = 'Thank you so much for calling. Have a good day!'
    call_started_at: str | None = None
    pending_reservation_request: str | None = None
    stopped: bool = False
    awaiting_restart: bool = False
    survey: dict = field(default_factory=lambda: {'status':'not_offered','rating':None})


sessions: dict[str, Session] = {}


class TurnRequest(BaseModel):
    request_id: UUID
    text: str = Field(min_length=1, max_length=2000)
    input_kind: Literal['text', 'unrecognized_audio'] = 'text'


class CreateSessionRequest(BaseModel):
    channel: Literal['chat', 'voice'] = 'chat'


@app.post('/api/sessions/{session_id}/feedback/voice-notes/{note_id}')
async def feedback_voice_note(session_id:UUID,note_id:UUID,request:Request):
    import feedback_audio
    data=bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data)>feedback_audio.MAX_AUDIO:raise HTTPException(413,'Voice note must be at most 30 seconds.')
    return await asyncio.to_thread(feedback_audio.save,session_id,note_id,bytes(data))


@app.get('/api/sessions/{session_id}/feedback/voice-notes/{note_id}')
def read_feedback_voice_note(session_id:UUID,note_id:UUID):
    from feedback_audio import read
    return read(session_id,note_id)


def new_bot() -> Receptionist:
    bot = Receptionist(restaurant, MODEL, chat=ollama.Client(timeout=90).chat)
    bot.menu_agent.chat = bot.chat
    return bot


@app.post('/api/voice/warmup')
def warm_voice_model():
    started=time.perf_counter()
    response=ollama.Client(timeout=120).chat(model=MODEL,messages=[],think=False,keep_alive=os.getenv('OLLAMA_KEEP_ALIVE','30m'),
        options={'num_ctx':int(os.getenv('OLLAMA_NUM_CTX','2048')),'num_predict':1})
    return {'ready':True,'model':MODEL,'elapsed_ms':round((time.perf_counter()-started)*1000)}


@app.get('/api/restaurant')
def restaurant_info() -> dict:
    return {'name': restaurant['name'], 'phone': restaurant.get('phone'), 'channel': 'chat', 'currency':restaurant.get('currency','USD'), 'menu':restaurant.get('menu',[]), 'specials':restaurant.get('specials_of_the_day',[]), 'address':restaurant.get('address',''), 'hours':restaurant.get('hours',{}), 'timezone':restaurant.get('timezone','UTC')}


@app.post('/api/sessions', status_code=201)
def create_session(request: CreateSessionRequest | None = None) -> dict:
    with lock:
        now = time.monotonic()
        for key in list(sessions):
            if now - sessions[key].touched > TTL_SECONDS and not sessions[key].turn_lock.locked():
                del sessions[key]
        if len(sessions) >= MAX_SESSIONS:
            raise HTTPException(503, 'Chat is busy; please try again later.')
        session_id = str(uuid4())
        arrived_at=restaurant_now()
        sessions[session_id] = Session(new_bot(), channel=request.channel if request else 'chat',
                                       farewell=voice_farewell(arrived_at),call_started_at=arrived_at.isoformat())
        sessions[session_id].bot.trace_session_id = session_id
        if hasattr(sessions[session_id].bot, 'tools'):
            sessions[session_id].bot.tools.session_id = session_id
            sessions[session_id].bot.tools.booking_source = 'phone' if sessions[session_id].channel=='voice' else 'chat'
        return {'session_id': session_id,'call_started_at':sessions[session_id].call_started_at,
                'timezone':restaurant.get('timezone','Asia/Kolkata'),
                'greeting': (f"Thank you for calling {restaurant['name']}. How can I help you today?" if sessions[session_id].channel=='voice'
                             else f"Thanks for contacting {restaurant['name']}. How can I help?")}


@app.delete('/api/sessions/{session_id}', status_code=204)
def delete_session(session_id: UUID):
    with lock:
        session = sessions.get(str(session_id))
        if session and session.turn_lock.locked():
            raise HTTPException(409, 'A turn is in progress; wait before ending this conversation.')
        sessions.pop(str(session_id), None)


@app.post('/api/sessions/{session_id}/stop')
def stop_session(session_id: UUID):
    with lock:
        session=sessions.get(str(session_id))
        if session is None:raise HTTPException(404,'Session not found.')
        session.stopped=True
        return {'stopped':True,'turn_in_progress':session.turn_lock.locked(),
                'message':'Conversation stopped. A request already processing may still complete. Saved reservations remain unchanged.'}


@app.post('/api/sessions/{session_id}/turns')
def send_turn(session_id: UUID, request: TurnRequest) -> dict:
    if not request.text.strip():
        raise HTTPException(422, 'Please enter a message.')
    with lock:
        session = sessions.get(str(session_id))
        if session is None or (time.monotonic() - session.touched > TTL_SECONDS and not session.turn_lock.locked()):
            sessions.pop(str(session_id), None)
            raise HTTPException(404, 'Conversation expired; start a new chat.')
        if request.input_kind == 'unrecognized_audio' and session.channel != 'voice':
            raise HTTPException(422, 'Audio status is only supported in voice sessions.')
        if session.stopped:
            raise HTTPException(409, 'Conversation stopped; start a new conversation.')
        if not session.turn_lock.acquire(blocking=False):
            raise HTTPException(409, 'This conversation already has a turn in progress; retry after it finishes.', headers={'Retry-After': '2'})
    try:
        return _session_turn(session_id, session, request)
    finally:
        session.turn_lock.release()


def _session_turn(session_id: UUID, session: Session, request: TurnRequest) -> dict:
    # The per-session lock is held; the registry lock is not.
    try:
        return _admit_turn(session_id, session, request)
    except QueueBusy as error:
        session.touched = time.monotonic()
        raise HTTPException(503, str(error), headers={'Retry-After': '2'}) from error


def _admit_turn(session_id: UUID, session: Session, request: TurnRequest) -> dict:
    key = str(request.request_id)
    if key in session.replies:
        old_input, result = session.replies[key]
        if old_input != (request.text, request.input_kind):
            raise HTTPException(409, 'A message identifier cannot be reused for different text.')
        return result
    if len(session.replies) >= MAX_TURNS:
        raise HTTPException(409, 'Conversation limit reached; start a new chat.')
    with scheduler.slot() as queue_wait_ms:
        return _execute_turn(session_id, session, request, queue_wait_ms)


def explicit_booking_command(text):
    return bool(re.match(r'^\s*(?:actually[, ]+)?(?:(?:please|(?:can|could) (?:i|we|you)|(?:i|we) (?:want|need|would like) to)\s+)?(?:book|reserve)\b',text,re.I))


def voice_reservation_turn(session, text):
    """Ask the existing/new question before the voice booking workflow starts."""
    bot=session.bot
    choice=re.sub(r'[^a-z\s]','',text.lower()).strip()
    if required_handoff(text):
        session.pending_reservation_request=None
        return bot.turn(text)
    if session.pending_reservation_request is not None:
        if change_intent(text) or explicit_booking_command(text):
            session.pending_reservation_request=None
            bot._availability_enquiry=None
            return voice_reservation_turn(session,text)
        if choice in {'cancel','stop','never mind','nevermind'}:
            session.pending_reservation_request=None
            return 'I’ve stopped this booking request; no reservation was made.',[]
        # An unfamiliar information question must also be able to interrupt the
        # gate. Share the semantic decision with bot.turn, rather than infer twice.
        from information_requests import confirmation_control
        if bot.unified_decisions and not confirmation_control(text) and re.search(r'\?|\b(?:what|how|which|whether|do you|does|is there|are there)\b',text,re.I):
            from decision_engine import turn_context,decide
            with turn_context(bot,text):
                value=decide(bot,text)
                if value['relevance']=='relevant' and value['intent'] in {'policy','menu','hours','location_parking'}:
                    reply,tools=bot.turn(text)
                    return reply+' Returning to your original request, do you have a reservation with us?',tools
        yes=bool(re.match(r'^(yes|yeah|yep|i do)\b',choice))
        no=bool(re.match(r'^(no|nope|i dont|i do not)\b',choice))
        if yes and not no:
            session.pending_reservation_request=None
            bot.slots.clear()
            bot.reserving=bot.awaiting_confirmation=False
            bot.reservation_changes.begin('lookup')
            bot.last_trace_id=None
            bot.last_decision=Decision('reservation','rule',reasoning='Caller has an existing reservation; request phone for MCP lookup.')
            audit.event('reservation.existing_selected',{})
            return 'What phone number was used for the reservation?',[]
        if no:
            original=session.pending_reservation_request
            session.pending_reservation_request=None
            audit.event('reservation.new_selected',{})
            # The customer chose creation explicitly; keep this in the booking
            # workflow rather than reclassifying their negative answer.
            bot.reserving=True
            bot._dietary_followup=False
            reply,tools=bot.turn(original+'\n'+text)
            if reply=='What phone number should we use to check for an existing reservation?':
                reply='What phone number can I use for your reservation?'
            if not bot.ended:
                reply='Lemme help you with the reservation. '+reply
            return reply,tools
        return repeat_or_handoff(bot)
    if (keyword_intent(text)=='reservation' and not change_intent(text)
            and not getattr(bot,'reserving',False) and not bot.reservation_changes.active):
        session.pending_reservation_request=text
        bot.last_trace_id=None
        bot.last_decision=Decision('reservation','rule',reasoning='Ask whether to look up an existing reservation or create a new one.')
        audit.event('reservation.existing_or_new_asked',{})
        return 'Do you have a reservation with us?',[]
    return bot.turn(text)


def conversation_restart(session_id, session, text):
    choice=re.sub(r'[^a-z\s]','',text.lower()).strip()
    if session.awaiting_restart:
        if re.match(r'^(yes|yeah|yep|sure|confirm|please do|go ahead)\b',choice):
            session.bot=new_bot()
            session.bot.trace_session_id=str(session_id)
            if hasattr(session.bot,'tools'):
                session.bot.tools.session_id=str(session_id)
                session.bot.tools.booking_source='phone' if session.channel=='voice' else 'chat'
            session.pending_reservation_request=None
            session.awaiting_more_help=False
            session.voice_failures=0
            session.awaiting_restart=False
            session.bot.last_trace_id=None
            session.bot.last_decision=Decision('other','rule',reasoning='Customer confirmed restarting; discard pending conversation details only.')
            audit.event('conversation.restarted',{'saved_reservations_unchanged':True})
            return f"Let’s start again. Thank you for {'calling' if session.channel=='voice' else 'contacting'} {restaurant['name']}. How can I help you today?",[]
        if re.match(r'^(no|nope|dont|do not|keep|continue)\b',choice):
            session.awaiting_restart=False
            session.bot.last_trace_id=None
            session.bot.last_decision=Decision('other','rule',reasoning='Customer declined restart; preserve the current workflow.')
            audit.event('conversation.restart_declined',{})
            return 'Okay, we’ll continue where we left off. Your pending details are unchanged. Please answer my previous question or tell me what you would like to correct.',[]
        return repeat_or_handoff(session.bot)
    restart=bool(re.search(r'\b(?:i(?:ve| have)?\s+(?:(?:wanna|want to|would like to)\s+)?(?:change|changed) my mind|forget (?:everything|what i (?:told|said))|start (?:over|again|from (?:the )?beginning)|restart (?:this |the )?(?:conversation|call|chat))\b',choice))
    negated=bool(re.search(r'\b(?:dont|do not|never)\s+(?:restart|start|forget)\b',choice))
    if restart and not negated:
        session.awaiting_restart=True
        session.bot.last_trace_id=None
        session.bot.last_decision=Decision('other','rule',reasoning='Customer requested a restart; confirmation required before clearing details.')
        audit.event('conversation.restart_requested',{})
        return 'Would you like to start this conversation over? I’ll clear the pending details, but any saved reservations will stay unchanged. Please say yes or no.',[]
    return None


def _execute_turn(session_id: UUID, session: Session, request: TurnRequest, queue_wait_ms: int) -> dict:
    if session.stopped:
        raise HTTPException(409, 'Conversation stopped before processing.')
    key = str(request.request_id)
    started = time.perf_counter()
    events = []
    try:
        with audit.capture(events):
            from customer_feedback import handle as feedback_turn,offer as offer_feedback
            from customer_intent import additional_questions,ADDITIONAL_QUESTIONS_REPLY
            invitation=request.input_kind!='unrecognized_audio' and not session.bot.ended and additional_questions(request.text)
            feedback=None if invitation else feedback_turn(session,request.text,request.input_kind=='unrecognized_audio')
            if invitation:
                from information_requests import remember_readback
                remember_readback(session.bot)
                session.awaiting_more_help=False
                session.bot.failures=session.voice_failures=0
                if session.survey['status'] in {'offered','awaiting_comment','awaiting_rating'}:
                    session.survey.update(status='skipped',completed_at=audit.now())
                    audit.event('feedback.skipped',{'reason':'customer_has_additional_questions'})
                session.bot.last_trace_id=None
                session.bot.menu_agent.last_sources=[]
                session.bot.last_decision=Decision('other','rule','additional_questions','Invite the customer to ask; preserve the pending reservation without inference or business tools.')
                reply,tools=ADDITIONAL_QUESTIONS_REPLY,[]
            elif feedback is not None:
                reply,tools=feedback
                session.bot.last_trace_id=None
                session.bot.last_decision=Decision('other','rule','customer_feedback','Optional customer feedback; no business tools or model call.')
            elif request.input_kind == 'unrecognized_audio':
                session.bot.last_trace_id = None
                session.bot.last_decision = Decision('other', 'rule', reasoning='No intelligible speech was detected.')
                reply,tools=repeat_or_handoff(session.bot)
                session.voice_failures=session.bot.failures
                audit.event('audio.unrecognized', {'failed_understandings': session.voice_failures})
            else:
                session.voice_failures = 0
                choice=re.sub(r'[^a-z0-9\s]','',request.text.lower()).strip()
                farewell=choice in {'bye','goodbye','quit','exit','thank you goodbye','stop','okay stop','ok stop','stop the call','end the call'}
                finished=session.awaiting_more_help and (choice in {'no','no thanks','no thank you','nothing else','thats all','that is all','im all set','thank you','thanks','no thats all','no thank you thats all'} or bool(re.fullmatch(r'no(?: (?:thanks|thank you))?(?: (?:that is all|thats all|nothing else|thats it))?',choice)))
                unfinished=incomplete_request(request.text)
                irrelevant=irrelevant_question(session.bot,request.text)
                restart=None if unfinished or irrelevant else conversation_restart(session_id,session,request.text)
                from information_requests import wants_information
                from customer_intent import declined_booking,abandon_draft,dietary_request
                from policy_requests import policy_question
                if declined_booking(request.text):
                    abandon_draft(session.bot)
                    session.pending_reservation_request=None
                    session.awaiting_restart=False
                    reply,tools=offer_feedback(session,expanded=True)
                    reply="I'm sorry I couldn't help you find what you needed. "+reply
                elif irrelevant:
                    reply,tools=IRRELEVANT_REPLY,[]
                elif unfinished:
                    session.bot.last_trace_id=None
                    session.bot.last_decision=Decision('other','rule','incomplete_request','Ask the caller to repeat the complete request; preserve pending state.')
                    reply,tools=repeat_or_handoff(session.bot)
                elif restart is not None:
                    reply,tools=restart
                elif session.channel=='voice' and session.pending_reservation_request is not None and re.match(r'^(yes|yeah|yep|i do|no|nope|i dont|i do not)\b',choice) and not dietary_request(request.text):
                    session.bot._dietary_followup=False
                    reply,tools=voice_reservation_turn(session,request.text)
                elif wants_information(session.bot,request.text) and not farewell:
                    session.awaiting_more_help=False
                    voice_pending=session.channel=='voice' and session.pending_reservation_request is not None
                    if voice_pending and not dietary_request(request.text) and not policy_question(request.text) and (change_intent(request.text) or explicit_booking_command(request.text)):
                        reply,tools=voice_reservation_turn(session,request.text)
                    else:reply,tools=session.bot.turn(request.text)
                    enquiry_pending=getattr(session.bot,'_availability_enquiry',None) is not None
                    active=session.bot.reserving or session.bot.reservation_changes.active or session.pending_reservation_request is not None
                    if session.pending_reservation_request is not None and not enquiry_pending and set(tools)&{'check_table','search_knowledge','check_dish'}:
                        reply+=' Returning to your original request, do you have a reservation with us?'
                    elif not active and not enquiry_pending and set(tools)&{'check_table','search_knowledge','check_dish'}:
                        reply+=' Is there anything else I can help you with?'
                        session.awaiting_more_help=True
                elif (finished or (farewell and choice not in {'quit','exit','stop','okay stop','ok stop','stop the call','end the call'})) and session.survey['status']=='not_offered' and not session.bot.ended:
                    reply,tools=offer_feedback(session)
                    session.bot.last_trace_id=None
                    session.bot.last_decision=Decision('other','rule','customer_feedback','Offer optional feedback after caller needs are complete.')
                elif farewell or finished:
                    session.bot.ended=True
                    session.bot.last_trace_id=None
                    session.bot.last_decision=Decision('other','rule',reasoning='Caller ended the voice conversation.')
                    reply,tools=session.farewell,[]
                    audit.event('voice.goodbye',{})
                elif session.awaiting_more_help and choice in {'yes','yes please'}:
                    session.awaiting_more_help=False
                    reply,tools='Of course. What else can I help you with?',[]
                    session.bot.last_trace_id=None
                    session.bot.last_decision=Decision('other','rule',reasoning='Caller requested more help.')
                else:
                    session.awaiting_more_help=False
                    reply, tools = (voice_reservation_turn(session,request.text) if session.channel=='voice'
                                    else session.bot.turn(request.text))
                    completed=reply.startswith(('Your table is booked','Your reservation is updated','Your reservation has been cancelled'))
                    answered=bool(set(tools)&{'get_hours','get_location','get_menu','check_dish','get_specials','search_knowledge'})
                    if not session.bot.ended and session.pending_reservation_request is None and 'transfer_to_human' not in tools and (completed or answered):
                        reply+=' Is there anything else I can help you with?'
                        session.awaiting_more_help=True
                if reply!=INCOMPLETE_REPLY and 'transfer_to_human' not in tools:
                    session.bot.failures=0
                    session.voice_failures=0
        handoff = 'transfer_to_human' in tools
        if handoff:
            # CLI transfer is an event, not a telephone connection in this channel.
            reply = 'A staff member can help with that. Please contact the restaurant directly.'
            if session.survey['status']=='not_offered' and getattr(session.bot,'failures',0)<4:
                invitation,_=offer_feedback(session)
                reply+=' '+invitation
        result = {'reply': reply, 'ended': session.bot.ended,
                  'handoff': handoff, 'staff_phone': restaurant.get('phone') if handoff else None,
                  'latency_ms': round((time.perf_counter() - started) * 1000)}
        decision = getattr(session.bot, 'last_decision', None)
        result['routing'] = asdict(decision) if decision else None
        result['survey']=dict(session.survey)
        result['trace_id'] = getattr(session.bot, 'last_trace_id', None)
        result['sources'] = getattr(getattr(session.bot, 'menu_agent', None), 'last_sources', [])
    except Exception:
        # A write might have succeeded before an error; never replay this turn automatically.
        logging.getLogger(__name__).exception('Chat turn failed')
        session.bot.ended = True
        result = {'reply': 'Something went wrong. Please contact staff to check whether your request was completed.',
                  'ended': True, 'handoff': True, 'staff_phone': restaurant.get('phone'),
                  'failed': True,
                  'latency_ms': round((time.perf_counter() - started) * 1000)}
    session.touched = time.monotonic()
    result['queue_wait_ms'] = queue_wait_ms
    result['request_id'] = key
    from structured_io import StructuredTurn
    calls={}
    for item in events:
        details=item.get('details',{})
        if details.get('call_id'):
            call=calls.setdefault(details['call_id'],{})
            for field,value in details.items():
                if field in {'call_id','schema_version','component'} or value is not None:call[field]=value
    result['structured']=StructuredTurn(session_id=str(session_id),request_id=key,channel=session.channel,
        input={'text':request.text,'kind':request.input_kind},
        output={'reply':result['reply'],'ended':result['ended'],'handoff':result.get('handoff',False),'failed':result.get('failed',False),'survey':dict(session.survey)},
        routing=result.get('routing'),tools_called=tools if 'tools' in locals() else [],calls=list(calls.values())).model_dump(mode='json')
    names={event['name'] for event in events}
    result['restart_state']=('confirmed' if 'conversation.restarted' in names else
                             'requested' if session.awaiting_restart else
                             'declined' if 'conversation.restart_declined' in names else None)
    session.replies[key] = ((request.text, request.input_kind), result)
    try:
        audit.append(audit.CALLS_FILE, {'session_id': str(session_id), 'request_id': key,
            'at': audit.now(), 'channel': session.channel, 'caller_text': request.text,
            'input_kind': request.input_kind,
            'result': result, 'events': events})
    except (OSError, ValueError):
        # Cache the reply first: audit failure must not cause a booking to replay.
        result['audit_warning'] = 'Local audit record could not be saved.'
        logging.getLogger(__name__).error('Local call audit write failed')
    return result


class ReviewRequest(BaseModel):
    target: str = Field(pattern=r'^(call|message):[a-zA-Z0-9_-]+$', max_length=100)
    status: str = Field(pattern=r'^(New|Reviewed|Resolved)$')


class FollowUpRequest(BaseModel):
    request_id: UUID
    text: str = Field(min_length=1,max_length=2000)
    resolve: bool = False


@app.post('/api/owner/conversations/{session_id}/notes',status_code=201)
def save_follow_up_note(session_id: UUID,request: FollowUpRequest):
    text=request.text.strip()
    if not text:raise HTTPException(422,'Enter a follow-up note.')
    with audit.io_lock:
        try:
            if not any(row.get('session_id')==str(session_id) for row in audit.read(audit.CALLS_FILE)):
                raise HTTPException(404,'Conversation not found.')
            existing=next((n for n in audit.read(audit.NOTES_FILE) if n['id']==str(request.request_id)),None)
            if existing:
                if existing['session_id']!=str(session_id) or existing['text']!=text or existing.get('resolve_requested',False)!=request.resolve:
                    raise HTTPException(409,'That note request was already used for different content.')
                note=existing
            else:
                note={'id':str(request.request_id),'session_id':str(session_id),'text':text,'at':audit.now(),'author':'Owner','resolve_requested':request.resolve}
                audit.append(audit.NOTES_FILE,note)
            if request.resolve:
                audit.append(audit.REVIEWS_FILE,{'target':'call:'+str(session_id),'status':'Resolved','at':audit.now()})
            return {'note':note,'resolved':request.resolve}
        except (OSError,ValueError):raise HTTPException(503,'Follow-up could not be saved. Retry without changing the note.')


@app.get('/api/owner/dashboard')
def owner_dashboard() -> dict:
    """Local-only owner view. Evaluation fixtures are deliberately excluded."""
    with audit.io_lock:
        try:
            records = audit.read(audit.CALLS_FILE)
            reviews = {row['target']: row['status'] for row in audit.read(audit.REVIEWS_FILE)}
            notes={}
            for note in audit.read(audit.NOTES_FILE):notes.setdefault(note['session_id'],[]).append(note)
            grouped = {}
            for row in records:
                grouped.setdefault(row['session_id'], []).append(row)
            calls = []
            for session_id, turns in grouped.items():
                latest = turns[-1]
                business=next((t for t in reversed(turns) if (t['result'].get('routing') or {}).get('reason')!='customer_feedback'),latest)
                result = business['result']
                route = result.get('routing') or {}
                events = [event for turn in turns for event in turn.get('events', [])]
                booked = any(event['name'] == 'book_table.completed' and event['details'].get('booked') for event in events)
                changed=any(event['name']=='change_reservation.completed' and event['details'].get('changed') for event in events)
                action=next((event['details'].get('action') for event in reversed(events) if event['name']=='reservation.change_confirmed'),None)
                failed = any(event['name'].endswith('.failed') or event['name'] == 'reservation.duplicate_blocked' for event in events)
                outcome = ('Needs review' if failed or result.get('failed') or route.get('intent') in {'other', 'policy'}
                           else 'Transferred' if result.get('handoff') else ('Cancelled' if action=='cancel' else 'Updated') if changed else 'Booked' if booked else 'Answered')
                calls.append({'id': session_id, 'at': latest['at'], 'channel': latest.get('channel', 'chat'),
                    'intent': route.get('intent', 'unknown'), 'outcome': outcome,
                    'latency_ms': result.get('latency_ms', 0),
                    'turns': turns, 'events': events, 'review': reviews.get('call:'+session_id, 'New'),
                    'follow_up_notes':notes.get(session_id,[]),
                    'feedback':next((t['result']['survey'] for t in reversed(turns) if t['result'].get('survey')),None)})
            calls.sort(key=lambda call: call['at'], reverse=True)
            booking_tools = engine.Tools(restaurant)
            bookings = reservation_sources(booking_tools.booking_store().list(booking_tools.business_id))
            booking_events=booking_tools.booking_store().history(booking_tools.business_id)
            messages = [{**message, 'id': str(index), 'review': reviews.get(f'message:{index}', 'New')}
                        for index, message in enumerate(audit.read(engine.MESSAGES_FILE))]
            return {'restaurant': restaurant['name'], 'updated_at': audit.now(),
                    'calls': calls, 'bookings': bookings, 'messages': messages,'booking_events':booking_events,
                    'reservation_settings':{'hours':restaurant['hours'],'seating':restaurant['seating'],
                        'timezone':restaurant.get('timezone','UTC'),'today':local_today(restaurant).isoformat()},
                    'queue': scheduler.status()}
        except (ValueError, KeyError, OSError, sqlite3.Error):
            raise HTTPException(503, 'Local data could not be read; check the audit files and reservation database.')


@app.get('/api/owner/reservations/day')
def reservation_day(day: str, preview: bool=False):
    try:
        if preview:
            return {**day_plan(restaurant,sample_reservations()['bookings'],day,now=restaurant_now()),'preview':True}
        tools=engine.Tools(restaurant)
        return day_plan(restaurant,reservation_sources(tools.booking_store().list(tools.business_id)),day,now=restaurant_now())
    except ValueError:
        raise HTTPException(422,'Use a valid calendar date in YYYY-MM-DD format.')
    except (OSError,sqlite3.Error):
        raise HTTPException(503,'Reservation data could not be read.')


@app.get('/api/owner/reservations/samples')
def sample_reservations():
    import json
    from reservation_samples import generate
    current=restaurant_now()
    return {'preview':True,'bookings':generate(json.dumps(restaurant,sort_keys=True),current.date().isoformat(),current.strftime('%H:%M')),'start':'2026-10-01','end':'2026-12-31'}


def reservation_sources(bookings):
    """Recover legacy sources only from recorded session evidence, never guess."""
    channels={row.get('session_id'):row.get('channel') for row in audit.read(audit.CALLS_FILE)}
    for booking in bookings:
        if booking.get('source','unknown')=='unknown':
            channel=channels.get(booking.get('session_id'))
            booking['source']={'voice':'phone','chat':'chat'}.get(channel,'unknown')
    from seating_time import observed_sources
    return observed_sources(bookings,restaurant_now())


class InPersonSeating(BaseModel):
    request_id: UUID
    name: str = Field(min_length=1,max_length=100)
    party_size: int = Field(ge=1,le=12,strict=True)
    date: str
    time: str


class OwnerReservation(BaseModel):
    request_id: UUID
    name: str = Field(min_length=1,max_length=100)
    phone: str = Field(min_length=7,max_length=30)
    party_size: int = Field(ge=1,le=12,strict=True)
    date: str
    time: str
    source: Literal['phone','chat','unknown'] = 'unknown'


class OwnerChange(BaseModel):
    request_id: UUID
    expected_version: int = Field(ge=1,strict=True)
    action: Literal['update','cancel']
    date: str | None = None
    time: str | None = None
    party_size: int | None = Field(default=None,ge=1,le=12,strict=True)


def validate_owner_date(day,time):
    from datetime import date as calendar_date, datetime
    try:
        if calendar_date.fromisoformat(day).isoformat()!=day or datetime.strptime(time,'%H:%M').strftime('%H:%M')!=time:
            raise ValueError()
        if day<local_today(restaurant).isoformat() or (day==local_today(restaurant).isoformat() and time<=restaurant_now().strftime('%H:%M')):
            raise HTTPException(422,'Choose a future arrival time in the restaurant timezone.')
    except (ValueError,TypeError):raise HTTPException(422,'Enter a valid reservation date and 24-hour time.')


@app.post('/api/owner/reservations',status_code=201)
def owner_create_reservation(request: OwnerReservation):
    from customer_identity import normalize_phone
    validate_owner_date(request.date,request.time)
    if not request.name.strip() or not normalize_phone(request.phone,restaurant.get('phone_region','IN')):
        raise HTTPException(422,'Enter a guest name and a valid phone number, including country code for international numbers.')
    tools=engine.Tools(restaurant)
    tools.booking_source=request.source
    tools.session_id='owner:'+str(request.request_id)
    try:
        result=tools.book_table(request.name.strip(),request.phone,request.party_size,request.date,request.time,request_key='owner-create:'+str(request.request_id))
        if not result.get('booked'):raise HTTPException(409,result.get('reason',result.get('error','Reservation unavailable.')))
        return result
    except ValueError as error:raise HTTPException(409,str(error))
    except (OSError,sqlite3.Error):raise HTTPException(503,'Reservation storage is unavailable; retry with the same request.')


@app.post('/api/owner/reservations/{reference}/change')
def owner_change_reservation(reference: UUID,request: OwnerChange):
    tools=engine.Tools(restaurant)
    tools.session_id='owner:'+str(request.request_id)
    try:
        rows=tools.booking_store().list(tools.business_id)
        current=next((row for row in rows if row['id']==str(reference)),None)
        if not current:raise HTTPException(404,'Reservation not found.')
        changes={}
        if request.action=='update':
            if current.get('source')=='walk_in':
                raise HTTPException(422,'Recorded in-person seating cannot be rescheduled. Record arrivals on the current restaurant date only.')
            if request.date is None or request.time is None or request.party_size is None:
                raise HTTPException(422,'Provide the date, time and party size for the updated reservation.')
            validate_owner_date(request.date,request.time)
            changes={'date':request.date,'time':request.time,'party_size':request.party_size}
        elif any(value is not None for value in (request.date,request.time,request.party_size)):
            raise HTTPException(422,'Cancellation cannot include changes to booking details.')
        result=tools.change_reservation(str(reference),current['phone'],request.action,request.expected_version,changes,'owner-change:'+str(request.request_id))
        if not result.get('changed'):raise HTTPException(409,result.get('reason','Reservation changed. Refresh and try again.'))
        return result
    except ValueError as error:raise HTTPException(409,str(error))
    except (OSError,sqlite3.Error):raise HTTPException(503,'Reservation storage is unavailable; retry with the same request.')


def voice_farewell(arrived_at):
    # Use restaurant-local call arrival time, including calls crossing midnight.
    closing='Have a good day!' if 6<=arrived_at.hour<18 else 'Good night!'
    return 'Thank you so much for calling. '+closing


def restaurant_now():
    return local_now(restaurant)


@app.post('/api/owner/reservations/in-person',status_code=201)
def seat_in_person(request: InPersonSeating):
    tools=engine.Tools(restaurant)
    tools.booking_source='walk_in'
    try:
        from datetime import date as calendar_date, datetime
        if calendar_date.fromisoformat(request.date).isoformat()!=request.date:
            raise ValueError('Use YYYY-MM-DD.')
        if datetime.strptime(request.time,'%H:%M').strftime('%H:%M')!=request.time or not request.name.strip():
            raise ValueError('Invalid seating details.')
        from seating_time import has_arrived
        tools.occupancy_now=restaurant_now()
        if not has_arrived(request.date,request.time,tools.occupancy_now):
            raise HTTPException(422,'In-person seating cannot be recorded before guests arrive; choose the actual arrival time.')
        result=tools.book_table(request.name.strip(),'',request.party_size,request.date,request.time,request_key=str(request.request_id))
        if not result.get('booked'):
            raise HTTPException(409,result.get('reason',result.get('error','That window is unavailable.')))
        return result
    except ValueError:
        raise HTTPException(422,'Enter a valid name, date and 24-hour seating time.')
    except (OSError,sqlite3.Error):
        raise HTTPException(503,'Seating could not be saved; retry the same request.')


@app.post('/api/owner/reviews')
def save_review(request: ReviewRequest) -> dict:
    with audit.io_lock:
        try:
            audit.append(audit.REVIEWS_FILE, {**request.model_dump(), 'at': audit.now()})
        except OSError:
            raise HTTPException(503, 'Review status could not be saved.')
    return request.model_dump()

