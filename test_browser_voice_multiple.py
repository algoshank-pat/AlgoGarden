"""Synthetic speech through the browser audio endpoints; never captures a microphone."""
import io
import re
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
    output=HERE/'results/browser-voice/multiple-reservations-voice-flow'
    output.mkdir(parents=True,exist_ok=True)
    report={'test_type':'synthetic_audio_browser_endpoints','microphone_tested':False,'speaker_playback_tested':False,'turns':[],'passed':False}
    restaurant=load_restaurant(HERE/'restaurants/demo_restaurant.yaml');tools=Tools(restaurant)
    now=local_now(restaurant);expected=(now.date()+timedelta(days=(5-now.weekday())%7 or 7)).isoformat()
    phone='9900'+str(int(uuid4().hex[:8],16)%1000000).zfill(6)
    assert not tools.booking_store().lookup_by_phone(tools.business_id,phone,now.date().isoformat())['found']
    assert tools.check_table(2,expected,'20:30')['available']
    words=' '.join(['zero','one','two','three','four','five','six','seven','eight','nine'][int(d)] for d in phone)
    sid=None; booking_id=None; fixture_ids=[]
    def save(): (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    with httpx.Client(base_url=os.getenv('VOICE_TEST_API_URL','http://127.0.0.1:8010'),timeout=180,trust_env=False) as client:
        def row(reference=None):
            result=tools.lookup_reservation(reference or booking_id,phone)
            assert result.get('found'),result
            return result['reservation']
        def speech(text,path):
            response=client.post('/api/voice/speak',json={'text':text});response.raise_for_status()
            path.write_bytes(response.content)
            return response.content
        def say(text):
            index=len(report['turns'])+1
            replay=os.getenv('VOICE_TEST_SELECTION_AUDIO') if text=='The first one, please.' else None
            if replay:
                caller=Path(replay).read_bytes()
                (output/f'{index:02d}-caller.wav').write_bytes(caller)
                report['selection_audio_fixture']=str(Path(replay).resolve())
            else:caller=speech(text,output/f'{index:02d}-caller.wav')
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
            for day in [expected,(now.date()+timedelta(days=((5-now.weekday())%7 or 7)+1)).isoformat()]:
                seeded=tools.book_table('Voice Multiple Reservation Test',phone,2,day,'20:00',request_key=str(uuid4()))
                assert seeded.get('booked'),seeded
                fixture_ids.append(seeded['id'])
            booking_id=fixture_ids[0];other_id=fixture_ids[1]
            report.update(booking_ids=fixture_ids,phone=phone,expected_date=expected)
            assert tools.check_table(4,expected,'20:30')['available']
            report['update_session']=start_session()
            assert 'phone number' in say('I would like to update my reservation.')['reply'].lower()
            reply=say(words+'.')['reply']
            assert 'multiple reservations' in reply.lower() and 'which reservation' in reply.lower(),reply
            assert row()['version']==row(other_id)['version']==1
            reply=say('Yes, please.')['reply']
            assert 'which reservation' in reply.lower(),reply
            assert row()['version']==row(other_id)['version']==1,'Ambiguous yes changed a booking'
            say('The first one, please.')
            reply=say('Make it four people at half past eight instead.')['reply']
            assert '4 guests' in reply and '20:30' in reply and 'correct' in reply.lower(),reply
            assert row()['version']==row(other_id)['version']==1
            say('Yes, please.')
            updated=row();assert updated['version']==2 and updated['party_size']==4 and updated['time']=='20:30'
            assert row(other_id)['version']==1 and row(other_id)['status']=='active'
            report['updated_booking']=updated
            assert say("No, thank you. That's all.")['ended']
            split=len(report['turns'])
            report['cancel_session']=start_session()
            assert 'phone number' in say('I need to cancel my reservation.')['reply'].lower()
            reply=say(words+'.')['reply']
            assert 'multiple reservations' in reply.lower() and 'which reservation' in reply.lower(),reply
            reply=say('Yes, please.')['reply']
            assert 'which reservation' in reply.lower(),reply
            assert row()['status']==row(other_id)['status']=='active'
            reply=say('The second one, please.')['reply']
            assert 'cancel' in reply.lower() and 'confirm' in reply.lower(),reply
            assert row(other_id)['status']=='active','Cancelled before confirmation'
            say('Yes, please.')
            if row(other_id)['status']=='active':
                # STT can append words to short generated clips. Do not relax
                # consent: verify no write, then supply a fresh explicit confirmation.
                heard=re.sub(r'[^a-z ]','',report['turns'][-1]['transcript'].lower()).strip()
                assert heard not in {'yes','yes please'},'An understood confirmation did not commit'
                assert row(other_id)['version']==1
                report['confirmation_repeated_after_uncertain_transcript']=heard
                say('Confirm.')
            assert row(other_id)['status']=='cancelled' and row(other_id)['version']==2
            assert row()['status']=='active' and row()['version']==2 and row()['party_size']==4,'Wrong reservation changed'
            report['cancelled_booking']=row(other_id)
            assert say("No, thank you. That's all.")['ended']
            for turns in [report['turns'][:split],report['turns'][split:]]:
                assert sum('phone number' in turn['reply'].lower() for turn in turns)==1
                assert not any('reference' in turn['reply'].lower() for turn in turns)
                assert any('lookup_reservations_by_phone' in turn['structured']['tools_called'] for turn in turns)
            response=client.get('/api/owner/dashboard');response.raise_for_status();data=response.json()
            histories={key:[e['kind'] for e in data['booking_events'] if e['booking_id']==key] for key in fixture_ids}
            assert histories[booking_id]==['created','updated'] and histories[other_id]==['created','cancelled'],histories
            assert len([r for r in data['bookings'] if r['phone'].endswith(phone)])==2
            report['audit_history_before_cleanup']=histories
            report['checks']={'multiple_choices_offered_for_update_and_cancel':True,'ambiguous_yes_cannot_select_or_confirm':True,'phone_asked_once_per_session':True,'no_reference_requested':True,'first_booking_only_updated':True,'second_booking_only_cancelled':True,'confirmation_required_for_both':True,'no_duplicates':True,'audit_history_correct':True}
            report['passed']=True
        except Exception as error:
            report['error']=str(error)
            raise
        finally:
            cleanup=[]
            for reference in fixture_ids:
                current=row(reference)
                if current['status']=='active':
                    result=tools.change_reservation(reference,phone,'cancel',current['version'],{},'voice-multiple-cleanup-'+reference)
                    cleanup.append({'id':reference,'cancelled':result.get('changed',False)})
                else:cleanup.append({'id':reference,'cancelled':True})
            report['cleanup']=cleanup
            if sid:client.delete(f'/api/sessions/{sid}')
            save()
    assert len(report['cleanup'])==2 and all(r['cancelled'] for r in report['cleanup']),'Test cleanup failed'
    print('PASS: multiple reservations require selection; only selected bookings updated/cancelled; fixtures cleaned up.',flush=True)


if __name__=='__main__':main()
