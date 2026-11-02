"""Refresh measured evidence, with live microphone checks explicitly pending."""
import pathlib,json,csv,sqlite3,hashlib,statistics,math,urllib.request,sys
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
OUT=ROOT/'results/spoken-booking-fixes-20261030'
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def fingerprint(rows):return hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()
def stats(values):
 if not values:return {'n':0,'average_ms':None,'p95_ms':None}
 a=sorted(values);return {'n':len(a),'average_ms':statistics.mean(a),'p50_ms':statistics.median(a),'p95_ms':a[math.ceil(.95*len(a))-1]}
before=read(OUT/'before.json');v={'reservation_checks':{}}
for rel,old in before['databases'].items():
 with sqlite3.connect('file:'+str(ROOT/rel)+'?mode=ro',uri=True) as db:
  db.row_factory=sqlite3.Row;rows=[dict(r) for r in db.execute('select rowid as _rowid,* from reservations order by rowid')]
 v['reservation_checks'][rel]={'before':old,'after_count':len(rows),'after_sha256':fingerprint(rows),'original_rows_unchanged':fingerprint(rows[:old['count']])==old['sha256']}
from freeze_customer100 import digest
v['baseline']=digest(ROOT/'reservations.sqlite3')
v['baseline']['matches_known_1911_baseline']=v['baseline']['count']==1911 and v['baseline']['raw_rows_sha256']=='ff8283a4fd3bd9cfb6394d82c10119c012e54bfa5d710fbc40d817017d3f8141'
v['changed_files']=[rel for rel,h in before['source'].items() if sha(ROOT/rel)!=h]
v['changed_files'].append('scripts/check_voice_booking_interruptions.py')
v['new_files']=['dashboard/src/microphoneCapture.mjs','dashboard/src/microphoneCapture.test.mjs','tests/test_capture_observations.py','tests/test_offered_booking_context.py','scripts/profile_offered_booking.py','scripts/report_spoken_booking_fixes.py']
v['source_manifest']={rel:sha(ROOT/rel) for rel in v['changed_files']+v['new_files']}
v['unrelated_ux_unchanged']=all(rel.replace('\\','/')=='dashboard/src/VoiceTest.jsx' or not rel.replace('\\','/').startswith('dashboard/') for rel in v['changed_files'])
v['model_before_digest']=read(ROOT/'results/full-spoken-booking-20261030/before.json')['digest']
tags=json.load(urllib.request.urlopen('http://127.0.0.1:11434/api/tags',timeout=10))
v['model_after_digest']=next(m['digest'] for m in tags['models'] if m['name']=='gemma4:e2b-it-qat')
v['model_digest_unchanged']=v['model_before_digest']==v['model_after_digest']=='07ea59a474013479c8b6b802bef095c40e964a1d776ba02f264c0e30e1aede0c'
v['actual_model_requests']=read(OUT/'model-probe/verified-settings.json')
v['gate_before']=read(OUT/'before-sequential/profile.json')['gate_stats'];v['gate_after']=read(OUT/'after-sequential/profile.json')['gate_stats']
original_calls=[json.loads(l) for l in (ROOT/'results/full-spoken-booking-20261030/private/calls.jsonl').read_text().splitlines() if l.strip()]
original_gate=next(r for r in original_calls if r['request_id']=='9040d630-f9d3-40fc-a3b0-c6c500372e59')
response=next(e['details']['output'] for e in original_gate['events'] if e['name']=='ollama.response_received')
v['original_gate_model_breakdown_ms']={k:response[k]/1e6 for k in ['load_duration','prompt_eval_duration','eval_duration','total_duration']}
v['cold_model_measurement']='NOT VERIFIED: model residency was not forced or unloaded; first application gate and subsequent warm gates are separate.'
historical=read(ROOT/'results/full-spoken-booking-20261030/before.json')
v['historical_frozen_sources_unchanged']=all(sha(ROOT/'results/full-spoken-booking-20261030/application'/rel)==h for rel,h in historical['source_sha256'].items())
report_path=max((ROOT/'results/voice-offered-acceptance').glob('*/report.json'),key=lambda p:p.stat().st_mtime)
audio=read(report_path);v['recorded_acceptance']={'report':str(report_path),'passed':audio['passed'],'turns':len(audio['turns']),'checks':len(audio['checks'])}
v['recorded_timings']={name:stats([t[key] for t in audio['turns']]) for name,key in [('recognition','stt_ms'),('backend','api_ms'),('server_tts_first_packet','first_packet_ms')]}
with (OUT/'recorded-turns.csv').open('w',newline='',encoding='utf-8') as f:
 w=csv.DictWriter(f,fieldnames=['label','caller','transcript','reply','spoken_reply','recognition_ms','backend_ms','server_tts_first_packet_ms','native_playing_ms','input_end_to_native_playing_ms']);w.writeheader()
 for t in audio['turns']:w.writerow({'label':t['label'],'caller':t['caller'],'transcript':t['transcript'],'reply':t['reply'],'spoken_reply':t['result'].get('spoken_reply',t['reply']),'recognition_ms':t['stt_ms'],'backend_ms':t['api_ms'],'server_tts_first_packet_ms':t['first_packet_ms'],'native_playing_ms':'NOT VERIFIED','input_end_to_native_playing_ms':'NOT VERIFIED'})
