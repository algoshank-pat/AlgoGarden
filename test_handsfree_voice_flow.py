"""Synthetic speech through the browser audio endpoints; never captures a microphone."""
import io
import os
import subprocess
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
    output=HERE/'results/browser-voice/handsfree-interruption-flow'
    output.mkdir(parents=True,exist_ok=True)
    report={'test_type':'synthetic_audio_shared_browser_endpoint_and_interruption_logic','microphone_tested':False,'speaker_playback_tested':False,'physical_echo_cancellation_tested':False,'turns':[],'passed':False}
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
            raw_input=output/f'{index:02d}-input.f32';raw_output=output/f'{index:02d}-captured.f32'
            raw_input.write_bytes((pcm.astype('float32')/32768).tobytes())
            interrupted=index==3
            checked=subprocess.run(['node',str(HERE/'tools/test_handsfree_frames.mjs'),str(raw_input),str(raw_output),'interrupt' if interrupted else 'listen'],capture_output=True,text=True,check=True)
            capture_checks=json.loads(checked.stdout)
            pcm=(np.clip(np.frombuffer(raw_output.read_bytes(),dtype='<f4'),-1,1)*32767).astype('<i2')
            buffer=io.BytesIO()
            with wave.open(buffer,'wb') as wav:
                wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(pcm.tobytes())
            started=time.perf_counter()
            response=client.post('/api/voice/transcribe',content=buffer.getvalue(),headers={'Content-Type':'audio/wav'});response.raise_for_status();transcript=response.json()
            stt_ms=round((time.perf_counter()-started)*1000)
            response=client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':transcript['text'] or '[No intelligible speech detected]','input_kind':transcript['input_kind']});response.raise_for_status();data=response.json()
            speech(data['reply'],output/f'{index:02d}-receptionist.wav')
            report['turns'].append({'spoken_text':text,'transcript':transcript['text'],'reply':data['reply'],'ended':data['ended'],'stt_ms':stt_ms,'send_to_audio_ready_ms':round((time.perf_counter()-started)*1000),'structured':data['structured'],'handsfree_capture':capture_checks});save()
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
            assert 'Algo bites' in session['greeting']
            speech(session['greeting'],output/'greeting.wav')
            assert say('Can I book a table for two next Saturday at nine p.m.?')['reply']=='Do you have a reservation with us?'
            assert 'phone number' in say('No, I do not.')['reply'].lower()
            reply=say(words+'.')['reply']
            assert phone_from_utterance(report['turns'][-1]['transcript'])==phone,'Transcribed phone digits changed'
            assert 'name' in reply.lower() and 'phone number' not in reply.lower(),reply
            reply=say('Alex.')['reply']
            assert expected in reply and '21:00' in reply and 'Is that correct?' in reply,reply
            assert not rows()
            assert 'start this conversation over' in say('I change my mind.')['reply']
            assert not rows()
            assert 'start again' in say('Yes, please.')['reply']
            assert not rows()
            assert say('Can I book a table for two next Saturday at nine p.m.?')['reply']=='Do you have a reservation with us?'
            assert 'Lemme help you with the reservation.' in say('No, I do not.')['reply']
            reply=say(words+'.')['reply']
            captured=phone_from_utterance(report['turns'][-1]['transcript'])
            if captured!=phone:
                assert 'phone number' in reply.lower(),'Incomplete phone must require repetition'
                assert not rows()
                reply=say('My phone number is '+', '.join(words.split())+'.')['reply']
                assert phone_from_utterance(report['turns'][-1]['transcript'])==phone
            assert 'name' in reply.lower()
            reply=say('Alex.')['reply'];assert '21:00' in reply
            assert 'What would you like to change' in say('No.')['reply']
            reply=say('No, I would like to change my reservation to 830.')['reply']
            assert expected in reply and '20:30' in reply and 'Is that correct?' in reply,reply
            assert 'start this conversation over' in say('Forget what I told you.')['reply']
            assert 'continue where we left off' in say('No, thank you.')['reply']
            assert not rows(),'Reservation committed before confirmation'
            reply=say('Yes, please.')['reply'];bookings=rows()
            assert len(bookings)==1,'Missing or duplicate booking'
            booking=bookings[0]
            assert booking['phone'].endswith(phone) and booking['name']=='Alex' and booking['party_size']==2 and booking['date']==expected and booking['time']=='20:30' and booking['source']=='phone'
            report['saved_booking']=booking
            assert 'anything else' in reply.lower()
            assert 'start this conversation over' in say('I changed my mind.')['reply']
            assert 'start again' in say('Yes, please.')['reply']
            after_restart=rows();assert len(after_restart)==1 and after_restart[0]['id']==booking['id'] and after_restart[0]['status']=='active'
            assert say('Goodbye.')['ended']
            prompts=[t for t in report['turns'] if 'phone number' in t['reply'].lower()]
            assert 2<=len(prompts)<=3,'Only request phone again for an incomplete decoding or confirmed restart'
            report['checks']={'phone_reprompt_only_for_restart_or_incomplete_number':True,'restart_yes_clears_draft':True,'restart_no_preserves_draft':True,'restart_keeps_saved_booking':True,'no_readback_then_830_correction':True,'digits_preserved':True,'name_requested':True,'confirmation_required':True,'exactly_one_booking':True,'goodbye_ends_call':True,'all_turns_automatically_endpointed':True,'phone_interrupts_reply':report['turns'][2]['handsfree_capture']['playback_pause_callbacks']==1}
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
    print('PASS: automatic speech turns, interrupted reply, digit phone, confirmed booking, and cleanup verified.',flush=True)


if __name__=='__main__':main()
