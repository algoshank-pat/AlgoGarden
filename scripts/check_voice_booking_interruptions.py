"""Actual local STT/API/TTS conversation with an isolated reservation database."""
import asyncio,base64,copy,hashlib,io,json,os,sys,time,urllib.request,wave
from datetime import datetime,timezone
from pathlib import Path
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT/'.env',override=False)
call_context='--call-context' in sys.argv
feedback_flow='--feedback-flow' in sys.argv
spoken_name='--spoken-name' in sys.argv
dietary_checks='--dietary-checks' in sys.argv
offered_acceptance='--offered-acceptance' in sys.argv
post_confirmation_checks='--post-confirmation-checks' in sys.argv
location_flow='--location-flow' in sys.argv or post_confirmation_checks
explicit_name_confirmation='--explicit-name-confirmation' in sys.argv or call_context or feedback_flow or location_flow
out=ROOT/'results'/('voice-post-confirmation-guard' if post_confirmation_checks else 'voice-location-return' if location_flow else 'voice-offered-acceptance' if offered_acceptance else 'voice-dietary-recovery' if dietary_checks else 'voice-booking-feedback' if feedback_flow else 'voice-call-context' if call_context else 'voice-name-confirmation' if explicit_name_confirmation else 'voice-booking-interruptions')/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
out.mkdir(parents=True);(out/'private').mkdir()
if offered_acceptance:
 import sqlite3
 with sqlite3.connect('file:'+str(ROOT/'reservations.sqlite3')+'?mode=ro',uri=True) as source,sqlite3.connect(out/'private/reservations.sqlite3') as target:source.backup(target)
os.environ.update(RECEPTIONIST_FIXTURE_DIR=str(out/'private'),LANGSMITH_TRACING='false')
import numpy as np
from fastapi.testclient import TestClient
from freeze_customer100 import digest
import chat_api as api,browser_voice as voice
from customer_identity import local_today
from receptionist import parse_date
from information_requests import pending_state

def ux_hashes():return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'dashboard/src').rglob('*') if p.is_file()}
def reservation_snapshots():
 import sqlite3
 state={}
 for rel in ['reservations.sqlite3','results/voice-turn-ownership-20261029/private/reservations.sqlite3']:
  path=ROOT/rel
  if not path.exists():continue
  with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True) as db:
   db.row_factory=sqlite3.Row;rows=[dict(r) for r in db.execute('SELECT rowid AS _rowid,* FROM reservations ORDER BY rowid')]
  state[rel]={'count':len(rows),'sha256':hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()}
 return state
baseline=digest(ROOT/'reservations.sqlite3');ux=ux_hashes()
report={'method':'Synthetic Piper caller WAV -> 16k PCM -> actual Parakeet -> isolated voice API with actual default Ollama model -> actual streamed Piper reply. No physical microphone/speaker or VAD acceptance test.','baseline_before':baseline,'existing_reservations_before':reservation_snapshots(),'turns':[],'checks':[],'passed':False}
def save():(out/'report.json').write_text(json.dumps(report,indent=2,default=str),encoding='utf-8')
def check(name,condition):
 report['checks'].append({'name':name,'passed':bool(condition)});save();print(name,'PASS' if condition else 'FAIL',flush=True)
 assert condition,name
