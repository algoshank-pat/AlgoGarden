import requests,uuid,wave,io,json,time
from pathlib import Path
import numpy as np
base='http://127.0.0.1:8010';out=Path('results/voice-dietary-booking');out.mkdir(parents=True,exist_ok=True);report={'turns':[],'passed':False};sid=None;created=None

def get(path):
 r=requests.get(base+path,timeout=30);r.raise_for_status();return r.json()
def post(path,payload):
 r=requests.post(base+path,json=payload,timeout=90);r.raise_for_status();return r.json()
def audio(text):
 r=requests.post(base+'/api/voice/speak',json={'text':text},timeout=90);r.raise_for_status();return r.content
def say(text):
 started=time.perf_counter();recording=audio(text);index=len(report['turns'])+1;(out/f'{index:02d}-caller.wav').write_bytes(recording)
 with wave.open(io.BytesIO(recording),'rb') as wav:rate=wav.getframerate();samples=np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2')
 samples=np.interp(np.arange(int(len(samples)*16000/rate))*rate/16000,np.arange(len(samples)),samples).astype('<i2');buf=io.BytesIO()
 with wave.open(buf,'wb') as wav:wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(samples.tobytes())
 r=requests.post(base+f'/api/voice/transcribe?session_id={sid}',data=buf.getvalue(),headers={'Content-Type':'audio/wav'},timeout=90);r.raise_for_status();heard=r.json()['text']
 result=post(f'/api/sessions/{sid}/turns',{'request_id':str(uuid.uuid4()),'text':heard});speech=audio(result['reply']);(out/f'{index:02d}-agent.wav').write_bytes(speech)
 report['turns'].append({'said':text,'heard':heard,'reply':result['reply'],'seconds':round(time.perf_counter()-started,2),'ended':result['ended']});(out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf8');print(index,heard,'=>',result['reply'],flush=True);return result
try:
 day='2026-12-17';plan=get('/api/owner/reservations/day?day='+day)
 window=next(w for service in plan['services'] for w in service['windows'] if w['accepts_arrival'] and w['free_seats']>=2 and any(not t['reserved'] for t in w['tables']))
 at=window['start'];hour,minute=map(int,at.split(':'));spoken=f"{hour%12 or 12}"+(' thirty' if minute==30 else '')+(' in the afternoon' if 12<=hour<18 else ' in the evening' if hour>=18 else ' in the morning')
 sid=post('/api/sessions',{'channel':'voice'})['session_id'];report.update(session_id=sid,expected={'date':day,'time':at,'party_size':2})
 r=say(f'I would like to book a table for two on December seventeenth, twenty twenty six, at {spoken}.');assert 'reservation with us' in r['reply'].lower()
 r=say('Before we book, what vegan options do you have?');assert 'Tomato Basil Risotto' in r['reply'] and 'Chickpea & Spinach Polenta' in r['reply'] and 'website' in r['reply']
 r=say('Does Peanut Chilli Spaghetti contain peanuts?');assert 'Contains peanuts' in r['reply']
 r=say('No, I do not have a reservation.');assert 'phone' in r['reply'].lower()
 r=say('My phone number is two zero two, five five five, zero one nine four.');assert 'name' in r['reply'].lower()
 r=say('My name is Avery Demo.');assert day in r['reply'] and at in r['reply'] and '2' in r['reply']
 rows=get('/api/owner/dashboard')['bookings'];assert not any(b.get('session_id')==sid for b in rows),'Booking saved before confirmation'
 r=say('Yes, that is correct.');rows=get('/api/owner/dashboard')['bookings'];matches=[b for b in rows if b.get('session_id')==sid and b.get('status')!='cancelled'];assert len(matches)==1,(r,matches)
 created=matches[0];report['saved_booking']=created
 assert (created['date'],created['time'],created['party_size'])==(day,at,2),created
 assert created['phone'].endswith('2025550194') and created['source']=='phone'
 r=say('No thanks, that is all.');assert r['survey']['status']=='offered'
 r=say('Skip the survey please.');assert r['ended'] and 'thank' in r['reply'].lower()
 report['passed']=True
finally:
 if sid:
  rows=get('/api/owner/dashboard')['bookings']
  for booking in [b for b in rows if b.get('session_id')==sid and b.get('status')!='cancelled']:
   post(f"/api/owner/reservations/{booking['id']}/change",{'request_id':str(uuid.uuid4()),'action':'cancel','expected_version':booking['version']});report['test_booking_cancelled']=True
  requests.post(base+f'/api/sessions/{sid}/stop',timeout=30)
 (out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf8')
print('PASS: original booking details preserved across dietary questions; fixture cancelled.')
