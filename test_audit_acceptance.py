"""Six real API/audio scenarios with audit timing verification; no microphone capture."""
import json,subprocess,sys,time
from pathlib import Path
from uuid import uuid4
import httpx
from receptionist import HERE,Tools,load_restaurant

out=HERE/'results/audit-flow/acceptance'
out.mkdir(parents=True,exist_ok=True)
report={'passed':False,'scenarios':[],'voice_method':'generated speech through local Parakeet and Piper browser endpoints'}
with httpx.Client(base_url='http://127.0.0.1:8010',timeout=180,trust_env=False) as client:
 tools=Tools(load_restaurant(HERE/'restaurants/demo_restaurant.yaml'))
 phone='9900'+str(int(uuid4().hex[:8],16)%1000000).zfill(6)
 booking_id=None;sessions=[]
 def start():
  r=client.post('/api/sessions',json={'channel':'chat'});r.raise_for_status();sid=r.json()['session_id'];sessions.append(sid);return sid
 def turn(sid,text):
  begun=time.perf_counter();r=client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':text});r.raise_for_status();data=r.json()
  assert not data.get('failed') and not data.get('handoff'),data
  assert data['latency_ms']<=((time.perf_counter()-begun)*1000)+2
  print('Chat:',data['reply'],flush=True);return data
 try:
  sid=start();turn(sid,'Can I book a table for two next Saturday at half past eight?');turn(sid,phone);reply=turn(sid,'Alex')
  assert 'correct' in reply['reply'].lower()
  result=turn(sid,'Yes, please.');assert 'book_table' in result['structured']['tools_called']
  r=client.get('/api/owner/dashboard');r.raise_for_status();rows=[r for r in r.json()['bookings'] if r.get('session_id')==sid];assert len(rows)==1
  booking_id=rows[0]['id'];report['scenarios'].append({'channel':'chat','action':'booking','session_id':sid,'tool':'book_table'})
  sid=start();turn(sid,'Please update my reservation.');turn(sid,phone);reply=turn(sid,'Make it four people at half past eight instead.');assert 'correct' in reply['reply'].lower()
  result=turn(sid,'Yes, please.');assert 'change_reservation' in result['structured']['tools_called'];report['scenarios'].append({'channel':'chat','action':'update','session_id':sid,'tool':'change_reservation'})
  sid=start();turn(sid,'Please cancel my reservation.');reply=turn(sid,phone);assert 'confirm' in reply['reply'].lower()
  result=turn(sid,'Yes, please.');assert 'cancelled' in result['reply'];report['scenarios'].append({'channel':'chat','action':'cancel','session_id':sid,'tool':'change_reservation'})
 finally:
  if booking_id:
   row=tools.lookup_reservation(booking_id,phone)['reservation']
   if row['status']=='active':assert tools.change_reservation(booking_id,phone,'cancel',row['version'],{},'audit-acceptance-cleanup-'+booking_id)['changed']
  for sid in sessions:client.delete(f'/api/sessions/{sid}')
 for script in ['test_browser_voice_flow.py','test_browser_voice_changes.py']:
  completed=subprocess.run([sys.executable,'-u',str(HERE/script)],cwd=HERE);assert completed.returncode==0,script
 booking=json.loads((HERE/'results/browser-voice/full-digit-phone-flow/report.json').read_text())
 changes=json.loads((HERE/'results/browser-voice/update-cancel-digit-phone-flow/report.json').read_text())
 report['scenarios'] += [{'channel':'voice','action':'booking','session_id':booking['session_id'],'tool':'book_table'},
                         {'channel':'voice','action':'update','session_id':changes['update_session'],'tool':'change_reservation'},
                         {'channel':'voice','action':'cancel','session_id':changes['cancel_session'],'tool':'change_reservation'}]
 r=client.get('/api/owner/dashboard');r.raise_for_status();allcalls=r.json()['calls']
 for scenario in report['scenarios']:
  call=next(c for c in allcalls if c['id']==scenario['session_id']);scenario['call']=call
  matches=[i for i,t in enumerate(call['turns']) if scenario['tool'] in t['result']['structured']['tools_called']];assert len(matches)==1
  scenario['turn_index']=matches[0];target=call['turns'][matches[0]];assert target['result']['routing']['intent']=='reservation'
  assert any(e['name']==scenario['tool']+'.completed' for e in target['events'])
  differences=[];measured=0
  for t in call['turns']:
   starts={}
   for e in t['events']:
    key=e.get('details',{}).get('call_id')
    if e['name'].endswith('.started'):starts[key]=e
    elif 'duration_ms' in e:
     assert key in starts;duration=e['duration_ms'];delta=e['offset_ms']-starts[key]['offset_ms'];assert duration>=0 and delta>=0
     differences.append(abs(delta-duration));assert abs(delta-duration)<2,(e['name'],delta,duration)
     assert e['offset_ms']<=t['result']['latency_ms']+2
     measured+=1
  assert measured>0
  scenario.update(timing_verified=True,measured_spans=measured,max_span_clock_difference_ms=round(max(differences),3),commit_latency_ms=target['result']['latency_ms'])
  print('PASS audit:',scenario['channel'],scenario['action'],measured,'spans',flush=True)
 report['passed']=True
(out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print('PASS: six audit scenarios; timing verified; all test reservations cancelled.',flush=True)
