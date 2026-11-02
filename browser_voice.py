"""Bounded local audio adapters for the browser voice testing page."""
import io
import wave
import json
import base64
import re
from pathlib import Path
from uuid import UUID
import time
from datetime import datetime, timezone
from threading import Lock

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

router = APIRouter(prefix='/api/voice')
audio_lock = Lock()
speech_lock = Lock()
transcriber = None
speaker = None
ROOT = Path(__file__).parent
MAX_AUDIO = 16000 * 2 * 30 + 44


def transcribe_wav(data, survey_answer=False):
    import numpy as np
    from voice.adapters import ParakeetTranscriber
    from voice.interfaces import CapturedAudio, PhoneNumberUnclear
    try:
        with wave.open(io.BytesIO(data), 'rb') as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000):
                raise ValueError('Use mono 16 kHz PCM WAV audio.')
            frames = wav.readframes(16000 * 30 + 1)
            if not frames or len(frames) > 16000 * 2 * 30:
                raise ValueError('Record between a moment and 30 seconds.')
    except (wave.Error, EOFError, ValueError) as error:
        raise HTTPException(422, str(error) or 'Invalid WAV recording.') from error
    global transcriber
    if not audio_lock.acquire(blocking=False):
        raise HTTPException(503, 'Audio service is busy. Please try again.')
    try:
        if transcriber is None:
            transcriber = ParakeetTranscriber(ROOT)
        try:
            samples=np.frombuffer(frames,dtype='<i2').astype('float32')/32768
            from observability import observed
            text = observed('speech.parakeet','tool')(transcriber.transcribe)(CapturedAudio(samples))
            if survey_answer:
                from voice.survey_recognition import recognize
                try:text=observed('speech.survey_second_pass','tool')(recognize)(samples,text,ROOT)
                except (OSError,RuntimeError):pass
        except PhoneNumberUnclear:
            return {'text': '', 'input_kind': 'unrecognized_audio', 'engine': 'Parakeet'}
        return {'text': text, 'input_kind': 'text' if text.strip() else 'unrecognized_audio', 'engine': 'Parakeet'}
    finally:
        audio_lock.release()


@router.post('/transcribe')
async def transcribe(request: Request, session_id: str | None = None, request_id: UUID | None = None):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_AUDIO:
            raise HTTPException(413, 'Recording must be at most 30 seconds.')
    survey_answer=False
    if session_id:
        from uuid import UUID
        from chat_api import sessions
        try:session=sessions.get(UUID(session_id))
        except ValueError:raise HTTPException(422,'Invalid session ID.')
        survey_answer=bool(session and not session.stopped and not session.bot.ended and session.survey.get('status')=='awaiting_rating')
    def measured_transcribe():
        if session_id and request_id:
            import turn_tracing as tracing
            with tracing.stage(session_id,request_id,'speech.to_text') as trace:
                result=transcribe_wav(bytes(data),survey_answer)
                tracing.set_payload(tracing.group_for(trace,'Input'),outputs={'transcript':result})
                return result
        return transcribe_wav(bytes(data),survey_answer)
    return await run_in_threadpool(measured_transcribe)


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    session_id: UUID | None = None
    request_id: UUID | None = None


@router.get('/greeting')
def greeting():
    from chat_api import restaurant
    from voice.greeting import recording
    metadata,data=recording(ROOT,restaurant,lambda text:speak(SpeechRequest(text=text)).body)
    return {**metadata,'audio':base64.b64encode(data).decode('ascii')}


