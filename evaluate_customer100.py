"""Testing only: 100 real API/model conversations, private SQLite and RAG copies."""
import os
os.environ['LANGSMITH_TRACING']='false'
os.environ['RECEPTIONIST_DECISION_MODE']='unified'
import sys,json,hashlib,sqlite3,shutil,time,re,argparse,subprocess
from pathlib import Path
from datetime import timedelta,datetime,timezone
from uuid import uuid4
from collections import Counter
import receptionist as engine
import chat_api as api
import local_audit,knowledge
from fastapi.testclient import TestClient
from customer_identity import local_today,normalize_phone
from evaluate_readiness import baseline

ROOT=Path(__file__).parent
FILES=['receptionist.py','routing.py','decision_engine.py','chat_api.py','reservation_changes.py','booking_db.py','knowledge.py','reservation_mcp_client.py']
def dump(path,value):path.write_text(json.dumps(value,indent=2,ensure_ascii=False),encoding='utf-8')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def copydb(source,dest):
 with sqlite3.connect(f'file:{source}?mode=ro',uri=True) as a,sqlite3.connect(dest) as b:a.backup(b)
def corpus():
 cases=[]
 openings=['Could you book a table for {party} on {date} at {time}?','We would love dinner for {party} on {date}, around {time}.','Any chance you can fit {party} of us in on {date} at {time}?','I need a reservation for {party} people on {date} at {time}.','Please reserve a table for {party} guests on {date} at {time}.','Can we come for dinner on {date} at {time}? There are {party} of us.','I am hoping to book for {party}, {date}, {time}.','A table for {party} on {date} at {time}, please.']
 names=['Priya Shah','Alex Morgan','Sam Patel','Maya Rao','Jordan Lee','Nina Das','Arun Kumar','Riya Sen']
 for i in range(100):
  kind='booking' if i<40 else 'update' if i<55 else 'cancel' if i<65 else 'lookup' if i<75 else 'information' if i<85 else 'policy' if i<95 else 'negative'
  case={'id':i+1,'kind':kind,'channel':'chat' if i%2==0 else 'voice','name':names[i%8],'phone':'990088'+str(i).zfill(4),'party':2+i%3,'opening_template':openings[i%8]}
  if kind=='booking':case['variant']='broad' if i in [5,13,21,29,37] else 'correction' if i in [6,14,22,30,38] else 'standard'
  cases.append(case)
 return cases

