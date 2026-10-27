"""Call the real MCP stdio tool with server-side restaurant/date scoping."""
import asyncio
import json
import sys
import time
import os
from uuid import uuid4
from pathlib import Path
from datetime import timedelta
from datetime import date
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from structured_io import LookupResult
import atexit
from threading import Lock

SERVER_SCRIPT=Path(__file__).with_name('reservation_mcp.py')
_pool=None
_pool_lock=Lock()

def decode(result):
    if result.isError:raise RuntimeError('Reservation lookup tool failed')
    data=result.structuredContent
    if data is None:data=json.loads(''.join(block.text for block in result.content if block.type=='text'))
    if isinstance(data,dict) and 'result' in data:data=data['result']
    return LookupResult.model_validate(data).model_dump(mode='json')

def scoped(value,business_id,today,phone,region):
    from customer_identity import normalize_phone
    key=normalize_phone(phone,region)
    if value['found']!=bool(value['reservations']):raise ValueError('Inconsistent lookup result')
    for row in value['reservations']:
        if (row.get('business_id')!=business_id or row.get('status')!='active' or
            date.fromisoformat(row['date']).isoformat()<today or normalize_phone(row.get('phone'),region)!=key):
            raise ValueError('Lookup response violated its trusted scope')
    return value

def close_mcp_connections():
    global _pool
    with _pool_lock:pool,_pool=_pool,None
    if pool is not None:pool.close()

atexit.register(close_mcp_connections)


def lookup_reservations_by_phone(store, business_id, today, phone):
    if os.getenv('RECEPTIONIST_MCP_MODE','oneshot')=='pooled':
        global _pool
        from mcp_lookup_pool import LookupPool
        started=time.perf_counter();request_id=str(uuid4())
        metrics_path=os.getenv('RECEPTIONIST_MCP_METRICS')
        parameters=StdioServerParameters(command=sys.executable,args=[str(SERVER_SCRIPT),'--db',str(store.path.resolve()),'--business',business_id,'--today',today,'--region',store.phone_region],env={'RECEPTIONIST_MCP_METRICS':metrics_path} if metrics_path else None)
        key=(str(store.path.resolve()),business_id,today,store.phone_region,str(SERVER_SCRIPT),metrics_path)
        with _pool_lock:
            if _pool is None:_pool=LookupPool()
            pool=_pool
        value,metrics=pool.lookup(key,parameters,decode,phone,request_id)
        try:value=scoped(value,business_id,today,phone,store.phone_region)
        except (ValueError,KeyError,TypeError):
            pool.retire(key)
            raise
        metrics['total_ms']=(time.perf_counter()-started)*1000
        from local_audit import event
        event('mcp.lookup.timings',metrics)
        if metrics_path:
            with open(metrics_path,'a',encoding='utf-8') as stream:stream.write(json.dumps({'stage':'client','request_id':request_id,**metrics})+'\n')
        return value
    async def call():
        started=time.perf_counter();request_id=str(uuid4());metrics={}
        parameters=StdioServerParameters(command=sys.executable,args=[
            str(SERVER_SCRIPT),
            '--db',str(store.path.resolve()),'--business',business_id,'--today',today,
            '--region',store.phone_region],env={'RECEPTIONIST_MCP_METRICS':os.environ['RECEPTIONIST_MCP_METRICS']} if os.getenv('RECEPTIONIST_MCP_METRICS') else None)
        async with asyncio.timeout(20):
            async with stdio_client(parameters) as (read,write):
                metrics['spawn_ms']=(time.perf_counter()-started)*1000
                async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=15)) as session:
                    mark=time.perf_counter();metrics['initialize_started']=mark
                    await session.initialize()
                    metrics['initialize_completed']=time.perf_counter();metrics['initialize_ms']=(time.perf_counter()-mark)*1000
                    mark=time.perf_counter()
                    result=await session.call_tool('lookup_reservations_by_phone',{'phone':phone,'request_id':request_id})
                    metrics['tool_ms']=(time.perf_counter()-mark)*1000
                    if result.isError:raise RuntimeError('Reservation lookup tool failed')
                    data=result.structuredContent
                    if data is None:
                        data=json.loads(''.join(block.text for block in result.content if block.type=='text'))
                    if isinstance(data,dict) and 'result' in data:data=data['result']
                    value=LookupResult.model_validate(data).model_dump(mode='json')
                    cleanup_started=time.perf_counter()
        metrics['cleanup_ms']=(time.perf_counter()-cleanup_started)*1000
        metrics['total_ms']=(time.perf_counter()-started)*1000
        from local_audit import event
        event('mcp.lookup.timings',metrics)
        if os.getenv('RECEPTIONIST_MCP_METRICS'):
            with open(os.environ['RECEPTIONIST_MCP_METRICS'],'a',encoding='utf-8') as stream:
                stream.write(json.dumps({'stage':'client','request_id':request_id,**metrics})+'\n')
        return scoped(value,business_id,today,phone,store.phone_region)
    return asyncio.run(call())
