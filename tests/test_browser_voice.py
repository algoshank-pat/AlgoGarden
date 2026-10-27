import io
import wave
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
import browser_voice as voice


def client():
    app = FastAPI()
    app.include_router(voice.router)
    return TestClient(app)


def wav(rate=16000):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(b'\0\0' * 1600)
    return buffer.getvalue()


def test_audio_limits_and_invalid_format():
    with client() as api:
        assert api.post('/api/voice/transcribe', content=b'bad').status_code == 422
        assert api.post('/api/voice/transcribe', content=wav(8000)).status_code == 422
        assert api.post('/api/voice/transcribe', content=b'x' * (voice.MAX_AUDIO + 1)).status_code == 413


def test_audio_transcription_contract(monkeypatch):
    observed = []
    def transcribe(audio):
        observed.append(audio)
        return 'My phone number is 9900112233'
    monkeypatch.setattr(voice, 'transcriber', SimpleNamespace(transcribe=transcribe))
    with client() as api:
        result = api.post('/api/voice/transcribe', content=wav()).json()
    assert result == {'text': 'My phone number is 9900112233', 'input_kind': 'text', 'engine': 'Parakeet'}
    assert observed[0].sample_rate == 16000
    assert len(observed[0].samples) == 1600


def test_unclear_phone_does_not_invent_digits(monkeypatch):
    from voice.interfaces import PhoneNumberUnclear
    def transcribe(audio):
        raise PhoneNumberUnclear()
    monkeypatch.setattr(voice, 'transcriber', SimpleNamespace(transcribe=transcribe))
    with client() as api:
        result = api.post('/api/voice/transcribe', content=wav()).json()
    assert result['text'] == '' and result['input_kind'] == 'unrecognized_audio'


def test_piper_wav_output_and_backpressure(monkeypatch):
    monkeypatch.setattr(voice, 'speaker', SimpleNamespace(
        voice=SimpleNamespace(config=SimpleNamespace(sample_rate=22050)),
        chunks=lambda text: [SimpleNamespace(audio_int16_bytes=b'\0\0' * 100)]))
    with client() as api:
        response = api.post('/api/voice/speak', json={'text': 'Hello'})
        assert response.status_code == 200 and response.headers['content-type'] == 'audio/wav'
        with wave.open(io.BytesIO(response.content)) as output:
            assert output.getframerate() == 22050 and output.getnframes() == 100
        with voice.audio_lock:
            assert api.post('/api/voice/speak', json={'text': 'Hello'}).status_code == 503
