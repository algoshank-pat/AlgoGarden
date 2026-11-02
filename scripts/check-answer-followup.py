"""Private API replay with the configured actual Ollama model; no hardware audio."""
import os,sys,json,copy,math
from pathlib import Path
from datetime import timedelta
from uuid import uuid4
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
out=root/'results/answer-followup-20261101'/str(uuid4());out.mkdir(parents=True)
os.environ['RECEPTIONIST_FIXTURE_DIR']=str(out/'private');(out/'private').mkdir()
os.environ['LANGSMITH_TRACING']='false'
from dotenv import load_dotenv
load_dotenv(root/'.env')
import chat_api as api
from fastapi.testclient import TestClient
from customer_identity import local_today
from conversation_followup import QUESTION,INVITE
from customer_feedback import OFFER,RATING
report={'method':'Actual API/default Ollama; voice-text, not microphone/STT/TTS playback','turns':[],'passed':False}
try:
 with TestClient(api.app) as c:
  for channel,phone in [('chat','2025550186'),('voice','2025550187')]:
   sid=c.post('/api/sessions',json={'channel':channel}).json()['session_id'];b=api.sessions[sid].bot
   before=b.tools.booking_store().list(b.tools.business_id)
   def say(text):
    r=c.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':text});r.raise_for_status();v=r.json()
    report['turns'].append({'channel':channel,'session_id':sid,'customer':text,'reply':v['reply'],'spoken_reply':v.get('spoken_reply'),'latency_ms':v['latency_ms'],'tools':v['structured']['tools_called'],'survey':v['survey'],'call_count':sum(x['component']=='ollama.chat' for x in v['structured']['calls'])})
    assert not v.get('failed') and report['turns'][-1]['call_count']<=1,v
    for call in v['structured']['calls']:
     if call['component']=='ollama.chat':
      cfg=call['inputs']['kwargs'];assert cfg['model']=='gemma4:e2b-it-qat' and cfg['think'] is False and cfg['keep_alive']=='30m'
      assert cfg['options']=={'num_ctx':2048,'num_predict':256,'temperature':0}
    return v
   day=(local_today(b.restaurant)+timedelta(days=5)).isoformat()
   say(f'Book a table for two on {day} at seven pm.')
   if channel=='voice':say('No')
   say(phone);say('Alex Morgan')
   if channel=='voice':say('Yes, that name is correct.')
   assert b.awaiting_confirmation
   draft=copy.deepcopy(b.slots)
   for question in ['Where can I park?','Can I bring my dog?','Do you have gluten-free food?','Who is Albert Einstein?']:
    assert say(question)['reply'].endswith(QUESTION)
    assert b.slots==draft and b.tools.booking_store().list(b.tools.business_id)==before
    assert say('Yes')['reply']==INVITE
   assert 'Is that correct?' in say('No more questions')['reply']
   assert b.tools.booking_store().list(b.tools.business_id)==before
   assert say('Yes')['reply'].endswith(QUESTION)
   saved=b.tools.booking_store().list(b.tools.business_id)
   assert len(saved)==len(before)+1
   new=next(row for row in saved if row['phone'].endswith(phone))
   assert new['date']==day and new['time']=='19:00' and new['party_size']==2 and new['name']=='Alex Morgan'
   assert say('What are your opening hours?')['reply'].endswith(QUESTION)
   assert say('Yes')['reply']==INVITE
   assert say('Can I bring my dog?')['reply'].endswith(QUESTION)
   assert say('No, thank you.')['reply']==OFFER
   assert say('Yes')['reply']==RATING
   assert say('5')['survey']['rating'] is None
   assert say('good')['survey']['rating']=='good' and b.ended
   assert b.tools.booking_store().list(b.tools.business_id)==saved
   sid=c.post('/api/sessions',json={'channel':channel}).json()['session_id'];b=api.sessions[sid].bot
   say('Where can I park?');assert say('No')['survey']['status']=='offered'
   assert say('No thanks')['survey']['status']=='declined' and b.ended
 report['passed']=True
except Exception as error:
 report['error']=repr(error)
 raise
finally:
 latencies=sorted(r['latency_ms'] for r in report['turns'])
 if latencies:report['timings']={'n':len(latencies),'average_ms':sum(latencies)/len(latencies),'p95_ms':latencies[math.ceil(.95*len(latencies))-1]}
 (out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
 print(json.dumps({'report':str(out/'report.json'),'passed':report['passed'],'turns':len(report['turns'])}))
