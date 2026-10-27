"""Interleaved chat and generated-voice survey acceptance tests with durable score isolation."""
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
    output=Path(__file__).resolve().parent/'results/browser-voice/survey-both-channels'
    output.mkdir(parents=True,exist_ok=True)
    from receptionist import HERE,Tools,load_restaurant
    tools=Tools(load_restaurant(HERE/'restaurants/demo_restaurant.yaml'))
    def snapshot():
        return hashlib.sha256(json.dumps(tools.booking_store().list(tools.business_id),sort_keys=True).encode()).hexdigest()
    before=snapshot()
    report={'scenario':'survey_both_channels','reservation_snapshot_before':before,'passed':False,'microphone_tested':False,'speaker_playback_tested':False,'turns':[]}
    with httpx.Client(base_url=os.getenv('VOICE_TEST_API_URL','http://127.0.0.1:8010'),timeout=180,trust_env=False) as client:
        def speech(text,path):
            response=client.post('/api/voice/speak',json={'text':text});response.raise_for_status()
            path.write_bytes(response.content)
            return response.content
        cases=[]
        for channel in ['chat','voice']:
            for scenario,rating in [('accept',2 if channel=='chat' else 5),('decline',None),('invalid_then_valid',3 if channel=='chat' else 4)]:
                response=client.post('/api/sessions',json={'channel':channel});response.raise_for_status()
                data=response.json()
                cases.append({'channel':channel,'scenario':scenario,'expected_rating':rating,'session_id':data['session_id'],'turns':[]})
        report['cases']=cases
        def say(case,text):
            index=len(report['turns'])+1
            heard=text;kind='text'
            if case['channel']=='voice':
                audio=speech(text,output/f'{index:02d}-caller.wav')
                with wave.open(io.BytesIO(audio)) as wav:
                    rate=wav.getframerate();samples=np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2')
                pcm=np.interp(np.arange(int(len(samples)*16000/rate))*rate/16000,np.arange(len(samples)),samples).astype('<i2')
                buffer=io.BytesIO()
                with wave.open(buffer,'wb') as wav:
                    wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(pcm.tobytes())
                response=client.post('/api/voice/transcribe',content=buffer.getvalue(),headers={'Content-Type':'audio/wav'});response.raise_for_status()
                transcript=response.json();heard=transcript['text'];kind=transcript['input_kind']
            payload={'request_id':str(uuid4()),'text':heard,'input_kind':kind}
            response=client.post(f"/api/sessions/{case['session_id']}/turns",json=payload);response.raise_for_status();data=response.json()
            assert not data.get('failed') and not data['handoff']
            assert not any(c['component']=='ollama.chat' for c in data['structured']['calls'])
            if case['channel']=='voice':speech(data['reply'],output/f'{index:02d}-agent.wav')
            entry={'session_id':case['session_id'],'channel':case['channel'],'scenario':case['scenario'],'caller':text,'heard':heard,'reply':data['reply'],'survey':data['survey'],'ended':data['ended']}
            case['turns'].append(entry);report['turns'].append(entry)
            print(json.dumps(entry),flush=True)
            if data['survey']['status']=='completed':
                duplicate=client.post(f"/api/sessions/{case['session_id']}/turns",json=payload);duplicate.raise_for_status();assert duplicate.json()==data
            return data
        try:
            # Interleave six sessions so rating state cannot accidentally be shared.
            for case in cases:
                say(case,'What are your opening hours?')
                data=say(case,'No, thank you. That is all.')
                assert not data['ended'] and data['survey']['status']=='offered'
            for case in cases:
                if case['scenario']=='decline':
                    data=say(case,'No thanks.');assert data['ended'] and data['survey']['rating'] is None
                else:
                    data=say(case,'Yes, please.');assert data['survey']['status']=='awaiting_rating' and not data['ended']
            for case in cases:
                if case['scenario']=='invalid_then_valid':
                    data=say(case,'Six.');assert data['survey']['rating'] is None and not data['ended']
            words={2:'Two',3:'Three',4:'Four',5:'Five'}
            for case in cases:
                if case['scenario']!='decline':
                    data=say(case,words[case['expected_rating']]+' out of five.')
                    assert data['ended'] and data['survey']['rating']==case['expected_rating']
            for case in cases:client.delete(f"/api/sessions/{case['session_id']}").raise_for_status()
            response=client.get('/api/owner/dashboard');response.raise_for_status();rows={r['id']:r for r in response.json()['calls']}
            for case in cases:
                row=rows[case['session_id']]
                assert row['channel']==case['channel'] and row['feedback']['rating']==case['expected_rating']
                assert row['feedback']['status']==('declined' if case['scenario']=='decline' else 'completed')
                assert sum(e['name']=='feedback.rated' for e in row['events'])==(0 if case['scenario']=='decline' else 1)
                assert row['intent']=='hours' and row['outcome']=='Answered'
                case['saved_feedback']=row['feedback'];case['passed']=True
            report['passed']=True
            report['checks']={'both_channels':True,'six_distinct_conversations':True,'consent_before_score':True,'invalid_score_not_saved':True,'corrected_score_saved':True,'declines_unrated':True,'ratings_match_conversation_ids':True,'request_retries_not_double_counted':True,'persists_after_session_deletion':True,'zero_llm_calls':True}
        finally:
            for case in cases:client.delete(f"/api/sessions/{case['session_id']}")
            report['reservations_unchanged']=snapshot()==before
            (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    assert report['passed'] and report['reservations_unchanged']
    print('PASS: all six survey scenarios; scores isolated and durable per conversation.',flush=True)

if __name__=='__main__':main()
