import requests,uuid,wave,io,json
from pathlib import Path
import numpy as np
base='http://127.0.0.1:8010';report=[]
cases=[('What vegan dishes do you have?',['Tomato Basil Risotto','Chickpea & Spinach Polenta']),('What gluten free options do you have?',['Tomato Basil Risotto','Chickpea & Spinach Polenta']),('What is Peanut Chilli Spaghetti? Is it vegan, gluten free and nut free?',['Listed as vegan','Not gluten-free','Contains peanuts']),('Is Tomato Basil Risotto nut free?',['Nut-free status is not confirmed'])]
for channel in ['chat','voice']:
 for question,expected in cases:
  sid=requests.post(base+'/api/sessions',json={'channel':channel},timeout=30).json()['session_id'];heard=question
  if channel=='voice':
   audio=requests.post(base+'/api/voice/speak',json={'text':question},timeout=90);audio.raise_for_status()
   with wave.open(io.BytesIO(audio.content),'rb') as wav:rate=wav.getframerate();samples=np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2')
   samples=np.interp(np.arange(int(len(samples)*16000/rate))*rate/16000,np.arange(len(samples)),samples).astype('<i2');buf=io.BytesIO()
   with wave.open(buf,'wb') as wav:wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(samples.tobytes())
   r=requests.post(base+'/api/voice/transcribe',data=buf.getvalue(),headers={'Content-Type':'audio/wav'},timeout=90);r.raise_for_status();heard=r.json()['text']
  r=requests.post(base+f'/api/sessions/{sid}/turns',json={'request_id':str(uuid.uuid4()),'text':heard},timeout=90);r.raise_for_status();reply=r.json()['reply']
  for phrase in expected:assert phrase.lower() in reply.lower(),(channel,heard,reply)
  if channel=='voice':
   if 'options' in question or 'dishes' in question:assert 'website' in reply and 'Peanut Chilli Spaghetti' not in reply
   audio=requests.post(base+'/api/voice/speak',json={'text':reply},timeout=90);audio.raise_for_status();assert len(audio.content)>1000
  requests.post(base+f'/api/sessions/{sid}/stop',timeout=30)
  report.append({'channel':channel,'question':question,'transcript':heard,'reply':reply,'passed':True});print(channel,question,'passed',flush=True)
Path('results/dietary-menu').mkdir(parents=True,exist_ok=True);Path('results/dietary-menu/report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