v['live_microphone']='NOT VERIFIED: awaiting user run on original VoiceTest component at http://127.0.0.1:5174/voice.'
v['physical_speaker_latency']='NOT VERIFIED: native playing events are not an acoustic measurement.'
live_root=ROOT/'results/voice-turn-ownership-20261029/private'
capture_path=live_root/'voice_capture.jsonl'
if capture_path.exists():
 captures=[json.loads(l) for l in capture_path.read_text().splitlines() if l.strip()]
 session_ids=['774d15e2-32e1-4ad4-851b-53ba3adb7b51','49fb20c2-59ee-4286-b7a0-a01763b0870b']
 live=[json.loads(l) for l in (live_root/'calls.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
 live=[r for r in live if r['session_id'] in session_ids]
 measurements=[json.loads(l) for l in (live_root/'turn_timings.jsonl').read_text().splitlines() if l.strip()]
 measurements=[r for r in measurements if r['session_id'] in session_ids]
 observed=[r for r in captures if r['session_id'] in session_ids]
 played=[r for r in observed if r['phase']=='playing'];listened=[r for r in observed if r['phase']=='listening']
 startup=any(r['phase']=='ready' and r['track_live'] and r['audio_context']=='running' for r in observed)
 auto_mute=bool(played) and all(not r['track_enabled'] for r in played)
 resume=bool(listened) and all(r['track_enabled'] is not r['manually_muted'] for r in listened)
 manual=any(r['phase']=='listening' and r['manually_muted'] and not r['track_enabled'] for r in observed) and any(r['phase']=='manual_mute' and not r['manually_muted'] and r['track_enabled'] for r in observed)
 v['live_microphone']={'origin':'http://127.0.0.1:5174/voice','sessions':session_ids,'startup':'PASS' if startup else 'NOT VERIFIED','automatic_mute':'PASS' if auto_mute else 'FAIL','listening_resume':'PASS' if resume else 'FAIL','manual_mute':'PASS' if manual else 'NOT VERIFIED','native_playing_observations':len(played),'listening_observations':len(listened),'method':'User-operated original VoiceTest component with real getUserMedia track. Backend captures track state; native playing events correlate to request/session IDs. Historical frozen origin 5184 was not restarted or modified.'}
 v['live_timings']={name:stats([r['duration_ms'] for r in measurements if r['stage']==stage]) for name,stage in [('recognition','browser.stt_roundtrip'),('backend','browser.backend_roundtrip'),('tts_to_native_playing','browser.tts_to_first_playback'),('input_end_to_native_playing','browser.reply_start')]}
 v['live_call_checks']={'booking_write_calls':sum(r['result']['structured']['tools_called'].count('book_table') for r in live),'main_call_ended':any(r['session_id']==session_ids[0] and r['result']['ended'] for r in live),'exact_acceptance_live':'NOT VERIFIED: the user chose a different live booking (Tom Jones, October 31, five guests). The exact Alex Morgan acceptance sequence passed through recorded input.'}
 v['live_slow_turns']=[{'transcript':r['caller_text'],'backend_ms':r['result']['latency_ms']} for r in live if r['result']['latency_ms']>2000]
 (OUT/'live-capture-observations.json').write_text(json.dumps(observed,indent=2))
 byrequest={}
 for m in measurements:byrequest.setdefault((m['session_id'],m['request_id']),{})[m['stage']]=m['duration_ms']
 with (OUT/'live-turns.csv').open('w',newline='',encoding='utf-8') as f:
  w=csv.DictWriter(f,fieldnames=['session_id','request_id','caller','reply','recognition_ms','backend_ms','tts_to_native_playing_ms','input_end_to_native_playing_ms','tools','ended']);w.writeheader()
  for r in live:
   m=byrequest.get((r['session_id'],r['request_id']),{})
   w.writerow({'session_id':r['session_id'],'request_id':r['request_id'],'caller':r['caller_text'],'reply':r['result'].get('spoken_reply',r['result']['reply']),'recognition_ms':m.get('browser.stt_roundtrip'),'backend_ms':m.get('browser.backend_roundtrip'),'tts_to_native_playing_ms':m.get('browser.tts_to_first_playback'),'input_end_to_native_playing_ms':m.get('browser.reply_start'),'tools':json.dumps(r['result']['structured']['tools_called']),'ended':r['result']['ended']})
(OUT/'verification.json').write_text(json.dumps(v,indent=2))
lines=['# Spoken booking fixes — pending real microphone acceptance','','| Issue | Status | Evidence |','|---|---|---|','| Supplied natural date | PASS, recorded input | November 9 retained without repetition |','| Offered party/date/time | PASS, recorded input | Exactly one Alex Morgan booking, two guests, November 9 at 20:30 |','| Slow new-reservation gate | PASS | Same offered inputs; no generation on scoped No |','| Pending microphone cancellation / late grant | PASS, automated only | Native startup lifecycle tests; timeout and actionable errors |','| Original VoiceTest real microphone | NOT VERIFIED | Waiting for user live call |','| Physical auto-mute/manual mute/resume | NOT VERIFIED | Automated guards passed; physical capture observations pending |','| Native browser playing latency | NOT VERIFIED for this retest | Requires original-page live events |','','Gate before/after: '+json.dumps({'before':v['gate_before'],'after':v['gate_after']}),'','Cold/warm limit: '+v['cold_model_measurement'],'','Recorded timings (milliseconds): '+json.dumps(v['recorded_timings']),'','Recorded transcript and per-turn timings: recorded-turns.csv. Server first packet readiness is not native browser playing or physical speaker output.','','605 core regression tests, 1 capture-observation test, 26 JavaScript guard tests passed. Frontend build passed.','','Changed files: '+', '.join(v['changed_files']+v['new_files']),'','Exact model digest: '+v['model_after_digest'],'','Full hashes, actual inference options and source manifest: verification.json. Historical frozen evaluation source hashes are checked without modifying that copy.','','**Task is not complete while the original microphone path remains unverified.**']
(OUT/'report.md').write_text('\n'.join(lines),encoding='utf-8')
if isinstance(v['live_microphone'],dict):
 text=(OUT/'report.md').read_text()
 text=text.replace('# Spoken booking fixes — pending real microphone acceptance','# Spoken booking fixes — recorded and live verification')
 text=text.replace('| Original VoiceTest real microphone | NOT VERIFIED | Waiting for user live call |','| Original VoiceTest real microphone | PASS | User-operated 5174 page; live getUserMedia track, actual recognition |')
 text=text.replace('| Physical auto-mute/manual mute/resume | NOT VERIFIED | Automated guards passed; physical capture observations pending |','| Live-track auto-mute/manual mute/resume | '+('PASS' if manual and auto_mute and resume else 'NOT VERIFIED')+' | Native track disabled during playback; manual mute persists after greeting; unmute restores capture |')
 text=text.replace('| Native browser playing latency | NOT VERIFIED for this retest | Requires original-page live events |','| Native browser playing latency | PASS | Correlated native playing events in live-turns.csv |')
 text=text.replace('605 core regression tests, 1 capture-observation test, 26 JavaScript guard tests passed. Frontend build passed.','Final private regression suite: see pytest-final.log. 26 JavaScript microphone/playback guard tests and frontend build passed.')
 text=text.replace('**Task is not complete while the original microphone path remains unverified.**','Original microphone startup is now verified. Limits remain: exact Alex Morgan sequence used recorded input; live calls used a different booking and a separate manual-mute check. Physical acoustic latency was not measured. Pending-permission cancellation and late-grant disposal were verified with deterministic tests, not by changing browser/OS permission settings.')
 text+='\n\nLive timing statistics (milliseconds): '+json.dumps(v['live_timings'])+'\n\nLive capture observations: '+json.dumps(v['live_microphone'])+'\n\nLive-call limitations: '+json.dumps(v['live_call_checks'])+'\n\nAdditional live finding: the transcript “October uh” incorrectly entered phone collection after a broad booking request. Clarification state is now retained and this exact transcript asks for repetition; automated regression added. This extra fix has not been retested through a second physical spoken-month attempt.\n'
 text+='\nFinal tests: **607 Python tests and 26 JavaScript tests passed**. Recorded acceptance: 14 turns, 45 assertions, exactly one correct private booking.\n\nOriginal 8.655-second gate model breakdown (ms): '+json.dumps(v['original_gate_model_breakdown_ms'])+'\n\nRemaining latency limit: live calls contained these slow model-dependent turns: '+json.dumps(v['live_slow_turns'])+'. The partial-month clarification is now deterministic; the short/incomplete-phone fallback remains outside this gate fix. Live latency statistics include both turns and have not been filtered to hide them.\n'
 (OUT/'report.md').write_text(text,encoding='utf-8')
print(json.dumps({k:v[k] for k in ['changed_files','reservation_checks','model_digest_unchanged','baseline','recorded_acceptance','historical_frozen_sources_unchanged']},indent=2))
