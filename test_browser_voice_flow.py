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
    output=HERE/'results/browser-voice/full-digit-phone-flow'
    output.mkdir(parents=True,exist_ok=True)
    report={'test_type':'synthetic_audio_browser_endpoints','microphone_tested':False,'speaker_playback_tested':False,'turns':[],'passed':False}
    restaurant=load_restaurant(HERE/'restaurants/demo_restaurant.yaml');tools=Tools(restaurant)
    now=local_now(restaurant);expected=(now.date()+timedelta(days=(5-now.weekday())%7 or 7)).isoformat()
    phone='9900'+str(int(uuid4().hex[:8],16)%1000000).zfill(6)
    assert not tools.booking_store().lookup_by_phone(tools.business_id,phone,now.date().isoformat())['found']
    assert tools.check_table(2,expected,'20:30')['available']
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
            report.update(session_id=sid,phone=phone,expected_date=expected,expected_time='20:30',greeting=session['greeting'])
            speech(session['greeting'],output/'greeting.wav')
            assert say('Can I book a table for two next Saturday at half past eight?')['reply']=='Do you have a reservation with us?'
            assert 'phone number' in say('No, I do not.')['reply'].lower()
            reply=say(words+'.')['reply']
            assert phone_from_utterance(report['turns'][-1]['transcript'])==phone,'Transcribed phone digits changed'
            assert 'name' in reply.lower() and 'phone number' not in reply.lower(),reply
            reply=say('Alex.')['reply']
            assert expected in reply and '20:30' in reply and 'Is that correct?' in reply,reply
            assert not rows(),'Booking saved before confirmation'
            reply=say('Yes, please.')['reply'];bookings=rows()
            assert len(bookings)==1,'Missing or duplicate booking'
            booking=bookings[0]
            assert booking['phone'].endswith(phone) and booking['name']=='Alex' and booking['party_size']==2 and booking['date']==expected and booking['time']=='20:30' and booking['source']=='phone'
            report['saved_booking']=booking
            assert 'anything else' in reply.lower()
            assert say("No, thank you. That's all.")['ended']
            prompts=[t for t in report['turns'] if 'phone number' in t['reply'].lower()]
            assert len(prompts)==1,'Repeated phone question'
            report['checks']={'phone_asked_once':True,'digits_preserved':True,'name_requested':True,'confirmation_required':True,'exactly_one_booking':True,'goodbye_ends_call':True}
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
    print('PASS: phone asked once; reservation confirmed, verified, and test booking cancelled.',flush=True)


if __name__=='__main__':main()