INFO=[('What time does dinner start?',r'18:00'),('What time does lunch finish?',r'15:00'),('Where is the restaurant?',r'jayanagar'),('Is there parking for a scooter?',r'two.wheeler'),('How much is Masala Dosa?',r'12'),('Does Masala Dosa contain dairy?',r'dairy'),('Is Anjal Tawa Fry available?',r'unavailable'),('What allergens are in Bisi Bele Bath?',r'peanuts'),('How much is Filter Coffee?',r'4'),('What are today\'s specials?',r'neer dosa')]
POLICY=[('If the train makes us twenty minutes late, will you still hold our table?','What is your late arrival policy?',r'15 minutes'),('How much warning do you need if I cancel?','What is your cancellation policy?',r'2 hours'),('May I bring my little dog inside?','What is your policy on pets indoors?',r'not permitted'),('Can we bring our own bottle of wine?','What is your corkage policy?',r'not permitted|not offered'),('Is a birthday cake from outside okay?','What is your policy on birthday cakes?',r'cakes.*welcome'),('Are there high chairs for small children?','What is your policy for kids and high chairs?',r'high chairs'),('Can I pay with UPI?','What payment methods do you accept?',r'upi'),('Will you guarantee us a private room?','What is the policy on private rooms?',r'cannot confirm|cannot.*promise|require.*approval'),('Can our group split the bill?','What is your policy on separate bills?',r'separate bills'),('Can you guarantee wheelchair seating?','What is your accessibility policy?',r'contact staff|confirm suitable')]
NEG=[('Who was Isaac Newton?',r"can.t help"),('Write Python code for my trading bot.',r"can.t help"),('Can I speak with a staff member?',r'connect'),('I need to order takeaway food.',r'staff.*order|connect'),('What is the weather in Mumbai?',r"can.t help")]

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--run-dir');parser.add_argument('--resume',action='store_true');args=parser.parse_args()
 out=Path(args.run_dir) if args.run_dir else ROOT/'results/customer100'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
 out.mkdir(parents=True,exist_ok=True)
 before=baseline();assert before['count']==1911
 baseline_path=out/'baseline-copy.sqlite3'
 if not args.resume:copydb(ROOT/'reservations.sqlite3',baseline_path)
 knowledge.INDEX_PATH=out/'chroma-private'
 if not args.resume:shutil.copytree(ROOT/'data/chroma',knowledge.INDEX_PATH,dirs_exist_ok=True)
 local_audit.CALLS_FILE=out/'calls.jsonl'
 engine.BOOKINGS_FILE=out/'empty-bookings.jsonl';engine.MESSAGES_FILE=out/'messages.jsonl';engine.ORDERS_FILE=out/'orders.jsonl'
 local_audit.REVIEWS_FILE=out/'reviews.jsonl';local_audit.NOTES_FILE=out/'notes.jsonl'
 cases=json.loads((out/'manifest.json').read_text(encoding='utf-8')) if args.resume else corpus()
 if not args.resume:dump(out/'manifest.json',cases)
 report={'method':'100 authored adaptive text conversations through FastAPI TestClient, actual unified local model, actual tools/MCP/SQLite, copied RAG index; 50 chat and 50 voice text-path sessions, no audio','baseline_before':before,'source_hashes_before':{f:sha(ROOT/f) for f in FILES},'scoring':'Mutually exclusive: wrong_outcome if unauthorized/incorrect reservation write or false success or fact contradicts oracle; otherwise goal achieved with/without repeated/rephrased valid information; otherwise safe_failure (includes stalls, handoff and missed answers without harm). Planned corrections are not repetitions. Max 12 turns and 2 repair attempts.','results':[]}
 if args.resume:
  report=json.loads((out/'report.json').read_text(encoding='utf-8'));assert report['baseline_before']==before
  assert all(sha(ROOT/f)==s for f,s in report['source_hashes_before'].items()),'Production sources changed; do not mix versions'
  report.setdefault('resumptions',[]).append({'after_completed':len(report['results']),'reason':'User requested explicit per-conversation latency breakdown; existing audit spans retained.'})
 dump(out/'report.json',report)
 with TestClient(api.app) as client:
  for c in cases:
   if any(done['case']['id']==c['id'] for done in report['results']):continue
   started=time.perf_counter();folder=out/f"case-{c['id']:03d}";folder.mkdir(exist_ok=True)
   engine.booking_db.DB_PATH=folder/'private.sqlite3';copydb(baseline_path,engine.booking_db.DB_PATH)
   tools=engine.Tools(api.restaurant);store=tools.booking_store();bid=tools.business_id
   today=local_today(api.restaurant);day=today+timedelta(days=7+c['id']%20)
   # Select an actually free slot in the copied baseline, before conversation.
   for offset in range(35):
    day0=day+timedelta(days=offset)
    if tools.check_table(c['party'],day0.isoformat(),'19:00').get('available') and tools.check_table(c['party'],day0.isoformat(),'20:30').get('available'):
     day=day0;break
   goal={'date':day.isoformat(),'time':'19:00','party_size':c['party'],'name':c['name'],'phone':c['phone']}
   seed=None
   if c['kind'] in {'update','cancel','lookup'}:
    rec={**goal,'ts':'2026-10-27T00:00:00','source':'chat'}
    seed=store.book(bid,rec,40,0,f"eval-seed-{c['id']}")
    assert seed['booked'];goal['id']=seed['id']
    if c['kind']=='update':goal['time']='20:30'
   initial=store.list(bid);initial_by_id={r['id']:r for r in initial}
   record={'case':c,'goal':goal,'turns':[],'repairs':0,'wrong_reasons':[],'success':False}
   sid=None;confirmed=False;correction_done=False;requests=Counter();answered_fields=set();repair_streak=0
   def say(text):
    nonlocal confirmed
    confirmation=re.sub(r'[^a-z ]','',text.lower()).strip()
    if confirmation in {'yes','yes please','correct','confirm'}:confirmed=True
    t0=time.perf_counter();resp=client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':text})
    resp.raise_for_status();data=resp.json()
    record['turns'].append({'caller':text,'response':data,'elapsed_ms':round(1000*(time.perf_counter()-t0),2)})
    return data
   def ownrows():return [r for r in store.list(bid) if normalize_phone(r['phone'])==normalize_phone(c['phone'])]
   def reached(data):
    rows=ownrows();kind=c['kind'];reply=data.get('reply','')
    if kind=='booking':return len(rows)==1 and rows[0]['status']=='active' and all(rows[0][k]==goal[k] for k in ['date','time','party_size','name']) and any('book_table' in t['response'].get('structured',{}).get('tools_called',[]) for t in record['turns'])
    if kind=='update':return len(rows)==1 and rows[0]['time']==goal['time'] and rows[0]['status']=='active' and rows[0]['version']==2
    if kind=='cancel':return len(rows)==1 and rows[0]['status']=='cancelled'
    if kind=='lookup':return 'reservation is for' in reply.lower() and goal['date'] in reply and goal['time'] in reply
    entry=INFO[c['id']-76] if kind=='information' else POLICY[c['id']-86] if kind=='policy' else NEG[c['id']-96]
    if kind=='negative' and c['id'] in {98,99}:return bool(data.get('handoff'))
    pattern=entry[-1]
    return bool(re.search(pattern,reply,re.I)) and (kind!='policy' or bool(data.get('sources')))
   try:
    sid=client.post('/api/sessions',json={'channel':c['channel']}).json()['session_id']
    say('Hello')
    kind=c['kind'];human_day=day.strftime('%B %d, %Y');human_time='seven in the evening'
    opening=c['opening_template'].format(party=c['party'],date=human_day,time=human_time)
    if kind=='booking' and c['variant']=='broad':opening=['Do you have anything free in November?','Any openings during November?','What is your availability in November?','Can we come sometime next week?','Do you have tables available next week?'][(c['id']//8)%5]
    if kind=='update':opening=['Could I change my reservation to half past eight in the evening?','Please update my booking to 8:30 pm.','I need to reschedule my reservation to 20:30.'][c['id']%3]
    if kind=='cancel':opening=['I need to cancel my reservation.','Please cancel my booking.','Can you cancel our table reservation?'][c['id']%3]
    if kind=='lookup':opening=['Can you find my reservation?','I would like to check my booking.','Please look up my reservation.'][c['id']%3]
    if kind=='information':opening=INFO[c['id']-76][0]
    if kind=='policy':opening=POLICY[c['id']-86][0]
    if kind=='negative':opening=NEG[c['id']-96][0]
    if kind=='booking' and c['variant']!='broad':answered_fields.update({'date','time','party'})
    if kind=='update':answered_fields.add('change')
    data=say(opening)
    for step in range(10):
     if reached(data):record['success']=True;break
     if data.get('ended') or data.get('handoff'):break
     reply=data.get('reply','').lower();text=None;field=None
     if kind in {'information','policy','negative'}:
      if repair_streak>=1:break
      text=POLICY[c['id']-86][1] if kind=='policy' else opening
      record['repairs']+=1;repair_streak+=1
     elif 'do you have a reservation with us' in reply:text='No, I do not.' if kind=='booking' else 'Yes, I do.';confirmed=False
     elif 'phone' in reply and any(x in reply for x in ['what phone','phone number','digit by digit','check the phone']):
      # Readbacks contain phone too; only actual questions belong here.
      if 'is that correct' not in reply and 'say yes' not in reply:
       field='phone';text=' '.join(['zero','one','two','three','four','five','six','seven','eight','nine'][int(d)] for d in c['phone']) if c['id']%2 else c['phone']
     if text is None and ('is that correct' in reply or 'say yes' in reply):
      if kind=='booking' and c.get('variant')=='correction' and not correction_done:
       text='No, change the time to half past eight instead.';goal['time']='20:30';correction_done=True
      else:text='Yes, please.'
     if text is None and 'which reservation' in reply:text='The first one, please.'
     if text is None and ('short date range' in reply or 'around what time' in reply):
      text=f'{human_day} at seven in the evening, for {c["party"]} people.';answered_fields.update({'date','time','party'})
     if text is None and ('name' in reply and ('may i' in reply or 'what name' in reply)):field='name';text=c['name']
     if text is None and ('how many' in reply or 'number of guests' in reply):field='party';text=f'{c["party"]} guests'
     if text is None and ('what date' in reply or 'which date' in reply):field='date';text=human_day
     if text is None and ('what time' in reply or 'which time' in reply):field='time';text='Half past eight in the evening' if goal['time']=='20:30' else 'Seven in the evening'
     if text is None and ('what date, time or party' in reply or 'what would you like to change' in reply or 'what new date' in reply):field='change';text='Change the time to 8:30 pm.'
     if text is None and 'change or cancel' in reply:text='Please keep the existing reservation unchanged.' if kind=='lookup' else 'Please update my reservation to 8:30 pm.'
     if text is None:
      if repair_streak>=2:break
      text=(f'I want to book for {c["party"]} on {human_day} at {"8:30 pm" if goal["time"]=="20:30" else "7 pm"}.' if kind=='booking' else opening)
      record['repairs']+=1;repair_streak+=1
     if field:
      if field in answered_fields:record['repairs']+=1
      answered_fields.add(field)
     if record['repairs']>2:break
     data=say(text)
    # Final oracle checks every baseline/fixture row and all created rows.
    final=store.list(bid);final_by_id={r['id']:r for r in final}
    for key,old in initial_by_id.items():
     new=final_by_id.get(key)
     if seed and key==seed['id']:
      allowed={'time','version'} if kind=='update' else {'status','version'} if kind=='cancel' else set()
      if new is None or any(old[k]!=new[k] for k in old if k not in allowed):record['wrong_reasons'].append('Existing reservation changed outside requested fields')
      if new and kind=='update' and new['time'] not in {'19:00','20:30'}:record['wrong_reasons'].append('Wrong update time saved')
     elif new!=old:record['wrong_reasons'].append('Unrelated/baseline reservation changed')
    added=[r for r in final if r['id'] not in initial_by_id]
    if kind!='booking' and added:record['wrong_reasons'].append('Unrequested reservation created')
    if kind=='booking' and (len(added)>1 or any(any(r[k]!=goal[k] for k in ['date','time','party_size','name','phone']) for r in added)):record['wrong_reasons'].append('Wrong or duplicate booking saved')
    if (added or (seed and final_by_id.get(seed['id'])!=initial_by_id[seed['id']])) and not confirmed:record['wrong_reasons'].append('Reservation changed without confirmation')
    last=record['turns'][-1]['response'].get('reply','').lower()
    if not record['success'] and any(x in last for x in ['your reservation is updated','your reservation has been cancelled','your table is booked','reservation is confirmed']):record['wrong_reasons'].append('False completion claim')
    if kind=='information' and not record['success'] and record['turns'][-1]['response'].get('structured',{}).get('tools_called') in [['get_hours'],['get_location'],['check_dish']]:record['wrong_reasons'].append('Information answer failed factual oracle')
    record['final_customer_rows']=ownrows();record['baseline_copy_preserved']=not any('baseline' in x for x in record['wrong_reasons'])
   except Exception as err:
    record['harness_error']=f'{type(err).__name__}: {err}'
   finally:
    if sid:client.delete(f'/api/sessions/{sid}')
   record['outcome']='harness_error' if record.get('harness_error') else 'wrong_outcome' if record['wrong_reasons'] else 'success_after_repetition' if record['success'] and record['repairs'] else 'success_first_time' if record['success'] else 'safe_failure'
   record['duration_seconds']=round(time.perf_counter()-started,2)
   dump(folder/'conversation.json',record)
   # Synthetic private rows are disposed with their entire private database.
   for suffix in ['','-wal','-shm']:
    path=Path(str(engine.booking_db.DB_PATH)+suffix)
    if path.exists():path.unlink()
   record['private_database_removed']=not engine.booking_db.DB_PATH.exists()
   report['results'].append(record);report['counts']=dict(Counter(r['outcome'] for r in report['results']))
   report['completed']=len(report['results']);report['baseline_current']=baseline();assert report['baseline_current']==before
   dump(out/'report.json',report)
   # Read-only reporting hook: refresh measured parameters/CSV after each case.
   # Reporter failures are recorded separately and never replay a customer turn.
   builder=ROOT/'results/customer100/csv-export/export_metrics.mjs'
   runtime=Path(r'C:\Users\patel\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe')
   if builder.exists() and runtime.exists():
    with (out/'csv-refresh.log').open('a',encoding='utf-8') as refresh_log:
     try:
      subprocess.run([sys.executable,str(ROOT/'latency_customer100.py'),str(out)],stdout=refresh_log,stderr=subprocess.STDOUT,check=True,timeout=60)
      subprocess.run([str(runtime),str(builder),str(out)],stdout=refresh_log,stderr=subprocess.STDOUT,check=True,timeout=60)
     except Exception as error:refresh_log.write(f'Report refresh failed: {type(error).__name__}: {error}\n')
   print(c['id'],kind,record['outcome'],report['counts'],flush=True)
 report['baseline_after']=baseline();report['baseline_unchanged']=before==report['baseline_after']
 report['source_hashes_after']={f:sha(ROOT/f) for f in FILES};report['production_sources_unchanged']=report['source_hashes_before']==report['source_hashes_after']
 report['percentages']={key:round(100*report['counts'].get(key,0)/100,1) for key in ['success_first_time','success_after_repetition','safe_failure','wrong_outcome','harness_error']}
 dump(out/'report.json',report);print('COMPLETE',out,report['percentages'],flush=True)
if __name__=='__main__':main()
