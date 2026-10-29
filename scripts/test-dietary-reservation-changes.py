import requests,uuid,wave,io,json,time
from pathlib import Path
import numpy as np
base='http://127.0.0.1:8010';out=Path('results/dietary-reservation-changes');out.mkdir(parents=True,exist_ok=True);report=[]
def get(path):
 r=requests.get(base+path,timeout=30);r.raise_for_status();return r.json()
def post(path,data):
 r=requests.post(base+path,json=data,timeout=90);r.raise_for_status();return r.json()
def speech(text):
 r=requests.post(base+'/api/voice/speak',json={'text':text},timeout=90);r.raise_for_status();return r.content
def row(booking):return next(b for b in get('/api/owner/dashboard')['bookings'] if b['id']==booking['id'])
def say(sid,channel,text,case):
 heard=text
 if channel=='voice':
  audio=speech(text)
  with wave.open(io.BytesIO(audio),'rb') as wav:rate=wav.getframerate();samples=np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2')
  samples=np.interp(np.arange(int(len(samples)*16000/rate))*rate/16000,np.arange(len(samples)),samples).astype('<i2');buf=io.BytesIO()
  with wave.open(buf,'wb') as wav:wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(samples.tobytes())
  r=requests.post(base+'/api/voice/transcribe',data=buf.getvalue(),headers={'Content-Type':'audio/wav'},timeout=90);r.raise_for_status();heard=r.json()['text']
 result=post(f'/api/sessions/{sid}/turns',{'request_id':str(uuid.uuid4()),'text':heard})
 if channel=='voice':assert len(speech(result['reply']))>1000
 case['turns'].append({'said':text,'heard':heard,'reply':result['reply']});(out/'report.json').write_text(json.dumps(report+[case],indent=2),encoding='utf8');print(channel,case['action'],len(case['turns']),result['reply'],flush=True);return result
for n,(channel,action) in enumerate([(c,a) for c in ['chat','voice'] for a in ['update','cancel']]):
 phone=f'202555014{n}';case={'channel':channel,'action':action,'turns':[],'passed':False};booking=None;sid=None
 try:
  booking=post('/api/owner/reservations',{'request_id':str(uuid.uuid4()),'name':f'Dietary {action} {channel} test','phone':phone,'date':f'2027-01-{14+n:02d}','time':'19:00','party_size':2,'source':'phone' if channel=='voice' else 'chat'})
  original=row(booking);sid=post('/api/sessions',{'channel':channel})['session_id'];case.update(session_id=sid,booking_id=booking['id'],before=original)
  r=say(sid,channel,'I need to '+action+' my reservation.',case);assert 'phone' in r['reply'].lower()
  digits=' '.join(['zero','one','two','three','four','five','six','seven','eight','nine'][int(c)] for c in phone)
  r=say(sid,channel,'My phone number is '+(digits if channel=='voice' else phone)+'.',case)
  if action=='update':r=say(sid,channel,'Make it four people at half past eight in the evening.',case)
  assert row(booking)['version']==original['version']
  assert any(w in r['reply'].lower() for w in ['correct','confirm']),r
  r=say(sid,channel,'Before I confirm, what vegan options do you have?',case);assert 'Tomato Basil Risotto' in r['reply']
  r=say(sid,channel,'Does Peanut Chilli Spaghetti contain peanuts?',case);assert 'Contains peanuts' in r['reply']
  assert row(booking)['version']==original['version'],'Dietary question mutated booking'
  r=say(sid,channel,'Yes, please.',case)
  assert row(booking)['version']==original['version'],'First yes after information should return readback'
  assert any(w in r['reply'].lower() for w in ['correct','confirm'])
  r=say(sid,channel,'Yes, please.',case);after=row(booking);case['after']=after
  assert after['version']==original['version']+1,(r,after)
  if action=='update':assert (after['date'],after['time'],after['party_size'],after['status'])==(original['date'],'20:30',4,'active')
  else:assert after['status']=='cancelled'
  assert after['id']==original['id'] and after['phone']==original['phone']
  case['passed']=True
 finally:
  if booking:
   current=row(booking)
   if current['status']!='cancelled':post(f"/api/owner/reservations/{booking['id']}/change",{'request_id':str(uuid.uuid4()),'action':'cancel','expected_version':current['version']})
   case['fixture_cancelled']=True
  if sid:requests.post(base+f'/api/sessions/{sid}/stop',timeout=30)
  report.append(case);(out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf8')
print('PASS: four change workflows preserved reservation and required explicit confirmation.')
