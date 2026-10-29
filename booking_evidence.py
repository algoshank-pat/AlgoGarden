"""Recover only unambiguous literal booking fields; never infer from a draft."""
import re

def explicit_fields(text):
 from decision_evidence import date_phrases,clock_phrases,negative_prefix
 from decision_fastpaths import MONTH
 result={}
 dates=[m for m in date_phrases(text) if not negative_prefix(text[:m[0]])]
 clocks=[m for m in clock_phrases(text) if not negative_prefix(text[:m[0]])]
 if len(dates)==1:result['date']=re.sub(r'^on\s+','',dates[0][2],flags=re.I)
 if len(clocks)==1:result['time']=clocks[0][2]
 numbers=dict(zip('one two three four five six seven eight nine ten eleven twelve'.split(),range(1,13)))
 n=r'(\d{1,2}|'+ '|'.join(numbers)+r')'
 matches=list(re.finditer(r'\b(?:for|party of|table for)\s+'+n+r'\b|\b'+n+r'\s+(?:people|guests|of us)\b',text,re.I))
 positive=[]
 for m in matches:
  if negative_prefix(text[:m.start()]):continue
  if re.match(r'\s+(?:of\s+)?'+MONTH+r'\b',text[m.end():],re.I):continue
  raw=(m[1] or m[2]).lower();value=int(raw) if raw.isdigit() else numbers[raw]
  if 1<=value<=99:positive.append(value)
 if len(set(positive))==1 and not re.search(r'\b(?:or|either)\b',text,re.I):result['party_size']=positive[0]
 return result
