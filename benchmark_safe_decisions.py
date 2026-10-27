"""Paired replay for accepted rules; unchanged model samples are reused to avoid cache bias."""
import json,time,hashlib
from pathlib import Path
from benchmark_unified_latency import workflow_fields,score,stats,FIELDS,baseline
ROOT=Path(__file__).parent;OUT=ROOT/'results/decision-latency'
def main():
 cases={c['name']:c for c in json.loads((OUT/'corpus.json').read_text())}
 measured=json.loads((OUT/'paired-final.json').read_text())
 report={'method':'Paired extraction-stage replay: identical input/state. Actual gate timings; same measured baseline generation reused when request is unchanged. After latency is replay-derived, not a separate live API run. No prompt/schema/model changes.','baseline_before':baseline(),'runs':[]}
 for row in [r for r in measured['runs'] if r['variant']=='baseline']:
  case=cases[row['case']]
  for variant in ['baseline','safe']:
   started=time.perf_counter();gate=workflow_fields(case,'compact' if variant=='safe' else 'baseline');gate_ms=(time.perf_counter()-started)*1000
   if gate is None:
    value=row['normalized'];elapsed=row['elapsed_ms']+gate_ms;tokens={k:row[k] for k in ['input_tokens','output_tokens']}
   else:
    value={'relevance':'relevant','intent':'reservation','fields':{**dict.fromkeys(FIELDS),**gate}};elapsed=gate_ms;tokens={'input_tokens':0,'output_tokens':0}
   errors=score(case,value)
   report['runs'].append({'case':row['case'],'repeat':row['repeat'],'variant':variant,'generation_calls':int(gate is None),'elapsed_ms':round(elapsed,3),'gate_ms':round(gate_ms,3),'generation_reused':gate is None,'normalized':value,'correct':not errors,'errors':errors,**tokens})
 for variant in ['baseline','safe']:
  rows=[r for r in report['runs'] if r['variant']==variant];gens=[r for r in rows if r['generation_calls']]
  report[variant]={'total':len(rows),'correct':sum(r['correct'] for r in rows),'generation_calls':len(gens),'elapsed_ms':stats([r['elapsed_ms'] for r in rows]),'input_tokens_per_generation':stats([r['input_tokens'] for r in gens]),'output_tokens_per_generation':stats([r['output_tokens'] for r in gens]),'total_input_tokens':sum(r['input_tokens'] for r in rows),'total_output_tokens':sum(r['output_tokens'] for r in rows)}
 by={(r['case'],r['repeat'],r['variant']):r for r in report['runs']}
 report['regressions']=[r['case'] for r in report['runs'] if r['variant']=='safe' and not r['correct'] and by[r['case'],r['repeat'],'baseline']['correct']]
 report['baseline_after']=baseline();report['baseline_unchanged']=report['baseline_before']==report['baseline_after']
 report['provenance']={f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in ['decision_engine.py','decision_fastpaths.py']}
 (OUT/'safe-replay.json').write_text(json.dumps(report,indent=2))
 print(json.dumps({k:report[k] for k in ['baseline','safe','regressions','baseline_unchanged']},indent=2))
 assert not report['regressions'] and report['baseline_unchanged']
if __name__=='__main__':main()
