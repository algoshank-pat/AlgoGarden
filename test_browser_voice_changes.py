"""Synthetic speech through the browser audio endpoints; never captures a microphone."""
import io
import os
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
    output=HERE/'results/browser-voice/update-cancel-digit-phone-flow'
    output.mkdir(parents=True,exist_ok=True)
    report={'test_type':'synthetic_audio_browser_endpoints','microphone_tested':False,'speaker_playback_tested':False,'turns':[],'passed':False}
    restaurant=load_restaurant(HERE/'restaurants/demo_restaurant.yaml');tools=Tools(restaurant)
    now=local_now(restaurant);expected=(now.date()+timedelta(days=(5-now.weekday())%7 or 7)).isoformat()
    phone='9900'+str(int(uuid4().hex[:8],16)%1000000).zfill(6)
    assert not tools.booking_store().lookup_by_phone(tools.business_id,phone,now.date().isoformat())['found']
    assert tools.check_table(2,expected,'20:30')['available']
    words=' '.join(['zero','one','two','three','four','five','six','seven','eight','nine'][int(d)] for d in phone)
    sid=None; booking_id=None
    def save(): (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    with httpx.Client(base_url=os.getenv('VOICE_TEST_API_URL','http://127.0.0.1:8010'),timeout=180,trust_env=False) as client:
        def row():
            result=tools.lookup_reservation(booking_id,phone)
            assert result.get('found'),result
            return result['reservation']
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
        def start_session():
            nonlocal sid
            if sid:client.delete(f'/api/sessions/{sid}')
            response=client.post('/api/sessions',json={'channel':'voice'});response.raise_for_status();data=response.json();sid=data['session_id']
            speech(data['greeting'],output/f'greeting-{len(report["turns"])}.wav')
            return sid
        try:
            response=client.post('/api/voice/warmup');response.raise_for_status()
            tools.booking_source='phone'
            seeded=tools.book_table('Voice Update Cancel Test',phone,2,expected,'20:00',request_key=str(uuid4()))
            assert seeded.get('booked'),seeded
            booking_id=seeded['id'];report.update(booking_id=booking_id,phone=phone,expected_date=expected)
            assert tools.check_table(4,expected,'20:30')['available'],'Updated slot unavailable'
            report['update_session']=start_session()
            reply=say('I would like to update my reservation.')['reply']
            assert 'phone number' in reply.lower(),reply
            reply=say(words+'.')['reply']
            assert phone_from_utterance(report['turns'][-1]['transcript'])==phone
            assert 'phone number' not in reply.lower() and 'reference' not in reply.lower(),reply
            reply=say('Make it four people at half past eight instead.')['reply']
            assert '4 guests' in reply and '20:30' in reply and 'correct' in reply.lower(),reply
            assert row()['version']==1,'Updated before confirmation'
            reply=say('Yes, please.')['reply']
            updated=row();assert updated['id']==booking_id and updated['version']==2 and updated['party_size']==4 and updated['time']=='20:30'
            report['updated_booking']=updated
            assert 'anything else' in reply.lower()
            assert say("No, thank you. That's all.")['ended']
            split=len(report['turns'])
            report['cancel_session']=start_session()
            reply=say('I need to cancel my reservation.')['reply']
            assert 'phone number' in reply.lower(),reply
            reply=say(words+'.')['reply']
            assert phone_from_utterance(report['turns'][-1]['transcript'])==phone
            assert 'confirm' in reply.lower() and 'cancel' in reply.lower(),reply
            assert row()['status']=='active' and row()['version']==2,'Cancelled before confirmation'
            reply=say('Yes, please.')['reply']
            cancelled=row();assert cancelled['id']==booking_id and cancelled['status']=='cancelled' and cancelled['version']==3
            report['cancelled_booking']=cancelled
            assert 'cancelled' in reply.lower() and 'anything else' in reply.lower()
            assert say("No, thank you. That's all.")['ended']
            for turns in [report['turns'][:split],report['turns'][split:]]:
                assert sum('phone number' in turn['reply'].lower() for turn in turns)==1,'Repeated phone request'
                assert not any('reference' in turn['reply'].lower() for turn in turns),'Reference requested'
                assert any('lookup_reservations_by_phone' in turn['structured']['tools_called'] for turn in turns),'Phone lookup tool missing'
            response=client.get('/api/owner/dashboard');response.raise_for_status()
            history=[e['kind'] for e in response.json()['booking_events'] if e['booking_id']==booking_id]
            assert history==['created','updated','cancelled'],history
            matching=[r for r in response.json()['bookings'] if r['phone'].endswith(phone)]
            assert len(matching)==1,'Duplicate booking created'
            report['audit_history']=history
            report['checks']={'phone_asked_once_per_session':True,'digits_preserved':True,'no_reference_requested':True,'phone_lookup_tool_called':True,'update_waits_for_confirmation':True,'cancel_waits_for_confirmation':True,'same_booking_updated_and_cancelled':True,'no_duplicates':True,'audit_history_correct':True,'goodbyes_end_calls':True}
            report['passed']=True
        except Exception as error:
            report['error']=str(error)
            raise
        finally:
            if booking_id:
                current=row()
                if current['status']=='active':
                    result=tools.change_reservation(booking_id,phone,'cancel',current['version'],{},'voice-change-cleanup-'+booking_id)
                    report['cleanup']={'id':booking_id,'cancelled':result.get('changed',False),'via':'fixture cleanup after failure'}
                else:report['cleanup']={'id':booking_id,'cancelled':True,'via':'tested voice cancellation'}
            if sid:client.delete(f'/api/sessions/{sid}')
            save()
    assert report.get('cleanup',{}).get('cancelled'),'Test booking cleanup failed'
    print('PASS: phone-only voice update and cancellation; database versions and audit verified.',flush=True)


if __name__=='__main__':main()
