"""Isolated MCP timings; original one-shot behavior is measured before reuse changes."""
import os,json,time,argparse,math,statistics,sqlite3,hashlib
from pathlib import Path
from run_private_decision_acceptance import production_baseline
ROOT=Path(__file__).parent

def stats(a):
 a=sorted(a);return {'n':len(a),'mean_ms':round(statistics.mean(a),3),'p50_ms':round(statistics.median(a),3),'p95_ms':round(a[math.ceil(.95*len(a))-1],3)}
def main():
 p=argparse.ArgumentParser();p.add_argument('--name',default='before');p.add_argument('--n',type=int,default=12);p.add_argument('--baseline-volume',action='store_true');args=p.parse_args()
 out=ROOT/'results/mcp-lookup';out.mkdir(exist_ok=True,parents=True);fixture=out/(args.name+'-private');fixture.mkdir(exist_ok=True)
 metrics=fixture/'timings.jsonl';metrics.write_text('');os.environ['RECEPTIONIST_MCP_METRICS']=str(metrics)
 from booking_db import BookingStore
 from reservation_mcp_client import lookup_reservations_by_phone,close_mcp_connections
 a,b,p1,p2,d1,d2,d3,cutoff='a','b','9900112233','9900112244','2026-11-01','2026-11-02','2026-11-03','2026-11-02'
 if args.baseline_volume:
  with sqlite3.connect(f'file:{ROOT/"reservations.sqlite3"}?mode=ro',uri=True) as source,sqlite3.connect(fixture/'db.sqlite3') as dest:
   source.backup(dest);a=source.execute("SELECT business_id FROM reservations WHERE request_key LIKE 'baseline:2026-q4:v1:%' LIMIT 1").fetchone()[0]
  b='private-mcp-other';p1='+12025550191';p2='+12025550192';d1,d2,d3,cutoff='2027-01-01','2027-01-02','2027-01-03','2027-01-02'
 store=BookingStore(fixture/'db.sqlite3');before=production_baseline()
 private_before=[r for r in store.list(a) if (r.get('request_key') or '').startswith('baseline:2026-q4:v1:')]
 for business,phone,day in [(a,p1,d1),(a,p2,d2),(b,p1,d3)]:
  store.book(business,{'name':'Private caller','phone':phone,'date':day,'time':'20:00','party_size':2,'ts':'2026-10-28'},40,0,business+phone)
 cases=[(a,'2026-10-28',p1,d1),(a,'2026-10-28',p2,d2),(a,cutoff,p1,None),(b,'2026-10-28',p1,d3)]
 rows=[]
 for i in range(args.n):
  business,today,phone,expected=cases[i%len(cases)];start=time.perf_counter();result=lookup_reservations_by_phone(store,business,today,phone)
  rows.append({'case':i,'total_ms':(time.perf_counter()-start)*1000,'correct':(result['reservations'][0]['date'] if result['found'] else None)==expected});assert rows[-1]['correct']
 close_mcp_connections()
 events=[json.loads(line) for line in metrics.read_text().splitlines()];clients=[x for x in events if x['stage']=='client'];database={x['request_id']:x for x in events if x['stage']=='database'};ready=[x for x in events if x['stage']=='server_ready']
 for row,client in zip(rows,clients):
  observed=row['total_ms']
  row.update(client);db=database[client['request_id']];row.update(store_ms=db['store_ms'],query_ms=db['query_ms'])
  row['client_total_ms']=client['total_ms'];row['total_ms']=observed
  cold=[x for x in ready if client['initialize_started']<=x['at']<=client['initialize_completed']]
  row['startup_after_spawn_ms']=(cold[-1]['at']-client['initialize_started'])*1000 if cold else 0
  row['protocol_after_ready_ms']=client['initialize_ms']-row['startup_after_spawn_ms']
 report={'method':'Real MCP stdio calls, private SQLite records, identical four trusted scope/phone cases cycled; no LLM.','rows':rows,'metrics':{k:stats([r[k] for r in rows]) for k in ['total_ms','spawn_ms','startup_after_spawn_ms','protocol_after_ready_ms','initialize_ms','tool_ms','store_ms','query_ms','cleanup_ms']},'pool_shutdown_ms':[x['cleanup_ms'] for x in events if x['stage']=='pool_cleanup'],'correct':sum(r['correct'] for r in rows),'baseline_before':before,'baseline_after':production_baseline()};report['baseline_unchanged']=report['baseline_before']==report['baseline_after']
 report['baseline_volume']=args.baseline_volume;report['private_baseline_rows']=len(private_before);report['private_baseline_unchanged']=private_before==[r for r in store.list(a) if (r.get('request_key') or '').startswith('baseline:2026-q4:v1:')]
 assert report['private_baseline_unchanged']
 (out/(args.name+'.json')).write_text(json.dumps(report,indent=2));print(json.dumps({k:report[k] for k in ['metrics','correct','baseline_unchanged']},indent=2));assert report['baseline_unchanged']
if __name__=='__main__':main()
