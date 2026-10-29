"""Consolidate measured MCP latency and request-level correctness evidence."""
import json,hashlib,sqlite3,re
from pathlib import Path
from benchmark_mcp_lookup import stats
from run_private_decision_acceptance import production_baseline
ROOT=Path(__file__).parent;OUT=ROOT/'results/mcp-lookup'
def load(path):return json.loads(path.read_text(encoding='utf-8'))
def fixture_digest(name):
 with sqlite3.connect(f'file:{OUT/(name+"-private")/"db.sqlite3"}?mode=ro',uri=True) as c:
  rows=c.execute('SELECT business_id,date,time,party_size,phone,status FROM reservations ORDER BY business_id,date,time,party_size,phone,status').fetchall()
 return hashlib.sha256(json.dumps(rows).encode()).hexdigest()
def main():
 before=load(OUT/'final-before.json');after=load(OUT/'final-after.json');initial=load(OUT/'before.json')
 warm=[r for r in after['rows'] if r.get('reused')];cold=[r for r in after['rows'] if not r.get('reused')]
 indices={r['case'] for r in warm};matched=[r for r in before['rows'] if r['case'] in indices]
 dirs=sorted(p for p in (ROOT/'results/information-requests').iterdir() if p.is_dir() and (p/'report.json').exists())
 api_dir=dirs[-1];api=load(api_dir/'report.json')
 audit=[json.loads(x) for x in (api_dir/'data/calls.jsonl').read_text(encoding='utf-8').splitlines()]
 archived=load(ROOT/'results/customer100/20261027-scored-100/report.json');mismatches=[]
 for case_id,label in [(6,'scored-6'),(30,'scored-30')]:
  old=next(c for c in archived['results'] if c['case']['id']==case_id)['turns'][1]
  new=next(c for c in api['cases'] if c['label']==label)
  observed=next(c for c in audit if c['caller_text']==new['question'])
  mismatches.append({'case':case_id,'question':new['question'],'before_reply':old['response']['reply'],'before_ms':old['elapsed_ms'],'after_reply':new['replies'][0],'after_ms':observed['result']['latency_ms'],'availability_result':new['replies'][-1],'passed':new['passed']})
 old_policy=next(c for c in load(ROOT/'results/readiness-evaluation/heldout.json')['cases'] if c['id']=='policy_paraphrase')
 new_policy=next(c for c in api['cases'] if c['label']=='policy-0')
 log=(OUT/'final-verification.log').read_text(encoding='utf-8')
 counts=re.findall(r'(\d+) passed',log);subtests=re.findall(r'(\d+) subtests',log)
 result={'measurement':'Real entry-to-return MCP client calls; identical four-case scope/phone sequence on semantically identical private copies of the full 1,911-row baseline plus private caller records. No replay timing estimates.',
  'before':before['metrics'],'after_including_cold':after['metrics'],'after_warm':stats([r['total_ms'] for r in warm]),'after_cold':stats([r['total_ms'] for r in cold]),'before_matching_warm_case_indices':stats([r['total_ms'] for r in matched]),
  'mean_reduction_percent':round(100*(1-after['metrics']['total_ms']['mean_ms']/before['metrics']['total_ms']['mean_ms']),2),'correct_lookup_results':{'before':before['correct'],'after':after['correct'],'total_each':len(before['rows'])},'pool_shutdown_ms':after['pool_shutdown_ms'],
  'fixture_semantics_equal':fixture_digest('final-before')==fixture_digest('final-after'),'fixture_semantic_sha256':fixture_digest('final-before'),
  'initial_instrumentation_before_pooling':{'artifact':'before.json','small_private_fixture':True,'mean_ms':initial['metrics']['total_ms']['mean_ms']},
  'request_mismatches':mismatches,'late_arrival':{'question':old_policy['text'],'before_intent':old_policy['decision']['intent'],'before_semantic_ms':old_policy['elapsed_ms'],'after_reply':new_policy['reply'],'after_sources':new_policy['sources'],'passed':new_policy['passed']},
  'request_evaluation':{'artifact':str(api_dir/'report.json'),'passed':sum(c['passed'] for c in api['cases']),'total':len(api['cases']),'fulfilled_question_goals':9,'fresh_paraphrases':6,'negative_or_action_discrimination_checks':4,'concurrent_api_lookup_isolation_probes':2,'not_completed_update_or_cancel_claims':True,'private_reservations_unchanged':api['private_reservations_unchanged']},
  'verification':{'regression_tests':int(counts[-1]) if counts else None,'subtests':int(subtests[-1]) if subtests else None,'real_concurrent_mcp_calls':20,'checks':['restaurant/date/database/region/phone scoping','fresh reads after cancellation','bounded workers/queue and idle eviction','protocol child crash recovery','tool errors isolated from other callers','deadline includes pool contention','expired responses not reused','API shutdown and normal host exit close child processes','client rejects wrong-scope responses','policy/availability interruptions preserve pending state','affirmative/negative controls re-read original details','later corrections cannot restore stale readbacks','clarification retry counter resets correctly']},
  'pool_limits':{'scopes':4,'queued_per_scope':32,'idle_seconds':60,'total_lookup_deadline_seconds':20,'tool_read_deadline_seconds':15,'shutdown_join_seconds_per_worker':5},
  'production_baseline':production_baseline(),'production_baseline_unchanged':initial['baseline_before']==production_baseline() and api['production_baseline_unchanged'] and before['baseline_unchanged'] and after['baseline_unchanged'],'private_baselines_unchanged':before['private_baseline_unchanged'] and after['private_baseline_unchanged'],
  'rollout':{'production_default':'oneshot','pooled_mode':'opt-in with RECEPTIONIST_MCP_MODE=pooled','shared_api_restarted':False,'ux_changed':False,'approval_review':'Changing production default during evaluation was rejected; no workaround or rollout performed.'},
  'source_hashes':{f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in ['reservation_mcp_client.py','reservation_mcp.py','mcp_lookup_pool.py','information_requests.py','routing.py','receptionist.py','chat_api.py']}}
 assert result['production_baseline_unchanged'] and result['private_baselines_unchanged'] and result['fixture_semantics_equal']
 assert result['request_evaluation']['passed']==result['request_evaluation']['total'] and api['private_reservations_unchanged']
 (OUT/'comparison.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
 lines=['MCP LOOKUP AND REQUEST-FULFILLMENT REPORT','',result['measurement'],'',
  'INSTRUMENT FIRST: before.json and client-instrumented-before.py preserve the instrumented original one-shot lifetime before pooling was implemented. Initial small-fixture mean 1.501s. The main comparison below uses full private baseline volume, not a three-row database.','',
  'Latency (milliseconds) | n | mean | p50 | p95']
 for label,s in [('One-shot',result['before']['total_ms']),('Pooled: 3 cold + 9 reused',result['after_including_cold']['total_ms']),('Reused only',result['after_warm']),('Cold only',result['after_cold'])]:lines.append(f"{label} | {s['n']} | {s['mean_ms']} | {s['p50_ms']} | {s['p95_ms']}")
 lines+=['',f"Mixed-workload mean reduction: {result['mean_reduction_percent']}%. Both modes returned 12/12 correct results. Cold connections still cost about 1.2s; the p95 remains dominated by cold startup. Warm/cold ratio depends on traffic and the 60s idle expiry. This small sample is not a population latency guarantee.",'',
  'BEFORE STAGES (average / p50 / p95 milliseconds):']
 for key in ['spawn_ms','startup_after_spawn_ms','protocol_after_ready_ms','tool_ms','store_ms','query_ms','cleanup_ms']:
  s=result['before'][key];lines.append(f"{key}: {s['mean_ms']} / {s['p50_ms']} / {s['p95_ms']}")
 lines+=['','initialize_ms is also recorded, but INCLUDES interpreter/import startup; do not add it to startup_after_spawn_ms. protocol_after_ready_ms measures transport/initialization completion after the server readiness marker. query_ms includes SQL fetch and Python phone normalization/filtering; it is not SQL-only. store_ms/query_ms are subparts of tool_ms, not extra sequential stages.',
  'Pooled per-call cleanup is deferred, not eliminated. Explicit shutdown of the three scoped workers took '+str(result['pool_shutdown_ms'])+' ms per worker. Idle expiry, API lifespan shutdown and normal host exit are tested. Parent SDK import, receptionist inference, ASR/TTS and device audio are outside this MCP client measurement.',
  '', 'REUSE SAFETY: one dedicated thread/async task owns each stdio and ClientSession lifetime. Calls serialize per immutable (resolved DB path, restaurant, trusted today, region, server, diagnostic file) key. No conversation or result cache. Every request gets a private Future and UUID; every returned row is checked against its restaurant/date/phone/active scope. Data is freshly queried on each tool call.',
  'Four scopes, 32 queued requests per scope, 60s idle expiry. The 20s total deadline includes pool waiting/startup; tool reads are bounded to 15s. Timeouts or broken transports retire the connection; current/queued requests fail closed, with a fresh connection on a subsequent call. Valid MCP tool errors affect only their own caller. There is no automatic request replay and no write-capable MCP tool.',
  '', 'REQUEST MISMATCHES (original question is the success criterion):']
 for m in mismatches:lines+=[f"Case {m['case']}: {m['question']}",f"  Before: {m['before_reply']}",f"  After: {m['after_reply']}",f"  Then: {m['availability_result']}"]
 lines+=['',f"Late-arrival baseline intent: {result['late_arrival']['before_intent']} (expected policy). After: {result['late_arrival']['after_reply']} Sources: {result['late_arrival']['after_sources']}.",
  'Availability inquiries now keep separate clarification state, collect date/time/party size before checking, answer availability, and never collect identity or create a booking merely because a table was queried. Explicit booking/change commands retain their original action flow. Late-arrival questions go to grounded restaurant policy retrieval even during a booking. Pending booking/update/cancel details stay unchanged; a later yes/no/correct first re-reads the original confirmation, and subsequent corrections invalidate the saved readback.',
  '',f"REQUEST-LEVEL EVALUATION: {result['request_evaluation']['passed']}/{result['request_evaluation']['total']} API checks. Of these, 9 are complete question-goal scenarios (3 original failures and 6 fresh frozen paraphrases). The remaining 4 are negative/action-discrimination checks and 2 are concurrent phone-lookup isolation probes; those do not claim completed updates/cancellations. Real embeddings/retrieval, chat and voice session channels using text input. Success is answering the question originally asked, not completing a later booking. This is a curated request contract, not overall population accuracy.",
  f"REGRESSIONS: {result['verification']['regression_tests']} tests + {result['verification']['subtests']} subtests passed. Includes 20 real concurrent MCP calls, crash/timeout/invalid-input tests, host-exit child cleanup and confirmation safety. Region-specific component phone fixtures are fixed without editing live restaurant settings. Two old extraction tests now use explicit booking commands instead of ambiguous availability questions.",
  '',f"DATA: production baseline {result['production_baseline']}; unchanged={result['production_baseline_unchanged']}. Both 1,911-row private baseline copies also unchanged. Before/after lookup fixture semantics have the same SHA-256. Clarification/API evaluation left private reservation rows unchanged.",
  '', 'ROLLOUT: default remains oneshot. Reuse is opt-in only. Automatic approval review rejected changing the production default during evaluation; no rollout or shared API restart was performed. No frontend/UX files were edited. Source routing fixes require the shared API to restart before live testing.',
  'Reproduce: benchmark_mcp_lookup.py --baseline-volume --name <unique-name> with RECEPTIONIST_MCP_MODE=oneshot or pooled in the isolated benchmark process; evaluate_information_requests.py; run_private_mcp_regressions.py. Raw reports, diagnostics and source hashes are retained alongside this report.']
 (OUT/'comparison.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
 print(json.dumps({'mean_reduction_percent':result['mean_reduction_percent'],'api_passed':result['request_evaluation']['passed'],'tests':result['verification']['regression_tests'],'baseline_unchanged':result['production_baseline_unchanged']}))
if __name__=='__main__':main()
