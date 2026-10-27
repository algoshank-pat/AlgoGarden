"""Prepare a verified application snapshot for testing; never alter live code/data."""
import hashlib,json,sqlite3,shutil,os
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).parent
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def digest(dbpath):
 with sqlite3.connect(f'file:{dbpath}?mode=ro',uri=True) as db:
  db.row_factory=sqlite3.Row
  rows=[dict(r) for r in db.execute("SELECT * FROM reservations WHERE request_key LIKE 'baseline:2026-q4:v1:%' ORDER BY rowid")]
 return {'count':len(rows),'raw_rows_sha256':hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()}
def main():
 out=ROOT/'results/customer100'/('frozen-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
 snap=out/'application';snap.mkdir(parents=True)
 files=list(ROOT.glob('*.py'))
 for folder in ['voice','restaurants','businesses']:
  files.extend(p for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix in {'.py','.yaml','.yml','.md','.json'})
 before={str(p.relative_to(ROOT)):sha(p) for p in files}
 for source in files:
  target=snap/source.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
 after={str(p.relative_to(ROOT)):sha(p) for p in files}
 assert before==after,'Source changed while copying; discard snapshot and retry'
 assert all(sha(snap/name)==value for name,value in before.items())
 with sqlite3.connect(f'file:{ROOT/"reservations.sqlite3"}?mode=ro',uri=True) as a,sqlite3.connect(snap/'reservations.sqlite3') as b:a.backup(b)
 shutil.copytree(ROOT/'data/chroma',snap/'data/chroma')
 # Historical feedback is read by the CSV exporter, never used as model input.
 shutil.copy2(ROOT/'calls.jsonl',snap/'calls.jsonl')
 for rel in ['results/browser-voice/customer-feedback/report.json','results/browser-voice/survey-both-channels/report.json','results/customer100/csv-export/export_metrics.mjs']:
  target=snap/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/rel,target)
 meta={'frozen_at_utc':datetime.now(timezone.utc).isoformat(),'original_root':str(ROOT),'application':str(snap),'source_sha256':before,'production_baseline_before':digest(ROOT/'reservations.sqlite3'),'snapshot_baseline':digest(snap/'reservations.sqlite3'),'settings':{k:os.getenv(k,default) for k,default in [('RECEPTIONIST_MODEL','gemma4:e2b-it-qat'),('RECEPTIONIST_DECISION_MODE','unified'),('RECEPTIONIST_DECISION_PROTOCOL','baseline'),('RECEPTIONIST_DECISION_FASTPATHS','baseline')]}}
 assert meta['production_baseline_before']['count']==1911 and meta['production_baseline_before']==meta['snapshot_baseline']
 (out/'freeze-manifest.json').write_text(json.dumps(meta,indent=2),encoding='utf-8')
 print(out)
if __name__=='__main__':main()
