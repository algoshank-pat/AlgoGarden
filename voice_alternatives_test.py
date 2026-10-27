"""Exact returning-customer positive experience and response-delay measurement.

Uses Piper WAV -> Silero/endpointing -> Parakeet -> API/Ollama -> Piper WAV.
Does not capture a microphone or open a speaker. All test audio is synthetic.
"""
import argparse
import json
import re
import time
import wave
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://127.0.0.1:8010')
    parser.add_argument('--output',default='results/voice-alternatives')
    parser.add_argument('--stt-backend', choices=['parakeet','whisper'], default='parakeet')
    parser.add_argument('--use-reference',action='store_true',help='Exercise optional legacy reference lookup instead of phone-only MCP lookup.')
    parser.add_argument('--combined-contact',action='store_true',help='Speak reference and phone in the same long utterance.')
    args=parser.parse_args()
    import numpy as np
    import torch
    import httpx
    from faster_whisper.audio import decode_audio
    from silero_vad import load_silero_vad
    from voice.adapters import WhisperTranscriber,ParakeetTranscriber,PiperSpeaker
    from voice.segmentation import SpeechSegmenter
    from voice.interfaces import CapturedAudio
    from voice.transport import VoiceService
    root=Path(__file__).parent
    output=root/args.output
    output.mkdir(parents=True,exist_ok=True)
    torch.set_num_threads(1)
    print(f'Loading Piper, Silero and {args.stt_backend} (real local models).',flush=True)
    speaker=PiperSpeaker(root/'models/piper/en_US-lessac-medium.onnx')
    transcriber=ParakeetTranscriber(root) if args.stt_backend=='parakeet' else WhisperTranscriber('small',root/'models/whisper')
    detector=load_silero_vad(onnx=True)
    service=VoiceService(args.base_url)
    owner=httpx.Client(base_url=args.base_url,timeout=30)
    report={'stt_backend':args.stt_backend,'test_type':'synthetic_audio_end_to_end','microphone_tested':False,'speaker_playback_tested':False,
            'same_piper_voice_for_caller_and_receptionist':True,'turns':[],'checks':{},'passed':False}
    def save():
        (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    def synth(text,path):
        started=time.perf_counter();first=None
        with wave.open(str(path),'wb') as wav:
            for chunk in speaker.chunks(text):
                if first is None:
                    first=(time.perf_counter()-started)*1000
                    wav.setnchannels(chunk.sample_channels);wav.setsampwidth(2);wav.setframerate(chunk.sample_rate)
                wav.writeframes(chunk.audio_int16_bytes)
        assert path.stat().st_size>44,'No synthesized response audio'
        return {'tts_first_chunk_ms':round(first or 0),'tts_total_ms':round((time.perf_counter()-started)*1000)}
    def own_booking():
        response=owner.get('/api/owner/dashboard');response.raise_for_status()
        rows=[b for b in response.json()['bookings'] if b.get('session_id')==service.session_id]
        assert len(rows)<=1,'Voice session created duplicate reservations'
        return rows[0] if rows else None
    def say(text,label):
        index=len(report['turns'])+1
        entry={'step':label,'spoken_text':text,'input_wav':f'{index:02d}-caller.wav'}
        report['turns'].append(entry);save()
        print(f'[{label}] Synthesizing caller: {text}',flush=True)
        synth(text,output/entry['input_wav'])
        audio=decode_audio(str(output/entry['input_wav']),sampling_rate=16000)
        audio=np.concatenate([np.zeros(512*10,dtype=np.float32),audio,np.zeros(512*25,dtype=np.float32)])
        detector.reset_states();segmenter=SpeechSegmenter();frames=None;positive=0
        for start in range(0,len(audio)-511,512):
            frame=audio[start:start+512]
            speech=float(detector(torch.from_numpy(frame),16000))>=.5
            positive+=speech
            frames=segmenter.feed(frame,speech)
            if frames is not None:
                break
        assert frames is not None,'No complete speech segment detected'
        entry['vad_speech_frames']=positive
        entry['captured_seconds']=round(len(frames)*512/16000,2)
        started=time.perf_counter()
        transcript=transcriber.transcribe(CapturedAudio(np.concatenate(frames),16000,448))
        entry['transcript']=transcript
        entry['stt_ms']=round((time.perf_counter()-started)*1000)
        print(f'[{label}] {args.stt_backend}: {transcript}',flush=True)
        save();started=time.perf_counter()
        result=service.turn(transcript)
        entry['api_ms']=round((time.perf_counter()-started)*1000)
        entry['reply']=result['reply'];entry['request_id']=result['request_id']
        entry['handoff']=result.get('handoff',False)
        entry['ended']=result.get('ended',False)
        entry['response_wav']=f'{index:02d}-receptionist.wav'
        print(f"[{label}] Receptionist: {result['reply']}",flush=True)
        entry.update(synth(result['reply'],output/entry['response_wav']))
        entry['estimated_end_of_speech_to_synthesized_audio_ms']=448+entry['stt_ms']+entry['api_ms']+entry['tts_first_chunk_ms']
        save()
        assert not result.get('failed') and not result.get('handoff'),'Unexpected failure or staff handoff'
        return result['reply']
    tools=None;fixtures=[]
    try:
        from datetime import timedelta
        from uuid import uuid4
        from receptionist import Tools,load_restaurant,HERE
        from customer_identity import local_today
        restaurant=load_restaurant(HERE/'restaurants/demo_restaurant.yaml');tools=Tools(restaurant)
        day=local_today(restaurant)+timedelta(days=7)
        assert not any(b['status']=='active' and b['date'] in {day.isoformat(),(day+timedelta(days=1)).isoformat(),(day+timedelta(days=2)).isoformat()} for b in tools.booking_store().list(tools.business_id)),'Choose an empty fixture date'
        for i in range(10):
            row=tools.book_table(f'Voice Capacity Fixture {i}',str(9880055600+i),4,day.isoformat(),'20:00',request_key=str(uuid4()))
            assert row.get('booked'),row
            fixtures.append(row)
        unavailable=tools.check_table(2,day.isoformat(),'20:00')
        assert not unavailable['available'] and unavailable['alternatives']
        report['requested_date']=day.isoformat();report['expected_alternatives']=unavailable['alternatives']
        greeting=service.start();report['session_id']=service.session_id;report['greeting']=greeting
        synth(greeting,output/'greeting.wav');save()
        spoken_day=day.strftime('%B')+' '+str(day.day)+', '+str(day.year)
        reply=say(f'Hello, can I book a table for two on {spoken_day} at eight in the evening?','request_full_slot')
        assert reply=='Do you have a reservation with us?'
        reply=say('No, I do not.','new_reservation_branch')
        assert reply=='What phone number can I use for your reservation?'
        reply=say('Nine nine zero zero one one two two three three.','phone_only')
        assert reply.endswith('May I have your name for the reservation?')
        reply=say('Alex.','name_only')
        if 'what name' in reply.lower():
            reply=say('My name is Alex Patel.', 'clarify_name')
        if 'what date' in reply.lower():
            reply=say(f'The date is {spoken_day}, at eight p.m.', 'clarify_date')
        assert 'Would any of these work for you?' in reply and ('fully booked' in reply.lower() or 'unavailable' in reply.lower()),reply
        assert own_booking() is None
        report['checks']['full_slot_rejected_and_alternatives_offered']=True
        reply=say('The first option works for me.','choose_alternative')
        chosen=unavailable['alternatives'][0]
        assert chosen['date'] in reply and chosen['time'] in reply and 'correct' in reply.lower(),reply
        assert own_booking() is None
        report['checks']['selection_readback_before_write']=True
        reply=say('Yes, please.','confirm_alternative')
        row=own_booking()
        assert row and row['status']=='active' and row['date']==chosen['date'] and row['time']==chosen['time'] and row['party_size']==2 and row['source']=='phone'
        fixtures.append(row)
        report['booking_id']=row['id'];report['checks']['confirmed_selected_slot_saved']=True
        assert 'anything else' in reply.lower()
        say("No, thank you. That's all.",'goodbye')
        assert report['turns'][-1]['ended']
        report['checks']['goodbye_ends_call']=True;report['passed']=True
        print('PASS: unavailable slot, real alternatives, first-option selection, confirmed booking and goodbye.',flush=True)
    except Exception as error:
        report['error']=str(error);print(f'FAIL: {error}',flush=True);raise
    finally:
        if tools:
            row=own_booking() if service.session_id else None
            if row and row['id'] not in {f['id'] for f in fixtures}:fixtures.append(row)
            report['cleanup']=[]
            for fixture in fixtures:
                row=tools.lookup_reservation(fixture['id'],fixture['phone'])['reservation']
                result=tools.change_reservation(row['id'],row['phone'],'cancel',row['version'],{},'alternatives-cleanup-'+row['id'])
                report['cleanup'].append({'id':row['id'],'changed':result.get('changed')})
        save();service.close();owner.close();transcriber.close()


if __name__=='__main__':main()
