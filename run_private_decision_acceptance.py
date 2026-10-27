"""Run acceptance against a private database/RAG/audit copy; never modify production rows."""
import os,json,sqlite3,hashlib,shutil,argparse
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).parent

def production_baseline():
 with sqlite3.connect(f'file:{ROOT/"reservations.sqlite3"}?mode=ro',uri=True) as c:
  c.row_factory=sqlite3.Row
  rows=[dict(x) for x in c.execute("SELECT * FROM reservations WHERE request_key LIKE 'baseline:2026-q4:v1:%' ORDER BY id")]
 return {'count':len(rows),'sha256':hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()}

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--protocol',choices=['baseline','compact'],default='baseline');args=parser.parse_args()
 before=production_baseline()
 folder=ROOT/'results/decision-latency'/('private-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S'))
 fixture=folder/'data';fixture.mkdir(parents=True)
 with sqlite3.connect(f'file:{ROOT/"reservations.sqlite3"}?mode=ro',uri=True) as a,sqlite3.connect(fixture/'reservations.sqlite3') as b:a.backup(b)
 if (ROOT/'data/chroma').exists():shutil.copytree(ROOT/'data/chroma',fixture/'chroma')
 os.environ.update(RECEPTIONIST_FIXTURE_DIR=str(fixture),RECEPTIONIST_DECISION_MODE='unified',RECEPTIONIST_DECISION_PROTOCOL=args.protocol,RECEPTIONIST_DECISION_FASTPATHS='safe',LANGSMITH_TRACING='false')
 provenance={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ['decision_engine.py','decision_protocol.py','decision_fastpaths.py']}
 from voice_test_harness import execute_suite
 seen={}
 def progress(summary):
  for item in summary['scenarios']:
   if seen.get(item['name'])!=item['status']:
    seen[item['name']]=item['status'];print(item['name'],item['status'],flush=True)
 result=execute_suite(progress)
 result.update(fixture_dir=str(fixture),production_baseline_before=before,production_baseline_after=production_baseline(),decision_protocol=args.protocol,decision_fastpaths='safe',provenance=provenance)
 result['production_baseline_unchanged']=result['production_baseline_before']==result['production_baseline_after']
 (folder/'acceptance.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
 (ROOT/'results/decision-latency/private-acceptance-latest.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
 assert result['production_baseline_unchanged']
 print('PRIVATE ACCEPTANCE',result['status'],str(folder),flush=True)
 if not result['passed']:raise SystemExit(1)

if __name__=='__main__':main()
