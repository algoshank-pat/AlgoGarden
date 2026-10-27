"""Summarize paired decision benchmarks and acceptance-suite evidence."""
import argparse,json,statistics
from pathlib import Path

def main():
 p=argparse.ArgumentParser();p.add_argument('before_suite');p.add_argument('after_suite');args=p.parse_args()
 root=Path(__file__).resolve().parent;out=root/'results/decision-engine'
 before=json.loads((out/'before.json').read_text());after=json.loads((out/'after.json').read_text())
 cases=[]
 for name in dict.fromkeys(r['case'] for r in before['runs']):
  a=[r for r in before['runs'] if r['case']==name];b=[r for r in after['runs'] if r['case']==name]
  am=statistics.median(r['elapsed_ms'] for r in a);bm=statistics.median(r['elapsed_ms'] for r in b)
  cases.append({'comparison_note':('Old fast path dropped additional requested fields; not equivalent correctness.' if name=='booking_correction' else 'Same input and initial workflow state.'),'case':name,'before_median_ms':round(am,1),'after_median_ms':round(bm,1),'change_percent':round((bm-am)*100/am,1),
                'before_llm_calls':sorted({r['llm_calls'] for r in a}),'after_llm_calls':sorted({r['llm_calls'] for r in b})})
 old=json.loads((root/args.before_suite/'summary.json').read_text());new=json.loads((root/args.after_suite/'summary.json').read_text())
 ids=set()
 def collect(x):
  if isinstance(x,dict):
   for key,value in x.items():
    if isinstance(value,str) and (key=='session_id' or key.endswith('_session')):ids.add(value)
    else:collect(value)
  elif isinstance(x,list):
   for item in x:collect(item)
 for item in new['scenarios']:
  if item.get('report'):collect(json.loads(Path(item['report']).read_text(encoding='utf-8')))
 rows=[json.loads(x) for x in (root/'calls.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
 rows=[r for r in rows if r['session_id'] in ids]
 counts=[sum(c.get('component')=='ollama.chat' for c in r['result']['structured']['calls']) for r in rows]
 report={'benchmark':'same warmed local model; 3 repetitions per case; no audio or writes in decision microbenchmark',
         'before_suite':args.before_suite,'after_suite':args.after_suite,'before_suite_status':old['status'],'after_suite_status':new['status'],'after_verification_strategy':new.get('verification_strategy','complete suite run'),
         'before_suite_note':'Hands-free baseline had a stale assertion for the former greeting; final suite uses the requested greeting.',
         'cases':cases,'before_mean_ms':before['mean_ms'],'after_mean_ms':after['mean_ms'],
         'mean_change_percent':round((after['mean_ms']-before['mean_ms'])*100/before['mean_ms'],1),
         'validation_note':'A field-domain validation guard was tightened after measurement; model, JSON schema and prompt stayed unchanged.',
         'after_voice_turns_audited':len(counts),'after_max_llm_calls_per_turn':max(counts,default=0),
         'after_baseline_unchanged':new.get('baseline_unchanged'),
         'suite_scenarios':new['scenarios'],
         'limitations':['Single laptop, sequential measurements, small sample; not a throughput benchmark.','Decision timings exclude VAD, ASR, audio synthesis/playback and network queues.','Physical microphone, speakers and echo cancellation not tested.']}
 assert len(counts)>0 and report['after_max_llm_calls_per_turn']<=1
 (out/'comparison.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
 print(json.dumps({k:v for k,v in report.items() if k not in {'suite_scenarios'}},indent=2))

if __name__=='__main__':main()
