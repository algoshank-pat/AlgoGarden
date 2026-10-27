"""Lazy imports keep the text service usable without voice dependencies."""
import time
import re
from pathlib import Path

from .interfaces import CapturedAudio,PhoneNumberUnclear
from customer_identity import normalize_phone, spoken_phone_digits


from .segmentation import SpeechSegmenter


class SileroMicrophone:
    sample_rate = 16000
    frame_size = 512

    def __init__(self, device=None, idle_seconds: float = 15, silence_ms: int = 448):
        import torch
        from silero_vad import load_silero_vad
        torch.set_num_threads(1)
        self.model = load_silero_vad(onnx=True)
        self.device, self.idle_seconds = device, idle_seconds
        self.silence_frames = max(1, round(silence_ms / 32))

    def listen(self) -> CapturedAudio:
        import numpy as np
        import sounddevice as sd
        import torch
        segmenter = SpeechSegmenter(silence_frames=self.silence_frames)
        self.model.reset_states()
        started = time.monotonic()
        last_speech = started
        # Input exists only while listening, so TTS is not recorded as caller speech.
        with sd.InputStream(samplerate=self.sample_rate, channels=1, dtype='float32',
                            blocksize=self.frame_size, device=self.device) as stream:
            while True:
                block, overflow = stream.read(self.frame_size)
                if overflow:
                    raise RuntimeError('Microphone overflow; audio discarded rather than submitting missing digits.')
                frame = block[:, 0].copy()
                speech = float(self.model(torch.from_numpy(frame), self.sample_rate)) >= .5
                if speech:
                    last_speech = time.monotonic()
                frames = segmenter.feed(frame, speech)
                if frames is not None:
                    return CapturedAudio(np.concatenate(frames), self.sample_rate,
                                         (time.monotonic() - last_speech) * 1000)
                if not segmenter.started and time.monotonic() - started >= self.idle_seconds:
                    raise TimeoutError('No speech detected')


class WhisperTranscriber:
    # No example numbers or customer data: guide formatting without priming a
    # particular phone number. Wider decoding helps preserve repeated digits.
    transcription_hint = ('Restaurant caller. Transcribe phone numbers digit by digit. '
                          'Keep every repeated digit exactly as spoken. Do not guess missing digits.')
    def __init__(self, model: str = 'small', cache_dir: Path | None = None, threads: int = 4, phone_region: str = 'IN'):
        from faster_whisper import WhisperModel
        self.model = WhisperModel(model, device='cpu', compute_type='int8', cpu_threads=threads,
                                  download_root=str(cache_dir) if cache_dir else None)
        self.phone_region=phone_region
        self._digit_suppression=[-1]+[token_id for token,token_id in self.model.hf_tokenizer.get_vocab().items()
                                     if any(c in '0123456789' for c in token)]

    def transcribe(self, audio: CapturedAudio | str) -> str:
        if isinstance(audio, CapturedAudio) and audio.sample_rate != 16000:
            raise ValueError('Whisper array input must be sampled at 16 kHz')
        segments, _ = self.model.transcribe(audio.samples if isinstance(audio, CapturedAudio) else audio,
                                           language='en', beam_size=5, vad_filter=False,
                                           initial_prompt=self.transcription_hint,word_timestamps=True,
                                           condition_on_previous_text=False)
        segments=list(segments)
        text=' '.join(segment.text.strip() for segment in segments).strip()
        return self._verify_phone(audio,text,segments)

    def _verify_phone(self,audio,text,segments):
        match=re.search(r'(?:phone(?:\s+number(?:[\x27’]s|s)?)?|telephone|contact number|my number)\s*(?:is|:)?\s*(?P<number>(?:plus\s*)?\+?\d[\d\s().-]*\d)',text,re.I)
        if not match:return text
        words=[w for segment in segments for w in (segment.words or [])]
        marker=next((i for i,w in enumerate(words) if w.word.strip().lower() in {'phone','telephone','contact'}),None)
        if marker is None:
            marker=next((i for i,w in enumerate(words) if w.word.strip().lower()=='number'),None)
        if marker is None:raise PhoneNumberUnclear('Please repeat the phone number one digit at a time.')
        first=next((i for i in range(marker+1,len(words)) if any(c.isdigit() for c in words[i].word)),None)
        if first is None:raise PhoneNumberUnclear('Please repeat the phone number one digit at a time.')
        last=first
        while last+1<len(words) and re.fullmatch(r'[\d\s+.,()-]+',words[last+1].word):last+=1
        if first>0 and words[first-1].word.strip().lower()=='plus':first-=1
        if isinstance(audio,CapturedAudio):samples=audio.samples
        else:
            from faster_whisper.audio import decode_audio
            samples=decode_audio(audio,sampling_rate=16000)
        # Numeric-token timestamps can collapse onto a single instant. Anchor
        # to the spoken phone label instead, and retain the utterance's tail.
        start=max(0,int((min(words[first].start,words[marker].end)-.15)*16000))
        end=int((words[last+1].start+.05)*16000) if last+1<len(words) else len(samples)
        candidate=None
        primary=re.sub(r'^plus\s*','+',match.group('number'),flags=re.I)
        primary_normalized=normalize_phone(primary,self.phone_region)
        if primary_normalized and primary_normalized.startswith('+91') and len(primary_normalized)!=13:
            primary_normalized=None
        starts=[start,max(0,int((words[first].start-.15)*16000))]
        for offset in dict.fromkeys(starts):
            clip=samples[offset:end]
            if len(clip)<4000:continue
            decoded,_=self.model.transcribe(clip,language='en',beam_size=5,condition_on_previous_text=False,
                suppress_tokens=self._digit_suppression,
                initial_prompt='Write every spoken telephone digit as a separate word. Preserve repetitions. Do not summarize.')
            literal=' '.join(s.text.strip() for s in decoded)
            proposed=spoken_phone_digits(literal)
            normalized=normalize_phone(proposed,self.phone_region) if proposed else None
            # The sample restaurant takes full ten-digit Indian contact numbers,
            # not short local landline forms that the library also calls possible.
            if normalized and (not normalized.startswith('+91') or len(normalized)==13):
                if primary_normalized and normalized!=primary_normalized:
                    raise PhoneNumberUnclear('Phone decodings disagree; please repeat one digit at a time.')
                candidate=proposed;break
        if not candidate:
            if primary_normalized:
                return text
            raise PhoneNumberUnclear('Please repeat the phone number slowly, one digit at a time.')
        # Replace only the phone field using a fresh decoding of its actual audio.
        # Never pad a string or consult stored customer numbers.
        return text[:match.start('number')]+candidate+text[match.end('number'):]


