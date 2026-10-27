"""Rerun selection update/cancel with the exact failed caller audio in a private fixture."""
import os,json,sqlite3,shutil,subprocess,sys
from pathlib import Path
from datetime import datetime,timezone
from run_private_decision_acceptance import production_baseline
ROOT=Path(__file__).parent

def main():
 before=production_baseline();folder=ROOT/'results/decision-latency'/('selection-replay-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S'));fixture=folder/'data';fixture.mkdir(parents=True)
 with sqlite3.connect(f'file:{ROOT/"reservations.sqlite3"}?mode=ro',uri=True) as a,sqlite3.connect(fixture/'reservations.sqlite3') as b:a.backup(b)
 if (ROOT/'data/chroma').exists():shutil.copytree(ROOT/'data/chroma',fixture/'chroma')
 audio=ROOT/'results/decision-latency/selection-failure/04-caller.wav'
 os.environ.update(RECEPTIONIST_FIXTURE_DIR=str(fixture),RECEPTIONIST_DECISION_MODE='unified',RECEPTIONIST_DECISION_PROTOCOL='baseline',RECEPTIONIST_DECISION_FASTPATHS='safe',LANGSMITH_TRACING='false',VOICE_TEST_SELECTION_AUDIO=str(audio))
 from voice_test_harness import private_api,evaluate_report,cleanup_in_database
 with private_api(folder) as base:
  with (folder/'scenario.log').open('w',encoding='utf-8') as log:
   code=subprocess.run([sys.executable,'-u',str(ROOT/'test_browser_voice_multiple.py')],cwd=ROOT,env={**os.environ,'VOICE_TEST_API_URL':base,'PYTHONIOENCODING':'utf-8'},stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).returncode
 report=json.loads((ROOT/'results/browser-voice/multiple-reservations-voice-flow/report.json').read_text())
 (folder/'scenario-report.json').write_text(json.dumps(report,indent=2))
 result={**evaluate_report(report,code),'fixture_dir':str(fixture),'report':str(folder/'scenario-report.json'),'same_selection_audio':str(audio),'cleanup_verified_in_database':cleanup_in_database(report),'production_baseline_before':before,'production_baseline_after':production_baseline()}
 result['production_baseline_unchanged']=result['production_baseline_before']==result['production_baseline_after']
 (ROOT/'results/decision-latency/safe-selection-rerun.json').write_text(json.dumps(result,indent=2))
 print(json.dumps(result,indent=2))
 assert result['status']=='passed' and result['cleanup_verified_in_database'] and result['production_baseline_unchanged']
if __name__=='__main__':main()
