"""Summarize private voice/state evidence; estimated silence is not physical mic timing."""
import json,math,statistics,hashlib,urllib.request,re
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));OUT=ROOT/'results/voice-recovery-20261029'
from freeze_customer100 import digest
r=json.loads((OUT/'complete-audio/report.json').read_text(encoding='utf-8'));assert r['passed']
def stats(v):
 v=sorted(v);return {'n':len(v),'average_ms':statistics.mean(v),'p50_ms':statistics.median(v),'p95_ms':v[math.ceil(.95*len(v))-1]}
hint=None;totals=[]
for t in r['turns']:
 silence=1500 if hint and (hint['expected_field']=='name' or hint['name_confirmation']) else 1300 if hint and hint['expected_field']=='phone' else 450
 totals.append(silence+t['recognized_audio_to_first_packet_ms']);hint=t['result'].get('voice_input')
baseline=digest(ROOT/'reservations.sqlite3');assert baseline==r['baseline_before']==r['baseline_after']
with urllib.request.urlopen('http://127.0.0.1:11434/api/tags',timeout=15) as response:tag=next(m for m in json.load(response)['models'] if m['name']=='gemma4:e2b-it-qat')
assert tag['digest']==r['model_digest']=='07ea59a474013479c8b6b802bef095c40e964a1d776ba02f264c0e30e1aede0c'
test_text=(OUT/'final-checked.txt').read_text();assert '559 passed' in test_text
assert 'pass 12' in (OUT/'browser-tests-final.txt').read_text()
files=[*ROOT.glob('*.py'),*ROOT.glob('voice/*.py'),*ROOT.glob('restaurants/*.yaml'),*ROOT.glob('businesses/*/docs/*.md'),ROOT/'dashboard/src/VoiceTest.jsx',ROOT/'dashboard/src/voicePlayback.mjs',ROOT/'dashboard/src/voiceEndpoint.mjs']
manifest={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
(OUT/'source-manifest.json').write_text(json.dumps(manifest,indent=2))
report={'model':tag['name'],'digest':tag['digest'],'baseline':baseline,'regressions_passed':559,'browser_tests_passed':12,'frontend_build_passed':True,
 'audio_checks':{'passed':sum(c['passed'] for c in r['checks']),'total':len(r['checks'])},
 'endpoint_silence_ms':{'normal':450,'phone':1300,'name_or_spelling':1500},
 'synthetic_pipeline_ms':{'asr':stats([t['stt_ms'] for t in r['turns']]),'api':stats([t['api_ms'] for t in r['turns']]),'tts_first_packet':stats([t['first_packet_ms'] for t in r['turns']]),'estimated_end_of_speech_to_first_packet':stats(totals)},
 'speech_resource_warmup_ms':r['speech_resource_warmup_ms'],
 'source_manifest_sha256':hashlib.sha256((OUT/'source-manifest.json').read_bytes()).hexdigest(),
 'changes':['Repeat, pause and resume preserve the pending stage, fields and confirmation; completing/resuming is not a write.',
  'Unclear yes/question combinations cannot accept alternatives. Clear confirmation aliases still require the existing confirmation state.',
  'Declarative high-chair requests retrieve the available-on-request fact, not only the staff-contact instruction.',
  'Voice asks for name/spelling, supports separate first/last spelling and phonetic examples, reads the decoded name back and waits before inserting it into the draft. Final booking requires a separate confirmation.',
  'Misheard spelling requests repetition instead of treating phonetic words as names. Known components survive retries; counters reset after a valid answer and hand off after four consecutive failures.',
  'NDJSON WAV packets play before whole-reply synthesis completes. Barge-in runs while speaking/preparing/processing, retains initial speech and cancels old playback. Interrupted queued input is serialized and old spoken replies yield to the caller.',
  'ASR and synthesis have separate bounded locks. Client disconnect closes the stream; obsolete speech packets cannot revive playback.',
  'Speech models warm before the first voice exchange. The existing model tag and decision inference settings are unchanged; no replacement weights downloaded.'],
 'limitations':['Synthetic Piper-to-Parakeet tests and browser logic tests do not establish physical laptop microphone/speaker echo behavior.',
  'End-of-speech latency is configured silence plus measured ASR, API and first-audio-packet readiness; browser/device output buffering and network latency are not included.',
  'Actual recognition of spelling can still need repetition. Earlier failed diagnostic runs are retained; unclear inputs now keep the draft unbooked.',
  'Unmatched requests can still require the unchanged model and take roughly ten seconds or more. The 13-turn successful speech run predominantly exercises deterministic paths.',
  'Interrupting speech cannot undo an already completed reservation transaction. Queued requests remain serialized and existing confirmation/idempotency protections are retained.',
  'The frozen independent acceptance suite and historical results were not modified. A physical live test and independent rerun remain necessary.']}
(OUT/'summary.json').write_text(json.dumps(report,indent=2))
lines=['VOICE RECOVERY IMPLEMENTATION',*report['changes'],
 'Model '+report['model']+' digest '+report['digest'],
 'Reservation baseline rowid hash unchanged: '+str(baseline),
 'Private regressions: 559 passed. Browser audio tests: 12 passed. Vite production build passed.',
 'Actual synthetic speech checks: '+str(report['audio_checks']),
 'Endpoint silence configuration: '+str(report['endpoint_silence_ms']),
 'Measured synthetic speech stages and estimated readiness: '+json.dumps(report['synthetic_pipeline_ms']),
 'One-time speech resource warmup: '+str(r['speech_resource_warmup_ms'])+'ms.',
 'Name-confirmation behavior intentionally adds a step to voice creation. Chat behavior remains compatible. The application tests were updated for the new explicit name step; independent scoring was untouched.',
 'Runtime manifest: '+str(OUT/'source-manifest.json'),
 'Raw real-service transcript, audio and timings: '+str(OUT/'complete-audio/report.json'),
 'The final post-booking name-state reset and confirmation aliases are covered by final regressions; raw audio runs retain their own source hashes.',
 *report['limitations']]
(OUT/'handoff.txt').write_text('\n'.join(lines),encoding='utf-8');print(json.dumps({k:report[k] for k in ['audio_checks','endpoint_silence_ms','synthetic_pipeline_ms']},indent=2))
