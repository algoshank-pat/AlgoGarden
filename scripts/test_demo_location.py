"""Private-copy chat and actual synthetic Parakeet/Piper location checks."""
import os, sys, json, sqlite3, hashlib, time, io, wave
from pathlib import Path
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
OUT=ROOT/'results/demo-location'/time.strftime('%Y%m%d-%H%M%S');OUT.mkdir(parents=True)
private=OUT/'private';private.mkdir()
os.environ['RECEPTIONIST_FIXTURE_DIR']=str(private)
os.environ['LANGSMITH_TRACING']='false'

def snapshot():
 with sqlite3.connect(f'file:{ROOT/"reservations.sqlite3"}?mode=ro',uri=True) as db:
  tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
  data={t:db.execute('SELECT * FROM "'+t+'" ORDER BY rowid').fetchall() for t in tables}
 return {'reservations':len(data['reservations']),'all_tables_sha256':hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()}

before=snapshot()
with sqlite3.connect(f'file:{ROOT/"reservations.sqlite3"}?mode=ro',uri=True) as src,sqlite3.connect(private/'reservations.sqlite3') as dst:src.backup(dst)
import numpy as np
import requests
from fastapi.testclient import TestClient
import chat_api as app
from information_requests import pending_state
from voice.phrases import spoken_text

report={'source':'restaurants/demo_restaurant.yaml:address','address':app.restaurant['address'],'before':before,'checks':[],'method':'Chat API; synthetic Piper caller audio -> actual Parakeet -> voice API -> streamed Piper. No physical microphone/browser test.'}
digest='07ea59a474013479c8b6b802bef095c40e964a1d776ba02f264c0e30e1aede0c'
models=requests.get('http://127.0.0.1:11434/api/tags',timeout=10).json()['models']
model=next(m for m in models if m['name']=='gemma4:e2b-it-qat');assert model['digest']==digest
report['model']={'name':model['name'],'digest':model['digest']}

def pcm(data):
 with wave.open(io.BytesIO(data)) as w:rate=w.getframerate();samples=np.frombuffer(w.readframes(w.getnframes()),dtype='<i2')
 converted=np.interp(np.arange(int(len(samples)*16000/rate))*rate/16000,np.arange(len(samples)),samples).astype('<i2');out=io.BytesIO()
 with wave.open(out,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(converted.tobytes())
 return out.getvalue()

try:
 with TestClient(app.app) as api:
  for channel in ['chat','voice']:
   sid=api.post('/api/sessions',json={'channel':channel}).json()['session_id'];bot=app.sessions[sid].bot
   def say(text):
    rid=str(uuid4());heard=text;kind='text'
    if channel=='voice':
     audio=api.post('/api/voice/speak',json={'text':text});audio.raise_for_status()
     trans=api.post(f'/api/voice/transcribe?session_id={sid}&request_id={rid}',content=pcm(audio.content),headers={'Content-Type':'audio/wav'});trans.raise_for_status();heard=trans.json()['text'];kind=trans.json()['input_kind']
    response=api.post(f'/api/sessions/{sid}/turns',json={'request_id':rid,'text':heard,'input_kind':kind});response.raise_for_status();value=response.json()
    if channel=='voice':
     assert value['spoken_reply']==spoken_text(value['reply'])
     audio=api.post('/api/voice/speak/stream',json={'session_id':sid,'request_id':rid,'text':value['spoken_reply']});audio.raise_for_status();assert any('audio' in json.loads(line) for line in audio.text.splitlines() if line.strip())
    return {'prompt':text,'transcript':heard,'reply':value['reply'],'spoken_reply':value.get('spoken_reply'),'tools':value['structured']['tools_called']}
   for question in ['Where is the restaurant?',"What's your address?",'How do I get there?']:
    evidence=say(question);assert app.restaurant['address'] in evidence['reply'];assert 'get_location' in evidence['tools']
    report['checks'].append({'channel':channel,'scenario':question,'passed':True,**evidence});print(channel,question,'PASS',flush=True)
   # A pending, fully collected booking at confirmation is the most sensitive
   # interruption point: the question must neither commit nor erase its draft.
   bot.reserving=True;bot.awaiting_confirmation=True
   bot.slots={'date':'2027-02-17','time':'19:00','party_size':2,'name':'Demo Guest','phone':'+12025550181' if channel=='chat' else '+12025550182'}
   bot._last_agent_reply='Table for 2 on 2027-02-17 at 19:00, under Demo Guest. Is that correct?'
   state=pending_state(bot);interruptions=[]
   for question in ['Where is the restaurant?',"What's your address?",'How do I get there?']:
    evidence=say(question);assert pending_state(bot)==state
    assert app.restaurant['address'] in evidence['reply'] and 'get_location' in evidence['tools']
    interruptions.append(evidence)
   resumed=say('Go back to my reservation');assert pending_state(bot)==state and '2027-02-17' in resumed['reply'] and '19:00' in resumed['reply']
   assert not [r for r in bot.tools.booking_store().list(bot.tools.business_id) if r.get('session_id')==sid]
   confirmed=say('Yes please')
   rows=[r for r in bot.tools.booking_store().list(bot.tools.business_id) if r.get('session_id')==sid]
   assert len(rows)==1 and all(rows[0][k]==v for k,v in bot.slots.items() if k in {'date','time','party_size'})
   assert rows[0]['date']=='2027-02-17' and rows[0]['time']=='19:00' and rows[0]['party_size']==2 and rows[0]['name']=='Demo Guest'
   report['checks'].append({'channel':channel,'scenario':'Address during booking, resume, confirm','passed':True,'address_turns':interruptions,'resume':resumed,'confirmation':confirmed,'private_booking':rows[0]});print(channel,'booking interruption PASS',flush=True)
finally:
 report['after']=snapshot();report['production_unchanged']=before==report['after'];report['passed']=len(report['checks'])==8 and report['production_unchanged']
 (OUT/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print('Report:',OUT,flush=True)
assert report['passed']
