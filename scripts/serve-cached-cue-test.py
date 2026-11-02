"""Disposable localhost browser test, no session or reservation endpoints."""
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import json
root=Path(__file__).resolve().parents[1]/'results/cached-cue-gate-20261101/browser'
class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(root),**kwargs)
    def do_POST(self):
        if self.path!='/report':self.send_error(404);return
        report=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        (root.parent/'browser-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        self.send_response(200);self.end_headers();self.wfile.write(b'{}')
ThreadingHTTPServer(('127.0.0.1',18118),Handler).serve_forever()
