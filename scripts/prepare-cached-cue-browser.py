"""Generate private real-Piper cue fixture and measure cold/warm cache reads."""
import json,os,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
OUT=ROOT/'results/cached-cue-gate-20261101';(OUT/'cue-private').mkdir(parents=True,exist_ok=True)
os.environ.update(RECEPTIONIST_FIXTURE_DIR=str(OUT/'cue-private'),LANGSMITH_TRACING='false')
from dotenv import load_dotenv
load_dotenv(ROOT/'.env')
import chat_api as api
import voice.greeting as greeting
from fastapi.testclient import TestClient
original=greeting.recording
greeting.recording=lambda *a,**k:original(*a,cache_root=OUT/'cue-private/speech-cache',**k)
rows=[]
with TestClient(api.app) as client:
    for i in range(5):
        start=time.perf_counter();r=client.get('/api/voice/thinking-cue');r.raise_for_status();data=r.json()
        rows.append({'sample':i+1,'condition':'first_read' if not i else 'warm_cache','ms':(time.perf_counter()-start)*1000,'version':data['version']})
        assert data['text']=='One moment.'
    # Prove persistent cache works even if synthesis is unavailable afterward.
    import browser_voice
    browser_voice.speak=lambda request:(_ for _ in ()).throw(AssertionError('Cached cue must not synthesize'))
    again=client.get('/api/voice/thinking-cue');again.raise_for_status();assert again.json()==data
browser=OUT/'browser';browser.mkdir(exist_ok=True)
(browser/'cue.json').write_text(json.dumps(data),encoding='utf-8')
(browser/'index.html').write_text('<!doctype html><html><head><meta charset="utf-8"><title>Private cached cue check</title></head><body><button id="run">Run isolated audio check</button><pre id="result">Ready: synthetic input only, no reservation writes.</pre><div id="root"></div><script type="module" src="/fixture.js"></script></body></html>',encoding='utf-8')
(OUT/'cue-cache.json').write_text(json.dumps({'rows':rows,'persistent_cache_no_synthesis':True},indent=2),encoding='utf-8')
print(json.dumps(rows),flush=True)
