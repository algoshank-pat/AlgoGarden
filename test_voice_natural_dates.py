"""Synthetic speech through the browser audio endpoints; never captures a microphone."""
import io
import os
import re
from voice.speech_text import natural_speech
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

def natural_audio_matches(spoken,heard,expected_date):
    """Require the actual offered period(s), including valid afternoon slots."""
    periods=set(re.findall(r'\b(morning|afternoon|evening|night|noon|midnight)\b',spoken.lower()))
    month=__import__('datetime').date.fromisoformat(expected_date).strftime('%B').lower()
    return bool(heard.strip() and periods and month in heard.lower() and
                all(re.search(r'\b'+period+r'\b',heard,re.I) for period in periods))


def main():
    output=HERE/'results/browser-voice/natural-speech-booking-update-flow'
    output.mkdir(parents=True,exist_ok=True)
    report={'test_type':'synthetic_audio_browser_endpoints','microphone_tested':False,'speaker_playback_tested':False,'turns':[],'passed':False}
    restaurant=load_restaurant(HERE/'restaurants/demo_restaurant.yaml');tools=Tools(restaurant)
    now=local_now(restaurant);selected=None
    baseline_before=json.dumps([r for r in tools.booking_store().list(tools.business_id) if (r.get('request_key') or '').startswith('baseline:2026-q4:v1:')],sort_keys=True)
    for offset in range(1,40):
        candidate=(now.date()+timedelta(days=offset)).isoformat()
        early=tools.check_table(2,candidate,'17:00');late=tools.check_table(2,candidate,'22:00')
        if early.get('alternatives') and late.get('alternatives') and early['alternatives'][0]['date']==candidate and late['alternatives'][0]['date']==candidate:
            selected=(candidate,early['alternatives'][0],late['alternatives'][0]);break
    assert selected
    expected,first_alternative,update_alternative=selected
    phone='9900'+str(int(uuid4().hex[:8],16)%1000000).zfill(6)
    assert not tools.booking_store().lookup_by_phone(tools.business_id,phone,now.date().isoformat())['found']
    report.update(booking_alternative=first_alternative,update_alternative=update_alternative)
    words=' '.join(['zero','one','two','three','four','five','six','seven','eight','nine'][int(d)] for d in phone)
    sid=None
    def save(): (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    with httpx.Client(base_url=os.getenv('VOICE_TEST_API_URL','http://127.0.0.1:8010'),timeout=180,trust_env=False) as client:
        def rows():
            return [r for r in tools.booking_store().list(tools.business_id) if r['phone']==phone]
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
            spoken=natural_speech(data['reply'])
            assert not re.search(r'\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}:\d{2}\b',spoken),spoken
            if re.search(r'\b\d{4}-\d{2}-\d{2}\b|(?:October|November|December) \d',data['reply']):
                assert 'twenty twenty-six' in spoken
            report.setdefault('speech_checks',[]).append({'raw_reply':data['reply'],'piper_text':spoken,'wav':f'{index:02d}-receptionist.wav'})
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
            report.update(session_id=sid,phone=phone,expected_date=expected,expected_time='18:00',greeting=session['greeting'])
            speech(session['greeting'],output/'greeting.wav')
            spoken_date=__import__('datetime').date.fromisoformat(expected).strftime('%B %d, %Y')
            assert say(f'Can I book a table for two on {spoken_date} at five in the afternoon?')['reply']=='Do you have a reservation with us?'
            say('No, I do not.');assert 'name' in say(words+'.')['reply'].lower()
            reply=say('Alex.')['reply'];assert 'I can offer' in reply and not rows()
            reply=say('The first option works for me.')['reply']
            assert first_alternative['time'] in reply and 'Is that correct?' in reply and not rows()
            say('Yes, please.');booking=rows()[0]
            assert booking['time']==first_alternative['time'] and booking['version']==1
            report['saved_booking']=booking
            say('Goodbye.')
            client.delete(f'/api/sessions/{sid}')
            response=client.post('/api/sessions',json={'channel':'voice'});response.raise_for_status();sid=response.json()['session_id']
            speech(response.json()['greeting'],output/'update-greeting.wav')
            say('I would like to update my reservation.');say(words+'.')
            reply=say('Could we move it to ten at night?')['reply']
            assert 'I can offer' in reply and 'outside reservation hours' in reply
            assert rows()[0]['version']==1
            reply=say('The first option works for me.')['reply']
            assert update_alternative['time'] in reply and 'Is that correct?' in reply
            assert rows()[0]['version']==1
            say('Yes, please.');updated=rows()[0]
            assert updated['id']==booking['id'] and updated['time']==update_alternative['time'] and updated['version']==2
            report['updated_booking']=updated
            assert say('Goodbye.')['ended']
            assert json.dumps([r for r in tools.booking_store().list(tools.business_id) if (r.get('request_key') or '').startswith('baseline:2026-q4:v1:')],sort_keys=True)==baseline_before
            report['checks']={'booking_readback_natural':True,'booking_confirmation_natural':True,'booking_alternatives_natural':True,'update_alternatives_natural':True,'update_readback_natural':True,'update_confirmation_natural':True,'canonical_database_values_preserved':True,'confirmation_required':True,'baseline_unchanged':True}
            roundtrip=[]
            for item in report['speech_checks']:
                raw=item['raw_reply']
                if not ('I can offer' in raw or raw.startswith(('Table for','Your table is booked','Change ','Your reservation is updated'))):continue
                with wave.open(str(output/item['wav'])) as wav:
                    rate=wav.getframerate();samples=np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2')
                pcm=np.interp(np.arange(int(len(samples)*16000/rate))*rate/16000,np.arange(len(samples)),samples).astype('<i2')
                buffer=io.BytesIO()
                with wave.open(buffer,'wb') as wav:
                    wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(pcm.tobytes())
                response=client.post('/api/voice/transcribe',content=buffer.getvalue(),headers={'Content-Type':'audio/wav'});response.raise_for_status()
                heard=response.json()['text']
                valid=natural_audio_matches(item['piper_text'],heard,expected)
                roundtrip.append({'wav':item['wav'],'audio_transcript':heard,'natural_date_and_period_heard':valid})
            report['synthesized_audio_roundtrip']=roundtrip
            report['audio_roundtrip_passed']=len(roundtrip)==6 and all(item['natural_date_and_period_heard'] for item in roundtrip)
            assert report['audio_roundtrip_passed'],roundtrip
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
    print('PASS: booking and update readbacks, alternatives and confirmations use natural speech; fixture cancelled.',flush=True)


if __name__=='__main__':main()
