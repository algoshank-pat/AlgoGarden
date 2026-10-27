"""Request-level checks through the public API and real private policy retrieval."""
import os,json,copy
from pathlib import Path
from datetime import datetime,timezone
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from run_private_decision_acceptance import production_baseline
ROOT=Path(__file__).parent

def main():
 out=ROOT/'results/information-requests'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S');data=out/'data';data.mkdir(parents=True)
 os.environ.update(RECEPTIONIST_FIXTURE_DIR=str(data),LANGSMITH_TRACING='false',RECEPTIONIST_DECISION_MODE='unified',RECEPTIONIST_DECISION_PROTOCOL='baseline',RECEPTIONIST_MCP_MODE='pooled')
 import chat_api as api,knowledge
 from fastapi.testclient import TestClient
 from receptionist import Tools
 tools=Tools(api.restaurant);business=tools.business_id;before=production_baseline();knowledge.ingest(api.restaurant['knowledge_id'])
 for i in range(10):tools.booking_store().book(business,{'name':'Private baseline','phone':'990022'+str(i).zfill(4),'date':'2026-11-07','time':'20:00','party_size':4,'ts':'2026-10-28'},40,0,'private-full-'+str(i))
 callers=[]
 for i,day in enumerate(['2026-11-09','2026-11-10']):
  phone='99003311'+str(22+i)
  row=tools.booking_store().book(business,{'name':'Private caller','phone':phone,'date':day,'time':'19:00','party_size':2,'ts':'2026-10-28'},40,0,'private-caller-'+str(i));callers.append((phone,row['id']))
 snapshot=tools.booking_store().list(business);results=[]
 fresh_availability=['Are any tables still available during December?','Next week, is there any space for us to eat?','Could we visit on one of the evenings in November?']
 fresh_policy=['Suppose traffic means we get there late — is our table kept?','Would a delayed train mean losing our reservation?','What is the allowed arrival delay for a reserved table?']
 (out/'heldout-before-run.json').write_text(json.dumps({'availability':fresh_availability,'policy':fresh_policy},indent=2))
 with TestClient(api.app) as client:
  def start(channel='chat'):return client.post('/api/sessions',json={'channel':channel}).json()['session_id']
  def turn(sid,text):
   response=client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':text});response.raise_for_status();v=response.json();assert not v.get('failed') and not v.get('handoff');return v
  def availability(item):
   label,question,channel,day=item;sid=start(channel);records=[]
   v=turn(sid,question);records.append(v);assert 'date' in v['reply'].lower() and 'phone' not in v['reply'].lower() and not v['structured']['tools_called']
   v=turn(sid,day+' at eight pm');records.append(v);assert 'people' in v['reply'].lower() and not v['structured']['tools_called']
   v=turn(sid,'for two people');records.append(v);truth=tools.check_table(2,day,'20:00')['available']
   assert v['reply'].startswith('Yes,')==truth and v['structured']['tools_called']==['check_table'] and 'phone' not in v['reply'].lower()
   assert all(not x['structured']['tools_called'] or x['structured']['tools_called']==['check_table'] for x in records)
   client.delete('/api/sessions/'+sid);return {'label':label,'question':question,'passed':True,'truth_available':truth,'replies':[x['reply'] for x in records]}
  cases=[('scored-6','Do you have anything free in November?','voice','2026-11-06'),('scored-30','Can we come sometime next week?','chat','2026-11-07')]+[('heldout-'+str(i),q,'voice' if i%2 else 'chat','2026-11-06') for i,q in enumerate(fresh_availability)]
  with ThreadPoolExecutor(max_workers=4) as executor:results.extend(executor.map(availability,cases))
  def lookup(person):
   phone,expected=person;sid=start('voice');turn(sid,'I would like to update my reservation.');v=turn(sid,phone)
   b=api.sessions[next(k for k in api.sessions if str(k)==sid)].bot
   assert b.reservation_changes.current['id']==expected and v['structured']['tools_called']==['lookup_reservations_by_phone']
   client.delete('/api/sessions/'+sid);return {'label':'concurrent-phone-lookup','passed':True,'matched_correct_private_reservation':True}
  with ThreadPoolExecutor(max_workers=2) as executor:results.extend(executor.map(lookup,callers))
  for i,question in enumerate(['If the train makes us twenty minutes late, will you still hold our table?']+fresh_policy):
   sid=start('voice' if i%2 else 'chat');b=api.sessions[next(k for k in api.sessions if str(k)==sid)].bot
   b.reserving=True;b.awaiting_confirmation=True;b.slots={'date':'2026-11-06','time':'19:00','party_size':2,'name':'Private caller','phone':'9900112233'};saved=copy.deepcopy(b.slots)
   v=turn(sid,question);assert '15 minutes' in v['reply'] and v['sources']==['cancellations.md'] and v['routing']['intent']=='policy' and v['structured']['tools_called']==['search_knowledge']
   assert b.slots==saved and b.awaiting_confirmation
   confirm=turn(sid,'Yes');assert not confirm['structured']['tools_called'] and 'correct' in confirm['reply'].lower() and b.slots==saved
   results.append({'label':'policy-'+str(i),'question':question,'reply':v['reply'],'sources':v['sources'],'passed':True,'pending_preserved':True,'yes_requires_original_readback':True});client.delete('/api/sessions/'+sid)
  for question,tool in [('Is parking complimentary during November?','get_location'),('Could you show me the menu for November?','get_menu'),('Please cancel my reservation',None),('Who is Albert Einstein?',None)]:
   sid=start();v=turn(sid,question)
   if tool:assert tool in v['structured']['tools_called'] and 'phone' not in v['reply'].lower()
   elif 'cancel' in question:assert 'phone' in v['reply'].lower()
   else:assert v['reply']=="I can't help you with this question."
   results.append({'label':'negative','question':question,'reply':v['reply'],'passed':True});client.delete('/api/sessions/'+sid)
 after=tools.booking_store().list(business);result={'method':'Private API, real policy embeddings/retrieval; success means fulfilling original request, not later booking completion. Fresh holdout text frozen before execution; no schema/prompt changes.','cases':results,'private_reservations_unchanged':snapshot==after,'production_baseline_before':before,'production_baseline_after':production_baseline()};result['production_baseline_unchanged']=result['production_baseline_before']==result['production_baseline_after']
 (out/'report.json').write_text(json.dumps(result,indent=2));(ROOT/'results/information-requests/latest.json').write_text(json.dumps(result,indent=2));print(json.dumps({'passed':len(results),'private_unchanged':result['private_reservations_unchanged'],'production_unchanged':result['production_baseline_unchanged'],'path':str(out)}));assert snapshot==after and result['production_baseline_unchanged']
if __name__=='__main__':main()
