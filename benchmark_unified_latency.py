"""Paired, gold-scored local generation benchmark; no business writes or shared API."""
import argparse,copy,json,time,statistics,math,sqlite3,hashlib,os
from types import SimpleNamespace
from pathlib import Path
from datetime import date
import ollama
from decision_protocol import compile_request,normalize,FIELDS
from structured_io import validate_llm_response
from receptionist import parse_date,parse_time

ROOT=Path(__file__).parent
OUT=ROOT/'results/decision-latency'

def workflow_fields(case,variant):
 from decision_engine import rule_fields
 from receptionist import load_restaurant
 ctx=json.loads(case['request']['messages'][-1]['content'])['context']
 bot=SimpleNamespace(reserving=ctx.get('creating',False),awaiting_confirmation=ctx.get('confirming',False),
  _awaiting_slot=ctx.get('expected_field'),restaurant=load_restaurant(ROOT/'restaurants/demo_restaurant.yaml'),
  _offered_alternatives=[],reservation_changes=SimpleNamespace(action=ctx.get('change_action'),
   active=bool(ctx.get('change_action')),selection_offered=ctx.get('selection_offered',False),offered_alternatives=[]))
 previous=os.environ.get('RECEPTIONIST_DECISION_FASTPATHS')
 os.environ['RECEPTIONIST_DECISION_FASTPATHS']='safe' if variant=='compact' else 'baseline'
 try:return rule_fields(bot,json.loads(case['request']['messages'][-1]['content'])['caller'])
 finally:
  if previous is None:os.environ.pop('RECEPTIONIST_DECISION_FASTPATHS',None)
  else:os.environ['RECEPTIONIST_DECISION_FASTPATHS']=previous

def baseline():
 with sqlite3.connect(f'file:{ROOT / "reservations.sqlite3"}?mode=ro',uri=True) as c:
  c.row_factory=sqlite3.Row
  rows=[dict(r) for r in c.execute("SELECT * FROM reservations WHERE request_key LIKE 'baseline:2026-q4:v1:%' ORDER BY id")]
 return {'count':len(rows),'sha256':hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()}

def canonical(key,value):
 if value is None:return None
 if key=='date':
  d=parse_date(value,today=date(2026,10,28));return d.isoformat() if d else value.lower().strip()
 if key=='time':return parse_time(value) or value.lower().strip()
 if key=='phone':
  from customer_identity import phone_from_utterance,normalize_phone
  return normalize_phone(phone_from_utterance(value,'IN') or value,'IN') or value
 if isinstance(value,str):return value.lower().strip().rstrip('.!?')
 return value

def score(case,value):
 gold=case['gold'];errors=[]
 if value['relevance']!=gold['relevance']:errors.append('relevance')
 if gold['relevance']=='relevant' and value['intent']!=gold['intent']:errors.append('intent')
 for k in FIELDS:
  if canonical(k,value['fields'].get(k))!=canonical(k,gold['fields'].get(k)):errors.append(k)
 return errors

def stats(values):
 values=sorted(values)
 return {'n':len(values),'average':round(statistics.mean(values),3),'p50':round(statistics.median(values),3),'p95':round(values[max(0,math.ceil(.95*len(values))-1)],3)} if values else {}

