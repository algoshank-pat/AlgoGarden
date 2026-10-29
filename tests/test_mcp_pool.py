import os,json,signal,time
import subprocess,sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from booking_db import BookingStore
import reservation_mcp_client as client

@pytest.fixture
def fixture(tmp_path,monkeypatch):
 monkeypatch.setenv('RECEPTIONIST_MCP_MODE','pooled');monkeypatch.setenv('RECEPTIONIST_MCP_METRICS',str(tmp_path/'metrics.jsonl'))
 client.close_mcp_connections();store=BookingStore(tmp_path/'private.sqlite3');ids={}
 for business,phone,day in [('a','9900112233','2026-11-01'),('a','9900112244','2026-11-03'),('b','9900112233','2026-11-02')]:
  row=store.book(business,{'name':'Private','phone':phone,'date':day,'time':'20:00','party_size':2,'ts':'2026-10-28'},40,0,business+phone);ids[business,phone]=row['id']
 yield store,ids,tmp_path
 client.close_mcp_connections()

def test_concurrent_lookup_scope_and_response_isolation(fixture):
 store,ids,_=fixture
 cases=[('a','2026-10-28','9900112233',ids['a','9900112233']),('b','2026-10-28','9900112233',ids['b','9900112233']),('a','2026-11-02','9900112233',None),('a','2026-10-28','9900112244',ids['a','9900112244'])]*5
 def call(c):
  b,d,p,expected=c;r=client.lookup_reservations_by_phone(store,b,d,p)
  assert [x['id'] for x in r['reservations']]==([expected] if expected else [])
  return r
 with ThreadPoolExecutor(max_workers=8) as executor:results=list(executor.map(call,cases))
 assert len(results)==20 and len(client._pool.workers)==3
 results[0]['reservations'].clear()
 assert client.lookup_reservations_by_phone(store,'a','2026-10-28','9900112233')['found']

def test_fresh_reads_database_path_and_region_scopes(fixture):
 store,ids,path=fixture
 assert client.lookup_reservations_by_phone(store,'a','2026-10-28','9900112233')['found']
 store.change('a',ids['a','9900112233'],'9900112233','cancel',1,{},40,0,'private-cancel')
 assert not client.lookup_reservations_by_phone(store,'a','2026-10-28','9900112233')['found']
 other=BookingStore(path/'other.sqlite3')
 assert not client.lookup_reservations_by_phone(other,'a','2026-10-28','9900112244')['found']
 region=BookingStore(store.path,phone_region='US')
 assert not client.lookup_reservations_by_phone(region,'a','2026-10-28','+919900112244')['found']
 assert client.lookup_reservations_by_phone(store,'a','2026-10-28','+919900112244')['found']

def test_crashed_child_fails_closed_and_recovers(fixture):
 store,ids,path=fixture
 client.lookup_reservations_by_phone(store,'a','2026-10-28','9900112233')
 worker=next(iter(client._pool.workers.values()))
 pid=next(json.loads(x)['pid'] for x in (path/'metrics.jsonl').read_text().splitlines() if json.loads(x)['stage']=='server_ready')
 assert pid!=os.getpid();os.kill(pid,signal.SIGTERM)
 try:client.lookup_reservations_by_phone(store,'a','2026-10-28','9900112244')
 except RuntimeError:pass
 worker.thread.join(5)
 assert not worker.thread.is_alive()
 assert client.lookup_reservations_by_phone(store,'a','2026-10-28','9900112244')['reservations'][0]['id']==ids['a','9900112244']

def test_idle_and_shutdown_cleanup(fixture):
 store,_,_=fixture
 from mcp_lookup_pool import LookupPool
 client._pool=LookupPool(max_scopes=1,idle_seconds=.15)
 client.lookup_reservations_by_phone(store,'a','2026-10-28','9900112233');worker=next(iter(client._pool.workers.values()));worker.thread.join(5)
 assert not worker.thread.is_alive()
 client.lookup_reservations_by_phone(store,'b','2026-10-28','9900112233');worker=next(iter(client._pool.workers.values()))
 client.close_mcp_connections();assert not worker.thread.is_alive() and client._pool is None

def test_timeout_bounded_no_stale_response_and_next_connection_recovers(fixture,monkeypatch):
 store,ids,path=fixture
 from mcp_lookup_pool import LookupPool
 # Custom private server delays one read; no production server behavior changes.
 server=path/'slow.py';root=Path(client.__file__).parent
 server.write_text('import sys,time,runpy\nsys.path.insert(0,'+repr(str(root))+')\nfrom booking_db import BookingStore\nold=BookingStore.lookup_by_phone\ndef read(self,business,phone,today):\n if phone=="9900999999":time.sleep(2)\n return old(self,business,phone,today)\nBookingStore.lookup_by_phone=read\nrunpy.run_path('+repr(str(root/'reservation_mcp.py'))+',run_name="__main__")\n')
 monkeypatch.setattr(client,'SERVER_SCRIPT',server)
 client.lookup_reservations_by_phone(store,'a','2026-10-28','9900112233');pool=client._pool;key=next(iter(pool.workers));worker=pool.workers[key]
 started=time.perf_counter()
 with pytest.raises(TimeoutError):pool.lookup(key,worker.parameters,client.decode,'9900999999','private-timeout',timeout=.15)
 assert time.perf_counter()-started<.6
 worker.thread.join(6);assert not worker.thread.is_alive()
 assert client.lookup_reservations_by_phone(store,'a','2026-10-28','9900112244')['reservations'][0]['id']==ids['a','9900112244']

