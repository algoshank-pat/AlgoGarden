"""Policy intent/facet detection. Contains routing vocabulary, never policy facts."""
import re
from dataclasses import dataclass

@dataclass(frozen=True)
class Topic:
 name:str
 request:str
 evidence:str
 query:str

TOPICS=(
 Topic('late_arrival',r'\b(late|delayed|traffic|running behind|train|grace|released|hold|held)\b',r'\b(held|hold|grace)\b','late arrival table held grace period'),
 Topic('cancellation',r'\bcancell?\w*\b|\bcannot make\b',r'\bcancell?\w*\b','cancellation advance notice warning deadline'),
 Topic('pets',r'\b(pets?|dogs?|pupp(?:y|ies)|animals?|cats?|kittens?|canines?|felines?|terriers?|dachshunds?|retrievers?|labradors?|poodles?|beagles?|spaniels?|husk(?:y|ies)|chihuahuas?|bulldogs?|corgis?|greyhounds?)\b',r'\b(pets?|animals?)\b','pets dogs indoor dining assistance animal'),
 Topic('outside_alcohol',r'\b(corkage|byob|wine|champagne|alcohol(?:ic)?|bottles?)\b',r'\b(outside|corkage)\b','outside alcoholic beverages corkage'),
 Topic('outside_cake',r'\b(cakes?|bakery|desserts?|pastries|cookies|brownies|food)\b',r'\b(outside food|cakes?|outside desserts?)\b','outside food birthday cakes homemade desserts prohibition exceptions notice permission'),
 Topic('children',r'\b(high[ -]?chairs?|boosters?|toddlers?|children|kids?|bab(?:y|ies))\b',r'\b(high chairs?|children|child)\b','children high chairs available request staff'),
 Topic('payment',r'\b(pay(?:ment)?s?|upi|cash|cards?|contactless|till|tender)\b',r'\b(accepted|payment)\b','payment methods cash UPI debit credit cards accepted'),
 Topic('private_dining',r'\bprivate\s+(?:room|dining)|\b(?:separate|exclusive)\b.*\b(?:room|dining)|\bbuyouts?\b',r'\b(private|buyouts?|event packages)\b','private dining room promise approval confirmation'),
 Topic('separate_bills',r'\b(split|separate)\b.*\b(bills?|checks?|pay)|\b(?:each|our own|individually)\b.*\b(?:settle|pay|part|bills?|checks?)|\bbills?\b.*\bseparate\b',r'\bseparate bills\b','Payment methods and separate bills'),
 Topic('accessibility',r'\b(wheelchairs?|step[ -]?free|accessibility|mobility)\b',r'\b(wheelchair|step.free)\b','accessibility wheelchair seating step-free contact staff suitable arrangements'),
 Topic('dress',r'\b(dress|attire|clothing|footwear|shirts?)\b',r'\b(clothing|shirts?|footwear)\b','dress code clothing shirts footwear'),
)

def policy_topics(text):
 t=text.lower().replace('’',"'")
 # A request to perform a transaction or speak to a person keeps its meaning.
 if re.search(r'\b(?:speak|talk|connect|transfer)\b.*\b(?:staff|manager|human|person|someone)\b|\b(?:complaint|food poisoning)\b',t):return ()
 if re.search(r'^\s*(?:(?:please|actually)[, ]+)?(?:(?:can|could|would)\s+(?:i|we|you)\s+|(?:i|we)\s+(?:want|need|would like)\s+to\s+)?(?:book|reserve|reschedule|cancel|update|modify|order)\b',t):return ()
 question=bool('?' in t or re.search(r'\b(?:policy|policies|rules|allowed|permitted|welcome|acceptable|guarantee|promise|how|whether|can|could|may|will|would|do|does|is|are)\b',t))
 if not question:return ()
 found=[]
 for topic in TOPICS:
  if not re.search(topic.request,t):continue
  if topic.name=='late_arrival' and not re.search(r'\b(table|seats?|reservations?|booking|arriv\w*|late|traffic|grace)\b',t):continue
  if topic.name=='cancellation' and not re.search(r'\b(policy|notice|warning|hours?|ahead|before|deadline|penalt\w*|fees?|how|if|cannot make)\b',t):continue
  if topic.name=='outside_alcohol' and not re.search(r'\b(outside|own|bring|brought|corkage|byob|supply|supplied|ourselves|themselves)\b',t):continue
  if topic.name=='outside_cake' and not re.search(r'\b(outside|bring|brought|bakery|own|home[ -]?made|permission)\b',t):continue
  if topic.name=='payment' and re.search(r'\b(?:bill|check)\b',t):continue
  found.append(topic)
 if any(t.name=='separate_bills' for t in found):found=[t for t in found if t.name!='payment']
 return tuple(found)


def policy_question(text):
 if policy_topics(text):return True
 return bool(re.search(r'\b(?:your|restaurants?|dining)\b.*\b(?:policy|policies|rules)\b|\b(?:policy|policies|rules)\b.*\b(?:your|restaurant|dining)\b',text,re.I)) and not re.search(r'\b(?:speak|talk|connect|transfer)\b',text,re.I)


def retrieval_query(text):
 topics=policy_topics(text)
 # Topic expansion improves recall for colloquial wording, not the answer text.
 return text+' '+ '; '.join(t.query for t in topics) if topics else text


def select_quotes(text,candidates):
 topics=policy_topics(text)
 stop={'what','when','where','how','do','does','is','are','can','could','may','will','would','i','we','you','your','our','the','a','an','to','my','me','for','of','in','with','should','it','have','much','about','policy','policies','rules','please','dining','room','restaurant','allowed','permitted','welcome','well','behaved','inside'}
 words=set(re.findall(r'[a-z]+',text.lower()))-stop
 selected=[]
 for topic in topics:
  relevant=[c for c in candidates if re.search(topic.evidence,c['text'],re.I)]
  if topic.name=='pets':
   assistance=bool(re.search(r'\b(assistance|service|guide|support)\b',text,re.I))
   relevant=[c for c in relevant if bool(re.search(r'\b(assistance|service|guide|support)\b',c['text'],re.I))==assistance]
  if topic.name=='cancellation':
   # Administrative lookup instructions do not answer notice/timing questions.
   relevant=[c for c in relevant if not re.search(r'\b(reference|phone number)\b',c['text'],re.I)]
  relevant.sort(key=lambda c:len(words&set(re.findall(r'[a-z]+',c['text'].lower()))),reverse=True)
  # Private-room availability and approval are often separate source sentences.
  count=2 if topic.name=='private_dining' else 1
  for c in relevant[:count]:
   if c not in selected and len(selected)<2 and len(' '.join(x['text'] for x in [*selected,c]).split())<=60:selected.append(c)
 if not topics:
  ranked=sorted(candidates,key=lambda c:len(words&set(re.findall(r'[a-z]+',c['text'].lower()))),reverse=True)
  if ranked and len(words&set(re.findall(r'[a-z]+',ranked[0]['text'].lower())))>=2:selected=ranked[:1]
 return selected
