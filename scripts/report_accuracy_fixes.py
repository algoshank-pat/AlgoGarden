"""Summarize this implementation's private evidence without editing acceptance results."""
import json,statistics,math,hashlib,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from freeze_customer100 import digest
OUT=ROOT/'results/accuracy-fixes-20261028'
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def stats(v):
 v=sorted(v)
 return {'n':len(v),'average':statistics.mean(v),'p50':statistics.median(v),'p95':v[math.ceil(.95*len(v))-1]} if v else None
def details(phase):
 r=read(OUT/phase/'report.json');cases=r['cases'];info=[x for x in cases if not x['label'].startswith('booking-')]
 calls=[c for x in cases for c in x['calls'] if c['component']=='ollama.chat']
 settings=[{k:c['inputs']['kwargs'].get(k) for k in ['model','think','options','keep_alive']} for c in calls]
 stages={}
 for line in (OUT/phase/'private/calls.jsonl').read_text(encoding='utf-8').splitlines():
  for e in json.loads(line)['events']:
   if e['name'].endswith('.completed') and 'duration_ms' in e:stages.setdefault(e['name'],[]).append(e['duration_ms'])
 facts=[x for x in info if not x['label'].startswith(('location-','unrelated-'))]
 return {'information_checks':{'passed':sum(x['passed'] for x in info),'total':len(info)},
   'factual_requests_answered':{'passed':sum(x['passed'] for x in facts),'total':len(facts)},
   'unfulfilled_address_requests':4,'correct_scope_refusals':sum(x['passed'] for x in info if x['label'].startswith('unrelated-')),
   'booking_flows':r['booking_checks'],
   'turn_latency_ms':stats([x['ms'] for x in cases]),'generations':len(calls),'max_generations_per_turn':max(x['generations'] for x in cases),
   'model_duration_ms':stats([c['total_duration']/1e6 for c in calls if c.get('total_duration')]),
   'prompt_tokens':stats([c['prompt_eval_count'] for c in calls if c.get('prompt_eval_count') is not None]),
   'generated_tokens':stats([c['eval_count'] for c in calls if c.get('eval_count') is not None]),
   'per_stage_ms':{k:stats(v) for k,v in stages.items()},'settings':list({json.dumps(s,sort_keys=True) for s in settings}),
   'baseline_before':r['baseline_before'],'baseline_after':r['baseline_after'],
   'model_digest_before':r['model_digest_before'],'model_digest_after':r['model_digest_after']}
a=details('before');b=details('release')
one=read(ROOT/'results/mcp-lookup/accuracy-20261028-paired-oneshot.json');pool=read(ROOT/'results/mcp-lookup/accuracy-20261028-paired-pooled.json')
summary={'before':a,'after':b,'mcp':{'oneshot':one['metrics'],'pooled':pool['metrics'],'correct_before':one['correct'],'correct_after':pool['correct'],
 'mean_reduction_percent':100*(1-pool['metrics']['total_ms']['mean_ms']/one['metrics']['total_ms']['mean_ms']),
 'cold_scopes':sum(not r.get('reused',False) for r in pool['rows']),'warm_reused':sum(r.get('reused',False) for r in pool['rows']),
 'pool_shutdown_ms':pool['pool_shutdown_ms'],'private_baseline_unchanged':one['private_baseline_unchanged'] and pool['private_baseline_unchanged']},
 'limitations':['48 information checks cover 12 prompts across chat/voice-text and idle/phone states; this is targeted development evidence, not overall production accuracy. These are 40 factual requests, 4 missing-address honesty checks and 4 scope-refusal controls. Address requests remain unfulfilled even when the honesty check passes; scope controls are reported separately.',
 'Four authored booking flows passed after; original initial fields were already preserved in all four before flows. Improvements observed here concern service-hours interruption, resumption, spoken corrections and confirmation. Missing-field recovery is additionally forced in a private unit test.',
 'The 77-turn latency comparison uses the same inputs and state setup. Gains primarily come from 28 fewer generations; remaining model requests are still slow. Cache first misses and cold retrieval are included. Host load was not controlled.',
 'Earlier one-shot MCP timing during active inference averaged 3553ms; the quieter paired oneshot/pooled comparison is reported separately to avoid attributing load changes to pooling.',
 'No physical microphone/ASR/TTS included in this implementation replay. Independent 100-conversation acceptance remains pending.',
 'The street address remains unconfigured and requires owner input. Scope refusals for unrelated questions are preserved.'],
 'inference_settings_equal':set(a['settings'])==set(b['settings']),
 'production_baseline_now':digest(ROOT/'reservations.sqlite3')}
