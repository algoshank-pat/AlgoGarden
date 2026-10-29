"""Optional end-of-conversation feedback. No model or reservation tools."""
import re
from local_audit import event,now

OFFER='Thanks for calling. Before you go, would you feel comfortable taking a quick, optional survey?'
RATING='Was your experience good or bad?'


def handle(session,text,unrecognized=False):
    state=session.survey
    if state['status'] not in {'offered','awaiting_comment','awaiting_rating'}:return None
    if state['status']=='awaiting_comment':
        if unrecognized:return 'I didn\'t get that. Please repeat your feedback comment, or say skip.',[]
        if text.strip().lower().rstrip('.!?') not in {'skip','no','no thanks','no comment'}:
            state['comment']=text
            event('feedback.comment',{'comment':text})
        state['status']='awaiting_rating'
        return RATING,[]
    from routing import required_handoff,keyword_intent
    declining=bool(re.match(r'^\s*(?:no\b|nope\b|skip\b|not now\b)',text,re.I))
    if not unrecognized and (required_handoff(text) or (not declining and keyword_intent(text)!='other' and not re.search(r'\b(rate|rating|interaction|experience|feedback|out of|good|bad)\b',text,re.I))):
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
            state.update(status='awaiting_comment' if state.get('expanded') else 'awaiting_rating',consented_at=now())
            event('feedback.consent',{})
            if state.get('expanded'):
                prompt=('Your next spoken feedback comment will be saved as a voice note and transcript. Say skip if you do not want to leave one.' if session.channel=='voice' else 'You can type a comment, record an optional voice note, or say skip.')
                return 'What feedback would you like to leave? '+prompt+' Then I’ll ask whether your experience was good or bad.',[]
            return RATING,[]
        return 'Would you like to give feedback? Please say yes or no. You can skip it.',[]
    # Accept one explicit sentiment; do not translate numbers into feedback.
    answers=set(re.findall(r'\b(?:good|bad)\b',t))
    negated=bool(re.search(r'\b(?:not|wasnt|isnt|never)\s+(?:really\s+)?(?:good|bad)\b',t))
    if not unrecognized and len(answers)==1 and not negated:
        rating=answers.pop()
        state.update(status='completed',rating=rating,completed_at=now())
        session.bot.ended=True
        event('feedback.rated',{'rating':rating})
        return 'Thank you for your feedback. '+session.farewell,[]
    return 'Please say good or bad, or say skip.',[]



def offer(session,expanded=False):
    session.awaiting_more_help=False
    session.bot.ended=False
    session.survey.update(status='offered',offered_at=now(),expanded=expanded)
    event('feedback.offered',{})
    return ('Before you go, would you like to leave an optional feedback comment or voice note and say whether your experience was good or bad?' if expanded else OFFER),[]
