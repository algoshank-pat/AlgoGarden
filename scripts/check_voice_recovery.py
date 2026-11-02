"""Real local synthetic speech, private API/state and first streamed audio timings."""
import os,sys,json,time,io,wave,base64,asyncio,hashlib,urllib.request
from pathlib import Path
from uuid import uuid4
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
out=ROOT/'results/voice-recovery-20261029'/sys.argv[1];out.mkdir(parents=True,exist_ok=False);(out/'private').mkdir()
os.environ.update(RECEPTIONIST_FIXTURE_DIR=str(out/'private'),LANGSMITH_TRACING='false',RECEPTIONIST_DECISION_PROTOCOL='baseline')
from freeze_customer100 import digest
before=digest(ROOT/'reservations.sqlite3')
assert before=={'count':1911,'raw_rows_sha256':'ff8283a4fd3bd9cfb6394d82c10119c012e54bfa5d710fbc40d817017d3f8141'}
from fastapi.testclient import TestClient
import chat_api as api,browser_voice as voice
report={'method':'Synthetic Piper caller audio -> 16k PCM -> actual Parakeet -> private voice API -> real Piper streamed WAV packets. First-ready is synthesis/transport-ready, not physical speaker onset. VAD timing separately tested in browser logic.','turns':[],'checks':[],'baseline_before':before,'sources':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [*ROOT.glob('*.py'),*ROOT.glob('dashboard/src/*.mjs'),ROOT/'dashboard/src/VoiceTest.jsx']}}
def save():(out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
def check(name,ok):report['checks'].append({'name':name,'passed':bool(ok)});print(name,'PASS' if ok else 'FAIL',flush=True);save()
def pcm(data):
 with wave.open(io.BytesIO(data)) as w:rate=w.getframerate();samples=np.frombuffer(w.readframes(w.getnframes()),dtype='<i2').astype(np.float32)
 positions=np.arange(int(len(samples)*16000/rate))*rate/16000
 samples=np.interp(positions,np.arange(len(samples)),samples);b=io.BytesIO()
 with wave.open(b,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(np.clip(samples,-32768,32767).astype('<i2').tobytes())
 return b.getvalue()
async def speech(text,n):
 start=time.perf_counter();packets=[];first=None
 response=voice.speak_stream(voice.SpeechRequest(text=text))
 async for raw in response.body_iterator:
  if first is None:first=(time.perf_counter()-start)*1000
  packet=json.loads(raw);assert 'error' not in packet;packets.append(packet)
  (out/f'{n:02d}-reply-{packet["index"]}.wav').write_bytes(base64.b64decode(packet['audio']))
 return {'first_packet_ms':first,'synthesis_total_ms':(time.perf_counter()-start)*1000,'audio_duration_ms':sum(p['duration_ms'] for p in packets),'packets':len(packets)}
with TestClient(api.app) as c:
 s=c.post('/api/sessions',json={'channel':'voice'}).json();sid=s['session_id'];b=api.sessions[sid].bot
 start=time.perf_counter();voice.warm_audio(s['greeting']);report['speech_resource_warmup_ms']=(time.perf_counter()-start)*1000;save()
 def rows():return b.tools.booking_store().list(b.tools.business_id)
 def say(label,spoken):
  n=len(report['turns'])+1
  caller=c.post('/api/voice/speak',json={'text':spoken});caller.raise_for_status();audio=pcm(caller.content);(out/f'{n:02d}-caller.wav').write_bytes(audio)
  started=time.perf_counter();tr=voice.transcribe_wav(audio);stt=(time.perf_counter()-started)*1000
  text=tr['text'] or '[No intelligible speech detected]';old=rows();started=time.perf_counter()
  response=c.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':text,'input_kind':tr['input_kind']});response.raise_for_status();r=response.json();api_ms=(time.perf_counter()-started)*1000
  synthesis=asyncio.run(speech(r['reply'],n))
  record={'label':label,'caller':spoken,'transcript':text,'reply':r['reply'],'stt_ms':stt,'api_ms':api_ms,**synthesis,'recognized_audio_to_first_packet_ms':stt+api_ms+synthesis['first_packet_ms'],'result':r,'slots':dict(b.slots),'name_candidate':getattr(b,'_name_candidate',None),'confirming':b.awaiting_confirmation,'database_before':old,'database_after':rows()}
  report['turns'].append(record);save();check(label+'-one-call',sum(x['component']=='ollama.chat' for x in r['structured']['calls'])<=1)
  return record
 try:
  r=say('request','Can I book a table for five next Saturday at five in the evening?');check('existing-new-gate','have a reservation' in r['reply'])
  say('new','No.');r=say('phone','My number is two zero two five five five zero one seven three.');check('asks-to-spell-name','spell' in r['reply'] and 'name' in r['reply'])
  say('repeat','Say it.');check('repeat-is-not-name','name' not in b.slots and not getattr(b,'_name_candidate',None))
  r=say('highchair',"I have a child. I'm looking for a high chair.");check('highchair-grounded','high chairs are available on request' in r['reply'])
  say('first-name','First name Tango Echo Sierra Tango.');r=say('last-name','Last name Golf Uniform Echo Sierra Tango.')
  if getattr(b,'_name_candidate',None)!='Test Guest':
   report['name_repair_required']=True
   if 'first' not in getattr(b,'_name_parts',{}):say('repeat-first-spelling','First name T as in Tiger, E as in Elephant, S as in Sun, T as in Tiger.')
   if 'last' not in getattr(b,'_name_parts',{}):say('repeat-last-spelling','Last name G as in Garden, U as in Umbrella, E as in Elephant, S as in Sun, T as in Tiger.')
  check('decoded-name-awaits-own-confirmation',getattr(b,'_name_candidate',None)=='Test Guest' and 'name' not in b.slots and not rows())
  r=say('verify-name','Yes please.');check('outside-hours-offer','offer' in r['reply'].lower() and '18:00' not in b.slots.get('time',''))
  old=dict(b.slots);say('confused-yes',"Yes. What? We're skipping how we.");check('confused-yes-cannot-select-time',b.slots==old and not b.awaiting_confirmation and not rows())
  r=say('accept-alternative','Yes please.');check('full-readback-before-commit',b.awaiting_confirmation and 'Test Guest' in r['reply'] and not rows())
  say('pause','Yes! Wait, two minutes.');check('pause-cannot-book',not rows() and b.awaiting_confirmation)
  r=say('resume',"Let's complete the reservation.");check('resume-reads-details','Test Guest' in r['reply'] and 'correct' in r['reply'].lower() and not rows())
  say('confirm','Yes please.')
  if not rows() and not b.ended:
   report['confirmation_repeat_required']=True
   say('repeat-confirm','I confirm this reservation.')
  check('only-final-confirmation-writes',len(rows())==1 and rows()[0]['name']=='Test Guest' and rows()[0]['party_size']==5 and rows()[0]['time']=='18:00')
 finally:
  c.post(f'/api/sessions/{sid}/stop');report['baseline_after']=digest(ROOT/'reservations.sqlite3');check('baseline-unchanged',report['baseline_after']==before)
  with urllib.request.urlopen('http://127.0.0.1:11434/api/tags') as res:report['model_digest']=next(x['digest'] for x in json.load(res)['models'] if x['name']=='gemma4:e2b-it-qat')
  check('model-unchanged',report['model_digest']=='07ea59a474013479c8b6b802bef095c40e964a1d776ba02f264c0e30e1aede0c')
  report['passed']=all(x['passed'] for x in report['checks']);save()
print('PASSED',report['passed'],flush=True)
