import time
from pathlib import Path
from local_audit import append, now
from .interfaces import Listener, Transcriber, Speaker,PhoneNumberUnclear
from .segmentation import UtteranceTooLong


class VoiceRunner:
    def __init__(self, listener: Listener, stt: Transcriber, tts: Speaker, service, metrics_path: Path):
        self.listener, self.stt, self.tts, self.service = listener, stt, tts, service
        self.metrics_path = metrics_path

    def run(self):
        try:
            greeting = self.service.start()
            print('Receptionist:', greeting)
            self.tts.speak(greeting)
            while True:
                print('Listening… (Ctrl+C to end)')
                phone_unclear=False
                try:
                    audio = self.listener.listen()
                    started = time.perf_counter()
                    text = self.stt.transcribe(audio)
                    stt_ms = (time.perf_counter() - started) * 1000
                except (TimeoutError, UtteranceTooLong):
                    text, audio, stt_ms = '', None, 0
                except PhoneNumberUnclear:
                    phone_unclear=True
                    text, stt_ms = '', (time.perf_counter()-started)*1000
                print('Caller:', text or '[No intelligible speech detected]')
                started = time.perf_counter()
                result = self.service.turn(text) if text.strip() else self.service.unrecognized()
                api_ms = (time.perf_counter() - started) * 1000
                reply = result['reply']
                if phone_unclear and not result.get('ended'):
                    reply='I couldn’t clearly hear every phone digit. Please repeat your number slowly, one digit at a time.'
                if result.get('handoff') and result.get('staff_phone'):
                    reply += ' The number is ' + ' '.join(c for c in result['staff_phone'] if c.isdigit()) + '.'
                print('Receptionist:', reply)
                playback = self.tts.speak(reply)
                endpoint_ms = audio.endpoint_ms if audio is not None else 0
                first_chunk = playback.get('tts_first_chunk_ms')
                metrics = {'at': now(), 'session_id': self.service.session_id, 'request_id': result.get('request_id'), 'channel': 'voice',
                           'endpoint_ms': endpoint_ms, 'stt_ms': stt_ms, 'api_ms': api_ms,
                           'queue_wait_ms': result.get('queue_wait_ms', 0),
                           'llm_calls':sum(c.get('component')=='ollama.chat' for c in result.get('structured',{}).get('calls',[])), **playback,
                           'llm_ms':sum((c.get('output') or {}).get('total_duration',0)/1_000_000 for c in result.get('structured',{}).get('calls',[]) if c.get('component')=='ollama.chat'),
                           'estimated_end_of_speech_to_audio_ms': endpoint_ms+stt_ms+api_ms+first_chunk if first_chunk is not None and audio is not None else None}
                try:
                    append(self.metrics_path, metrics)
                except OSError:
                    print('Voice timing log could not be saved; the turn has already completed.')
                print(f'Timing: STT {stt_ms:.0f} ms, API {api_ms:.0f} ms, first TTS chunk {first_chunk} ms')
                if result.get('ended'):
                    break
        finally:
            self.service.close()
