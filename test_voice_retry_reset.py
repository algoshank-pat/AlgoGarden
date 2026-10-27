"""Synthetic audio regression for the live voice retry/reset policy; no microphone or bookings."""
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
    output=Path(__file__).resolve().parent/'results/browser-voice/retry-reset'
    output.mkdir(parents=True,exist_ok=True)
    from receptionist import HERE,Tools,load_restaurant
    tools=Tools(load_restaurant(HERE/'restaurants/demo_restaurant.yaml'))
    def snapshot():
        return hashlib.sha256(json.dumps(tools.booking_store().list(tools.business_id),sort_keys=True).encode()).hexdigest()
    before=snapshot()
    report={'scenario':'retry_reset','reservation_snapshot_before':before,'passed':False,'microphone_tested':False,'speaker_playback_tested':False,'turns':[]}
    with httpx.Client(base_url=os.getenv('VOICE_TEST_API_URL','http://127.0.0.1:8010'),timeout=180,trust_env=False) as client:
        def speech(text,path):
            response=client.post('/api/voice/speak',json={'text':text});response.raise_for_status()
            path.write_bytes(response.content)
            return response.content
        session=client.post('/api/sessions',json={'channel':'voice'});session.raise_for_status()
        data=session.json();sid=data['session_id'];report['session_id']=sid
        try:
            speech(data['greeting'],output/'greeting.wav')
            for index,text in enumerate(['I would like to']*3+['What are your opening hours?']+['I would like to']*4,1):
                started=time.perf_counter()
                audio=speech(text,output/f'{index:02d}-caller.wav')
                with wave.open(io.BytesIO(audio)) as wav:
                    rate=wav.getframerate();samples=np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2')
                pcm=np.interp(np.arange(int(len(samples)*16000/rate))*rate/16000,np.arange(len(samples)),samples).astype('<i2')
                buffer=io.BytesIO()
                with wave.open(buffer,'wb') as wav:
                    wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(pcm.tobytes())
                response=client.post('/api/voice/transcribe',content=buffer.getvalue(),headers={'Content-Type':'audio/wav'});response.raise_for_status();transcript=response.json()
                payload={'request_id':str(uuid4()),'text':transcript['text'] or '[No intelligible speech detected]','input_kind':transcript['input_kind']}
                response=client.post(f'/api/sessions/{sid}/turns',json=payload);response.raise_for_status();result=response.json()
                speech(result['reply'],output/f'{index:02d}-agent.wav')
                retry=client.post(f'/api/sessions/{sid}/turns',json=payload);retry.raise_for_status();assert retry.json()==result
                item={'attempt':index,'caller':text,'transcript':transcript,'reply':result['reply'],'handoff':result['handoff'],'ended':result['ended'],'elapsed_seconds':round(time.perf_counter()-started,2)}
                report['turns'].append(item)
                print(json.dumps(item),flush=True)
                assert result['handoff']==(index==8) and result['ended']==(index==8),item
                if index==4:assert 'hours' in result['reply'].lower(),item
                elif index<8:assert result['reply']=="I didn't get that. Can you please repeat?",item
            report['passed']=True
            report['checks']={'three_failures_no_handoff':True,'valid_answer_resets_counter':True,'next_three_failures_no_handoff':True,'fourth_consecutive_failure_handoff':True,'request_retries_do_not_increment':True,'reply_audio_generated':True}
        finally:
            response=client.delete(f'/api/sessions/{sid}')
            report['cleanup']={'no_reservations_created':snapshot()==before,'session_closed':response.is_success}
            report['checks']=dict(report.get('checks',{}),reservations_unchanged=snapshot()==before,session_closed=response.is_success)
            (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('PASS: synthetic audio retry/reset flow',flush=True)

if __name__=='__main__':main()
