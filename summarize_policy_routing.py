"""Summarize paired policy checks without treating route-only controls as request accuracy."""
import json,math,hashlib
from pathlib import Path
from statistics import mean,median
from run_private_decision_acceptance import production_baseline
ROOT=Path(__file__).parent
OUT=ROOT/'results/policy-routing'
def metrics(rows):
 v=sorted(r['latency_ms'] for r in rows)
 return {'passed':sum(r['passed'] for r in rows),'total':len(rows),'average_ms':round(mean(v),2),'p50_ms':round(median(v),2),'p95_ms':v[math.ceil(.95*len(v))-1],'search_knowledge_calls':sum(r['result']['structured']['tools_called'].count('search_knowledge') for r in rows),'generation_calls':sum(r['generations'] for r in rows),'max_generations_per_turn':max(r['generations'] for r in rows)}

def main():
 a=json.loads((OUT/'before.json').read_text(encoding='utf-8'));b=json.loads((OUT/'after.json').read_text(encoding='utf-8'));w=json.loads((OUT/'workflow-validation.json').read_text(encoding='utf-8'))
 assert len(a['cases'])==len(b['cases'])==48
 assert [x['case'] for x in a['cases']]==[x['case'] for x in b['cases']]
 report={'model':b['model'],'paired_inputs_and_states_identical':True,'method':b['method'],'limitations':['Voice-text API tests; no microphone, ASR or TTS included.','40 policy requests are scored against answer/source/conditions, unchanged draft and database count, and one-generation cap. Eight controls score booking/menu routing only, not final request fulfillment.','Cold first retrieval is included. Local sequential client latency includes API, actual RAG embedding and, when needed, actual default-model generation.','Known clear policy topics bypass text generation. Unfamiliar questions still use the actual model backstop and can remain substantially slower.','No frontend files were edited for this task. The shared API was not restarted.'],'groups':{},'workflow_checks':{'passed':sum(c['passed'] for c in w['checks']),'total':len(w['checks']),'all_passed':w['passed']},'baseline_before':a['baseline_before'],'baseline_after':production_baseline(),'source_hashes_after':{f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in ['receptionist.py','routing.py','decision_engine.py','chat_api.py','information_requests.py','policy_requests.py','knowledge.py']},'tests':{'broad_regressions':'546 passed; 11 subtests passed (before the final unfamiliar-gate fallback)','after_final_gate_change':'197 API/decision/policy tests passed; final real-model workflow checks passed'}}
 for name,select in [('policy_requests',lambda r:r['case']['kind']!='contrast'),('recovery',lambda r:r['case']['kind']=='recovery'),('fresh_paraphrases',lambda r:r['case']['kind']=='fresh'),('original_chat_voice',lambda r:r['case']['kind']=='policy'),('routing_controls_only',lambda r:r['case']['kind']=='contrast'),('all_checks_mixed',lambda r:True)]:
  report['groups'][name]={'before':metrics([r for r in a['cases'] if select(r)]),'after':metrics([r for r in b['cases'] if select(r)])}
 unknown=[t for t in w['turns'] if t['caller']=='Do you offer gift vouchers?']
 report['unfamiliar_model_backstop']=[{'latency_ms':t['latency_ms'],'intent':t['result']['routing']['intent'],'tools':t['result']['structured']['tools_called'],'generations':sum(c['component']=='ollama.chat' for c in t['result']['structured']['calls']),'grounded_sources':t['result']['sources']} for t in unknown]
 frozen=ROOT/'results/customer100/frozen-20261027T214340Z';fr=json.loads((frozen/'run/report.json').read_text(encoding='utf-8'));cases=[r for r in fr['results'] if 86<=r['case']['id']<=95]
 ids={t['response']['request_id'] for r in cases for t in r['turns']}
 audit=[json.loads(l) for l in (frozen/'run/calls.jsonl').read_text(encoding='utf-8').splitlines()]
 report['frozen_evidence']={'successful_policy_cases':sum(bool(r['success']) for r in cases),'total_policy_cases':len(cases),'search_knowledge_started_events':sum(e['name']=='tool.search_knowledge.started' for row in audit if row['request_id'] in ids for e in row['events']),'input_hashes':{str(p.relative_to(frozen)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [frozen/'run/report.json',frozen/'run/calls.jsonl',frozen/'scoring-rules.json']}}
 assert report['baseline_before']==report['baseline_after'];assert w['passed']
 (OUT/'comparison.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
 lines=['Policy routing: current application sources; actual default model '+b['model'],'','Paired requests and workflow states: identical. Before and after reports retain individual replies, routing decisions, sources, tools, generation counts and latency.','']
 for name,g in report['groups'].items():
  x,y=g['before'],g['after'];lines.append(f"{name}: {x['passed']}/{x['total']} -> {y['passed']}/{y['total']}; mean {x['average_ms']/1000:.3f}s -> {y['average_ms']/1000:.3f}s; p50 {x['p50_ms']/1000:.3f}s -> {y['p50_ms']/1000:.3f}s; p95 {x['p95_ms']/1000:.3f}s -> {y['p95_ms']/1000:.3f}s; search_knowledge {x['search_knowledge_calls']} -> {y['search_knowledge_calls']}")
 lines+=['','Grounding: complete exact source sentences only; conditions and negations retained; existing similarity/staleness/scoping guards unchanged.','No policy facts are stored in the intent detector. Vocabulary detects information topics and expands retrieval queries; answers come from scoped RAG sources.','Booking drafts survive policy answers. A yes after an information interruption repeats the original readback; another explicit confirmation is needed for a write. Private full workflows verify before/after row equality during policy handling and explicit single-write confirmation.','Different policy corpora returned different cake and late-arrival policies in interleaved sessions.','Unfamiliar policy questions were tested through both normal dispatch and the pending voice gate with the real default model, one generation, retrieval and safe abstention on unsupported facts.','',f"Workflow checks: {report['workflow_checks']}",f"Baseline unchanged: {report['baseline_after']}",'','Limits:']+report['limitations']
 (OUT/'comparison.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8');print(json.dumps(report['groups']['policy_requests']));print(report['workflow_checks'])
if __name__=='__main__':main()
