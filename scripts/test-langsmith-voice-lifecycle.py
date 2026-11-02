"""One private synthetic voice session; real speech, tools, writes and LangSmith."""
import os,sys,json,sqlite3,hashlib,io,wave,time
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit
ROOT=Path.cwd();sys.path.insert(0,str(ROOT))
OUT=ROOT/'results/langsmith-voice-lifecycle'/time.strftime('%Y%m%d-%H%M%S');OUT.mkdir(parents=True);private=OUT/'private';private.mkdir()
os.environ['RECEPTIONIST_FIXTURE_DIR']=str(private)
import numpy as np
import requests

def snapshot():
 with sqlite3.connect(f'file:{ROOT/"reservations.sqlite3"}?mode=ro',uri=True) as db:
  db.row_factory=sqlite3.Row;rows=[dict(r) for r in db.execute('SELECT rowid AS _rowid,* FROM reservations ORDER BY rowid')]
 return {'count':len(rows),'sha256':hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()}
before=snapshot()
with sqlite3.connect(f'file:{ROOT/"reservations.sqlite3"}?mode=ro',uri=True) as src,sqlite3.connect(private/'reservations.sqlite3') as dst:src.backup(dst)
from fastapi.testclient import TestClient
from langsmith import Client
import observability as obs,turn_tracing as tracing,chat_api,browser_voice
from trace_payloads import payload
from voice.phrases import spoken_text,speech_segments
uploads=[];transport=requests.Session()
def hook(response,*args,**kwargs):
 if 'runs' in urlsplit(response.url).path:uploads.append({'path':urlsplit(response.url).path,'status':response.status_code})
