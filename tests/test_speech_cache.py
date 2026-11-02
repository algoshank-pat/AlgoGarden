"""Speech-only changes must preserve data, intent and confirmation semantics."""
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from voice.adapters import PiperSpeaker
from voice.phrases import concise_speech, cacheable, speech_segments, SHORTEN
from voice.speech_text import natural_speech


def speaker():
    s = object.__new__(PiperSpeaker)
    s._cache = OrderedDict()
    s.synthesis_config = SimpleNamespace(length_scale=1.03)
    s.voice = SimpleNamespace(synthesize=Mock(side_effect=lambda *a, **kw: iter([b'one', b'two'])))
    return s


@pytest.mark.parametrize('original,short', SHORTEN.items())
def test_templates_are_shorter_and_idempotent(original, short):
    assert concise_speech(original) == short
    assert concise_speech(short) == short
    assert len(short) <= len(original)


@pytest.mark.parametrize('text', [
    'Table for 4 on 2027-02-17 at 20:30, under Priya Shah, phone 2 0 2 5 5 5 0 1 7 3. Is that correct?',
    'Change the reservation from 19:00 to 20:30. Please confirm before I update it.',
    'All outside food is prohibited, including birthday cakes. Advance notice does not change this.',
    'Gluten-free options are available. Please discuss cross-contamination with staff.',
    'I can offer 2027-02-17 at 18:00 or 2027-02-18 at 19:00. Would either work?',
    'Your next spoken feedback comment will be saved as a voice note and transcript. Say skip if you do not want to leave one.',
])
def test_details_conditions_and_consent_pass_through(text):
    assert concise_speech(text) == text
    assert ' '.join(speech_segments(text)) == natural_speech(text)
    assert not cacheable(natural_speech(text))


@pytest.mark.parametrize('name',["Priya Shah","Sam O'Neill","Anne-Marie Davis","A B Lamb"])
def test_short_name_readback_keeps_complete_name_and_confirmation(name):
    text=f'I heard {name}. Is that name correct?'
    assert concise_speech(text)==f'I heard {name}. Is that right?'
    assert not cacheable(concise_speech(text))
    assert concise_speech(concise_speech(text))==concise_speech(text)


def test_exact_audio_reused_but_settings_voice_and_business_cannot_collide():
    s = speaker()
    a = list(s.chunks('Do you have a reservation with us?'))
    assert list(s.chunks('Do you already have a reservation?')) == a
    assert s.voice.synthesize.call_count == 1
    s.synthesis_config.length_scale = 1.1
    list(s.chunks('Do you already have a reservation?'))
    assert s.voice.synthesize.call_count == 2
    for name in ['Algo bites', 'Another Restaurant', 'Algo bites']:
        list(s.chunks(f'Thank you for calling {name}. How can I help you today?'))
    assert s.voice.synthesize.call_count == 4
    new = Mock(side_effect=lambda *a, **kw: iter([b'new']))
    s.voice = SimpleNamespace(synthesize=new)
    assert list(s.chunks('Do you already have a reservation?')) == [b'new']
    new.assert_called_once()


@pytest.mark.parametrize('text', [
    'Thanks for calling Algo bites. How can I help? My phone is 2025550173.',
    'Thank you for calling Sam. Your reservation is confirmed.',
    'I heard Priya Shah. Is that name correct?',
    'No tables are available at 19:00.',
    'Dogs are not permitted indoors.',
])
def test_personalized_and_business_results_never_cached(text):
    s = speaker()
    list(s.chunks(text)); list(s.chunks(text))
    assert s.voice.synthesize.call_count == 2 and not s._cache


def test_interrupted_and_failed_synthesis_does_not_cache_partial_audio():
    s = speaker()
    stream = s.chunks('Do you already have a reservation?')
    assert next(stream) == b'one'
    stream.close()
    assert not s._cache
    def broken(*a, **kw):
        yield b'first'
        raise RuntimeError('synthesis failed')
    s.voice.synthesize.side_effect = broken
    with pytest.raises(RuntimeError): list(s.chunks('Do you already have a reservation?'))
    assert not s._cache


def test_lru_bound_and_instance_isolation():
    s = speaker(); s._cache_limit = 2
    for name in ['One', 'Two', 'Three']:
        list(s.chunks(f'Thanks for calling {name}. How can I help?'))
    assert len(s._cache) == 2
    assert not any('One' in key[-1] for key in s._cache)
    assert not speaker()._cache


def test_both_browser_audio_routes_shorten_before_splitting(monkeypatch):
    import browser_voice as browser
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    seen = []
    def chunks(text):
        seen.append(text)
        yield SimpleNamespace(audio_int16_bytes=b'\0\0' * 100)
    monkeypatch.setattr(browser, 'speaker', SimpleNamespace(chunks=chunks,voice=SimpleNamespace(config=SimpleNamespace(sample_rate=22050))))
    app = FastAPI(); app.include_router(browser.router)
    with TestClient(app) as client:
        for endpoint in ['/api/voice/speak', '/api/voice/speak/stream']:
            assert client.post(endpoint,json={'text':'Do you have a reservation with us?'}).status_code == 200
    assert seen == ['Do you already have a reservation?'] * 2
