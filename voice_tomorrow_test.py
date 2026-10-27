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
    parser.add_argument('--when',choices=['tomorrow','next Saturday'],default='tomorrow')
    parser.add_argument('--base-url',default='http://127.0.0.1:8010')
    parser.add_argument('--output',default='results/voice-tomorrow-clock')
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
    report={'endpoint_ms':448,'stt_backend':args.stt_backend,'test_type':'synthetic_audio_end_to_end','microphone_tested':False,'speaker_playback_tested':False,
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
    tools=None
    try:
        from datetime import timedelta
        from receptionist import Tools,load_restaurant,HERE
        from customer_identity import local_now
        restaurant=load_restaurant(HERE/'restaurants/demo_restaurant.yaml');tools=Tools(restaurant)
        arrival=local_now(restaurant)
        offset=1 if args.when=='tomorrow' else ((5-arrival.weekday())%7 or 7)
        expected=(arrival.date()+timedelta(days=offset)).isoformat()
        report['requested_relative_date']=args.when
        report['server_clock_at_start']=arrival.isoformat();report['restaurant_timezone']=restaurant['timezone']
        report['expected_booking_date']=expected;report['expected_booking_time']='20:30'
        assert not tools.booking_store().lookup_by_phone(tools.business_id,'9900112233',arrival.date().isoformat())['found'],'Do not touch existing customer reservations'
        assert tools.check_table(2,expected,'20:30')['available'],'Test requires available requested slot'
        report['warmup']=service.warmup()
        report['model']=report['warmup']['model']
        report['warmup_excluded_from_turn_timings']=True
        speaker.prime([f"Thank you for calling {restaurant['name']}. How can I help you today?",'Do you have a reservation with us?','What phone number can I use for your reservation?','May I have your name for the reservation?'])
        greeting=service.start();report['session_id']=service.session_id;report['greeting']=greeting
        synth(greeting,output/'greeting.wav');save()
        reply=say(f'Hello, can I book a table for two {args.when} at half past eight?','relative_booking_request')
        assert reply=='Do you have a reservation with us?'
        reply=say('No, I do not.','new_reservation_branch')
        assert not any(x in reply.lower() for x in ("what is todays date","what is today",'current time'))
        assert reply=='Let me help you create a new reservation. What phone number can I use for your reservation?',reply
        assert own_booking() is None
        reply=say('Nine nine zero zero one one two two three three.','phone_number_only')
        assert reply.endswith('May I have your name for the reservation?'),reply
        assert own_booking() is None
        reply=say('Alex.','name_only')
        report['checks']['phone_and_name_only_given_when_asked']=True
        assert expected in reply and '20:30' in reply and 'correct' in reply.lower(),reply
        assert own_booking() is None,'Reservation committed before confirmation'
        report['checks']['clock_resolved_relative_date_and_half_past_eight']=True
        report['checks']['waits_for_confirmation']=True
        reply=say('Yes, please.','confirm_booking')
        row=own_booking()
        assert row and row['date']==expected and row['time']=='20:30' and row['party_size']==2 and row['source']=='phone' and row['name']=='Alex'
        report['booking_id']=row['id'];report['saved_booking']=row
        report['checks']['correct_date_time_saved']=True
        assert 'anything else' in reply.lower()
        say("No, thank you. That's all.",'goodbye')
        assert report['turns'][-1]['ended']
        assert all(not any(c.isdigit() for c in t['spoken_text']) for t in report['turns'])
        report['checks']['no_current_date_or_time_supplied_by_caller']=True
        report['checks']['goodbye_ends_call']=True;report['passed']=True
        print('PASS: '+args.when+' at half past eight resolved from server clock to '+expected+' 20:30.',flush=True)
    except Exception as error:
        report['error']=str(error);print('FAIL: '+str(error),flush=True);raise
    finally:
        if tools and service.session_id:
            row=own_booking()
            if row:
                cleanup=tools.change_reservation(row['id'],row['phone'],'cancel',row['version'],{},'tomorrow-cleanup-'+row['id'])
                report['cleanup']={'changed':cleanup.get('changed'),'id':row['id']}
        save();service.close();owner.close();transcriber.close()


if __name__=='__main__':main()
