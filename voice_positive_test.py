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
    parser.add_argument('--output',default='results/voice-positive-experience')
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
            for chunk in speaker.voice.synthesize(text):
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
        transcript=transcriber.transcribe(CapturedAudio(np.concatenate(frames),16000,480))
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
        entry['estimated_end_of_speech_to_synthesized_audio_ms']=480+entry['stt_ms']+entry['api_ms']+entry['tts_first_chunk_ms']
        save()
        assert not result.get('failed') and not result.get('handoff'),'Unexpected failure or staff handoff'
        return result['reply']
    tools=None
    seeded=None
    try:
        from datetime import timedelta
        from uuid import uuid4
        from receptionist import Tools,load_restaurant,HERE
        from customer_identity import local_today
        restaurant=load_restaurant(HERE/'restaurants/demo_restaurant.yaml')
        tools=Tools(restaurant)
        tomorrow=(local_today(restaurant)+timedelta(days=1)).isoformat()
        assert not tools.booking_store().lookup_by_phone(tools.business_id,'9900112233',local_today(restaurant).isoformat())['found'],'Existing active customer booking: do not alter it for a test'
        greeting=service.start();report['session_id']=service.session_id
        tools.session_id=service.session_id;tools.booking_source='phone'
        seeded=tools.book_table('Voice Positive Experience Test','9900112233',2,tomorrow,'20:00',request_key=str(uuid4()))
        assert seeded.get('booked'),seeded
        report['booking_id']=seeded['id'];report['booking_date']=tomorrow
        report['setup']='Seeded two guests tomorrow at 20:00; setup excluded from response timing.'
        report['greeting']=greeting
        assert greeting.startswith('Thank you for calling') and 'How can I help you today?' in greeting
        report['checks']['opening_greeting']=True
        save();synth(greeting,output/'greeting.wav')
        reply=say('Hi, I booked a table for tomorrow, but a couple of friends are joining us. Can you change it?','request_change')
        assert 'phone' in reply.lower(),'Should request phone, not a booking reference'
        reply=say('My number is nine nine zero zero one one two two three three.','phone_lookup')
        assert 'reference' not in reply.lower(),'Must not ask for booking reference'
        assert 'I found your reservation' in reply,'Expected an existing-booking readback before collecting changes'
        assert own_booking()['version']==1
        reply=say('Make it four people, and could we come at half past eight instead?','change_details')
        assert '20:30' in reply and '4 guests' in reply and 'correct' in reply.lower(),'Expected four guests at 20:30 readback'
        assert own_booking()['version']==1,'Update before confirmation'
        report['checks']['waits_for_confirmation']=True
        reply=say('Yes, please.','confirm_update')
        assert reply.endswith('Is there anything else I can help you with?')
        assert not report['turns'][-1]['ended']
        report['checks']['anything_else_after_update']=True
        updated=own_booking()
        assert updated['party_size']==4 and updated['time']=='20:30' and updated['date']==tomorrow and updated['version']==2
        assert updated['id']==seeded['id'] and updated['source']=='phone'
        report['checks']['updated_four_people_2030_same_booking']=True
        history=[e['kind'] for e in owner.get('/api/owner/dashboard').json()['booking_events'] if e['booking_id']==seeded['id']]
        assert history==['created','updated'],history
        report['checks']['audit_history_correct']=True
        reply=say("No, thank you. That's all.",'decline_more_help')
        assert reply.startswith('Thank you so much for calling.') and reply.endswith(('Have a good day!','Good night!'))
        assert report['turns'][-1]['ended'],'Goodbye must end the call'
        report['checks']['polite_goodbye_and_call_ended']=True
        report['passed']=True
        print('PASS: exact positive-experience caller dialogue; four guests at 20:30, phone only.',flush=True)
    except Exception as error:
        report['error']=str(error);print(f'FAIL: {error}',flush=True)
        raise
    finally:
        if tools and seeded and seeded.get('booked'):
            row=tools.lookup_reservation(seeded['id'],'9900112233')['reservation']
            report['status_before_cleanup']=row['status']
            cleanup=tools.change_reservation(row['id'],'9900112233','cancel',row['version'],{},'positive-cleanup-'+row['id'])
            report['cleanup']={'method':'typed tool cancellation of test fixture, excluded from voice flow','changed':cleanup.get('changed')}
        save();service.close();owner.close()
        if hasattr(transcriber,'close'):transcriber.close()
    import statistics
    report['delay_summary_ms']={'median':statistics.median(t['estimated_end_of_speech_to_synthesized_audio_ms'] for t in report['turns']),
        'min':min(t['estimated_end_of_speech_to_synthesized_audio_ms'] for t in report['turns']),
        'max':max(t['estimated_end_of_speech_to_synthesized_audio_ms'] for t in report['turns'])}
    save()
    print(json.dumps(report['delay_summary_ms']),flush=True)


if __name__=='__main__':main()