transport.hooks['response'].append(hook);obs._client=Client(session=transport,auto_batch_tracing=True,timeout_ms=10000)
report={'method':'Synthetic Piper recordings -> actual Parakeet -> private voice API -> streamed Piper; no browser or physical playback measurements','agent':'algo-garden','environment':'local','production_before':before,'turns':[],'greeting':None,'passed':False}
def save():(OUT/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
def pcm(data):
 with wave.open(io.BytesIO(data)) as w:rate=w.getframerate();samples=np.frombuffer(w.readframes(w.getnframes()),dtype='<i2')
 out=np.interp(np.arange(int(len(samples)*16000/rate))*rate/16000,np.arange(len(samples)),samples).astype('<i2');buffer=io.BytesIO()
 with wave.open(buffer,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(out.tobytes())
 return buffer.getvalue()
with TestClient(chat_api.app) as api:
 session=api.post('/api/sessions',json={'channel':'voice'});session.raise_for_status();sid=session.json()['session_id'];report['session_id']=sid;report['greeting']=session.json();assert session.json()['spoken_greeting']==spoken_text(session.json()['greeting'])
 bot=chat_api.sessions[sid].bot
 def rows():return [r for r in bot.tools.booking_store().list(bot.tools.business_id) if r.get('session_id')==sid]
 def say(text):
  rid=str(uuid4());number=len(report['turns'])+1
  audio=api.post('/api/voice/speak',json={'text':text});audio.raise_for_status()
  result=api.post(f'/api/voice/transcribe?session_id={sid}&request_id={rid}',content=pcm(audio.content),headers={'Content-Type':'audio/wav'});result.raise_for_status();transcript=result.json();assert transcript['text'],'Empty transcription'
  response=api.post(f'/api/sessions/{sid}/turns',json={'request_id':rid,'text':transcript['text'],'input_kind':transcript['input_kind']});response.raise_for_status();value=response.json()
  displayed=value['spoken_reply'];assert displayed==spoken_text(value['reply'])
  captured=[];speaker=browser_voice.speaker;original_chunks=speaker.chunks
  def inspected_chunks(segment):
   captured.append(spoken_text(segment));yield from original_chunks(segment)
  speaker.chunks=inspected_chunks
  audio=api.post('/api/voice/speak/stream',json={'session_id':sid,'request_id':rid,'text':displayed});
  speaker.chunks=original_chunks
  assert ' '.join(captured)==displayed,'Displayed text differs from Piper input'
  audio.raise_for_status();packets=[json.loads(line) for line in audio.text.splitlines() if line.strip()];assert packets and all('audio' in p for p in packets)
  root=tracing._runs[(sid,rid)]['run'];assert root.inputs['text']==payload(transcript['text']);assert root.outputs['reply']==payload(value['reply']);assert root.extra['metadata']['thread_id']==sid
  groups={g.name:g for g in root.child_runs};assert set(groups)>={'Input','Processing','Response'}
  assert groups['Input'].outputs['transcript']['text']==payload(transcript['text']);assert groups['Response'].inputs['text']==payload(displayed)
  stages=[json.loads(line) for line in (private/'turn_timings.jsonl').read_text().splitlines()];stages=[r for r in stages if r['request_id']==rid]
  assert {'speech.to_text','backend.total','queue.wait','speech.time_to_first_audio','speech.synthesis_compute'}<={r['stage'] for r in stages}
  report['turns'].append({'number':number,'request_id':rid,'trace_id':str(root.id),'said':text,'heard':transcript['text'],'reply':value['reply'],'spoken_reply':displayed,'piper_text':' '.join(captured),'text_matches_audio_input':True,'response':value,'use_case':value.get('performance_use_case'),'groups':list(groups),'stages':stages,'database':rows()});save();print(number,transcript['text'],'=>',value['reply'],flush=True)
  return value
 try:
  say('Can I book a table for two on January fourteenth twenty twenty seven at seven in the evening?')
  say('No, I do not have a reservation.')
  say('My phone number is two zero two five five five zero one six eight.')
  say('First name Tango Echo Sierra Tango. Last name Golf Uniform Echo Sierra Tango.')
  if getattr(bot,'_name_candidate',None):say('Yes please.')
  assert bot.awaiting_confirmation,'Expected booking confirmation readback'
  assert not rows(),'No booking before confirmation'
  say('Yes please.');bookings=rows();assert len(bookings)==1;original=bookings[0];assert original['party_size']==2 and original['time']=='19:00' and original['date']=='2027-01-14'
  report['booking_id']=original['id']
  say('I would like to update my reservation.')
  if 'phone' in report['turns'][-1]['reply'].lower():say('My phone number is two zero two five five five zero one six eight.')
  say('Make it four people at half past eight in the evening.')
  assert rows()[0]['version']==original['version'],'Update requires confirmation'
  say('Yes please.');updated=rows()[0];assert updated['id']==original['id'] and updated['party_size']==4 and updated['time']=='20:30' and updated['date']==original['date']
  say('I would like to cancel my reservation.')
  if 'phone' in report['turns'][-1]['reply'].lower():say('My phone number is two zero two five five five zero one six eight.')
  assert rows()[0]['status']!='cancelled','Cancellation requires confirmation'
  say('Yes please.');assert rows()[0]['status']=='cancelled'
  say('No thanks, that is all.');last=say('Skip the survey please.');assert last['ended']
  report['date_time_wording_verified']=all('twenty twenty-seven' in r['spoken_reply'] and 'fourteenth' in r['spoken_reply'] for r in report['turns'] if '2027-01-14' in r['reply'])
  assert report['date_time_wording_verified']
  assert any('half past eight in the evening' in r['spoken_reply'] for r in report['turns'])
  report['passed']=True
 finally:
  api.post(f'/api/sessions/{sid}/stop');obs.drain_exports(timeout=20);obs.client().flush();report['uploads']=uploads;report['production_after']=snapshot();report['production_unchanged']=before==report['production_after'];save()
 assert report['production_unchanged']
 assert uploads and all(u['status'] in [200,201,202] for u in uploads)
 print('PASS',len(report['turns']),'voice turns in one session:',sid,'Report:',OUT,flush=True)