@router.post('/speak')
def speak(request: SpeechRequest):
    from voice.adapters import PiperSpeaker
    global speaker
    if not speech_lock.acquire(blocking=False):
        raise HTTPException(503, 'Audio service is busy. Please try again.')
    try:
        if speaker is None:
            speaker = PiperSpeaker(ROOT / 'models/piper/en_US-lessac-high.onnx')
        output = io.BytesIO()
        with wave.open(output, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(speaker.voice.config.sample_rate)
            from voice.phrases import speech_segments
            for segment in speech_segments(request.text):
                for chunk in speaker.chunks(segment):
                    wav.writeframes(chunk.audio_int16_bytes)
        return Response(output.getvalue(), media_type='audio/wav', headers={'Cache-Control': 'no-store'})
    finally:
        speech_lock.release()


@router.get('/thinking-cue')
def thinking_cue():
    # Fixed public phrase, cached by business, wording and voice configuration.
    from chat_api import restaurant
    from voice.greeting import recording
    metadata,data=recording(ROOT,restaurant,lambda text:speak(SpeechRequest(text=text)).body,text='One moment.')
    return {**metadata,'audio':base64.b64encode(data).decode('ascii')}


@router.post('/speak/stream')
def speak_stream(request: SpeechRequest, http_request: Request = None):
    """Independent WAV chunks start playback before the full reply is synthesized."""
    def packets():
        global speaker
        import turn_tracing as tracing
        trace=tracing.root_for(request.session_id,request.request_id,'voice') if request.session_id and request.request_id else None
        from voice.phrases import spoken_text
        tracing.set_payload(tracing.group_for(trace,'Response'),inputs={'text':spoken_text(request.text)})
        began=time.perf_counter();trace_at=datetime.now(timezone.utc);status='completed';synthesis_ms=0
        if not speech_lock.acquire(blocking=False):
            yield json.dumps({'error':'Speech service is busy. Please try again.'})+'\n'
            return
        try:
            from voice.adapters import PiperSpeaker
            if speaker is None:speaker=PiperSpeaker(ROOT/'models/piper/en_US-lessac-high.onnx')
            rate=speaker.voice.config.sample_rate
            from voice.phrases import speech_segments
            segments=speech_segments(request.text)
            index=0
            for segment in segments:
                iterator=iter(speaker.chunks(segment))
                while True:
                    chunk_start=time.perf_counter()
                    try:chunk=next(iterator)
                    except StopIteration:break
                    synthesis_ms+=(time.perf_counter()-chunk_start)*1000
                    if index==0 and request.session_id and request.request_id:
                        tracing.record(request.session_id,request.request_id,'speech.time_to_first_audio',(time.perf_counter()-began)*1000,trace,trace_at)
                    output=io.BytesIO()
                    with wave.open(output,'wb') as wav:
                        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(rate);wav.writeframes(chunk.audio_int16_bytes)
                    yield json.dumps({'index':index,'audio':base64.b64encode(output.getvalue()).decode('ascii'),'duration_ms':len(chunk.audio_int16_bytes)/2/rate*1000})+'\n'
                    index+=1
        except BaseException:
            status='failed';raise
        finally:
            speech_lock.release()
            if request.session_id and request.request_id:
                tracing.record(request.session_id,request.request_id,'speech.synthesis_compute',synthesis_ms,trace,trace_at,status)
                tracing.record(request.session_id,request.request_id,'speech.stream_lifetime',(time.perf_counter()-began)*1000,trace,trace_at,status)
                tracing.finish(trace,speech_completed=status=='completed')
    async def stream():
        source=packets()
        try:
            while True:
                if http_request is not None and await http_request.is_disconnected():return
                packet=await run_in_threadpool(next,source,None)
                if packet is None:return
                yield packet
        finally:source.close()
    return StreamingResponse(stream(),media_type='application/x-ndjson',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})


def close_audio():
    global transcriber,speaker
    if transcriber is not None:
        transcriber.close()
        transcriber=None
    speaker=None


def warm_audio(greeting):
    """Load each speech resource once before the first customer utterance."""
    global transcriber,speaker
    from voice.adapters import ParakeetTranscriber,PiperSpeaker
    with audio_lock:
        if transcriber is None:transcriber=ParakeetTranscriber(ROOT)
    with speech_lock:
        if speaker is None:speaker=PiperSpeaker(ROOT/'models/piper/en_US-lessac-high.onnx')
        speaker.prime([greeting,'Do you have a reservation with us?','Please say and spell your first and last name.'])
    # Other standard phrases populate lazily on first use. No background
    # synthesis competes with a caller or holds the shared speech lock.