class ParakeetTranscriber:
    """Own a local CPU server; never send caller audio to a hosted provider."""
    def __init__(self, root: Path, threads: int = 4, phone_region: str = 'IN'):
        import hashlib
        import socket
        import subprocess
        import httpx
        model=root/'models/nemotron/nvidia/parakeet-tdt-0.6b-v3/541d1f99c6b0c3cd0b11a95167540bb8edefd82b/parakeet-tdt-0.6b-v3.q8_0.gguf'
        executable=root/'tools/nemo-speech-0.2.0/runtime/nemo-speech-0.2.0-windows-x86_64-cpu/bin/nemo-speech.exe'
        if not executable.exists() or not model.exists():
            raise FileNotFoundError('Local Parakeet runtime/weights missing; see VOICE.md setup.')
        with model.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!='e3880d0aaaaf2c308ea2c35016b2b895c423eb3fda924c1b463d1c19b7f4d32e':
                raise ValueError('Parakeet model checksum mismatch')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        self.phone_region=phone_region
        self.client=httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=120,trust_env=False)
        self.process=subprocess.Popen([str(executable),'serve','--asr-model',str(model),'--device','cpu',
            '--asr.backend.threads',str(threads),'--threads','1','--host','127.0.0.1','--port',str(port),'--no-ui'],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            deadline=time.monotonic()+90
            while time.monotonic()<deadline:
                if self.process.poll() is not None:raise RuntimeError('Local Parakeet server exited during startup')
                try:
                    response=self.client.get('/ready',timeout=1)
                    if response.is_success and response.json().get('ready'):break
                except httpx.TransportError:pass
                time.sleep(.2)
            else:raise TimeoutError('Parakeet startup timed out')
        except BaseException:
            self.close();raise

    def transcribe(self, audio: CapturedAudio | str) -> str:
        import io
        import wave
        import numpy as np
        if isinstance(audio,CapturedAudio):
            if audio.sample_rate!=16000:raise ValueError('Parakeet input must be 16 kHz')
            buffer=io.BytesIO()
            with wave.open(buffer,'wb') as wav:
                wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000)
                wav.writeframes((np.clip(audio.samples,-1,1)*32767).astype('<i2').tobytes())
            payload=buffer.getvalue()
        else:payload=Path(audio).read_bytes()
        response=self.client.post('/v1/audio/transcriptions',files={'file':('caller.wav',payload,'audio/wav')},
            data={'response_format':'json','verbatim':'true','automatic_punctuation':'true'})
        response.raise_for_status()
        from structured_io import SpeechTranscript
        text=SpeechTranscript.model_validate(response.json()).text.strip()
        # Reject incomplete explicitly labelled phone fields; never pad digits.
        match=re.search(r'(?:phone(?:\s+number)?|telephone|contact number|my number)\s*(?:is|:)?\s*(.*)',text,re.I)
        if match:
            number=re.search(r'(?:plus\s*)?\+?\d[\d\s().-]*\d',match[1],re.I)
            candidate=re.sub(r'^plus\s*','+',number[0],flags=re.I) if number else spoken_phone_digits(match[1])
            normalized=normalize_phone(candidate,self.phone_region) if candidate else None
            if not normalized or (normalized.startswith('+91') and len(normalized)!=13):
                raise PhoneNumberUnclear('Please repeat the phone number slowly, one digit at a time.')
        return text

    def close(self):
        import subprocess
        self.client.close()
        if self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait()


