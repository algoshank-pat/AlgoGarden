"""Read-only aggregation of immutable suite evidence and current baseline."""
import json, math, hashlib, sqlite3
from pathlib import Path
from customer_identity import phone_from_utterance, normalize_phone
from evaluate_readiness import baseline
ROOT=Path(__file__).parent
OUT=ROOT/'results/readiness-evaluation'
def load(path):return json.loads((ROOT/path).read_text(encoding='utf-8'))
def stats(values):
 v=sorted(x for x in values if isinstance(x,(float,int)))
 return {'n':len(v),'p50':v[(len(v)-1)//2] if v else None,'p95_nearest_rank':v[math.ceil(.95*len(v))-1] if v else None,'max':max(v) if v else None}
def main():
 parent=load('results/voice-suite/20261027T195107-76548163/summary.json')
 verified=load('results/voice-suite/20261027T200224-verify-54ead9/summary.json')
 reports=[];turns=[];phones=[];cleanup=[]
 for scenario in verified['scenarios']:
  if not scenario.get('report'):continue
  path=Path(scenario['report']);report=json.loads(path.read_text(encoding='utf-8'));reports.append(report)
  for index,t in enumerate(report.get('turns',[])):
   if isinstance(t,dict):
    turns.append(t)
    expected=phone_from_utterance(t.get('spoken_text',''))
    if expected and len(''.join(c for c in t.get('spoken_text','') if c.isdigit()))<8:
     actual=phone_from_utterance(t.get('transcript',''))
     phones.append({'report':str(path.relative_to(ROOT)),'turn':index+1,'expected':normalize_phone(expected),'actual':normalize_phone(actual) if actual else None,'exact':normalize_phone(expected)==(normalize_phone(actual) if actual else None)})
  c=report.get('cleanup',[]);cleanup.extend(c if isinstance(c,list) else [c])
 with sqlite3.connect(f'file:{ROOT / "reservations.sqlite3"}?mode=ro',uri=True) as db:
  db.row_factory=sqlite3.Row
  rows=[dict(r) for r in db.execute("SELECT * FROM reservations WHERE request_key LIKE 'baseline:2026-q4:v1:%' ORDER BY date,time,ts")]
  legacy_digest=hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()
  cleanup_check=[{'id':c['id'],'status':(db.execute('SELECT status FROM reservations WHERE id=?',(c['id'],)).fetchone() or ['missing'])[0]} for c in cleanup if c.get('id')]
 sessions={t.get('structured',{}).get('session_id') for t in turns}-{None}
 def collect_sessions(obj):
  if isinstance(obj,dict):
   for k,v in obj.items():
    if k in {'session_id','update_session','cancel_session'} and isinstance(v,str):sessions.add(v)
    collect_sessions(v)
  elif isinstance(obj,list):
   for v in obj:collect_sessions(v)
 for report in reports:collect_sessions(report)
 audit=[];bad=0
 for line in (ROOT/'calls.jsonl').read_text(encoding='utf-8').splitlines():
  try:
   r=json.loads(line)
   if r.get('session_id') in sessions:audit.append(r)
  except ValueError:bad+=1
 metrics=[]
 for line in (ROOT/'voice_metrics.jsonl').read_text(encoding='utf-8').splitlines():
  try:metrics.append(json.loads(line))
  except ValueError:bad+=1
 result={'quantile_method':'nearest rank p95; lower middle p50','baseline':baseline(),'baseline_legacy_order_sha256':legacy_digest,'baseline_matches_archived':legacy_digest==parent['baseline_before']['sha256'],'original_scenario_passes':sum(s['status']=='passed' for s in parent['scenarios']),'original_scenario_total':len(parent['scenarios']),'verified_scenario_passes':sum(s['status']=='passed' for s in verified['scenarios']),'verified_strategy':'13 original passes plus targeted rerun, not uninterrupted run','synthetic_turn_count':len(turns),'synthetic_turn_latency':{k:stats([t.get(k) for t in turns]) for k in ['stt_ms','send_to_audio_ready_ms']},'spoken_phone_turns':phones,'phone_exact_turn_accuracy':{'correct':sum(p['exact'] for p in phones),'total':len(phones),'note':'Includes repeat attempts; not first-attempt accuracy; only digit-word inputs that parse as a phone in source text.'},'cleanup_status':cleanup_check,'audit_records_for_selected_sessions':len(audit),'audit_schema_example_keys':list(audit[0]) if audit else [],'invalid_jsonl_lines':bad,'voice_metrics':{'rows':len(metrics),'selected_suite_sessions':sum(m.get('session_id') in sessions for m in metrics),'warning':'Mixed historical runner records; not attributable to current unified suite without session match. Estimated end-to-audio is a sum, not a physical device timestamp.','all_historical':{k:stats([m.get(k) for m in metrics]) for k in ['endpoint_ms','stt_ms','queue_wait_ms','llm_ms','tts_first_chunk_ms','estimated_end_of_speech_to_audio_ms']}},'comparison':load('results/decision-engine/comparison.json')['cases'],'rag':{k:load('results/rag-latest.json').get(k) for k in ['at','retrieval_recall_at_4','negative_abstention','positive_cases','negative_cases']}}
 result['audit_stages']={key:stats([r['result'].get(key) for r in audit]) for key in ['latency_ms','queue_wait_ms']}
 result['audit_stages']['llm_generation_ms']=stats([e.get('duration_ms') for r in audit for e in r['events'] if e['name']=='ollama.chat.completed'])
 tool_names={'check_table','recognize_customer','lookup_reservations_by_phone','lookup_reservation','book_table','change_reservation','get_hours','get_location','get_menu','check_dish','search_knowledge','transfer_to_human'}
 result['audit_stages']['tool_spans_ms']=stats([e.get('duration_ms') for r in audit for e in r['events'] if e['name'] in {name+'.completed' for name in tool_names}])
 result['audit_llm_calls']={'turns':len(audit),'max_per_turn':max([sum(e['name']=='ollama.request_prepared' for e in r['events']) for r in audit],default=0),'zero_call_turns':sum(not any(e['name']=='ollama.request_prepared' for e in r['events']) for r in audit),'generations':sum(e['name']=='ollama.request_prepared' for r in audit for e in r['events'])}
 result['audit_model_load_ms']=stats([e['details']['output'].get('load_duration',0)/1e6 for r in audit for e in r['events'] if e['name']=='ollama.response_received'])
 (OUT/'evidence.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
 print(json.dumps({k:v for k,v in result.items() if k not in ['spoken_phone_turns','comparison','cleanup_status']},indent=2))
if __name__=='__main__':main()
