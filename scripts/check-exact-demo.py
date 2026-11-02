"""Replay the customer's exact eleven-line script, in private fixtures only."""
import sys,os,json,time,sqlite3,hashlib,urllib.request,math,io,wave
from pathlib import Path
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
MODE='recorded' if '--spoken' in sys.argv else 'voice-text'
OUT=ROOT/'results/exact-demo-20261101'/MODE/str(uuid4());(OUT/'private').mkdir(parents=True)
from dotenv import load_dotenv
load_dotenv(ROOT/'.env')
os.environ.update(RECEPTIONIST_FIXTURE_DIR=str(OUT/'private'),LANGSMITH_TRACING='false')
import chat_api as api
from fastapi.testclient import TestClient
from customer_identity import local_today
from conversation_followup import QUESTION
from voice.phrases import spoken_text
PROMPTS=[
 'Who is Albert Einstein?',
 'Okay, where can I park?',
 'Do you have any gluten-free options?',
 'Can I bring my dog inside?',
 'Thanks. I’d like to book a table for two on November   tenth, twenty twenty-six, at seven in the evening.',
 'No, I don’t have a reservation.',
 'Two zero two, five five five, zero one nine four.',
 'My name is Alex Morgan.',
 'Yes, that name is correct.',
 'Yes, please confirm the reservation.',
 'No more questions, thank you.',
]
EXPECTED=[
 'Decline unrelated question, then invite further questions; do not start booking.',
 'Give parking facts including Friday-Sunday dinner valet restriction, then invite questions.',
 'Give grounded gluten-free options and cross-contact caution, then invite questions.',
 'Explain indoor pet prohibition with grounded conditions, then invite questions.',
 'Ask existing/new reservation question and preserve the request.',
 'Ask phone; preserve November 10, 2026, 19:00, two people.',
 'Capture 2025550194 and ask name, without repeating phone collection.',
 'Read Alex Morgan back and ask whether the name is correct.',
 'Read all reservation details for final confirmation; no write.',
 'Create exactly one correct reservation; confirm briefly and invite further questions.',
 'Thank the customer and offer the optional survey; do not book again.',
]
def snapshots():
 result={}
 for label,path in [('production',ROOT/'reservations.sqlite3'),('live_private',ROOT/'results/voice-turn-ownership-20261029/private/reservations.sqlite3')]:
  with sqlite3.connect(f'file:{path.as_posix()}?mode=ro',uri=True) as db:
   db.row_factory=sqlite3.Row;rows=[dict(r) for r in db.execute('select rowid as _rowid,* from reservations order by rowid')]
  base=[r for r in rows if str(r.get('request_key','')).startswith('baseline:2026-q4:v1:')]
  result[label]={'count':len(rows),'sha256':hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest(),'baseline_count':len(base),'baseline_sha256':hashlib.sha256(json.dumps(base,sort_keys=True).encode()).hexdigest()}
 return result
