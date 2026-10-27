"""Synthetic speech through the browser audio endpoints; never captures a microphone."""
import io
import os
from math import ceil
from reservation_schedule import minute, peak_load
import json
import time
import wave
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import numpy as np

from customer_identity import local_now, phone_from_utterance
from receptionist import HERE, Tools, load_restaurant


def main():
    output=HERE/'results/browser-voice/baseline-full-slot-voice-flow'
    output.mkdir(parents=True,exist_ok=True)
    report={'test_type':'synthetic_audio_browser_endpoints','microphone_tested':False,'speaker_playback_tested':False,'turns':[],'passed':False}
    restaurant=load_restaurant(HERE/'restaurants/demo_restaurant.yaml');tools=Tools(restaurant)
    now=local_now(restaurant);store=tools.booking_store()
    baseline=[r for r in store.list(tools.business_id) if (r.get('request_key') or '').startswith('baseline:2026-q4:v1:')]
    before_baseline=json.dumps(baseline,sort_keys=True)
    assert len(baseline)==1911,'Expected the seeded baseline'
    selected=None
    for day in sorted({r['date'] for r in baseline if r['date']>now.date().isoformat()}):
        check=tools.check_table(2,day,'19:00')
        if not check.get('available') and 'fully booked' in check.get('reason','').lower() and check.get('alternatives') and store.seats(tools.business_id,day,'19:00')==40:
            selected=(day,check['alternatives'][0]);break
    assert selected,'No full baseline slot with an available alternative found'
    requested,alternative=selected;expected=alternative['date'];expected_time=alternative['time']
    report.update(requested_date=requested,requested_time='19:00',selected_alternative=alternative,baseline_count=len(baseline))
    phone='9900'+str(int(uuid4().hex[:8],16)%1000000).zfill(6)
    assert not store.lookup_by_phone(tools.business_id,phone,now.date().isoformat())['found']
    def capacity_snapshot():
        active=[r for r in store.list(tools.business_id) if r.get('status')=='active']
        result={}
        for day in {requested,expected}:
            intervals=[(minute(r['time']),minute(r['time'])+90,r['party_size']) for r in active if r['date']==day]
            seats=peak_load(intervals,0,1440)
            tables=peak_load([(a,b,ceil(n/4)) for a,b,n in intervals],0,1440)
            assert seats<=40 and tables<=10,(day,seats,tables)
            result[day]={'peak_reserved_seats':seats,'peak_reserved_tables':tables}
        return result
    report['capacity_before']=capacity_snapshot()
    words=' '.join(['zero','one','two','three','four','five','six','seven','eight','nine'][int(d)] for d in phone)
    sid=None
    def save(): (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    with httpx.Client(base_url=os.getenv('VOICE_TEST_API_URL','http://127.0.0.1:8010'),timeout=180,trust_env=False) as client:
        def rows():
            response=client.get('/api/owner/dashboard');response.raise_for_status()
            return [r for r in response.json()['bookings'] if r.get('session_id')==sid]
        def speech(text,path):
            response=client.post('/api/voice/speak',json={'text':text});response.raise_for_status()
            path.write_bytes(response.content)
            return response.content
        def say(text):
            index=len(report['turns'])+1
            caller=speech(text,output/f'{index:02d}-caller.wav')
            with wave.open(io.BytesIO(caller)) as wav:
                rate=wav.getframerate();samples=np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2')
            pcm=np.interp(np.arange(int(len(samples)*16000/rate))*rate/16000,np.arange(len(samples)),samples).astype('<i2')
            buffer=io.BytesIO()
            with wave.open(buffer,'wb') as wav:
                wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(pcm.tobytes())
            started=time.perf_counter()
            response=client.post('/api/voice/transcribe',content=buffer.getvalue(),headers={'Content-Type':'audio/wav'});response.raise_for_status();transcript=response.json()
            stt_ms=round((time.perf_counter()-started)*1000)
            response=client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':transcript['text'] or '[No intelligible speech detected]','input_kind':transcript['input_kind']});response.raise_for_status();data=response.json()
            speech(data['reply'],output/f'{index:02d}-receptionist.wav')
            report['turns'].append({'spoken_text':text,'transcript':transcript['text'],'reply':data['reply'],'ended':data['ended'],'stt_ms':stt_ms,'send_to_audio_ready_ms':round((time.perf_counter()-started)*1000),'structured':data['structured']});save()
            print(f"{index}. Caller: {transcript['text']}\n   Agent: {data['reply']}",flush=True)
            assert not data.get('failed') and not data.get('handoff')
            if data.get('survey',{}).get('status')=='offered':
                # Exercise the optional survey decline explicitly and keep it in the audio transcript.
                report['survey_declined_at_close']=True
                return say('No thanks.')
            return data
        try:
            response=client.post('/api/voice/warmup');response.raise_for_status()
            response=client.post('/api/sessions',json={'channel':'voice'});response.raise_for_status();session=response.json();sid=session['session_id']
            report.update(session_id=sid,phone=phone,expected_date=expected,expected_time=expected_time,greeting=session['greeting'])
            speech(session['greeting'],output/'greeting.wav')
            spoken_date=__import__('datetime').date.fromisoformat(requested).strftime('%B %d, %Y')
            assert say(f'Can I book a table for two on {spoken_date} at seven in the evening?')['reply']=='Do you have a reservation with us?'
            assert 'phone number' in say('No, I do not.')['reply'].lower()
            reply=say(words+'.')['reply']
            if phone_from_utterance(report['turns'][-1]['transcript'])!=phone:
                assert 'phone number' in reply.lower() and not rows(),'Uncertain phone must be repeated before any booking'
                report['phone_repeated_after_uncertain_transcript']=report['turns'][-1]['transcript']
                reply=say('My phone number is '+', '.join(words.split())+'.')['reply']
            assert phone_from_utterance(report['turns'][-1]['transcript'])==phone,'Transcribed phone digits changed after repetition'
            assert 'name' in reply.lower() and 'phone number' not in reply.lower(),reply
            reply=say('Alex.')['reply']
            assert 'fully booked' in reply.lower() and 'I can offer' in reply,reply
            assert not rows(),'Invalid requested time was saved'
            reply=say('The first option works for me.')['reply']
            assert expected in reply and expected_time in reply and 'Is that correct?' in reply,reply
            assert not rows(),'Booking saved before confirmation'
            reply=say('Yes, please.')['reply'];bookings=rows()
            assert len(bookings)==1,'Missing or duplicate booking'
            booking=bookings[0]
            assert booking['phone'].endswith(phone) and booking['name']=='Alex' and booking['party_size']==2 and booking['date']==expected and booking['time']==expected_time and booking['source']=='phone'
            report['saved_booking']=booking
            report['capacity_after']=capacity_snapshot()
            assert store.seats(tools.business_id,requested,'19:00')==40
            assert json.dumps([r for r in store.list(tools.business_id) if (r.get('request_key') or '').startswith('baseline:2026-q4:v1:')],sort_keys=True)==before_baseline
            assert 'anything else' in reply.lower()
            assert say("No, thank you. That's all.")['ended']
            prompts=[t for t in report['turns'] if 'phone number' in t['reply'].lower()]
            assert len(prompts)==1,'Repeated phone question'
            report['checks']={'phone_asked_once':True,'digits_preserved':True,'name_requested':True,'confirmation_required':True,'exactly_one_booking':True,'goodbye_ends_call':True,'full_baseline_slot_rejected':True,'valid_alternative_suggested':True,'seats_never_above_40':True,'tables_never_above_10':True,'baseline_1911_rows_unchanged':True,'chosen_alternative_read_back':True}
            report['passed']=True
        except Exception as error:
            report['error']=str(error)
            raise
        finally:
            if sid:
                owned=rows()
                for row in owned:
                    if row.get('status','active')=='active':
                        result=tools.change_reservation(row['id'],row['phone'],'cancel',row['version'],{},'browser-voice-cleanup-'+row['id'])
                        report['cleanup']={'id':row['id'],'cancelled':result.get('changed',False)}
                client.delete(f'/api/sessions/{sid}')
            save()
    assert report.get('cleanup',{}).get('cancelled'),'Test booking cleanup failed'
    print('PASS: full baseline slot rejected; alternative confirmed without overbooking; all baseline rows preserved; test booking cleaned up.',flush=True)


if __name__=='__main__':main()
