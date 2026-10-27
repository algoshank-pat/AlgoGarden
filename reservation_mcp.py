"""Local, read-only MCP server scoped by the trusted application, not the LLM."""
import time
_BOOT_STARTED=time.perf_counter()
import argparse
import json
import os
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from booking_db import BookingStore
from structured_io import LookupResult

def timing(stage,**values):
    path=os.getenv('RECEPTIONIST_MCP_METRICS')
    if path:
        with open(path,'a',encoding='utf-8') as stream:
            stream.write(json.dumps({'stage':stage,'at':time.perf_counter(),'pid':os.getpid(),**values})+'\n')


def create_server(db_path, business_id, today, phone_region='IN'):
    server=FastMCP('Restaurant reservation lookup')

    @server.tool()
    def lookup_reservations_by_phone(phone: str, request_id: str = '') -> LookupResult:
        """Find active upcoming reservations for this restaurant using the customer's phone."""
        started=time.perf_counter()
        store=BookingStore(Path(db_path),phone_region)
        opened=time.perf_counter()
        result=LookupResult.model_validate(store.lookup_by_phone(business_id,phone,today))
        timing('database',request_id=request_id,store_ms=(opened-started)*1000,query_ms=(time.perf_counter()-opened)*1000)
        return result

    return server


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',required=True)
    parser.add_argument('--business',required=True)
    parser.add_argument('--today',required=True)
    parser.add_argument('--region',default='IN')
    args=parser.parse_args()
    server=create_server(args.db,args.business,args.today,args.region)
    timing('server_ready',boot_started=_BOOT_STARTED)
    server.run(transport='stdio')