report={'mode':MODE,'method':'Actual default model, private empty reservation DB; '+('Piper synthetic caller WAV -> Parakeet -> voice API -> Piper response WAV. No physical microphone/browser playback.' if MODE=='recorded' else 'Exact text through voice API; no ASR or audio playback.'),'turns':[],'before':snapshots()}
def save(): (OUT/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
def pcm16(data):
 import numpy as np
 with wave.open(io.BytesIO(data)) as w:rate=w.getframerate();samples=np.frombuffer(w.readframes(w.getnframes()),dtype='<i2').astype(np.float32)
 values=np.interp(np.arange(int(len(samples)*16000/rate))*rate/16000,np.arange(len(samples)),samples)
 buffer=io.BytesIO()
 with wave.open(buffer,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(np.clip(values,-32768,32767).astype('<i2').tobytes())
 return buffer.getvalue()
try:
 models=json.load(urllib.request.urlopen('http://127.0.0.1:11434/api/tags'))['models']
 m=next(m for m in models if m['name']==api.MODEL);report['model']={'tag':m['name'],'digest':m['digest']}
 assert m['name']=='gemma4:e2b-it-qat' and m['digest']=='07ea59a474013479c8b6b802bef095c40e964a1d776ba02f264c0e30e1aede0c'
 with TestClient(api.app) as client:
  greeting=client.post('/api/sessions',json={'channel':'voice'}).json();sid=greeting['session_id'];bot=api.sessions[sid].bot
  report.update(session_id=sid,greeting=greeting['spoken_greeting'])
  for i,prompt in enumerate(PROMPTS,1):
   heard=prompt;request_id=str(uuid4());stt_ms=None;tts_ms=None
   if MODE=='recorded':
    caller=client.post('/api/voice/speak',json={'text':prompt});caller.raise_for_status();wav=pcm16(caller.content)
    (OUT/f'{i:02d}-caller.wav').write_bytes(wav)
    at=time.perf_counter();r=client.post(f'/api/voice/transcribe?session_id={sid}&request_id={request_id}',content=wav,headers={'Content-Type':'audio/wav'});r.raise_for_status()
    heard=r.json()['text'];stt_ms=round((time.perf_counter()-at)*1000,2)
   at=time.perf_counter();result=client.post(f'/api/sessions/{sid}/turns',json={'request_id':request_id,'text':heard or '[No intelligible speech detected]'});result.raise_for_status();r=result.json()
   roundtrip=round((time.perf_counter()-at)*1000,2);rows=bot.tools.booking_store().list(bot.tools.business_id)
   tools=r['structured']['tools_called'];reply=r['reply'];calls=[c for c in r['structured']['calls'] if c['component']=='ollama.chat']
   correct=lambda row:row['name']=='Alex Morgan' and row['phone'].endswith('2025550194') and row['date']=='2026-11-10' and row['time']=='19:00' and row['party_size']==2
   state=api.sessions[sid]
   checks={'no_early_or_duplicate_write':len(rows)==(1 if i>=10 else 0),'one_generation_budget':len(calls)<=1,'no_backend_failure':not r.get('failed')}
   if i==1:ok=reply.startswith("I can't help you with this question.") and reply.endswith(QUESTION) and not bot.reserving
   elif i==2:ok='get_location' in tools and bot.restaurant['parking'] in reply and reply.endswith(QUESTION)
   elif i==3:ok='Tomato Basil Risotto' in reply and 'Polenta' in reply and 'cross-contact' in reply and reply.endswith(QUESTION)
   elif i==4:ok='search_knowledge' in tools and 'not permitted' in reply and 'indoor' in reply and reply.endswith(QUESTION)
   elif i==5:ok=state.pending_reservation_request is not None and 'reservation with us?' in reply
   elif i==6:ok='phone' in reply.lower() and bot.slots.get('party_size')==2 and api.engine.parse_date(bot.slots.get('date',''),local_today(bot.restaurant)).isoformat()=='2026-11-10' and api.engine.parse_time(bot.slots.get('time',''))=='19:00'
   elif i==7:ok='name' in reply.lower() and bot.slots.get('phone','').endswith('2025550194')
   elif i==8:ok=getattr(bot,'_name_candidate',None)=='Alex Morgan' and 'Alex Morgan' in reply and '?' in reply
   elif i==9:ok=bot.awaiting_confirmation and '2026-11-10' in reply and '19:00' in reply and 'Alex Morgan' in reply
   elif i==10:ok=len(rows)==1 and correct(rows[0]) and reply=='Your reservation is confirmed. '+QUESTION
   else:ok=state.survey['status']=='offered' and not r['ended'] and len(rows)==1 and correct(rows[0])
   checks['requested_outcome']=bool(ok)
   for call in calls:
    cfg=call['inputs']['kwargs']
    checks['inference_settings_unchanged']=cfg['model']=='gemma4:e2b-it-qat' and cfg['think'] is False and cfg['keep_alive']=='30m' and cfg['options']=={'num_ctx':2048,'num_predict':256,'temperature':0}
   if MODE=='recorded':
    at=time.perf_counter();audio=client.post('/api/voice/speak',json={'text':r['spoken_reply']});audio.raise_for_status();tts_ms=round((time.perf_counter()-at)*1000,2)
    (OUT/f'{i:02d}-agent.wav').write_bytes(audio.content)
    checks['reply_audio_generated']=audio.content.startswith(b'RIFF')
   entry={'step':i,'customer_exact':prompt,'heard':heard,'expected':EXPECTED[i-1],'agent':reply,'spoken_reply':r['spoken_reply'],'checks':checks,'pass':all(checks.values()),'backend_ms':r['latency_ms'],'api_roundtrip_ms':roundtrip,'stt_ms':stt_ms,'tts_ms':tts_ms,'tools':tools,'slots':dict(bot.slots),'reservation_count':len(rows),'survey':r['survey']}
   report['turns'].append(entry);save();print(json.dumps({'step':i,'pass':entry['pass'],'heard':heard,'agent':r['spoken_reply']},ensure_ascii=True),flush=True)
  report['private_reservations']=rows;report['pass']=all(t['pass'] for t in report['turns'])
except Exception as error:
 report.update(error=repr(error),pass_=False)
 raise
finally:
 report['after']=snapshots();report['existing_reservations_unchanged']=report['before']==report['after']
 report['pass']=bool(report.get('pass') and report['existing_reservations_unchanged'])
 lat=sorted(t['backend_ms'] for t in report['turns'])
 if lat:report['backend_timing']={'n':len(lat),'mean_ms':round(sum(lat)/len(lat),2),'p95_ms':lat[math.ceil(len(lat)*.95)-1]}
 save();print(json.dumps({'report':str(OUT/'report.json'),'pass':report['pass']}),flush=True)
