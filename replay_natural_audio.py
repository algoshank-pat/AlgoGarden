"""Recheck the exact natural-date reply WAVs; no booking mutations or new speech."""
import os,json,io,wave,sqlite3
from pathlib import Path
from datetime import datetime,timezone
from run_private_decision_acceptance import production_baseline
ROOT=Path(__file__).parent

def main():
 before=production_baseline();folder=ROOT/'results/decision-latency'/('natural-audio-replay-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S'));fixture=folder/'data';fixture.mkdir(parents=True)
 with sqlite3.connect(f'file:{ROOT/"reservations.sqlite3"}?mode=ro',uri=True) as a,sqlite3.connect(fixture/'reservations.sqlite3') as b:a.backup(b)
 os.environ.update(RECEPTIONIST_FIXTURE_DIR=str(fixture),RECEPTIONIST_DECISION_MODE='unified',RECEPTIONIST_DECISION_PROTOCOL='baseline',RECEPTIONIST_DECISION_FASTPATHS='safe',LANGSMITH_TRACING='false')
 from voice_test_harness import private_api
 from test_voice_natural_dates import natural_audio_matches
 import httpx,numpy as np
 source=json.loads((ROOT/'results/decision-latency/natural-audio-failure/report.json').read_text());rows=[]
 with private_api(folder) as base,httpx.Client(base_url=base,trust_env=False,timeout=180) as client:
  for item in source['speech_checks']:
   raw=item['raw_reply']
   if not ('I can offer' in raw or raw.startswith(('Table for','Your table is booked','Change ','Your reservation is updated'))):continue
   path=ROOT/'results/browser-voice/natural-speech-booking-update-flow'/item['wav']
   with wave.open(str(path)) as wav:rate=wav.getframerate();samples=np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2')
   pcm=np.interp(np.arange(int(len(samples)*16000/rate))*rate/16000,np.arange(len(samples)),samples).astype('<i2');buffer=io.BytesIO()
   with wave.open(buffer,'wb') as wav:wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(pcm.tobytes())
   response=client.post('/api/voice/transcribe',content=buffer.getvalue(),headers={'Content-Type':'audio/wav'});response.raise_for_status();heard=response.json()['text']
   rows.append({'wav':str(path),'heard':heard,'passed':natural_audio_matches(item['piper_text'],heard,source['expected_date'])})
   print(item['wav'],rows[-1]['passed'],flush=True)
 result={'method':'Audio-only replay of exactly the same six stored reply WAVs; original business-flow checks retained separately. No regenerated audio.','original_business_checks':source['checks'],'audio':rows,'passed':len(rows)==6 and all(r['passed'] for r in rows),'production_baseline_before':before,'production_baseline_after':production_baseline()}
 result['production_baseline_unchanged']=result['production_baseline_before']==result['production_baseline_after']
 (ROOT/'results/decision-latency/natural-audio-replay.json').write_text(json.dumps(result,indent=2));assert result['production_baseline_unchanged']
 if not result['passed']:raise SystemExit(1)
if __name__=='__main__':main()
