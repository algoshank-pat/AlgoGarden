"""Bounded local audio adapters for the browser voice testing page."""
import io
import wave
from pathlib import Path
from threading import Lock

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

router = APIRouter(prefix='/api/voice')
audio_lock = Lock()
transcriber = None
speaker = None
ROOT = Path(__file__).parent
MAX_AUDIO = 16000 * 2 * 30 + 44


def transcribe_wav(data):
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
            text = transcriber.transcribe(CapturedAudio(np.frombuffer(frames, dtype='<i2').astype('float32') / 32768))
        except PhoneNumberUnclear:
            return {'text': '', 'input_kind': 'unrecognized_audio', 'engine': 'Parakeet'}
        return {'text': text, 'input_kind': 'text' if text.strip() else 'unrecognized_audio', 'engine': 'Parakeet'}
    finally:
        audio_lock.release()


@router.post('/transcribe')
async def transcribe(request: Request):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_AUDIO:
            raise HTTPException(413, 'Recording must be at most 30 seconds.')
    return await run_in_threadpool(transcribe_wav, bytes(data))


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@router.post('/speak')
def speak(request: SpeechRequest):
    from voice.adapters import PiperSpeaker
    global speaker
    if not audio_lock.acquire(blocking=False):
        raise HTTPException(503, 'Audio service is busy. Please try again.')
    try:
        if speaker is None:
            speaker = PiperSpeaker(ROOT / 'models/piper/en_US-lessac-high.onnx')
        output = io.BytesIO()
        with wave.open(output, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(speaker.voice.config.sample_rate)
            for chunk in speaker.chunks(request.text):
                wav.writeframes(chunk.audio_int16_bytes)
        return Response(output.getvalue(), media_type='audio/wav', headers={'Cache-Control': 'no-store'})
    finally:
        audio_lock.release()


def close_audio():
    if transcriber is not None:
        transcriber.close()
