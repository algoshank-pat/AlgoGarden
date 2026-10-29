"""Small source-backed FAQ cache. No generation, embeddings or vector database."""
import copy,hashlib,json,re
from functools import lru_cache

@lru_cache(maxsize=64)
def _snapshot(business,documents):
    # The complete content is part of the key: edits cannot reuse stale facts.
    from policy_requests import TOPICS
    facts=[]
    for source,text in documents:
        from knowledge import sentences as split_sentences
        for topic in TOPICS:
            sentences=tuple(line for line in split_sentences(text) if re.search(topic.evidence,line,re.I))
            if sentences:facts.append((topic.name,source,text,sentences))
    return tuple(facts)

def lookup(business,query,docs):
    """Only recognized, stable single-facet facts bypass semantic retrieval."""
    from policy_requests import policy_topics
    topics=policy_topics(query)
    if len(topics)!=1:return None
    topic=topics[0].name
    gates={
      'pets':r'\b(bring|take|allow(?:ed)?|permitted|welcome|friendly|okay|policy|policies)\b',
      'late_arrival':r'\b(hold|held|grace|late|delayed|traffic)\b',
      'cancellation':r'\b(notice|ahead|before|deadline|warning)\b',
      'outside_cake':r'\b(bring|brought|outside|bakery|own|home[ -]?made|permission|allowed|permitted)\b',
      'outside_alcohol':r'\b(bring|outside|corkage|byob)\b',
      'children':r'\b(high[ -]?chairs?|kid[ -]?friendly|children welcome)\b',
      'payment':r'\b(methods?|accept\w*|cash|upi|debit|credit|cards?)\b',
      'separate_bills':r'\b(split|separate)\b',
      'accessibility':r'\b(step[ -]?free|wheelchair)\b',
      'dress':r'\b(dress code|attire|shirts?|footwear)\b',
    }
    if topic not in gates or not re.search(gates[topic],query,re.I):return None
    # These ask for facts beyond the simple indoor/assistance-animal policy.
    if re.search(r'\b(fees?|charges?|vaccin\w*|certificat\w*|breeds?|weights?|parking|menu|book|reserve|update|bitcoin|crypto|seatbelts?|straps?)\b',query,re.I):return None
    if topic=='pets' and re.search(r'\b(patio|terrace|outdoors?|outside|hours?|cancel)\b',query,re.I):return None
    facts=_snapshot(business,tuple(sorted(docs.items())))
    if not facts:return None
    digest=hashlib.sha256(json.dumps(docs,sort_keys=True).encode()).hexdigest()
    chunks=[{'id':'faq:'+digest+':'+source,'source':source,'text':text,'sentences':list(sentences),'score':None,'business':business} for facet,source,text,sentences in facts if facet==topic]
    if not chunks:return None
    return {'relevant':True,'chunks':copy.deepcopy(chunks),'retrieval_mode':'source_faq_cache','business':business,'source_digest':digest}
