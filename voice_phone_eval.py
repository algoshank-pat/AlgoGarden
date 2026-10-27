"""Small synthetic phone-digit regression set; no API calls or booking writes."""
import json
import re
import time
import wave
from pathlib import Path


def main():
    import numpy as np
    import torch
    from faster_whisper.audio import decode_audio
    from silero_vad import load_silero_vad
    from voice.adapters import WhisperTranscriber,PiperSpeaker
    from voice.interfaces import CapturedAudio
    from voice.segmentation import SpeechSegmenter
    root=Path(__file__).parent
    output=root/'results/voice-phone-eval'
    output.mkdir(parents=True,exist_ok=True)
    torch.set_num_threads(1)
    stt=WhisperTranscriber('small',root/'models/whisper')
    piper=PiperSpeaker(root/'models/piper/en_US-lessac-medium.onnx')
    vad=load_silero_vad(onnx=True)
    cases=[
        ('original_failure',None,'9900112233',root/'results/voice-flow/04-caller.wav'),
        ('different_repeats','I would like to change my reservation for tomorrow evening. My phone number is nine eight eight zero zero five five six six seven.','9880055667',None),
        ('repeated_zero_one','My name is Alex and I need a table for three at eight in the evening. My phone number is nine zero zero zero zero one one one two two.','9000011122',None),
        ('descending_digits','Please change my reservation to four people at nine tomorrow evening. My phone number is nine eight seven six five four three two one zero.','9876543210',None),
        ('country_code','I want to check my booking. My phone number is plus nine one nine nine zero zero one one two two three three.','919900112233',None),
        ('short_phone','My phone number is nine nine zero zero one one two two three three.','9900112233',None),
        ('no_phone_date','Please change it to three guests at nine in the evening on December second, twenty twenty six.',None,None),
        ('no_phone_yes','Yes, that is correct.',None,None),
    ]
    report={'test_type':'clean_synthetic_audio_phone_regression','held_out':False,'microphone_tested':False,'cases':[]}
    def phone(text):
        match=re.search(r'phone(?:\s+number)?\s*(?:is|:)?\s*(.*)',text,re.I)
        if not match:return None
        # Score what ASR produced; do not repair, pad, or fetch stored numbers.
        number=re.search(r'\+?\d[\d\s().-]*\d',match[1])
        return re.sub(r'\D','',number[0]) if number else None
    for key,text,expected,existing in cases:
        path=existing or output/(key+'.wav')
        if not existing and not path.exists():
            with wave.open(str(path),'wb') as wav:piper.voice.synthesize_wav(text,wav)
        samples=decode_audio(str(path),sampling_rate=16000)
        samples=np.concatenate([np.zeros(512*10,dtype=np.float32),samples,np.zeros(512*25,dtype=np.float32)])
        vad.reset_states();segmenter=SpeechSegmenter();frames=None
        for offset in range(0,len(samples)-511,512):
            frame=samples[offset:offset+512]
            frames=segmenter.feed(frame,float(vad(torch.from_numpy(frame),16000))>=.5)
            if frames is not None:break
        assert frames is not None,key
        captured=CapturedAudio(np.concatenate(frames),16000,480)
        started=time.perf_counter()
        segments,_=stt.model.transcribe(captured.samples,language='en',beam_size=1,vad_filter=False,condition_on_previous_text=False)
        before=' '.join(s.text.strip() for s in segments)
        before_ms=round((time.perf_counter()-started)*1000)
        started=time.perf_counter();unclear=False
        from voice.interfaces import PhoneNumberUnclear
        try:after=stt.transcribe(captured)
        except PhoneNumberUnclear:after='';unclear=True
        row={'case':key,'expected_phone':expected,'before_transcript':before,'after_transcript':after,
             'before_phone':phone(before),'after_phone':phone(after),'before_passed':phone(before)==expected,
             'after_passed':phone(after)==expected,'needs_repeat':unclear,'before_ms':before_ms,'after_ms':round((time.perf_counter()-started)*1000)}
        report['cases'].append(row)
        report['before_passed']=sum(r['before_passed'] for r in report['cases'])
        report['after_passed']=sum(r['after_passed'] for r in report['cases'])
        report['clarifications']=sum(r['needs_repeat'] for r in report['cases'])
        report['unsafe_extractions']=sum(not r['after_passed'] and not r['needs_repeat'] for r in report['cases'])
        (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(row),flush=True)
    if report['unsafe_extractions']:raise SystemExit(1)


if __name__=='__main__':main()
