"""Dietary answers from explicit menu fields; no model inference or safety guesses."""
import re
from routing import Decision
from local_audit import event

def normalized(text):
 return ' '.join(re.sub(r'[^a-z0-9 ]',' ',text.lower().replace('&',' and ').replace('chilli','chili')).split())

def named_dishes(menu,text):
 t=normalized(text)
 return [d for d in menu if normalized(d['name']) in t]

def specific_question(menu,text):
 from routing import required_handoff
 return not required_handoff(text) and bool(named_dishes(menu,text)) and bool(re.search(r'\b(what|how much|price|cost|set me back|describe|tell|is|does|contain|vegan|gluten|nuts?|peanuts?|allergens?|dairy|milk|butter|ghee|cheese)\b',text,re.I))

def answer(bot,text):
 from information_requests import remember_readback
 remember_readback(bot);bot._dietary_followup=True;bot.failures=0
 bot.last_decision=Decision('menu','rule','dietary_menu','Use explicit menu labels and allergens; never infer allergy safety.')
 menu=bot.restaurant.get('menu',[]);named=named_dishes(menu,text)
 wanted=[]
 if re.search(r'\bvegan\b',text,re.I):wanted.append('vegan')
 if re.search(r'gluten|celiac|coeliac',text,re.I):wanted.append('gluten_free')
 nuts=bool(re.search(r'nuts?|peanuts?',text,re.I))
 if named:
  replies=[]
  for original in named[:2]:
   d=bot.tools.check_dish(original['name']);allergens={str(a).lower() for a in (d.get('allergens') or [])};parts=[]
   price=bool(re.search(r'\b(how much|price|cost\w*|set me back|pay for)\b',text,re.I))
   complete_allergens=bool(re.search(r'\ballergens?\b',text,re.I))
   dairy=bool(re.search(r'\b(dairy|milk|butter|ghee|cheese)\b',text,re.I))
   if price:parts.append(f"Costs ${d['price_usd']}.")
   if complete_allergens:parts.append('Recorded allergens: '+', '.join(sorted(allergens))+'.' if allergens else 'No allergens are listed; this does not establish allergy safety.' if d.get('allergens') is not None else 'Allergen information is not recorded.')
   if dairy:
    parts.append('Contains dairy.' if allergens&{'dairy','milk','butter','ghee','cheese'} else 'Dairy is not listed; our kitchen must confirm ingredients and cross-contact.' if d.get('allergens') is not None else 'Dairy information is not recorded; I cannot confirm whether it contains dairy.')
   if re.search(r'describe|tell.*about|\bwhat is\b',text,re.I):
    parts.append(original.get('description') or 'A description is not recorded for this dish.')
   if not wanted and not nuts and not parts:wanted=['vegan','gluten_free'];nuts=True
   for field in wanted:
    label='vegan' if field=='vegan' else 'gluten-free';flag=d.get(field)
    contains=field=='gluten_free' and 'gluten' in allergens or field=='vegan' and (d.get('veg') is False or bool(allergens&{'dairy','eggs','fish','shellfish'}))
    parts.append('Listed as '+label+'.' if flag is True else 'Not '+label+'.' if flag is False or contains else 'Its '+label+' status is not confirmed.')
   nut_allergens=sorted(allergens&{'peanut','peanuts','cashew','cashews','almond','almonds','walnut','walnuts','pistachio','pistachios','tree nuts','nuts'})
   if nut_allergens and not complete_allergens and (nuts or wanted):parts.append('Contains '+', '.join(nut_allergens)+'.')
   elif nuts:parts.append('Listed as nut-free.' if original.get('nut_free') is True else 'Nut-free status is not confirmed; no nuts are listed, which is not a guarantee.')
   if not d.get('available'):parts.append('Currently unavailable.')
   replies.append(original['name']+': '+' '.join(parts))
  dietary=bool(wanted or nuts or re.search(r'allergen|dairy|milk|butter|ghee|cheese',text,re.I))
  if not dietary:bot._dietary_followup=False
  return ' '.join(replies)+(' Please confirm allergies and cross-contact with our kitchen.' if dietary else ''),['check_dish']
 if re.search(r'^\s*(is|does)\b',text,re.I):return 'Which menu dish would you like me to check? I cannot confirm an unlisted dish’s dietary status.',[]
 if re.search(r'celiac|coeliac|\bsafe\b|guarantee',text,re.I):return 'I cannot guarantee allergy or celiac safety. Our kitchen must confirm ingredients and cross-contact.',[]
 items=[d for d in menu if d.get('available') and d.get('category')!='beverages' and all(d.get(field) is True for field in wanted)]
 if nuts:items=[d for d in items if d.get('nut_free') is True]
 if not wanted and not nuts:return 'Which dietary requirement would you like me to check?',[]
 voice=getattr(bot.tools,'booking_source','chat')=='phone';items=items[:2 if voice else 4]
 event('dietary.menu_checked',{'basis':'explicit_dietary_fields','restrictions':wanted,'nut_free_requested':nuts,'candidates':[d['name'] for d in items]})
 if not items:return 'I cannot confirm suitable dishes from the recorded menu labels. Please check with our kitchen.',[]
 options=[]
 for dish in items:
  recorded=bot.tools.check_dish(dish['name'])
  options.append(dish['name']+(' (contains peanuts)' if any(a in {'peanut','peanuts'} for a in recorded.get('allergens',[])) else ''))
 reply='A couple of options are ' if voice else 'Options include '
 reply+=', '.join(options)+'.'
 if voice:reply+=' Please check our website for the full menu.'
 return reply+' Please tell our staff about allergies and cross-contact.',['check_dish']