def main():
 p=argparse.ArgumentParser();p.add_argument('--workflow-rules',action='store_true');p.add_argument('--pilot',action='store_true');p.add_argument('--corpus',default='corpus.json');p.add_argument('--only',choices=['baseline','compact']);p.add_argument('--repeats',type=int,default=1);p.add_argument('--output',default='pilot.json');args=p.parse_args()
 cases=json.loads((OUT/args.corpus).read_text())
 if args.pilot:cases=[c for c in cases if c['name'] in {'booking_nov6','correct_multiple','outside_update','choice','menu_topic_change','irrelevant'}]
 report={'method':'balanced paired order; identical caller and workflow; required-field gold scoring; no ASR/TTS or writes','baseline_before':baseline(),'runs':[]}
 report['workflow_rules']=args.workflow_rules
 client=ollama.Client(timeout=180)
 report['warmup']=[]
 for variant in ['baseline','compact']:
  warm=copy.deepcopy(cases[0]['request'])
  if variant=='compact':warm=compile_request(warm)
  response=client.chat(**warm)
  report['warmup'].append({'variant':variant,'model_ms':response.total_duration/1e6,'input_tokens':response.prompt_eval_count,'output_tokens':response.eval_count})
 report['corpus_sha256']=hashlib.sha256((OUT/args.corpus).read_bytes()).hexdigest()
 report['protocol_sha256']=hashlib.sha256((ROOT/'decision_protocol.py').read_bytes()).hexdigest()
 report['evidence_sha256']=hashlib.sha256((ROOT/'decision_evidence.py').read_bytes()).hexdigest()
 report['fastpaths_sha256']=hashlib.sha256((ROOT/'decision_fastpaths.py').read_bytes()).hexdigest()
 for repeat in range(args.repeats):
  for index,case in enumerate(cases):
   for variant in (['baseline','compact'] if (index+repeat)%2==0 else ['compact','baseline']):
    if args.only and variant!=args.only:continue
    req=copy.deepcopy(case['request'])
    if variant=='compact':req=compile_request(req)
    started=time.perf_counter()
    gate=None
    if args.workflow_rules:
     gate=workflow_fields(case,variant)
    elif variant=='compact':
     from decision_fastpaths import fields as fast_fields
     from receptionist import load_restaurant
     ctx=json.loads(case['request']['messages'][-1]['content'])['context']
     bot=SimpleNamespace(reserving=ctx.get('creating',False),_awaiting_slot=ctx.get('expected_field'),restaurant=load_restaurant(ROOT/'restaurants/demo_restaurant.yaml'),reservation_changes=SimpleNamespace(action=ctx.get('change_action'),active=bool(ctx.get('change_action')),selection_offered=ctx.get('selection_offered',False)))
     gate=fast_fields(bot,json.loads(req['messages'][-1]['content'])['caller'])
    if gate is not None:
     value={'relevance':'relevant','intent':'reservation','confidence':1.0,'reason_summary':'Expected-field rule.','fields':{**dict.fromkeys(FIELDS),**gate}}
     errors=score(case,value)
     row={'case':case['name'],'repeat':repeat,'variant':variant,'generation_calls':0,'elapsed_ms':round((time.perf_counter()-started)*1000,3),'input_tokens':0,'output_tokens':0,'raw':None,'normalized':value,'errors':errors,'correct':not errors}
     report['runs'].append(row);(OUT/args.output).write_text(json.dumps(report,indent=2),encoding='utf-8')
     print(case['name'],variant,row['elapsed_ms'],'RULE','PASS' if row['correct'] else errors,flush=True)
     continue
    try:
     response=client.chat(**req);elapsed=(time.perf_counter()-started)*1000
     raw=json.loads(response.message.content)
     validated=validate_llm_response(response,req)
     value=json.loads(validated.message.content)
     if variant=='compact':value=normalize(value,req)
     errors=score(case,value)
     row={'case':case['name'],'repeat':repeat,'variant':variant,'elapsed_ms':round(elapsed,3),'input_tokens':response.prompt_eval_count,'output_tokens':response.eval_count,
          'prompt_ms':response.prompt_eval_duration/1e6,'generation_ms':response.eval_duration/1e6,'model_ms':response.total_duration/1e6,
          'raw':raw,'raw_errors':score(case,normalize(raw,req,verify=False) if variant=='compact' else raw),'generation_calls':1,'normalized':value,'errors':errors,'correct':not errors,'done_reason':response.done_reason}
    except Exception as e:row={'case':case['name'],'repeat':repeat,'variant':variant,'correct':False,'errors':[str(e)]}
    report['runs'].append(row)
    (OUT/args.output).write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(case['name'],variant,row.get('elapsed_ms'),row.get('input_tokens'),row.get('output_tokens'),'PASS' if row['correct'] else row['errors'],flush=True)
 for variant in ['baseline','compact']:
  rows=[r for r in report['runs'] if r['variant']==variant]
  if not rows:continue
  report[variant]={'accuracy':sum(r['correct'] for r in rows)/len(rows),'correct':sum(r['correct'] for r in rows),'total':len(rows),'generation_calls':sum(r.get('generation_calls',1) for r in rows),'raw_accuracy_on_generations':sum(not r.get('raw_errors',r['errors']) for r in rows if r.get('generation_calls',1))/max(1,sum(r.get('generation_calls',1) for r in rows)),
    'generation_elapsed_ms':stats([r['elapsed_ms'] for r in rows if r.get('generation_calls',1)]),
    **{k:stats([r[k] for r in rows if k in r and (k=='elapsed_ms' or r.get('generation_calls',1))]) for k in ['elapsed_ms','input_tokens','output_tokens','prompt_ms','generation_ms','model_ms']}}
 report['baseline_after']=baseline();report['baseline_unchanged']=report['baseline_before']==report['baseline_after'];assert report['baseline_unchanged']
 (OUT/args.output).write_text(json.dumps(report,indent=2),encoding='utf-8')
 print(json.dumps({k:report[k] for k in ['baseline','compact','baseline_unchanged'] if k in report},indent=2),flush=True)

if __name__=='__main__':main()