def test_metrics_never_record_phone(fixture):
 store,_,path=fixture;client.lookup_reservations_by_phone(store,'a','2026-10-28','9900112233')
 assert '9900112233' not in (path/'metrics.jsonl').read_text()

def test_pool_is_default_and_remains_a_fresh_read(fixture,monkeypatch):
 store,ids,_=fixture;monkeypatch.delenv('RECEPTIONIST_MCP_MODE',raising=False)
 assert client.lookup_reservations_by_phone(store,'a','2026-10-28','9900112233')['found']
 worker=next(iter(client._pool.workers.values()))
 store.change('a',ids['a','9900112233'],'9900112233','cancel',1,{},40,0,'private-default-cancel')
 assert not client.lookup_reservations_by_phone(store,'a','2026-10-28','9900112233')['found']
 assert next(iter(client._pool.workers.values())) is worker

def test_host_exit_closes_child_without_waiting_idle_ttl(fixture):
 store,_,path=fixture
 code='from pathlib import Path;from booking_db import BookingStore;from reservation_mcp_client import lookup_reservations_by_phone;print(lookup_reservations_by_phone(BookingStore(Path('+repr(str(store.path))+')),"a","2026-10-28","9900112233")["found"])'
 completed=subprocess.run([sys.executable,'-c',code],cwd=Path(client.__file__).parent,capture_output=True,text=True,timeout=10)
 assert completed.returncode==0 and 'True' in completed.stdout
 pids=[json.loads(x)['pid'] for x in (path/'metrics.jsonl').read_text().splitlines() if json.loads(x)['stage']=='server_ready']
 def alive(pid):
  if os.name=='nt':
   import ctypes
   from ctypes import wintypes
   kernel=ctypes.WinDLL('kernel32',use_last_error=True)
   kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
   kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
   kernel.CloseHandle.argtypes=[wintypes.HANDLE]
   handle=kernel.OpenProcess(0x00100000,False,pid)
   if not handle:return False
   try:return kernel.WaitForSingleObject(handle,0)==258
   finally:kernel.CloseHandle(handle)
  try:os.kill(pid,0);return True
  except ProcessLookupError:return False
 assert pids and all(not alive(pid) for pid in pids)

def test_scope_limit_and_idle_eviction(fixture):
 store,_,_=fixture
 from mcp_lookup_pool import LookupPool,LookupUnavailable
 client._pool=LookupPool(max_scopes=1)
 with ThreadPoolExecutor(max_workers=1) as executor:
  first=executor.submit(client.lookup_reservations_by_phone,store,'a','2026-10-28','9900112233')
  deadline=time.perf_counter()+3
  while not client._pool.workers and time.perf_counter()<deadline:time.sleep(.005)
  with pytest.raises(LookupUnavailable):client.lookup_reservations_by_phone(store,'b','2026-10-28','9900112233')
  assert first.result()['found']
 old=next(iter(client._pool.workers.values()))
 assert client.lookup_reservations_by_phone(store,'b','2026-10-28','9900112233')['found']
 assert len(client._pool.workers)==1 and not old.thread.is_alive()

def test_invalid_phone_never_replays_previous_results(fixture):
 store,ids,_=fixture
 assert client.lookup_reservations_by_phone(store,'a','2026-10-28','9900112233')['found']
 worker=next(iter(client._pool.workers.values()))
 with pytest.raises(RuntimeError):client.lookup_reservations_by_phone(store,'a','2026-10-28','invalid-phone')
 assert client.lookup_reservations_by_phone(store,'a','2026-10-28','9900112244')['reservations'][0]['id']==ids['a','9900112244']
 assert next(iter(client._pool.workers.values())) is worker

@pytest.mark.parametrize('change',[{'business_id':'other'},{'date':'2026-10-01'},{'status':'cancelled'},{'phone':'9900112244'}])
def test_scoped_response_validation_rejects_crossed_results(change):
 row={'business_id':'a','date':'2026-11-01','status':'active','phone':'9900112233',**change}
 with pytest.raises(ValueError):client.scoped({'found':True,'reservations':[row]},'a','2026-10-28','9900112233','IN')

def test_pool_lock_wait_is_inside_total_deadline():
 from mcp_lookup_pool import LookupPool
 pool=LookupPool()
 with ThreadPoolExecutor(max_workers=1) as executor:
  pool.lock.acquire()
  try:
   future=executor.submit(pool.lookup,('private',),None,None,'9900112233','deadline',.05)
   with pytest.raises(TimeoutError):future.result(timeout=.5)
   assert future.done()
  finally:pool.lock.release()
 assert not pool.workers
