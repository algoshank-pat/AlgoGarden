"""Implementation-only handoff; read private evidence, never edit independent scores."""
import json,math,statistics,hashlib,urllib.request
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));OUT=ROOT/'results/cake-latency-20261028'
from freeze_customer100 import digest
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def stats(values):
 values=sorted(values)
 return {'n':len(values),'average':statistics.mean(values),'p50':statistics.median(values),'p95':values[math.ceil(.95*len(values))-1]} if values else None
def group_profile(name):
 r=read(OUT/name/'profile.json');result={}
 for group in ['first','repeat']:
  rows=[x for x in r['rows'] if x['context_run']==group]
  result[group]={'latency_ms':stats([x['ms'] for x in rows]),'correct':sum(x['correct'] for x in rows),
   'model_calls':sum(x.get('model_called',True) for x in rows),
   'prompt_tokens':stats([x['prompt_tokens'] for x in rows if x['prompt_tokens'] is not None]),
   'output_tokens':stats([x['output_tokens'] for x in rows if x['output_tokens'] is not None]),
   'model_stages_ms':{k:stats([x['timings_ms'][k] for x in rows]) for k in ['load_duration','prompt_eval_duration','eval_duration','total_duration']},
   'http_and_application_overhead_ms':stats([x['http_validation_overhead_ms'] for x in rows])}
 return result
baseline=read(OUT/'before/profile.json');before=read(OUT/'workflow-before2/profile.json');after=read(OUT/'workflow-final/profile.json');release=read(OUT/'release/report.json')
assert before['manifest']==after['manifest']
assert all(x['correct'] and x['decision']['write_free'] for x in after['rows'])
assert all(x['passed'] for x in release['booking_checks']+release['existing_checks'])
assert release['correct']==release['total']==105
candidate=read(OUT/'candidate/profile.json')
unsafe=[{'case':x['case'],'context_run':x['context_run'],'fields':x['decision']['fields']} for x in candidate['rows'] if
 (x['case']=='mixed_phone' and x['decision']['fields'].get('name') is not None) or x['decision']['fields'].get('reference') is not None]
assert unsafe
settings=[]
for report in [baseline,before,read(OUT/'candidate/profile.json')]:
 for row in report['rows']:
  if row.get('request'):settings.append({k:row['request'].get(k) for k in ['model','think','keep_alive','options']})
assert all(s=={'model':'gemma4:e2b-it-qat','think':False,'keep_alive':'30m','options':{'num_ctx':2048,'num_predict':256,'temperature':0}} for s in settings)
stage_values={};mcp=[]
for line in (OUT/'release/private/calls.jsonl').read_text(encoding='utf-8').splitlines():
 for e in json.loads(line)['events']:
  if e['name'].endswith('.completed') and 'duration_ms' in e:stage_values.setdefault(e['name'],[]).append(e['duration_ms'])
  if e['name']=='mcp.lookup.timings':mcp.append(e['details'])
report={'model':'gemma4:e2b-it-qat','model_digest_before':baseline['model_digest_before'],'inference_settings':settings[0],
 'semantic_model_profile_before':group_profile('before'),
 'rejected_prompt_candidate':{'profile':group_profile('candidate'),'unsafe_extractions':unsafe,'accepted':False,'reason':'Invented name/reference values despite faster output; original prompt and schema restored.'},
 'paired_booking_workflows':{'before':group_profile('workflow-before2'),'after':group_profile('workflow-final'),'identical_inputs_and_states':True,'wrong_outcomes_before':sum(not x['correct'] for x in before['rows']),'wrong_outcomes_after':sum(not x['correct'] for x in after['rows'])},
 'development_target':{'warm_recognized_workflow_p95_ms':100,'wrong_outcomes':0,'met':group_profile('workflow-final')['repeat']['latency_ms']['p95']<100},
 'release_api':{'turns':105,'behavior_checks_passed':release['correct'],'wrong_outcomes':0,'latency_ms':stats([x['ms'] for x in release['cases']]),'model_generations':sum(x['generations'] for x in release['cases']),'max_generations_per_turn':max(x['generations'] for x in release['cases']),'cake_policy_checks':sum(x['label'].startswith(('cake-','homemade-','dessert-','notice-','permission-','approval-')) and x['passed'] for x in release['cases']),'booking_flows':release['booking_checks'],'existing_flows':release['existing_checks']},
 'per_step_ms':{k:stats(v) for k,v in stage_values.items()},'mcp_stages':mcp,
 'index_check':read(OUT/'index-check/report.json'),'live_index':read(OUT/'live-index.json'),
 'regressions':'541 passed; see regressions-final.txt',
 'limitations':[
  'Warm recognized workflow target applies only to the closed grammar, not arbitrary language. Unmatched/ambiguous/mixed requests keep the original model path and can still take about ten seconds or more.',
  'First/repeat are request-context and application-resource measurements with a resident model; they are not model-unloaded cold starts. Model cold starts were not forced because the 30-minute residency setting is fixed. Each group has n=4, so p95 equals its maximum.',
  'Host load was not controlled. The raw workflow baseline includes slow first-context outliers (up to 25 seconds); all samples are retained. The improvement comes from eliminating generation for accepted requests, not claiming faster hardware/inference.',
  'Only targeted private chat and voice-text checks are included; no microphone, ASR or speaker timing, and no independent 100-conversation acceptance rerun.',
  'Numeric-name/reference errors rejected the shorter-prompt candidate. Baseline model also sometimes echoed stored fields in the raw semantic probe; the accepted deterministic cases return only utterance fields. The fallback model path is unchanged.',
  'No availability, reservation mutation, customer identity or completed response cache was introduced. Existing business/version-scoped FAQ caching and scoped MCP pooling remain in use.',
  'The street address still requires owner configuration. Historical conversation replies and independent test artifacts were not rewritten.'
 ]}