assert summary['inference_settings_equal']
assert summary['production_baseline_now']==a['baseline_before']==b['baseline_after']
assert a['model_digest_before']==b['model_digest_after']=='07ea59a474013479c8b6b802bef095c40e964a1d776ba02f264c0e30e1aede0c'
assert all(x['passed'] for x in b['booking_flows']) and b['information_checks']['passed']==48
files=[*ROOT.glob('*.py'),*ROOT.glob('voice/*.py'),*ROOT.glob('restaurants/*.yaml'),*ROOT.glob('businesses/*/docs/*.md')]
manifest={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
(OUT/'source-manifest.json').write_text(json.dumps(manifest,indent=2))
summary['source_manifest_sha256']=hashlib.sha256((OUT/'source-manifest.json').read_bytes()).hexdigest()
summary['unchanged_model_prompt_protocol_settings_files']={n:manifest[n]==read(OUT/'before/report.json')['source_hashes'][n] for n in ['decision_engine.py','decision_protocol.py','observability.py','restaurants\\demo_restaurant.yaml']}
assert all(summary['unchanged_model_prompt_protocol_settings_files'].values())
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
lines=['ACCURACY AND LATENCY IMPLEMENTATION HANDOFF',
 'Model: gemma4:e2b-it-qat; digest verified before/after: '+b['model_digest_after'],
 'Inference settings identical: '+str(summary['inference_settings_equal'])+' '+str(b['settings']),
 'Compact protocol untouched. Decision engine prompt and schema unchanged. UX and independent testing project untouched.',
 'Baseline rowid-order hash before/after: '+str(summary['production_baseline_now']),
 'Information behavior checks (not request-fulfillment accuracy): '+str(a['information_checks'])+' -> '+str(b['information_checks']),
 'Targeted factual requests answered: '+str(a['factual_requests_answered'])+' -> '+str(b['factual_requests_answered'])+'. Four address requests remain unfulfilled; four unrelated-question controls are correctly refused.',
 'Booking/recovery flows passed: '+str(sum(x['passed'] for x in a['booking_flows']))+'/4 -> 4/4.',
 '77-turn reply latency ms (average/p50/p95): '+str(a['turn_latency_ms'])+' -> '+str(b['turn_latency_ms']),
 'Generations: '+str(a['generations'])+' -> '+str(b['generations'])+'; max one per turn.',
 'Prompt token counts per invoked generation: '+str(a['prompt_tokens'])+' -> '+str(b['prompt_tokens']),
 'Output token counts per invoked generation: '+str(a['generated_tokens'])+' -> '+str(b['generated_tokens']),
 'MCP oneshot -> pooled milliseconds: '+str(one['metrics']['total_ms'])+' -> '+str(pool['metrics']['total_ms']),
 'MCP correct: 16/16 each; mean reduction '+str(round(summary['mcp']['mean_reduction_percent'],2))+'%. Cold/warm: '+str(summary['mcp']['cold_scopes'])+'/'+str(summary['mcp']['warm_reused'])+'.',
 'Default pooled mode; explicit RECEPTIONIST_MCP_MODE=oneshot remains available. Fresh reads, scope, timeout, idle expiry, crash recovery and cleanup checked by tests/test_mcp_pool.py.',
 'Runtime changes: dietary_menu.py, information_requests.py, receptionist.py, booking_evidence.py, policy_requests.py, policy_faq.py, reservation_mcp_client.py, decision_fastpaths.py.',
 'Tests: tests/test_accuracy_fixes.py, tests/test_mcp_pool.py, tests/test_decision_engine.py (allergen fast-path now asserts zero generations and still requires dairy in the answer).',
 'Documentation: VOICE.md, README.md. Development diagnostics: scripts/check_accuracy_fixes.py and scripts/report_accuracy_fixes.py.',
 'Source manifest: '+str(OUT/'source-manifest.json')+'; SHA256 '+summary['source_manifest_sha256'],
 'Per-stage raw timings retained in private/calls.jsonl and results/mcp-lookup/accuracy-20261028-paired-*.json.',
 *summary['limitations']]
(OUT/'handoff.txt').write_text('\n'.join(lines),encoding='utf-8')
print('\n'.join(lines[:12]))
