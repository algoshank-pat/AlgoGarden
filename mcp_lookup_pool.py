"""Bounded scope-bound MCP workers. Each thread owns one async session lifetime."""
import asyncio,concurrent.futures,queue,threading,time,json
from datetime import timedelta
from mcp import ClientSession
from mcp.client.stdio import stdio_client

class LookupUnavailable(RuntimeError):
 def __init__(self,message,phase='pool'):
  super().__init__(message);self.phase=phase
class ToolFailure(RuntimeError):
 phase='tool'
STOP=object()

class Worker:
 def __init__(self,parameters,decode,idle_seconds):
  self.parameters=parameters;self.decode=decode;self.idle=idle_seconds
  self.jobs=queue.Queue(maxsize=32);self.stopping=False;self.failed=False
  self.loop=None;self.task=None;self.current=None;self.wake=None;self.phase='starting'
  self.thread=threading.Thread(target=self.run,name='reservation-mcp',daemon=True);self.thread.start()
 def submit(self,phone,request_id,deadline):
  if self.stopping or self.failed:raise LookupUnavailable('MCP connection is recovering')
  future=concurrent.futures.Future()
  try:self.jobs.put_nowait((phone,request_id,deadline,time.perf_counter(),future))
  except queue.Full:raise LookupUnavailable('MCP lookup queue is full')
  if self.loop and self.wake:self.loop.call_soon_threadsafe(self.wake.set)
  return future
 async def next_job(self):
  deadline=time.perf_counter()+self.idle
  while True:
   self.wake.clear()
   try:return self.jobs.get_nowait()
   except queue.Empty:pass
   try:await asyncio.wait_for(self.wake.wait(),max(.001,deadline-time.perf_counter()))
   except TimeoutError:return STOP
 async def serve(self):
  self.loop=asyncio.get_running_loop();self.task=asyncio.current_task();self.wake=asyncio.Event()
  job=await self.next_job()
  if job is STOP:return
  self.current=job;remaining=job[2]-time.perf_counter()
  if remaining<=0:raise TimeoutError('Lookup deadline expired before startup')
  started=time.perf_counter()
  async with asyncio.timeout(remaining) as startup:
   async with stdio_client(self.parameters) as (read,write):
    spawn=(time.perf_counter()-started)*1000
    async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=15)) as session:
     self.phase='initializing'
     init_started=time.perf_counter();await session.initialize();init_completed=time.perf_counter()
     startup.reschedule(None);first=True
     while job is not STOP:
      self.current=job;phone,request_id,deadline,submitted,future=job
      if self.stopping:raise LookupUnavailable('MCP connection closing')
      if future.cancelled() or deadline<=time.perf_counter():
       if not future.done():future.set_exception(TimeoutError('Lookup deadline expired in queue'))
      else:
       mark=time.perf_counter()
       self.phase='tool'
       try:
        async with asyncio.timeout(min(15,deadline-mark)):
         result=await session.call_tool('lookup_reservations_by_phone',{'phone':phone,'request_id':request_id})
        self.phase='validating';value=self.decode(result)
       except ToolFailure as exc:
        self.current=None;self.phase='idle'
        if not future.done():future.set_exception(exc)
        first=False;job=await self.next_job();continue
       metrics={'spawn_ms':spawn if first else 0,'initialize_started':init_started if first else mark,'initialize_completed':init_completed if first else mark,'initialize_ms':(init_completed-init_started)*1000 if first else 0,'tool_ms':(time.perf_counter()-mark)*1000,'queue_wait_ms':max(0,(started if first else mark)-submitted)*1000,'cleanup_ms':0,'reused':not first}
       self.current=None;self.phase='idle'
       if not future.done():future.set_result((value,metrics))
      first=False;self.current=None
      job=await self.next_job()
 def run(self):
  error=LookupUnavailable('MCP connection closed')
  try:asyncio.run(self.serve())
  except BaseException as exc:
   self.failed=True;error=LookupUnavailable('MCP connection failed: '+type(exc).__name__,self.phase)
  finally:
   self.stopping=True
   if self.current is not None and not self.current[-1].done():self.current[-1].set_exception(error)
   while True:
    try:job=self.jobs.get_nowait()
    except queue.Empty:break
    if job is not STOP and not job[-1].done():job[-1].set_exception(error)
 def stop(self):
  self.stopping=True
  try:self.jobs.put_nowait(STOP)
  except queue.Full:pass
  if self.loop and self.wake:
   try:self.loop.call_soon_threadsafe(self.wake.set)
   except RuntimeError:pass
  if self.loop and self.task and self.current is not None:
   try:self.loop.call_soon_threadsafe(self.task.cancel)
   except RuntimeError:pass
 def close(self,timeout=5):
  started=time.perf_counter()
  self.stop()
  self.thread.join(timeout=timeout)
  if self.thread.is_alive():raise LookupUnavailable('MCP worker cleanup did not finish')
  path=(self.parameters.env or {}).get('RECEPTIONIST_MCP_METRICS')
  if path:
   with open(path,'a',encoding='utf-8') as stream:stream.write(json.dumps({'stage':'pool_cleanup','cleanup_ms':(time.perf_counter()-started)*1000})+'\n')

class LookupPool:
 def __init__(self,max_scopes=4,idle_seconds=60):
  self.max_scopes=max_scopes;self.idle_seconds=idle_seconds;self.workers={};self.lock=threading.Lock();self.closed=False
 def lookup(self,key,parameters,decode,phone,request_id,timeout=20):
  deadline=time.perf_counter()+timeout
  if not self.lock.acquire(timeout=max(0,deadline-time.perf_counter())):raise TimeoutError('MCP pool wait exceeded deadline')
  try:
   if self.closed:raise LookupUnavailable('MCP pool is closed')
   for scope,worker in list(self.workers.items()):
    if not worker.thread.is_alive():del self.workers[scope]
   worker=self.workers.get(key)
   if worker and (worker.failed or worker.stopping):raise LookupUnavailable('MCP connection is recovering')
   if worker is None:
    if len(self.workers)>=self.max_scopes:
     idle=next(((k,w) for k,w in self.workers.items() if w.current is None and w.jobs.empty()),None)
     if idle is None:raise LookupUnavailable('MCP scope limit reached')
     idle[1].close(timeout=max(0,min(5,deadline-time.perf_counter())));del self.workers[idle[0]]
    worker=Worker(parameters,decode,self.idle_seconds);self.workers[key]=worker
   future=worker.submit(phone,request_id,deadline)
  finally:self.lock.release()
  try:return future.result(timeout=max(0,deadline-time.perf_counter()))
  except concurrent.futures.TimeoutError:
   future.cancel()
   # A timed-out operation never lends its session to another caller.
   worker.stop()
   raise TimeoutError('MCP lookup exceeded its deadline')
 def close(self):
  with self.lock:self.closed=True;workers=list(self.workers.values());self.workers.clear()
  errors=[]
  for worker in workers:
   try:worker.close()
   except Exception as exc:errors.append(exc)
  if errors:raise LookupUnavailable('MCP worker cleanup failed') from errors[0]

 def retire(self,key):
  with self.lock:
   worker=self.workers.get(key)
   if worker:worker.stop()
