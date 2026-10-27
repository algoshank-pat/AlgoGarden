"""Audit saved model decisions through current workflow gates, without new inference."""
import json,time,hashlib
from pathlib import Path
from benchmark_unified_latency import workflow_fields,score,FIELDS
ROOT=Path(__file__).parent
OUT=ROOT/'results/decision-latency'

def main():
 import argparse
 parser=argparse.ArgumentParser();parser.add_argument('--corpus',default='heldout.json');parser.add_argument('--saved',default='heldout-results.json');parser.add_argument('--output',default='workflow-replay.json');args=parser.parse_args()
 cases={c['name']:c for c in json.loads((OUT/args.corpus).read_text())}
 saved=json.loads((OUT/args.saved).read_text())
 report={'method':'Accuracy-only replay of frozen model outputs through public scope guard and extraction gates; not fresh inference or a latency benchmark. The discovered date correction is now a regression, not blind held-out evidence.','source':args.saved,'runs':[]}
 for row in saved['runs']:
  case=cases[row['case']]; gate=workflow_fields(case,row['variant'])
  from routing import irrelevant_question
  from types import SimpleNamespace
  text=json.loads(case['request']['messages'][-1]['content'])['caller']
  blocked=irrelevant_question(SimpleNamespace(unified_decisions=True,failures=0),text)
  value=({'relevance':'unrelated','intent':'other','fields':dict.fromkeys(FIELDS)} if blocked else {'relevance':'relevant','intent':'reservation','fields':{**dict.fromkeys(FIELDS),**gate}} if gate is not None else row['normalized'])
  errors=score(case,value)
  report['runs'].append({'case':row['case'],'variant':row['variant'],'scope_blocked':blocked,'gate':gate,'errors':errors,'correct':not errors})
 for variant in ['baseline','compact']:
  rows=[r for r in report['runs'] if r['variant']==variant]
  report[variant]={'correct':sum(r['correct'] for r in rows),'total':len(rows),'gated':sum(r['gate'] is not None or r['scope_blocked'] for r in rows)}
 report['fastpaths_sha256']=hashlib.sha256((ROOT/'decision_fastpaths.py').read_bytes()).hexdigest()
 (OUT/args.output).write_text(json.dumps(report,indent=2))
 print(json.dumps({k:report[k] for k in ['baseline','compact']}))
 if report['compact']['correct']!=len(cases):raise SystemExit(1)
if __name__=='__main__':main()
