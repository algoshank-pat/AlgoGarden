"""Audit and render the customer conversation test, without replaying operations."""
import json,sys,re,math,hashlib
from pathlib import Path
from collections import Counter,defaultdict
ROOT=Path(__file__).parent
def quantile(v,q):
 v=sorted(v);return round(v[max(0,math.ceil(len(v)*q)-1)],2) if v else None
def main():
 out=Path(sys.argv[1]);r=json.loads((out/'report.json').read_text(encoding='utf-8'));rows=r['results']
 assert len({x['case']['id'] for x in rows})==len(rows)
 audits=[];invalid=[]
 for n,line in enumerate((out/'calls.jsonl').read_text(encoding='utf-8').splitlines(),1):
  try:audits.append(json.loads(line))
  except ValueError:invalid.append(n)
 confirmations=[];false_complete=[]
 for c in rows:
  for turn in c['turns']:
   tools=turn['response'].get('structured',{}).get('tools_called',[])
   if any(t in tools for t in ['book_table','change_reservation']):
    confirmation=re.sub(r'[^a-z ]','',turn['caller'].lower()).strip()
    confirmations.append({'case':c['case']['id'],'confirmed':confirmation in {'yes','yes please','correct','confirm'},'caller':turn['caller'],'tools':tools})
  if c['outcome']=='wrong_outcome':false_complete.append({'case':c['case']['id'],'reasons':c['wrong_reasons']})
 counts=Counter(c['outcome'] for c in rows)
 by_kind={kind:dict(Counter(c['outcome'] for c in rows if c['case']['kind']==kind)) for kind in dict.fromkeys(c['case']['kind'] for c in rows)}
 by_channel={channel:dict(Counter(c['outcome'] for c in rows if c['case']['channel']==channel)) for channel in ['chat','voice']}
 llms=[sum(e['name']=='ollama.request_prepared' for e in a['events']) for a in audits]
 timings=[t['elapsed_ms'] for c in rows for t in c['turns']]
 summary={'completed':len(rows),'counts':dict(counts),'percentages':{k:round(100*counts[k]/len(rows),1) for k in ['success_first_time','success_after_repetition','safe_failure','wrong_outcome','harness_error']},'by_kind':by_kind,'by_channel':by_channel,'api_turns':len(timings),'turn_latency_ms':{'p50':quantile(timings,.5),'p95':quantile(timings,.95)},'conversation_duration_seconds':{'p50':quantile([c['duration_seconds'] for c in rows],.5),'p95':quantile([c['duration_seconds'] for c in rows],.95)},'audit_turns':len(audits),'audit_invalid_lines':invalid,'model_generations':sum(llms),'max_generations_per_turn':max(llms,default=0),'write_confirmation_checks':confirmations,'all_writes_have_confirmation':all(c['confirmed'] for c in confirmations),'wrong_outcomes':false_complete,'private_databases_removed':all(c.get('private_database_removed') for c in rows),'source_hashes_before':r['source_hashes_before'],'baseline_before':r['baseline_before'],'baseline_after':r.get('baseline_after',r.get('baseline_current')),'baseline_unchanged':r['baseline_before']==r.get('baseline_after',r.get('baseline_current')),'source_files_unchanged':r.get('production_sources_unchanged'),'limitations':['Text/API evaluation only. Voice-labelled sessions exercise voice conversation logic with text, not ASR/TTS or microphones.','100 authored cases, fixed mix; not 100 independent real customers or representative production traffic.','Adaptive caller has the intended answers; at most two repairs and 12 total turns. Only declared caller actions are used, not internal bot state.','Safe failures include incomplete tasks, wrong routing and unsupported answers where no wrong facts or writes were observed.','Requested staff/order handoff counts as correct task completion; a real telephone connection is not tested.','Scripted task oracles and all exceptions require human audit; no generative model judges its own answers.','One pilot run was stopped after a punctuation error in the test scorer; excluded from these 100.']}
 summary['limitations'].append('Booking targets were selected as available in the copied baseline; capacity stress, invalid dates, multiple-match ambiguity, device audio and concurrent load are not scored in these 100.')
 (out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
 lines=['ALGO BITES — 100 CUSTOMER CONVERSATIONS','Testing only; actual model/API/tools, private reservation copies.','',f'Completed: {len(rows)}/100','']
 for key in ['success_first_time','success_after_repetition','safe_failure','wrong_outcome','harness_error']:lines.append(f'{key.replace("_"," ").capitalize()}: {counts[key]}/{len(rows)} = {summary["percentages"][key]}%')
 lines+=['','First time means no avoidable repetition/rephrasing of valid information.','Planned corrections and ordinary follow-up questions do not count as repetition.','Safe failure means the goal was not achieved, but no wrong fact, false completion','or unauthorized/incorrect reservation change was detected. It is still a failed task.','','BREAKDOWN BY CUSTOMER GOAL']
 for kind,values in by_kind.items():lines.append(f'{kind}: {values}')
 lines+=['','INDIVIDUAL CONVERSATIONS']
 for c in rows:
  lines.append(f"{c['case']['id']:03d} {c['case']['kind']} ({c['case']['channel']} text path): {c['outcome']}; repairs={c['repairs']}; {c['duration_seconds']}s")
  if c['wrong_reasons']:lines.append('  Problem: '+'; '.join(c['wrong_reasons']))
  if c.get('harness_error'):lines.append('  Harness error: '+c['harness_error'])
  lines.append('  Customer: '+c['turns'][1]['caller'] if len(c['turns'])>1 else '  No customer request recorded')
  lines.append('  Final reply: '+c['turns'][-1]['response'].get('reply',''))
 lines+=['','LIMITATIONS']+['- '+s for s in summary['limitations']]
 lines+=['','BASELINE',json.dumps(summary['baseline_before']),f'Before/after hash equal: {summary["baseline_unchanged"]}',f'All private scenario databases removed: {summary["private_databases_removed"]}','',f'API turn latency: p50 {summary["turn_latency_ms"]["p50"]}ms; p95 {summary["turn_latency_ms"]["p95"]}ms. Excludes speech/audio.',f'Model generations: {summary["model_generations"]}; max per turn {summary["max_generations_per_turn"]}.','',r'Reproduce: .venv\Scripts\python.exe evaluate_customer100.py --run-dir results/customer100/NEW-RUN',r'Render/audit: .venv\Scripts\python.exe report_customer100.py results/customer100/NEW-RUN']
 (out/'human-report.txt').write_text('\n'.join(lines),encoding='utf-8')
 transcript=[]
 for c in rows:
  transcript += [f"CONVERSATION {c['case']['id']:03d} — {c['case']['kind']} — {c['outcome']}",f"Goal: {json.dumps(c['goal'],ensure_ascii=False)}"]
  for turn in c['turns']:transcript += ['Customer: '+turn['caller'],'Receptionist: '+turn['response'].get('reply','')]
  transcript += ['Final customer reservation rows: '+json.dumps(c.get('final_customer_rows'),ensure_ascii=False),'']
 (out/'transcripts.txt').write_text('\n'.join(transcript),encoding='utf-8')
 print(json.dumps({k:summary[k] for k in ['completed','counts','by_kind','all_writes_have_confirmation','baseline_unchanged']},indent=2))
if __name__=='__main__':main()
