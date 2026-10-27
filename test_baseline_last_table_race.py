"""Two concurrent voice-session confirmations for one baseline table. No UX changes."""
import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from math import ceil
from threading import Barrier
from uuid import uuid4

import httpx
from customer_identity import local_now
from receptionist import HERE, Tools, load_restaurant
from reservation_schedule import minute, clock, shifts_for, peak_load


def main():
    output=HERE/'results/concurrency/baseline-last-table';output.mkdir(parents=True,exist_ok=True)
    restaurant=load_restaurant(HERE/'restaurants/demo_restaurant.yaml');tools=Tools(restaurant);store=tools.booking_store()
    base_url=os.getenv('VOICE_TEST_API_URL','http://127.0.0.1:8012')
    def baseline_digest():
        rows=[r for r in store.list(tools.business_id) if (r.get('request_key') or '').startswith('baseline:2026-q4:v1:')]
        assert len(rows)==1911
        return hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()
    initial_digest=baseline_digest();active=[r for r in store.list(tools.business_id) if r['status']=='active']
    def load(day,time):
        start=minute(time);duration=restaurant['seating']['duration_minutes']
        intervals=[(minute(r['time']),minute(r['time'])+duration,r['party_size']) for r in store.list(tools.business_id) if r['date']==day and r['status']=='active']
        return {'seats':peak_load(intervals,start,start+duration),
                'tables':peak_load([(a,b,ceil(n/4)) for a,b,n in intervals],start,start+duration)}
    selected=None
    for day in sorted({r['date'] for r in active if r['date']>local_now(restaurant).date().isoformat()}):
        for shift in shifts_for(restaurant,date.fromisoformat(day)).values():
            for start in range(minute(shift['open']),minute(shift['close'])-90+1,30):
                time_slot=clock(start);counts=load(day,time_slot)
                if counts['tables']==9 and counts['seats']<=36 and tools.check_table(4,day,time_slot)['available']:
                    selected=(day,time_slot,counts);break
            if selected:break
        if selected:break
    assert selected,'No baseline slot with exactly one available four-seat table'
    day,time_slot,before=selected
    report={'test_type':'simultaneous_voice_session_API_confirmations','audio_devices_tested':False,
            'date':day,'time':time_slot,'party_size_each':4,'before':before,'baseline_digest_before':initial_digest,
            'api_workers':2,'customers':[],'passed':False}
    customers=[]
    def save(): (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    with httpx.Client(base_url=base_url,timeout=180,trust_env=False) as owner:
        def turn(customer,text):
            request={'request_id':str(uuid4()),'text':text}
            response=owner.post(f'/api/sessions/{customer["session_id"]}/turns',json=request);response.raise_for_status();result=response.json()
            customer['turns'].append({'text':text,'reply':result['reply'],'request_id':request['request_id']})
            return result
        try:
            warm=owner.post('/api/voice/warmup');warm.raise_for_status()
            for name in ['Alex','Jordan']:
                response=owner.post('/api/sessions',json={'channel':'voice'});response.raise_for_status()
                customer={'name':name,'phone':'9900'+str(int(uuid4().hex[:8],16)%1000000).zfill(6),
                          'session_id':response.json()['session_id'],'turns':[]}
                customers.append(customer);report['customers']=customers
                spoken_date=date.fromisoformat(day).strftime('%B %d, %Y')
                assert turn(customer,f'Can I book a table for four on {spoken_date} at {time_slot}?')['reply']=='Do you have a reservation with us?'
                assert 'phone number' in turn(customer,'No.')['reply'].lower()
                assert 'name' in turn(customer,customer['phone'])['reply'].lower()
                reply=turn(customer,name)['reply']
                assert 'Is that correct?' in reply and time_slot in reply,reply
            assert load(day,time_slot)==before,'An unconfirmed draft consumed capacity'
            gate=Barrier(3)
            def confirm(customer):
                payload={'request_id':str(uuid4()),'text':'Yes, please.'}
                with httpx.Client(base_url=base_url,timeout=180,trust_env=False) as client:
                    gate.wait();started=time.perf_counter()
                    response=client.post(f'/api/sessions/{customer["session_id"]}/turns',json=payload);response.raise_for_status()
                    return {'session_id':customer['session_id'],'payload':payload,'response':response.json(),'elapsed_ms':round((time.perf_counter()-started)*1000)}
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures=[pool.submit(confirm,c) for c in customers];gate.wait();outcomes=[f.result() for f in futures]
            report['outcomes']=outcomes
            winners=[r for r in outcomes if r['response']['reply'].startswith('Your table is booked')]
            losers=[r for r in outcomes if 'fully booked' in r['response']['reply'].lower()]
            assert len(winners)==len(losers)==1,outcomes
            assert 'I can offer' in losers[0]['response']['reply'],losers
            owned=[r for r in store.list(tools.business_id) if r.get('session_id') in {c['session_id'] for c in customers}]
            assert len(owned)==1 and owned[0]['session_id']==winners[0]['session_id']
            report['saved_booking']=owned[0];report['after']=load(day,time_slot)
            assert report['after']['tables']==10 and report['after']['seats']<=40
            assert not tools.check_table(4,day,time_slot)['available']
            # Retrying the winning confirmation with the same ID must not book twice.
            win=winners[0];retry=owner.post(f'/api/sessions/{win["session_id"]}/turns',json=win['payload']);retry.raise_for_status()
            assert retry.json()['reply']==win['response']['reply']
            assert len([r for r in store.list(tools.business_id) if r.get('session_id')==win['session_id']])==1
            assert baseline_digest()==initial_digest
            report['checks']={'both_drafts_saw_last_table':True,'simultaneous_confirmation_requests':True,
                'exactly_one_success':True,'loser_received_alternatives':True,'no_overbooking':True,
                'confirmation_retry_idempotent':True,'all_1911_baseline_rows_unchanged':True}
            report['passed']=True
            for result in outcomes:print(result['response']['reply'],flush=True)
        except Exception as error:report['error']=str(error);raise
        finally:
            cleanup=[]
            for row in store.list(tools.business_id):
                if row.get('session_id') in {c['session_id'] for c in customers} and row['status']=='active':
                    changed=tools.change_reservation(row['id'],row['phone'],'cancel',row['version'],{},'last-table-cleanup-'+row['id'])
                    cleanup.append({'id':row['id'],'cancelled':changed.get('changed',False)})
            report['cleanup']=cleanup;report['after_cleanup']=load(day,time_slot)
            report['baseline_digest_after']=baseline_digest()
            for customer in customers:owner.delete(f'/api/sessions/{customer["session_id"]}')
            save()
    assert report['after_cleanup']==before and report['baseline_digest_after']==initial_digest
    assert report['cleanup'] and all(r['cancelled'] for r in report['cleanup'])
    print('PASS: one winner, one alternative offer, capacity respected, retry safe, baseline restored.',flush=True)


if __name__=='__main__':main()
