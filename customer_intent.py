"""Resolve explicit customer intent before a pending booking can consume a reply."""
import re
from routing import Decision

ADDITIONAL_QUESTIONS_REPLY='Go ahead. I can help you answer them.'

def additional_questions(text):
    """A conversational invitation, not a new booking or the actual question."""
    t=text.lower().replace('’',"'").replace("i've",'i have').replace("we've",'we have')
    t=' '.join(re.sub(r'[^a-z\s]',' ',t).split())
    amount=r'(?:(?:a couple(?: of)?|a few|couple(?: of)?|some|few|one|an|a)\s+)?'
    kind=r'(?:(?:additional|more|other|another|further|follow up|quick)\s+)*'
    question=amount+kind+r'questions?'
    lead=r'(?:(?:yes|yeah|okay|ok|actually|also|well)\s+)*'
    topic=r'(?:\s+about\s+(?:(?:my|our|the|your)\s+)?(?:reservations?|bookings?|restaurant|menu|parking|hours|policies))?'
    return bool(re.fullmatch(lead+r'(?:(?:i|we)\s+(?:have(?: got)?|got|had)\s+'+question+r'|(?:can|could|may)\s+(?:i|we)\s+ask\s+(?:you\s+)?'+question+r'|(?:i|we)\s+(?:want|wanted|would like)\s+to\s+ask\s+(?:you\s+)?'+question+r'|'+question+r')'+topic+r'(?:\s+please)?',t))


def declined_booking(text):
    t=text.lower().replace('’', "'")
    # Negation must govern creating a booking, not cancelling one or a dietary need.
    return bool(re.search(r"\b(?:i|we)\s+(?:(?:do\s+not|don't|dont)\s+(?:want|wanna|wish)(?:\s+to)?|(?:would\s+)?(?:rather\s+not|prefer\s+not)(?:\s+to)?|(?:am|are)\s+not\s+(?:going|looking)\s+to)\s+(?:make\s+(?:a\s+)?(?:reservation|booking)|book\b|reserve\b)",t))


def dietary_request(text):
    return bool(re.search(r'\b(gluten|vegan|peanuts?|nuts?|celiac|coeliac)\b',text,re.I)) and not declined_booking(text)


def abandon_draft(bot):
    bot.reserving=False
    bot.awaiting_confirmation=False
    bot.slots.clear()
    bot.reservation_changes.action=None
    bot._offered_alternatives=[]
    bot._existing_choice=False
    bot._dietary_followup=False
    for name in ('_availability_enquiry','_information_readback','_information_readback_state','_awaiting_slot','_routed_fields'):
        setattr(bot,name,None)
    bot.last_decision=Decision('other','rule','declined_booking','Customer declined booking; discard unsaved drafts only. No reservation mutation or identity lookup.')


def dietary_turn(bot,text):
    from dietary_menu import answer,named_dishes
    if named_dishes(bot.restaurant.get('menu',[]),text) or re.search(r'vegan|nuts?|peanuts?|gluten[ -]?free|without gluten|celiac|coeliac',text,re.I) and not re.search(r'\b(add|include|put|note)\b',text,re.I):
        return answer(bot,text)
    from information_requests import remember_readback
    remember_readback(bot)
    bot._dietary_followup=True
    bot.last_decision=Decision('menu','rule','dietary_enquiry','Answer the dietary question using recorded allergen information; clarify the requested next action.')
    bot.failures=0
    menu=bot.restaurant.get('menu',[])
    named=[d for d in menu if d['name'].lower() in text.lower()]
    allergen_question=bool(named and re.search(r'\b(does|do|is|are|has|have|can you tell|could you tell)\b',text,re.I)
                           and re.search(r'\b(contain|contains|have|has|include|includes|ingredients?|allergens?)\b|\bgluten\s+in\b',text,re.I))
    # An unknown ASR modifier must be clarified rather than silently repaired.
    if not allergen_question and not re.search(r'gluten[ -]?free|no gluten|without gluten|avoid(?:ing)? gluten|celiac|coeliac',text,re.I):
        bot._dietary_followup='confirm_gluten'
        return 'Are you asking about gluten-free options?',[]
    if not allergen_question and re.search(r'\b(add|include|put|note)\b',text,re.I):
        return 'Would you like information about dishes, or would you like a gluten-free dietary request noted for a reservation? I have not added anything yet.',[]
    if named:
        answers=[]
        for dish in named:
            if 'allergens' not in dish:answer=f"I do not have allergen information for {dish['name']}."
            elif 'gluten' in [a.lower() for a in dish['allergens']]:answer=f"{dish['name']} lists gluten as an allergen."
            else:answer=f"{dish['name']} has no gluten listed, but that does not certify it as gluten-free."
            if not dish.get('available'):answer+=' It is currently unavailable.'
            answers.append(answer)
        return ' '.join(answers)+' Our kitchen needs to confirm ingredients and cross-contact. Would you like more dietary information, or help with a reservation?',[]
    if re.search(r'celiac|coeliac|\bsafe\b|\ballerg(?:y|ic)\b|cross[ -]?contact|guarantee',text,re.I):
        return 'I cannot confirm that a dish is safe for a gluten allergy or coeliac disease from allergen listings alone. Our kitchen needs to confirm ingredients and cross-contact. Would you like to speak to staff about your dietary requirements?',[]
    candidates=[d for d in menu if d.get('available') and 'allergens' in d and 'gluten' not in [a.lower() for a in d['allergens']] and d.get('category')!='beverages']
    from local_audit import event
    event('dietary.menu_checked',{'restriction':'gluten','basis':'recorded_allergens','candidates':[d['name'] for d in candidates],'certified_gluten_free':False})
    if candidates:
        answer='Yes, we have options with no gluten listed in their allergen information, including '+', '.join(d['name'] for d in candidates[:3])+'.'
    else:
        answer='I cannot confirm suitable options from our current menu information.'
    return answer+' This does not confirm they are gluten-free or free from cross-contact; our kitchen needs to confirm that. Would you like more dietary information, or help with a reservation?',[]
