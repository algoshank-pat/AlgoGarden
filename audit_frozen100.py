"""Independent, deterministic review of captured results; no application execution."""
import json,re,sys,hashlib,statistics
from pathlib import Path
from collections import Counter
from freeze_customer100 import digest

def main():
 root=Path(sys.argv[1]).resolve();out=root/'run';snap=root/'application'
 report=json.loads((out/'report.json').read_text(encoding='utf-8'))
 manifest=json.loads((root/'freeze-manifest.json').read_text(encoding='utf-8'))
 gold=json.loads((root/'scoring-rules.json').read_text(encoding='utf-8'))
 verdicts=[]
 for c in report['results']:
  number=c['case']['id'];success=c['success'];reasons=[]
  if str(number) in gold.get('literal_fulfillment_negative_cases',{}):
   success=False
   reasons.append(gold['literal_fulfillment_negative_cases'][str(number)])
  if str(number) in gold['policy_gold']:
   rule=gold['policy_gold'][str(number)]
   answers=[t for t in c['turns'][1:] if rule['source'] in t['response'].get('sources',[]) and re.search(rule['pattern'],t['response'].get('reply',''),re.I)]
   success=bool(answers)
   if not success:reasons.append('Requested policy answer/conditions not returned from its source')
  if c['case'].get('variant')=='broad':
   first=c['turns'][1]['response']['reply']
   if not (re.search(r'date|range|when',first,re.I) and re.search(r'time|around',first,re.I) and not re.search(r'phone',first,re.I)):
    reasons.append('Broad availability request not narrowed before asking for phone')
  wrong_response=False
  if not success and c['case']['kind'] in {'information','policy'}:
   answer=c['turns'][-1]['response']
   answer_tools=set(answer.get('structured',{}).get('tools_called',[]))
   if answer.get('sources') or answer_tools & {'get_menu','check_dish','get_hours','get_location','get_specials'}:
    wrong_response=True
    reasons.append('Returned factual content that did not answer the requested question')
  accurate=success and not c['wrong_reasons'] and not reasons and not c.get('harness_error')
  outcome='harness_error' if c.get('harness_error') else 'wrong_outcome' if c['wrong_reasons'] or wrong_response else 'success_after_repetition' if success and c['repairs'] else 'success_first_time' if success else 'safe_failure'
  verdicts.append({'case_id':number,'accuracy_pct':100 if accurate else 0,'final_task_completed':success,'task_outcome':outcome,'reason':'; '.join(reasons+c['wrong_reasons']) or ('Requested answer/action matched evidence' if success else 'Requested task not completed'),'raw_task_outcome':c['outcome']})
 expected=dict(manifest['source_sha256'])
 for name,v in manifest.get('test_harness_adjustments',{}).items():expected[name]=v['sha256_used']
 changes=[name for name,s in expected.items() if hashlib.sha256((snap/name).read_bytes()).hexdigest()!=s]
 before=manifest['production_baseline_before'];after=digest(Path(manifest['original_root'])/'reservations.sqlite3')
 audit={'completed':len(verdicts),'request_accuracy_pct':round(statistics.mean(v['accuracy_pct'] for v in verdicts),3),'request_mismatches':[v['case_id'] for v in verdicts if not v['accuracy_pct']],'task_outcomes':dict(Counter(v['task_outcome'] for v in verdicts)),'case_verdicts':verdicts,'frozen_sources_verified':not changes,'changed_frozen_sources':changes,'production_baseline_before':before,'production_baseline_after':after,'production_baseline_unchanged':before==after,'private_case_database_files':[str(p.relative_to(out)) for p in out.glob('case-*/private.sqlite3*')],'policy_scoring':'Predeclared source and conditional-fact gold; no generative evaluator.'}
 (out/'adjudication.json').write_text(json.dumps(audit,indent=2),encoding='utf-8')
 assert not changes,changes
 assert before==after,'Production baseline changed'
 print(json.dumps({k:v for k,v in audit.items() if k!='case_verdicts'},indent=2))
if __name__=='__main__':main()