def pcm(data):
 with wave.open(io.BytesIO(data)) as w:rate=w.getframerate();samples=np.frombuffer(w.readframes(w.getnframes()),dtype='<i2').astype(np.float32)
 positions=np.arange(int(len(samples)*16000/rate))*rate/16000
 samples=np.interp(positions,np.arange(len(samples)),samples);buffer=io.BytesIO()
 with wave.open(buffer,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(np.clip(samples,-32768,32767).astype('<i2').tobytes())
 return buffer.getvalue()
async def speak(text,label):
 start=time.perf_counter();first=None;packets=[]
 response=voice.speak_stream(voice.SpeechRequest(text=text))
 async for raw in response.body_iterator:
  row=json.loads(raw);assert 'error' not in row,row
  if first is None:first=(time.perf_counter()-start)*1000
  packets.append(row);(out/f'{label}-reply-{row["index"]}.wav').write_bytes(base64.b64decode(row['audio']))
 return {'first_packet_ms':first,'tts_compute_ms':(time.perf_counter()-start)*1000,'speech_duration_ms':sum(p['duration_ms'] for p in packets)}
try:
 with TestClient(api.app) as client:
  s=client.post('/api/sessions',json={'channel':'voice'}).json();sid=s['session_id'];bot=api.sessions[sid].bot
  check('private-database',api.engine.booking_db.DB_PATH.resolve().is_relative_to((out/'private').resolve()))
  report['model']=api.MODEL
  check('exact-model-tag',api.MODEL=='gemma4:e2b-it-qat')
  with urllib.request.urlopen('http://127.0.0.1:11434/api/tags',timeout=10) as response: report['model_digest']=next(m['digest'] for m in json.load(response)['models'] if m['name']==api.MODEL)
  check('exact-model-digest',report['model_digest']=='07ea59a474013479c8b6b802bef095c40e964a1d776ba02f264c0e30e1aede0c')
  expected_date=parse_date('next Saturday',today=local_today(api.restaurant)).isoformat()
  original_rows=bot.tools.booking_store().list(bot.tools.business_id) if offered_acceptance else []
  original_ids={r['id'] for r in original_rows}
  def rows():return [r for r in bot.tools.booking_store().list(bot.tools.business_id) if r['id'] not in original_ids]
  start=time.perf_counter();voice.warm_audio(s['greeting']);report['audio_setup_ms']=(time.perf_counter()-start)*1000
  report['greeting']=s['greeting'];report['greeting_audio']=asyncio.run(speak(s['greeting'],'greeting'));save()
  def say(label,spoken,final=False,expected_count=0):
   audio_response=client.post('/api/voice/speak',json={'text':spoken});audio_response.raise_for_status()
   audio=pcm(audio_response.content);(out/f'{label}-caller.wav').write_bytes(audio)
   start=time.perf_counter();tr=voice.transcribe_wav(audio);stt_ms=(time.perf_counter()-start)*1000
   before=rows();start=time.perf_counter()
   response=client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':tr['text'] or '[No intelligible speech detected]','input_kind':tr['input_kind']});response.raise_for_status()
   result=response.json();api_ms=(time.perf_counter()-start)*1000
   timing=asyncio.run(speak(result['reply'],label))
   record={'label':label,'caller':spoken,'transcript':tr['text'],'reply':result['reply'],'stt_ms':stt_ms,'api_ms':api_ms,**timing,'result':result,'slots':copy.deepcopy(bot.slots),'confirming':bot.awaiting_confirmation,'database_before':before,'database_after':rows()}
   report['turns'].append(record);save();print(label,repr(tr['text']),'->',result['reply'],flush=True)
   check(label+'-one-generation',sum(c['component']=='ollama.chat' for c in result['structured']['calls'])<=1)
   if not final:check(label+('-no-extra-write' if expected_count else '-no-early-write'),len(rows())==expected_count and rows()==before)
   return record
  if offered_acceptance:
   r=say('01-availability','Do you have a table for two on November 9, 2026 at seven in the evening?')
   check('supplied-date-recognized','2026-11-09' in r['reply'] and 'check_table' in r['result']['structured']['tools_called'])
   say('02-book-offer','I would like to book that table.')
   r=say('03-new','No.')
   check('gate-under-two-seconds',r['api_ms']<2000)
   check('gate-no-generation',not any(c['component']=='ollama.chat' for c in r['result']['structured']['calls']))
   check('offer-fields-preserved',bot.slots=={'party_size':2,'date':'2026-11-09','time':'19:00'})
   say('04-phone','Nine nine zero zero eight eight one two three four.')
   say('05-name','My name is Alex Morgan.')
   r=say('06-name-confirm','Yes, that name is correct.')
   check('no-repeated-party-question',bot.awaiting_confirmation and bot.slots['party_size']==2 and 'How many' not in r['reply'])
   say('07-correct-time','Actually, change the time to half past eight in the evening.')
   draft=copy.deepcopy(bot.slots)
   r=say('08-cake-policy','Is a birthday cake from outside okay?')
   check('cake-policy-grounded','search_knowledge' in r['result']['structured']['tools_called'] and 'prohibited' in r['reply'])
   check('correction-preserved-through-policy',bot.slots==draft and api.engine.parse_time(bot.slots['time'])=='20:30')
   say('09-resume',"Let's return to my reservation.")
   r=say('10-confirm','Yes, please book it.',final=True)
   check('short-confirmation',r['reply']=='Your reservation is confirmed. Do you have any questions?')
   saved=copy.deepcopy(rows());check('exactly-one-correct-booking',len(saved)==1 and all(saved[0][k]==v for k,v in {'date':'2026-11-09','time':'20:30','party_size':2,'name':'Alex Morgan','phone':'9900881234'}.items()))
   r=say('11-status','Is my seat reserved?',expected_count=1)
   check('post-booking-status','confirmed' in r['reply'] and 'Alex Morgan' in r['reply'] and r['result']['structured']['tools_called']==['lookup_reservation'])
   for label,text in [('12-sure','Sure.'),('13-yes','Yes.')]:
    r=say(label,text,expected_count=1)
    check(label+'-invites-question','What else would you like to ask' in r['reply'] and not r['result']['structured']['tools_called'])
   r=say('14-end','Please end the call.',expected_count=1)
   check('call-ended',r['result']['ended'])
   check('no-duplicate-writes',rows()==saved and sum(t['result']['structured']['tools_called'].count('book_table') for t in report['turns'])==1)
   check('private-original-rows-unchanged',[r for r in bot.tools.booking_store().list(bot.tools.business_id) if r['id'] in original_ids]==original_rows)
   report['passed']=True
   raise SystemExit(0)
  r=say('01-booking','Can I book a table for four next Saturday at half past eight in the evening?')
  check('existing-new-gate','have a reservation' in r['reply'])
  if dietary_checks:
   original=api.sessions[sid].pending_reservation_request
   r=say('01a-dietary-question','What gluten-free dishes do you have?')
   check('dietary-gate-answer-grounded','check_dish' in r['result']['structured']['tools_called'] and 'cross-contact' in r['reply'])
   r=say('01b-dietary-yes','Yes, please.')
   check('dietary-yes-clarifies-intent','dietary information' in r['reply'] and 'phone' not in r['reply'].lower())
   check('dietary-yes-preserves-gate',api.sessions[sid].pending_reservation_request==original and not bot.reservation_changes.active)
  say('02-new','No, I do not have a reservation.' if dietary_checks else 'No.')
  r=say('03-phone','My phone number is nine nine zero zero one one two two three three.')
  check('asks-name','name' in r['reply'].lower())
  if spoken_name:
   r=say('04-spoken-name','My name is Test Guest.')
   check('name-asked-without-spelling','spell' not in report['turns'][-2]['reply'].lower())
  else:
   say('04-first-name','First name T as in Tiger, E as in Elephant, S as in Sun, T as in Tiger.')
   r=say('05-last-name','Last name G as in Garden, U as in Umbrella, E as in Elephant, S as in Sun, T as in Tiger.')
  if explicit_name_confirmation and bot._name_candidate!='Test Guest':
   check('uncertain-spelling-asks-repeat','repeat the spelling' in r['reply'].lower())
   report['name_spelling_retry']=True
   # Follow the agent's clarification, rather than correcting ASR text or
   # bypassing name collection in the test fixture.
   if 'first' not in (getattr(bot,'_name_parts',None) or {}):
    say('05a-repeat-first-name','First name T E S T.')
   if 'last' not in (getattr(bot,'_name_parts',None) or {}):
    say('05b-repeat-last-name','Last name G U E S T.')
  check('correct-name-awaits-confirmation',bot._name_candidate=='Test Guest' and 'name' not in bot.slots)
  name_result=say('06-name-confirm','Yes, that name is correct.' if explicit_name_confirmation else 'Yes.')
  if explicit_name_confirmation:
   from conversation_controls import name_assent
   check('explicit-name-approval-recognized',name_assent(name_result['transcript']))
   check('name-check-not-repeated','is that name correct' not in name_result['reply'].lower())
   check('name-approval-needs-no-generation',not any(c['component']=='ollama.chat' for c in name_result['result']['structured']['calls']))
  check('complete-draft',bot.awaiting_confirmation and bot.slots.get('name')=='Test Guest' and bot.slots.get('party_size')==4 and parse_date(bot.slots['date'],today=local_today(api.restaurant)).isoformat()==expected_date and api.engine.parse_time(bot.slots['time'])=='20:30')
  draft=pending_state(bot)
  if location_flow:
   r=say('07-address','What is your address?')
   check('address-grounded',r['reply'].startswith(api.restaurant['voice_address']) and r['result']['structured']['tools_called']==['get_location'])
   check('address-only-no-parking','valet' not in r['reply'].lower() and 'parking' not in r['reply'].lower())
   check('address-short',len(r['result']['spoken_reply'].split())<=30 and 'United States' not in r['reply'])
   check('address-return-invitation',r['reply'].endswith(api.RETURN_TO_RESERVATION))
   check('address-keeps-entire-draft',pending_state(bot)==draft)
   r=say('08-parking','Where can I park?')
   check('parking-grounded',api.restaurant['parking'] in r['reply'] and r['result']['structured']['tools_called']==['get_location'])
   check('parking-only-no-address','123 Demo' not in r['reply'])
   check('parking-short',len(r['result']['spoken_reply'].split())<=40)
   check('parking-return-invitation',r['reply'].endswith(api.RETURN_TO_RESERVATION))
   check('parking-keeps-entire-draft',pending_state(bot)==draft)
  else:
   r=say('07-pets','Can I bring my dog?')
   check('pet-answer-grounded','not permitted' in r['reply'].lower() and 'search_knowledge' in r['result']['structured']['tools_called'])
   check('pet-return-invitation',r['reply'].endswith(api.RETURN_TO_RESERVATION))
   check('pets-keeps-draft',pending_state(bot)==draft)
   r=say('08-gluten','Do you have gluten-free food?')
   check('dietary-answer-grounded','check_dish' in r['result']['structured']['tools_called'] and 'cross-contact' in r['reply'])
   check('dietary-return-invitation',r['reply'].endswith(api.RETURN_TO_RESERVATION))
   check('gluten-keeps-draft',pending_state(bot)==draft)
  r=say('09-return',"Yes, let's continue.")
  check('return-reads-details','Test Guest' in r['reply'] and 'correct' in r['reply'].lower() and pending_state(bot)==draft)
  r=say('10-final-confirm','Yes, please book it.',final=True)
  check('final-confirmation-with-question-only',r['reply']=='Your reservation is confirmed. Do you have any questions?')
  saved=rows();check('exactly-one-booking',len(saved)==1)
  check('correct-final-details',saved[0]['date']==expected_date and saved[0]['time']=='20:30' and saved[0]['party_size']==4 and saved[0]['name']=='Test Guest')
  check('only-final-turn-writes',all(not r['database_after'] for r in report['turns'][:-1]) and r['result']['structured']['tools_called'].count('book_table')==1)
  if post_confirmation_checks:
   committed=copy.deepcopy(saved)
   say('11-more-questions','Yes.',expected_count=1)
   for label,text in [('12-confirmed-address',"What's your address?"),('13-confirmed-parking','Where can I park?')]:
    r=say(label,text,expected_count=1)
    check(label+'-only-invites-questions',r['reply'].endswith('Do you have any other questions?') and 'return to your reservation' not in r['reply'])
   r=say('14-return-confirmed',"Let's return to my reservation.",expected_count=1)
   check('return-reports-saved-booking','is confirmed' in r['reply'] and r['result']['structured']['tools_called']==['lookup_reservation'])
   for label,text in [('15-confirm-again','Yes, please book it.'),('16-confirm-variation','Yeah, go ahead and book it.')]:
    r=say(label,text,expected_count=1)
    check(label+'-does-not-reopen',r['reply'].startswith('Your reservation is already confirmed.') and not bot.reserving and api.sessions[sid].pending_reservation_request is None)
   r=say('16b-unfamiliar-approval',"Lovely, let's make that happen.",expected_count=1)
   check('semantic-approval-never-reopens',not bot.reserving and api.sessions[sid].pending_reservation_request is None and 'phone number' not in r['reply'].lower())
   r=say('17-no-thanks','No, thank you.',expected_count=1)
   check('refusal-offers-feedback-not-phone',r['result']['survey']['status']=='offered' and 'phone' not in r['reply'].lower())
   r=say('18-decline-survey','No, thank you.',expected_count=1)
   check('declining-survey-ends-call',r['result']['ended'])
   check('completed-record-unchanged',rows()==committed)
   check('single-write-entire-call',sum(t['result']['structured']['tools_called'].count('book_table') for t in report['turns'])==1)
  if feedback_flow:
   committed=copy.deepcopy(saved)
   r=say('11-have-question','Yes, I have a question.',expected_count=1)
   check('invites-question',r['reply'] in {'Go ahead. What else would you like to ask?','Go ahead. I can help you answer them.'} and not r['result']['structured']['tools_called'])
   r=say('12-hours-question','What are your opening hours?',expected_count=1)
   check('answers-hours','get_hours' in r['result']['structured']['tools_called'] and not r['result']['ended'])
   r=say('13-no-more-questions','No more questions.',expected_count=1)
   check('offers-feedback',r['result']['survey']['status']=='offered')
   r=say('14-feedback-consent','Yes, please.',expected_count=1)
   check('asks-feedback-rating',r['result']['survey']['status']=='awaiting_rating')
   r=say('15-feedback-rating','My experience was good.',expected_count=1)
   check('feedback-completed',r['result']['survey']['status']=='completed' and r['result']['survey']['rating']=='good' and r['result']['ended'])
   dashboard=client.get('/api/owner/dashboard').json()
   recorded=next(c for c in dashboard['calls'] if c['id']==sid)
   check('feedback-saved-to-correct-call',recorded['feedback']['rating']=='good')
   check('one-booking-tool-call-entire-conversation',sum(t['result']['structured']['tools_called'].count('book_table') for t in report['turns'])==1)
   check('reservation-unchanged-through-feedback',rows()==committed)
  if call_context:
   committed=copy.deepcopy(saved)
   check('successful-tool-receipt-remembered',len(api.sessions[sid].reservation_receipts)==1)
   say('11-pets-after-booking','Can I bring my dog?',expected_count=1)
   say('12-gluten-after-booking','Do you have gluten-free food?',expected_count=1)
   r=say('13-more-questions','Uh sure.',expected_count=1)
   check('acknowledgement-invites-question','what else would you like to ask' in r['reply'].lower() and not r['result']['structured']['tools_called'])
   r=say('14-confirm-saved-status','Did you reserve the seat for me?',expected_count=1)
   check('remembers-actual-committed-booking','is confirmed' in r['reply'] and expected_date in r['reply'] and '20:30' in r['reply'] and 'Test Guest' in r['reply'])
   check('status-is-grounded-read-only',r['result']['structured']['tools_called']==['lookup_reservation'] and rows()==committed)
   check('status-does-not-restart-gate',api.sessions[sid].pending_reservation_request is None and not bot.reserving)
   r=say('15-end-call','Please end the call.',expected_count=1)
   check('end-command-ends-conversation',r['result']['ended'] and 'repeat' not in r['reply'].lower())
   check('completed-booking-unchanged-after-followups',rows()==committed)
  report['passed']=True
except Exception as error:
 report['error']=repr(error);raise
finally:
 report['baseline_after']=digest(ROOT/'reservations.sqlite3');report['ux_unchanged']=ux_hashes()==ux
 report['existing_reservations_after']=reservation_snapshots();report['all_existing_reservations_unchanged']=report['existing_reservations_before']==report['existing_reservations_after']
 report['baseline_unchanged']=baseline==report['baseline_after'];report['passed']=report['passed'] and report['ux_unchanged'] and report['baseline_unchanged'] and report['all_existing_reservations_unchanged']
 report['source_hashes']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'chat_api.py',ROOT/'conversation_controls.py',ROOT/'conversation_memory.py',ROOT/'information_requests.py',ROOT/'name_spelling.py',ROOT/'voice/phrases.py',Path(__file__)]}
 save();print('REPORT',out/'report.json','PASSED',report['passed'],flush=True)
