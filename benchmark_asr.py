"""CPU ASR comparison on fixed saved synthetic audio; no reservation API calls."""
import argparse
import hashlib
import json
import platform
import random
import re
import statistics
import time
import wave
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def extract_phone(text):
    """Shared scoring for numeric and literal digit-word transcripts."""
    from voice.adapters import spoken_phone_digits
    match=re.search(r'(?:phone(?:\s+number(?:[\x27’]s|s)?)?|telephone|contact number|my number)\s*(?:is|:)?\s*(.*)',text,re.I)
    if not match:return None
    tail=match[1]
    literal=spoken_phone_digits(tail)
    if literal:return literal.lstrip('+')
    number=re.search(r'\+?\d[\d\s().-]*\d',tail)
    if not number:return None
    digits=re.sub(r'\D','',number[0])
    return digits if len(digits)>=7 else None


def percentile(values,fraction):
    values=sorted(values)
    return values[min(len(values)-1,int((len(values)-1)*fraction+.5))]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nemotron-url',default='http://127.0.0.1:8021')
    parser.add_argument('--repetitions',type=int,default=3)
    parser.add_argument('--output',default='results/asr-benchmark')
    args=parser.parse_args()
    import httpx
    import numpy as np
    import torch
    from faster_whisper.audio import decode_audio
    from silero_vad import load_silero_vad
    from voice.adapters import WhisperTranscriber
    from voice.interfaces import CapturedAudio,PhoneNumberUnclear
    from voice.segmentation import SpeechSegmenter
    torch.set_num_threads(1)
    root=Path(__file__).parent;output=root/args.output
    inputs=output/'inputs';inputs.mkdir(parents=True,exist_ok=True)
    fixtures=[('original_failure',root/'results/voice-flow/04-caller.wav','9900112233')]
    for name,expected in [('different_repeats','9880055667'),('repeated_zero_one','9000011122'),
                          ('descending_digits','9876543210'),('country_code','919900112233'),
                          ('short_phone','9900112233'),('no_phone_date',None),('no_phone_yes',None)]:
        fixtures.append((name,root/'results/voice-phone-eval'/(name+'.wav'),expected))
    cases=[];vad=load_silero_vad(onnx=True)
    for name,source,expected in fixtures:
        samples=decode_audio(str(source),sampling_rate=16000)
        samples=np.concatenate([np.zeros(512*10,dtype=np.float32),samples,np.zeros(512*25,dtype=np.float32)])
        vad.reset_states();segmenter=SpeechSegmenter();frames=None
        for offset in range(0,len(samples)-511,512):
            frame=samples[offset:offset+512]
            frames=segmenter.feed(frame,float(vad(torch.from_numpy(frame),16000))>=.5)
            if frames is not None:break
        if frames is None:raise ValueError(f'No complete utterance: {name}')
        path=inputs/(name+'.wav')
        pcm=(np.clip(np.concatenate(frames),-1,1)*32767).astype('<i2')
        with wave.open(str(path),'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(pcm.tobytes())
        audio=decode_audio(str(path),sampling_rate=16000)
        cases.append({'id':name,'file':str(path.relative_to(root)),'source':str(source.relative_to(root)),
                      'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'expected_phone':expected,
                      'duration_seconds':round(len(audio)/16000,3),'audio':audio})
    client=httpx.Client(base_url=args.nemotron_url,timeout=120)
    ready=client.get('/ready');ready.raise_for_status()
    if ready.json().get('device')!='cpu':raise ValueError('Nemotron must use CPU for this comparison')
    start=time.perf_counter();whisper=WhisperTranscriber('small',root/'models/whisper',threads=4)
    whisper_load=round((time.perf_counter()-start)*1000)
    report={'started_at':datetime.now(ZoneInfo('America/Chicago')).isoformat(),
      'hardware':{'cpu':'Intel Core Ultra 7 155H','cores':16,'logical_processors':22,'gpu':'Intel Arc (not used)','threads_per_asr_engine':4},
      'platform':platform.platform(),'nemotron_version':client.get('/version').json(),
      'nemotron_model':'nvidia/nemotron-speech-streaming-en-0.6b',
      'nemotron_revision':'ebe59e5a817142986528bbbee5dba8db7b38ed50','nemotron_precision':'GGUF Q8_0',
      'whisper_model':'small (multilingual)','whisper_runtime':'faster-whisper/CTranslate2 CPU INT8','whisper_load_ms':whisper_load,
      'repetitions':args.repetitions,'warmup_excluded':True,'model_loading_excluded':True,
      'models_run_sequentially':True,'method':'Identical endpointed 16kHz PCM16 recordings; arrays/WAV bytes preloaded before timing. Nemotron includes loopback multipart HTTP overhead. No ITN/phone prompting for Nemotron. Whisper safe includes focused phone replays and uncertainty guards. Fixed shuffled order; profile order rotated.',
      'limitations':['Eight clean synthetic clips from one Piper voice, six containing phone numbers. Not a held-out human/noise/accent benchmark.',
                      'Offline whole-utterance transcription, not realtime streaming latency or full voice-agent response time.',
                      'Three repetitions per clip; p95 is descriptive and has a small sample.',
                      'Local background activity and laptop power/thermal scheduling are not controlled.'],
      'cases':[{k:v for k,v in case.items() if k!='audio'} for case in cases],'warmups':[],'runs':[]}
    def save():
        (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    payloads={case['id']:(root/case['file']).read_bytes() for case in cases}
    profiles=['whisper_original','whisper_phone_safe','nemotron']
    def transcribe(profile,case):
        if profile=='nemotron':
            response=client.post('/v1/audio/transcriptions',files={'file':('recording.wav',payloads[case['id']],'audio/wav')},
                                 data={'response_format':'json','verbatim':'true','automatic_punctuation':'true'})
            response.raise_for_status();return response.json()['text']
        if profile=='whisper_original':
            segments,_=whisper.model.transcribe(case['audio'],language='en',beam_size=1,vad_filter=False,condition_on_previous_text=False)
            return ' '.join(s.text.strip() for s in segments)
        return whisper.transcribe(CapturedAudio(case['audio'],16000,480))
    for profile in profiles:
        started=time.perf_counter()
        try:transcribe(profile,cases[0])
        except PhoneNumberUnclear:pass
        report['warmups'].append({'profile':profile,'ms':round((time.perf_counter()-started)*1000)})
        print('Warmed '+profile,flush=True)
    save()
    for repetition in range(args.repetitions):
        ordered=cases.copy();random.Random(4721+repetition).shuffle(ordered)
        for index,case in enumerate(ordered):
            rotation=(index+repetition)%len(profiles)
            for profile in profiles[rotation:]+profiles[:rotation]:
                start=time.perf_counter();text='';unclear=False;error=None
                try:text=transcribe(profile,case)
                except PhoneNumberUnclear:unclear=True
                except Exception as problem:error=f'{type(problem).__name__}: {problem}'
                ms=round((time.perf_counter()-start)*1000,2)
                phone=extract_phone(text)
                row={'profile':profile,'case':case['id'],'repetition':repetition+1,'ms':ms,
                     'real_time_factor':round(ms/1000/case['duration_seconds'],4),'transcript':text,
                     'phone':phone,'expected_phone':case['expected_phone'],'exact_match':phone==case['expected_phone'] and not unclear and not error,
                     'needs_repeat':unclear,'error':error}
                report['runs'].append(row);save()
                print(json.dumps({k:row[k] for k in ['profile','case','repetition','ms','phone','exact_match','needs_repeat','error']}),flush=True)
    report['summary']={}
    for profile in profiles:
        runs=[r for r in report['runs'] if r['profile']==profile]
        phones=[r for r in runs if r['expected_phone'] is not None]
        controls=[r for r in runs if r['expected_phone'] is None]
        durations=[r['ms'] for r in runs]
        report['summary'][profile]={'runs':len(runs),'phone_runs':len(phones),
          'phone_exact':sum(r['exact_match'] for r in phones),'phone_accuracy':sum(r['exact_match'] for r in phones)/len(phones),
          'phone_clips_correct_every_repeat':sum(all(r['exact_match'] for r in phones if r['case']==c['id']) for c in cases if c['expected_phone']),
          'clarifications':sum(r['needs_repeat'] for r in phones),'wrong_phone_outputs':sum(not r['exact_match'] and not r['needs_repeat'] and not r['error'] for r in phones),
          'control_runs':len(controls),'controls_passed':sum(r['exact_match'] for r in controls),'errors':sum(bool(r['error']) for r in runs),
          'median_ms':round(statistics.median(durations),2),'mean_ms':round(statistics.mean(durations),2),
          'p95_ms_small_sample':round(percentile(durations,.95),2),'phone_median_ms':round(statistics.median(r['ms'] for r in phones),2),
          'mean_real_time_factor':round(statistics.mean(r['real_time_factor'] for r in runs),4),
          'corpus_rtfx':round(sum(c['duration_seconds'] for c in cases)*args.repetitions/(sum(durations)/1000),2)}
    report['finished_at']=datetime.now(ZoneInfo('America/Chicago')).isoformat();save();client.close()
    lines=['# Local Nemotron vs Whisper benchmark','',
           'Intel Core Ultra 7 155H · Windows · CPU only · four threads per engine.',
           'Eight saved synthetic recordings (six phone clips, two controls), three measured repetitions after warmup. Both engines receive the same endpointed audio. No reservation writes.','',
           '| Profile | Median transcription | Phone exact | Phone clips correct every repeat | Clarifications | Wrong phone outputs | Controls |',
           '|---|---:|---:|---:|---:|---:|---:|']
    names={'whisper_original':'Whisper small, original beam 1','whisper_phone_safe':'Whisper small, current phone-safe pipeline','nemotron':'Nemotron Speech Streaming EN 0.6B Q8_0 (offline)'}
    for profile,summary in report['summary'].items():
        lines.append(f"| {names[profile]} | {summary['median_ms']/1000:.2f} s | {summary['phone_exact']}/{summary['phone_runs']} | {summary['phone_clips_correct_every_repeat']}/6 | {summary['clarifications']} | {summary['wrong_phone_outputs']} | {summary['controls_passed']}/{summary['control_runs']} |")
    lines+=['','Model loading and warmups are excluded. Nemotron timing includes local HTTP overhead; Whisper is in-process. Quantizations and runtimes differ. The current Whisper path includes phone-specific replays; Nemotron has no added digit safeguard.','',
            'Phone scoring strips punctuation and maps literal spoken digit words using the same scorer for both engines. It does not repair a number from the expected answer or database. A clarification is separate from an incorrect number.','',
            '## Scope limits','']+['- '+limitation for limitation in report['limitations']]
    lines+=['','Full transcripts, per-run timings, input hashes, exact model version and warmup results: [report.json](report.json).']
    (output/'comparison.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(report['summary'],indent=2),flush=True)


if __name__=='__main__':main()