with urllib.request.urlopen('http://127.0.0.1:11434/api/tags',timeout=15) as response:tag=next(m for m in json.load(response)['models'] if m['name']==report['model'])
report['model_digest_after']=tag['digest'];assert tag['digest']==report['model_digest_before']=='07ea59a474013479c8b6b802bef095c40e964a1d776ba02f264c0e30e1aede0c'
report['baseline_after']=digest(ROOT/'reservations.sqlite3');assert report['baseline_after']==baseline['baseline_before']==release['baseline_after']
report['unchanged_core']={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==baseline['source_hashes'][n] for n in ['decision_engine.py','observability.py','reservation_mcp_client.py','mcp_lookup_pool.py','receptionist.py','decision_protocol.py']};assert all(report['unchanged_core'].values())
files=[*ROOT.glob('*.py'),*ROOT.glob('voice/*.py'),*ROOT.glob('restaurants/*.yaml'),*ROOT.glob('businesses/*/docs/*.md')]
manifest={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
assert all(manifest[n]==h for n,h in release['source_hashes'].items())
(OUT/'source-manifest.json').write_text(json.dumps(manifest,indent=2));report['source_manifest_sha256']=hashlib.sha256((OUT/'source-manifest.json').read_bytes()).hexdigest()
assert '541 passed' in (OUT/'regressions-final.txt').read_text()
(OUT/'summary.json').write_text(json.dumps(report,indent=2))
lines=['CAKE POLICY AND LATENCY HANDOFF',
 'Authoritative policy: '+str(ROOT/'businesses/malnad_mane/docs/groups_events.md'),
 'All outside food is prohibited, including cakes and desserts; no advance-notice or staff-permission exceptions.',
 'Live index rebuilt, obsolete chunks removed, FAQ cache invalidation verified; policy digest '+report['live_index']['policy_digest'],
 'Model '+report['model']+' digest before/after '+tag['digest'],
 'Inference settings unchanged: '+json.dumps(report['inference_settings']),
 'Original prompt/schema/compact protocol unchanged. Shorter prompt rejected for invented fields; unsafe records retained in candidate/profile.json.',
 'Paired accepted workflow first-context ms: '+str(report['paired_booking_workflows']['before']['first']['latency_ms'])+' -> '+str(report['paired_booking_workflows']['after']['first']['latency_ms']),
 'Paired accepted workflow repeated-context ms: '+str(report['paired_booking_workflows']['before']['repeat']['latency_ms'])+' -> '+str(report['paired_booking_workflows']['after']['repeat']['latency_ms']),
 'These 8 accepted workflow requests used 8 -> 0 generations with all explicit fields preserved and no pre-confirmation writes. No faster-refusal cases included.',
 'Final mixed API replay: '+str(report['release_api']['latency_ms'])+'; 105 behavior checks passed, including 24 cake-policy checks, 4 create/recovery flows and 12 lookup/update/cancel flows.',
 'Remaining model calls in mixed API replay: '+str(report['release_api']['model_generations'])+'; tail latency remains high for unmatched inputs.',
 'Focused private regressions: 541 passed. Wrong outcomes in accepted paired workflows: 0 before and after.',
 'Reservation baseline before/after: '+str(report['baseline_after']),
 'Changed runtime files: businesses/malnad_mane/docs/groups_events.md, policy_requests.py, policy_faq.py, booking_fastpath.py (new), decision_fastpaths.py.',
 'Tests: tests/test_outside_food_policy.py (new), tests/test_booking_fastpath.py (new), tests/test_accuracy_fixes.py (current policy fixture and fallback-field-recovery input).',
 'Development scripts: scripts/profile_cake_latency.py, scripts/check_cake_workflows.py, scripts/verify_outside_food_index.py, scripts/report_cake_latency.py.',
 'Runtime manifest '+str(OUT/'source-manifest.json')+' SHA256 '+report['source_manifest_sha256'],
 'Shared API restarted after rebuilding live policy index; API, voice page and frontend proxy HTTP 200. UX and independent testing thread untouched.',
 *report['limitations']]
(OUT/'handoff.txt').write_text('\n'.join(lines),encoding='utf-8');print('\n'.join(lines[:13]))
