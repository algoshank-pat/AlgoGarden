"""Synthetic caller speech: narrow November availability before checking; cancel the booking fixture."""
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
    output=Path(__file__).resolve().parent/'results/browser-voice/november-narrowing'
    output.mkdir(parents=True,exist_ok=True)
    from receptionist import HERE,Tools,load_restaurant
    tools=Tools(load_restaurant(HERE/'restaurants/demo_restaurant.yaml'))
    def snapshot():
        return hashlib.sha256(json.dumps(tools.booking_store().list(tools.business_id),sort_keys=True).encode()).hexdigest()
    before=snapshot()
    report={'scenario':'november_narrowing','reservation_snapshot_before':before,'passed':False,'microphone_tested':False,'speaker_playback_tested':False,'turns':[]}
    with httpx.Client(base_url=os.getenv('VOICE_TEST_API_URL','http://127.0.0.1:8010'),timeout=180,trust_env=False) as client:
        def speech(text,path):
            response=client.post('/api/voice/speak',json={'text':text});response.raise_for_status()
            path.write_bytes(response.content)
            return response.content
        from datetime import date
        selected=None
        for day in range(1,29):
            candidate=f'2026-11-{day:02d}'
            if tools.check_table(2,candidate,'20:00').get('available'):
                selected=candidate;break
        assert selected
        phone='9900'+str(int(uuid4().hex[:8],16)%1000000).zfill(6)
        words=' '.join(['zero','one','two','three','four','five','six','seven','eight','nine'][int(d)] for d in phone)
        def rows():return [r for r in tools.booking_store().list(tools.business_id) if r['phone']==phone]
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
            response=client.post(f'/api/sessions/{sid}/turns',json={'request_id':str(uuid4()),'text':transcript['text'] or '[No intelligible speech detected]','input_kind':transcript['input_kind']});response.raise_for_status();result=response.json()
            speech(result['reply'],output/f'{index:02d}-agent.wav')
            report['turns'].append({'caller':text,'heard':transcript['text'],'reply':result['reply'],'tools':result['structured']['tools_called']})
            print(json.dumps(report['turns'][-1]),flush=True)
            assert not result['handoff'] and not result.get('failed')
            if result.get('survey',{}).get('status')=='offered':
                return say('No thanks.')
            return result
        try:
            speech(session.json()['greeting'],output/'greeting.wav')
            v=say('Tell me all the openings you have in the month of November.')
            assert v['reply']=='Which date or short date range would you prefer, and around what time?'
            assert not v['structured']['tools_called']
            spoken=date.fromisoformat(selected).strftime('%B %d, %Y')
            say(f'I would like a table for two on {spoken} at eight in the evening.')
            say('No, I do not have a reservation.')
            say(words+'.')
            v=say('Alex.')
            assert 'check_table' in v['structured']['tools_called'] and 'Is that correct?' in v['reply']
            assert not rows()
            say('Confirm.')
            booked=rows();assert len(booked)==1 and booked[0]['date']==selected and booked[0]['time']=='20:00'
            say('Goodbye.')
            report['checks']={'broad_request_asks_date_and_time':True,'no_tools_before_narrowing':True,'availability_after_narrowing':True,'confirmation_required':True,'correct_date_time_saved':True,'caller_and_reply_audio_generated':True}
            report['passed']=True
        finally:
            report['cleanup']=[]
            for row in rows():
                result=tools.change_reservation(row['id'],row['phone'],'cancel',row['version'],{},'narrow-cleanup-'+row['id'])
                report['cleanup'].append({'id':row['id'],'cancelled':result.get('changed',False)})
            client.delete(f'/api/sessions/{sid}')
            (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    assert report['passed'] and all(x['cancelled'] for x in report['cleanup'])
    print('PASS: November narrowing through synthetic speech; test booking cancelled.',flush=True)

if __name__=='__main__':main()
