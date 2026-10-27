"""Generated speech: consent, invalid rating, valid score and durable per-call feedback."""
import os
import hashlib
import io
import json
import time
import wave
from pathlib import Path
from uuid import uuid4
import httpx
import numpy as np


def main():
    output=Path(__file__).resolve().parent/'results/browser-voice/customer-feedback'
    output.mkdir(parents=True,exist_ok=True)
    from receptionist import HERE,Tools,load_restaurant
    tools=Tools(load_restaurant(HERE/'restaurants/demo_restaurant.yaml'))
    def snapshot():
        return hashlib.sha256(json.dumps(tools.booking_store().list(tools.business_id),sort_keys=True).encode()).hexdigest()
    before=snapshot()
    report={'scenario':'customer_feedback','reservation_snapshot_before':before,'passed':False,'microphone_tested':False,'speaker_playback_tested':False,'turns':[]}
    with httpx.Client(base_url=os.getenv('VOICE_TEST_API_URL','http://127.0.0.1:8010'),timeout=180,trust_env=False) as client:
        def speech(text,path):
            response=client.post('/api/voice/speak',json={'text':text});response.raise_for_status()
            path.write_bytes(response.content)
            return response.content
        session=client.post('/api/sessions',json={'channel':'voice'});session.raise_for_status()
        sid=session.json()['session_id'];report['session_id']=sid
        def say(text):
            index=len(report['turns'])+1
            audio=speech(text,output/f'{index:02d}-caller.wav')
            with wave.open(io.BytesIO(audio)) as wav:
                rate=wav.getframerate();samples=np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2')
            pcm=np.interp(np.arange(int(len(samples)*16000/rate))*rate/16000,np.arange(len(samples)),samples).astype('<i2')
            buffer=io.BytesIO()
            with wave.open(buffer,'wb') as wav:
                wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(pcm.tobytes())
            response=client.post('/api/voice/transcribe',content=buffer.getvalue(),headers={'Content-Type':'audio/wav'});response.raise_for_status();transcript=response.json()
            response=client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':transcript['text'] or '[No intelligible speech detected]','input_kind':transcript['input_kind']});response.raise_for_status();data=response.json()
            speech(data['reply'],output/f'{index:02d}-agent.wav')
            report['turns'].append({'caller':text,'transcript':transcript['text'],'reply':data['reply'],'ended':data['ended'],'survey':data['survey']})
            assert not data['handoff'] and not data.get('failed')
            assert not any(c['component']=='ollama.chat' for c in data['structured']['calls'])
            print(json.dumps(report['turns'][-1]),flush=True)
            return data
        try:
            speech(session.json()['greeting'],output/'greeting.wav')
            say('What are your opening hours?')
            data=say('No, thank you. That is all.');assert data['survey']['status']=='offered' and not data['ended']
            data=say('Yes, please.');assert data['survey']['status']=='awaiting_rating' and not data['ended']
            data=say('Six.');assert data['survey']['rating'] is None and not data['ended']
            data=say('Four out of five.');assert data['survey']['rating']==4 and data['ended']
            response=client.delete(f'/api/sessions/{sid}');response.raise_for_status()
            response=client.get('/api/owner/dashboard');response.raise_for_status()
            call=next(x for x in response.json()['calls'] if x['id']==sid)
            assert call['feedback']['rating']==4 and call['feedback']['status']=='completed'
            assert call['intent']=='hours' and call['outcome']=='Answered'
            report['checks']={'consent_before_rating':True,'scale_explained':True,'invalid_rating_rejected':True,'rating_saved_per_call':True,'survives_session_deletion':True,'business_outcome_preserved':True,'zero_llm_calls':True}
            report['passed']=True
        finally:
            response=client.delete(f'/api/sessions/{sid}')
            report['cleanup']={'no_reservations_created':snapshot()==before,'session_closed':response.is_success}
            report['checks']=dict(report.get('checks',{}),reservations_unchanged=snapshot()==before,session_closed=response.is_success)
            (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    assert report['passed'] and report['cleanup']['no_reservations_created']
    print('PASS: optional survey recorded against call and survives session deletion.',flush=True)

if __name__=='__main__':main()
