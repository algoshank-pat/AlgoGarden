"""Real-model synthetic voice acceptance flow against the local API.

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
    parser.add_argument('--output',default='results/voice-flow')
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
        entry['response_wav']=f'{index:02d}-receptionist.wav'
        print(f"[{label}] Receptionist: {result['reply']}",flush=True)
        entry.update(synth(result['reply'],output/entry['response_wav']))
        entry['estimated_end_of_speech_to_synthesized_audio_ms']=480+entry['stt_ms']+entry['api_ms']+entry['tts_first_chunk_ms']
        save()
        assert not result.get('failed') and not result.get('handoff'),'Unexpected failure or staff handoff'
        return result['reply']
    try:
        greeting=service.start();report['session_id']=service.session_id;save()
        synth(greeting,output/'greeting.wav')
        say('I would like to book a table for two on December second, twenty twenty six, at eight in the evening.','booking_details')
        reply=say('My name is Voice Flow Test and my phone number is nine nine zero zero one one two two three three.','contact_details')
        if 'what time' in reply.lower():
            reply=say('Eight p.m.','clarify_booking_time')
        assert '2026-12-02' in reply and '20:00' in reply and 'correct' in reply.lower(),'Booking readback mismatch'
        assert own_booking() is None,'Reservation written before explicit confirmation'
        report['checks']['booking_waits_for_confirmation']=True;save()
        say('Yes.','confirm_booking')
        original=own_booking();assert original and original['status']=='active' and original['source']=='phone'
        assert original['party_size']==2 and original['time']=='20:00'
        report['booking_id']=original['id'];report['checks']['booking_saved_with_phone_source']=True;save()
        digits={'0':'zero','1':'one','2':'two','3':'three','4':'four','5':'five','6':'six','7':'seven','8':'eight','9':'nine'}
        reference=' '.join(digits.get(c,c) for c in original['id'][:8])
        contact=' My phone number is nine nine zero zero one one two two three three.' if args.combined_contact else ''
        reference_text=f' My reference is {reference}.' if args.use_reference else ''
        report['lookup_mode']='reference_and_phone' if args.use_reference else 'phone_mcp'
        reply=say('I want to change my reservation.'+reference_text+contact,'identify_update')
        if not args.combined_contact:
            reply=say('My phone number is nine nine zero zero one one two two three three.','update_phone')
        reply=say('Please change it to three guests at nine in the evening on December second, twenty twenty six.','update_details')
        assert '21:00' in reply and '3 guests' in reply and 'correct' in reply.lower(),'Update readback mismatch'
        assert own_booking()['version']==original['version'],'Update committed before yes'
        report['checks']['update_waits_for_confirmation']=True;save()
        say('Yes.','confirm_update')
        updated=own_booking()
        assert updated['id']==original['id'] and updated['party_size']==3 and updated['time']=='21:00' and updated['version']==2
        report['checks']['update_preserves_reference_and_source']=updated['source']=='phone';save()
        reply=say('Please cancel my reservation.'+reference_text+contact,'identify_cancellation')
        if 'confirm' not in reply.lower():
            reply=say('My phone number is nine nine zero zero one one two two three three.','cancellation_phone')
        assert 'cancel' in reply.lower() and 'confirm' in reply.lower(),'Cancellation readback missing'
        assert own_booking()['status']=='active','Cancellation committed before yes'
        report['checks']['cancellation_waits_for_confirmation']=True;save()
        say('Yes.','confirm_cancellation')
        cancelled=own_booking();assert cancelled['status']=='cancelled' and cancelled['version']==3
        dashboard=owner.get('/api/owner/dashboard').json()
        history=[e['kind'] for e in dashboard['booking_events'] if e['booking_id']==original['id']]
        assert history==['created','updated','cancelled'],history
        plan=owner.get('/api/owner/reservations/day',params={'day':'2026-12-02'}).json()
        assert not any(b['id']==original['id'] for b in plan['bookings'])
        report['checks']['cancellation_retains_history_and_releases_capacity']=True
        report['final_status']='cancelled';report['passed']=True
        print('PASS: spoken-audio booking, update and cancellation; retained audit history and released capacity.',flush=True)
    except Exception as error:
        report['error']=str(error)
        print(f'FAIL: {error}',flush=True)
        raise
    finally:
        try:
            row=own_booking()
            report['final_status']=row['status'] if row else 'no_booking_created'
        except Exception as error:
            report['final_status_check_error']=str(error)
        save();service.close();owner.close()
        if hasattr(transcriber,'close'):transcriber.close()
    print(f'Report: {output/"report.json"}',flush=True)


if __name__=='__main__':
    main()
