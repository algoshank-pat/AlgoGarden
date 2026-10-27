"""Isolated MCP timings; original one-shot behavior is measured before reuse changes."""
import os,json,time,argparse,math,statistics
from pathlib import Path
from run_private_decision_acceptance import production_baseline
ROOT=Path(__file__).parent

def stats(a):
 a=sorted(a);return {'n':len(a),'mean_ms':round(statistics.mean(a),3),'p50_ms':round(statistics.median(a),3),'p95_ms':round(a[math.ceil(.95*len(a))-1],3)}
def main():
 p=argparse.ArgumentParser();p.add_argument('--name',default='before');p.add_argument('--n',type=int,default=12);args=p.parse_args()
 out=ROOT/'results/mcp-lookup';out.mkdir(exist_ok=True,parents=True);fixture=out/(args.name+'-private');fixture.mkdir(exist_ok=True)
 metrics=fixture/'timings.jsonl';metrics.write_text('');os.environ['RECEPTIONIST_MCP_METRICS']=str(metrics)
 from booking_db import BookingStore
 from reservation_mcp_client import lookup_reservations_by_phone,close_mcp_connections
 store=BookingStore(fixture/'db.sqlite3');before=production_baseline()
 for business,phone,day in [('a','9900112233','2026-11-01'),('a','9900112244','2026-11-02'),('b','9900112233','2026-11-03')]:
  store.book(business,{'name':'Private caller','phone':phone,'date':day,'time':'20:00','party_size':2,'ts':'2026-10-28'},40,0,business+phone)
 cases=[('a','2026-10-28','9900112233','2026-11-01'),('a','2026-10-28','9900112244','2026-11-02'),('a','2026-11-02','9900112233',None),('b','2026-10-28','9900112233','2026-11-03')]
 rows=[]
 for i in range(args.n):
  business,today,phone,expected=cases[i%len(cases)];start=time.perf_counter();result=lookup_reservations_by_phone(store,business,today,phone)
  rows.append({'case':i,'total_ms':(time.perf_counter()-start)*1000,'correct':(result['reservations'][0]['date'] if result['found'] else None)==expected});assert rows[-1]['correct']
 close_mcp_connections()
 events=[json.loads(line) for line in metrics.read_text().splitlines()];clients=[x for x in events if x['stage']=='client'];database={x['request_id']:x for x in events if x['stage']=='database'};ready=[x for x in events if x['stage']=='server_ready']
 for row,client in zip(rows,clients):
  row.update(client);db=database[client['request_id']];row.update(store_ms=db['store_ms'],query_ms=db['query_ms'])
  cold=[x for x in ready if client['initialize_started']<=x['at']<=client['initialize_completed']]
  row['startup_after_spawn_ms']=(cold[-1]['at']-client['initialize_started'])*1000 if cold else 0
  row['protocol_after_ready_ms']=client['initialize_ms']-row['startup_after_spawn_ms']
 report={'method':'Real MCP stdio calls, private SQLite records, identical four trusted scope/phone cases cycled; no LLM.','rows':rows,'metrics':{k:stats([r[k] for r in rows]) for k in ['total_ms','spawn_ms','startup_after_spawn_ms','protocol_after_ready_ms','initialize_ms','tool_ms','store_ms','query_ms','cleanup_ms']},'pool_shutdown_ms':[x['cleanup_ms'] for x in events if x['stage']=='pool_cleanup'],'correct':sum(r['correct'] for r in rows),'baseline_before':before,'baseline_after':production_baseline()};report['baseline_unchanged']=report['baseline_before']==report['baseline_after']
 (out/(args.name+'.json')).write_text(json.dumps(report,indent=2));print(json.dumps({k:report[k] for k in ['metrics','correct','baseline_unchanged']},indent=2));assert report['baseline_unchanged']
if __name__=='__main__':main()
