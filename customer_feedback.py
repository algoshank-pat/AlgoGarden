"""Optional end-of-conversation feedback. No model or reservation tools."""
import re
from local_audit import event,now

OFFER='Thanks for calling. Before you go, would you feel comfortable taking a quick, optional survey?'
RATING='Please rate this interaction from 1 to 5, where 1 is the lowest and 5 is the highest.'


def handle(session,text,unrecognized=False):
    state=session.survey
    if state['status'] not in {'offered','awaiting_rating'}:return None
    from routing import required_handoff,keyword_intent
    if not unrecognized and (required_handoff(text) or (keyword_intent(text)!='other' and not re.search(r'\b(rate|rating|interaction|experience|feedback|out of)\b',text,re.I))):
        state.update(status='skipped',completed_at=now())
        event('feedback.skipped',{'reason':'customer_resumed_conversation'})
        return None
    t=' '.join(re.sub(r'[^a-z0-9\s]',' ',text.lower()).split())
    no=not unrecognized and bool(re.match(r'^(no|nope|not now|skip|stop|goodbye|bye|end the call|dont want|do not want)\b',t))
    if no:
        state.update(status='declined',completed_at=now())
        session.bot.ended=True
        event('feedback.declined',{})
        return session.farewell,[]
    if state['status']=='offered':
        if not unrecognized and re.match(r'^(yes|yeah|yep|sure|okay|ok|of course|happy to|i can)\b',t):
            state.update(status='awaiting_rating',consented_at=now())
            event('feedback.consent',{})
            return RATING,[]
        return 'Would you like to give feedback? Please say yes or no. You can skip it.',[]
    # A scale reference is not a second rating. Ambiguous or non-integer answers
    # never become a recorded score.
    t=re.sub(r'\bout of (?:five|5)\b','',t)
    numbers=re.findall(r'\b(?:\d+(?:\.\d+)?|zero|one|two|three|four|five|six|seven|eight|nine|ten)\b',text.lower() if re.search(r'\d\.\d',text) else t)
    values={'one':1,'two':2,'three':3,'four':4,'five':5}
    rating=values.get(numbers[0],int(numbers[0]) if numbers and numbers[0].isdigit() else None) if len(numbers)==1 else None
    if not unrecognized and rating in range(1,6):
        state.update(status='completed',rating=rating,completed_at=now())
        session.bot.ended=True
        event('feedback.rated',{'rating':rating})
        return 'Thank you for your feedback. '+session.farewell,[]
    return 'Please give one whole-number rating from 1 to 5, or say skip.',[]


def offer(session):
    session.awaiting_more_help=False
    session.bot.ended=False
    session.survey.update(status='offered',offered_at=now())
    event('feedback.offered',{})
    return OFFER,[]
