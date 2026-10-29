from pathlib import Path
import requests,json,uuid,io,wave
import numpy as np
base='http://127.0.0.1:8010';rows=[];folder=Path('results/good-bad-survey');folder.mkdir(parents=True,exist_ok=True)
def post(path,body):
 r=requests.post(base+path,json=body,timeout=90);r.raise_for_status();return r.json()
for channel in ['chat','voice']:
 for answer in ['good','bad','skip']:
  sid=post('/api/sessions',{'channel':channel})['session_id'];turns=[]
  for index,text in enumerate(['Goodbye','Yes please',answer]):
   if channel=='voice':
    spoken_text='Skip the survey please.' if index==2 and answer=='skip' else text
    audio=requests.post(base+'/api/voice/speak',json={'text':spoken_text},timeout=90);audio.raise_for_status();(folder/f'voice-{answer}-{index}-caller.wav').write_bytes(audio.content)
    with wave.open(io.BytesIO(audio.content),'rb') as wav:
     rate=wav.getframerate();samples=np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2')
    resampled=np.interp(np.arange(int(len(samples)*16000/rate))*rate/16000,np.arange(len(samples)),samples).astype('<i2')
    recording=io.BytesIO()
    with wave.open(recording,'wb') as wav:wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(resampled.tobytes())
    stt=requests.post(base+f'/api/voice/transcribe?session_id={sid}',data=recording.getvalue(),headers={'Content-Type':'audio/wav'},timeout=90);stt.raise_for_status();heard=stt.json()['text'];assert heard.strip(),stt.text
   else:heard=text
   result=post(f'/api/sessions/{sid}/turns',{'request_id':str(uuid.uuid4()),'text':heard});turns.append({'said':text,'heard':heard,'reply':result['reply'],'survey':result['survey'],'ended':result['ended']})
   if channel=='voice':
    audio=requests.post(base+'/api/voice/speak',json={'text':result['reply']},timeout=90);audio.raise_for_status();assert len(audio.content)>1000;(folder/f'voice-{answer}-{index}-agent.wav').write_bytes(audio.content)
  assert result['ended'],result
  assert result['survey']['rating']==(None if answer=='skip' else answer),result
  assert 'thank' in result['reply'].lower(),result
  dashboard=requests.get(base+'/api/owner/dashboard',timeout=30).json();call=next(c for c in dashboard['calls'] if c['id']==sid)
  assert call['feedback']['rating']==(None if answer=='skip' else answer),call['feedback']
  rows.append({'channel':channel,'answer':answer,'session_id':sid,'turns':turns,'owner_feedback':call['feedback'],'passed':True})
  print(channel,answer,'passed',flush=True)
(folder/'report.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
