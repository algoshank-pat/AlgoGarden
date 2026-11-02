"""Short speech templates and exact owner-approved payment wording."""
import re

# Only complete, approved stock sentences are rewritten. Dynamic details and
# grounded answers pass through intact, except the exact payment wording below.
SHORTEN = {
    # Owner-approved concise rendering of this exact grounded payment sentence.
    # "Only" preserves the exclusion of UPI and all unlisted methods. If the
    # authoritative policy changes, this exact match no longer applies.
    'Only Apple Pay (Apple Wallet), Google Pay, credit cards, debit cards, cash and cheque are accepted; UPI and other payment methods are not accepted.':
        'We accept only Apple Pay, Google Pay, credit or debit cards, cash and cheques.',
    'Do you have a reservation with us?': 'Do you already have a reservation?',
    'What phone number can I use for your reservation?': "What's your phone number?",
    'What phone number was used for the reservation?': 'What phone number is the reservation under?',
    'Could you give your phone number digit by digit?': 'Please say your phone number digit by digit.',
    'May I have your name for the reservation?': "What's your name?",
    'Please say and spell your first and last name. You can say "last name" between them.': "What's your name?",
    'Please say and spell your first and last name. You can say last name between them.': "What's your name?",
    "I didn't get that. Can you please repeat?": 'Sorry, could you repeat that?',
    'Is there anything else I can help you with?': 'Anything else I can help with?',
    'How many guests would you like to book for?': 'How many people?',
    'How many guests?': 'How many people?',
    'What date would you like?': 'What date works for you?',
    'Which date would you like?': 'What date works for you?',
    'What time would you like? Please include morning, afternoon or evening if needed.': 'What time? Please say morning, afternoon or evening.',
    'What time would you like? Please include morning or evening.': 'What time? Please say morning or evening.',
    'What time would you like?': 'What time works for you?',
    'What other date or time would work for you?': 'What other date or time works?',
    'Which date or short date range would you prefer, and around what time?': 'Which date or few days work, and around what time?',
    'Please choose one date and one arrival time so I can check availability.': 'Which date and arrival time should I check?',
    'That requested time is unavailable.': "That time isn't available.",
    'That time is fully booked.': 'That time is fully booked.',
    'Would any of these work for you?': 'Would any of these work?',
    'Would you like the suggested time? Please say yes, or tell me a different time.': 'Does that time work? Say yes or give another time.',
    'I have not booked it yet.': "It's not booked yet.",
    'Of course. Take your time. Say "continue" when you are ready.': 'Take your time. Say "continue" when you\'re ready.',
    'Of course. What else can I help you with?': "Go ahead. What's your question?",
    'Go ahead. I can help you answer them.': "Go ahead. What's your question?",
    'Go ahead. What else would you like to ask?': "Go ahead. What's your question?",
    'Thanks for calling. Before you go, would you feel comfortable taking a quick, optional survey?': "Thanks. Would you like to give quick feedback? It's optional.",
    'Would you like to give feedback? Please say yes or no. You can skip it.': 'Would you like to give feedback? Say yes, no, or skip.',
    'Thank you for your feedback.': 'Thanks for your feedback.',
    'Thank you so much for calling.': 'Thanks for calling.',
}
# Policy audio stays outside the generic phrase cache.
STANDARD = (frozenset(SHORTEN.values()) - {'We accept only Apple Pay, Google Pay, credit or debit cards, cash and cheques.'}) | frozenset({
    'One moment.',
    'Lemme help you with the reservation.',
    'Your reservation is confirmed.',
    'Your reservation is confirmed. Do you have any questions?',
    'Do you have any questions?',
    'Do you have any other questions?',
    'May I have your name for the reservation?',
    'Could you say your name again?',
    'Which date would you like?', 'What date would you like?',
    'What time would you like? Please include morning or evening.',
    'What would you like to change?',
    'Go ahead. I can help you answer them.',
    'Any other questions, or shall we return to your reservation?',
    'Go ahead. What else would you like to ask?',
    'Of course. Take your time. Say "continue" when you are ready.',
    'Was your experience good or bad?', 'Please say good or bad, or say skip.',
    'Have a good day!', 'Good night!',
    "Lemme help you with the reservation. What's your phone number?",
    'Thanks for calling. Have a good day!', 'Thanks for calling. Good night!',
    'Thanks for your feedback. Thanks for calling. Have a good day!',
    'Thanks for your feedback. Thanks for calling. Good night!',
})
# Longest first prevents shortening a prefix before a longer template matches.
_REPLACEMENTS = tuple((re.compile(r'(?<!\w)' + re.escape(a) + r'(?!\w)'), b)
                      for a, b in sorted(SHORTEN.items(), key=lambda item: -len(item[0])))
_GREETING = re.compile(r'Thank you for calling ([^.!?\n]{1,100})\. How can I help you today\?')
_SHORT_GREETING = re.compile(r'Thanks for calling [^.!?\n]{1,100}\. How can I help\?')
_NAME_CHECK = re.compile(r'(?<!\w)I heard ([^.!?\n]{1,120})\. Is that name correct\?')


def concise_speech(text):
    text = _GREETING.sub(lambda m: f'Thanks for calling {m[1]}. How can I help?', text)
    for pattern, replacement in _REPLACEMENTS:
        text = pattern.sub(lambda m: replacement, text)
    # Preserve the complete captured name; only simplify the verification question.
    text = _NAME_CHECK.sub(lambda m: f'I heard {m[1]}. Is that right?', text)
    return text


def cacheable(text):
    # Generic prompts carry no customer or policy data. The whole exact greeting
    # includes the restaurant name; never cache arbitrary greeting-prefixed text.
    return text in STANDARD or bool(_SHORT_GREETING.fullmatch(text))


def spoken_text(text):
    """Canonical wording shared by voice transcript and synthesis."""
    from .speech_text import natural_speech
    return natural_speech(concise_speech(re.sub(r'\bSaffron\s*&\s*Basil\b', 'Saffron and Basil', text)))


def speech_segments(text):
    text = spoken_text(text)
    parts = []
    while len(text) > 110:
        boundaries = [m.end() for m in re.finditer(r'[,;.!?]\s+', text[:111]) if m.end() >= 35]
        at = boundaries[-1] if boundaries else text.rfind(' ', 0, 110)
        if at < 1:
            break
        parts.append(text[:at].strip())
        text = text[at:].strip()
    if text:
        parts.append(text)
    return parts