class PiperSpeaker:
    def __init__(self, model_path: Path, device=None):
        from piper import PiperVoice
        if not model_path.exists():
            raise FileNotFoundError(f'Piper voice missing: {model_path}. Run prepare_voice.py first.')
        self.voice = PiperVoice.load(str(model_path), use_cuda=False)
        self.device = device
        from piper.config import SynthesisConfig
        self.synthesis_config = SynthesisConfig(length_scale=1.03, noise_scale=0.6, noise_w_scale=0.75)
        from collections import OrderedDict
        self._cache=OrderedDict()

    def chunks(self,text):
        """Cache only public common phrases; stream sentence chunks otherwise."""
        from .speech_text import natural_speech
        text=natural_speech(text)
        cacheable=(text.startswith('Thank you for calling ') or text.startswith('Thank you so much for calling.') or text in {
            'Do you have a reservation with us?','What phone number was used for the reservation?',
            'What phone number can I use for your reservation?','May I have your name for the reservation?',
            'Is there anything else I can help you with?'})
        if cacheable and text in self._cache:
            self._cache.move_to_end(text)
            yield from self._cache[text];return
        saved=[]
        for chunk in self.voice.synthesize(text, **({'syn_config':self.synthesis_config} if hasattr(self,'synthesis_config') else {})):
            if cacheable:saved.append(chunk)
            yield chunk
        if cacheable:
            self._cache[text]=saved
            if len(self._cache)>16:self._cache.popitem(last=False)

    def prime(self,texts):
        for text in texts:list(self.chunks(text))

    def speak(self, text: str) -> dict:
        import sounddevice as sd
        started = time.perf_counter()
        stream = None
        first_chunk_ms = None
        # Synthesis of later chunks overlaps playback of the first chunk.
        import queue
        import threading
        chunks=queue.Queue(maxsize=2)
        stopped=threading.Event()
        def put(value):
            while not stopped.is_set():
                try:chunks.put(value,timeout=.1);return
                except queue.Full:pass
        def produce():
            try:
                for chunk in self.chunks(text):
                    if stopped.is_set():break
                    put(chunk)
            except BaseException as error:put(error)
            finally:put(None)
        producer=threading.Thread(target=produce,daemon=True,name='piper-synthesis')
        producer.start()
        try:
            # Piper yields sentence-sized audio chunks. Play each before synthesizing
            # the next rather than waiting for a complete response WAV.
            while True:
                chunk=chunks.get()
                if chunk is None:break
                if isinstance(chunk,BaseException):raise chunk
                if stream is None:
                    stream = sd.RawOutputStream(samplerate=chunk.sample_rate, channels=chunk.sample_channels,
                                                dtype='int16', device=self.device)
                    stream.start()
                    first_chunk_ms = (time.perf_counter() - started) * 1000
                stream.write(chunk.audio_int16_bytes)
        finally:
            stopped.set()
            if stream is not None:
                stream.stop()
                stream.close()
        return {'tts_first_chunk_ms': first_chunk_ms,
                'tts_total_ms': (time.perf_counter() - started) * 1000}
