"""Synthetic speech smoke test. Does not record a microphone, play audio or book."""
import json
import tempfile
import time
import wave
from pathlib import Path


def main():
    import numpy as np
    import torch
    from faster_whisper.audio import decode_audio
    from silero_vad import load_silero_vad
    from voice.adapters import WhisperTranscriber, PiperSpeaker
    root = Path(__file__).parent
    speaker = PiperSpeaker(root/'models/piper/en_US-lessac-medium.onnx')
    transcriber = WhisperTranscriber('small', root/'models/whisper')
    detector = load_silero_vad(onnx=True)
    phrase = 'What are your opening hours?'
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)/'synthetic.wav'
        started = time.perf_counter()
        with wave.open(str(path),'wb') as wav:
            speaker.voice.synthesize_wav(phrase,wav)
        tts_ms = (time.perf_counter()-started)*1000
        audio = decode_audio(str(path), sampling_rate=16000)
        speech_frames = 0
        for index in range(0,len(audio)-511,512):
            speech_frames += float(detector(torch.from_numpy(audio[index:index+512]),16000)) >= .5
        started = time.perf_counter()
        transcript = transcriber.transcribe(str(path))
        stt_ms = (time.perf_counter()-started)*1000
    result = {'source_text':phrase,'transcript':transcript,'vad_speech_frames':speech_frames,
              'tts_wav_ms':round(tts_ms),'stt_ms':round(stt_ms),
              'passed':speech_frames>0 and 'opening hours' in transcript.lower()}
    print(json.dumps(result,indent=2))
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
