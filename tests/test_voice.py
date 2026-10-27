from types import SimpleNamespace
from unittest.mock import Mock
import json
import sys

import httpx
import pytest

from voice.interfaces import CapturedAudio
from voice.segmentation import SpeechSegmenter, UtteranceTooLong
from voice.transport import VoiceService
from voice.runner import VoiceRunner


@pytest.mark.parametrize('text,expected',[('nine in the evening','21:00'),('eight in the morning','08:00'),('nine p.m.','21:00')])
def test_spoken_hour_words(text,expected):
    from receptionist import parse_time
    assert parse_time(text)==expected


@pytest.mark.parametrize('text,unclear',[
    ('My phone number is 9900112233.',False),
    ('My phone number is 990012233.',True),
    ('My phone number is plus 919900112233.',False),
])
def test_parakeet_rejects_incomplete_phone_and_sends_pcm(text,unclear):
    import io
    import wave
    import numpy as np
    from voice.adapters import ParakeetTranscriber
    from voice.interfaces import PhoneNumberUnclear
    stt=object.__new__(ParakeetTranscriber)
    stt.phone_region='IN';stt.client=Mock()
    stt.client.post.return_value.json.return_value={'text':text}
    audio=CapturedAudio(np.zeros(16000,dtype=np.float32))
    if unclear:
        with pytest.raises(PhoneNumberUnclear):stt.transcribe(audio)
    else:assert stt.transcribe(audio)==text
    payload=stt.client.post.call_args.kwargs['files']['file'][1]
    with wave.open(io.BytesIO(payload)) as wav:
        assert (wav.getframerate(),wav.getnchannels(),wav.getsampwidth(),wav.getnframes())==(16000,1,2,16000)


def test_transcriber_preserves_repeated_digits_and_uses_unbiased_hint():
    from voice.adapters import WhisperTranscriber
    transcriber=object.__new__(WhisperTranscriber)
    transcriber.model=Mock()
    transcriber._verify_phone=Mock(side_effect=lambda audio,text,segments:text)
    transcriber.model.transcribe.return_value=(iter([SimpleNamespace(text='My phone number is 9900-112233.')]),None)
    assert transcriber.transcribe('test.wav')=='My phone number is 9900-112233.'
    options=transcriber.model.transcribe.call_args.kwargs
    assert options['beam_size']==5
    assert options['condition_on_previous_text'] is False
    assert not any(c.isdigit() for c in options['initial_prompt'])


def test_transcriber_does_not_pad_or_repair_short_phone_numbers():
    from voice.adapters import WhisperTranscriber
    from voice.interfaces import PhoneNumberUnclear
    transcriber=object.__new__(WhisperTranscriber)
    transcriber.model=Mock()
    transcriber.model.transcribe.return_value=(iter([SimpleNamespace(text='My phone number is 990012233.',words=[])]),None)
    with pytest.raises(PhoneNumberUnclear):transcriber.transcribe('test.wav')


@pytest.mark.parametrize('text,expected',[
    ('There is nine zero zero zero zero one one one two two.','9000011122'),
    ('Number is plus-nine-one-nine-nine-zero-zero-one-one-two-two-three-three.','+919900112233'),
    ('double nine double zero double one double two double three','9900112233'),
    ('This is the end of this video, thank you for watching',None),
    ('nine zero zero triple',None),
])
def test_literal_phone_decoding_rejects_unrelated_speech(text,expected):
    from voice.adapters import spoken_phone_digits
    assert spoken_phone_digits(text)==expected


def test_uncertain_phone_is_not_submitted_as_customer_text(tmp_path):
    from voice.interfaces import PhoneNumberUnclear
    listener=Mock()
    listener.listen.return_value=CapturedAudio(samples=[])
    stt=Mock()
    stt.transcribe.side_effect=[PhoneNumberUnclear(),PhoneNumberUnclear()]
    service=Mock(session_id='test')
    service.start.return_value='Hello'
    service.unrecognized.side_effect=[{'reply':'Repeat','ended':False},{'reply':'Staff can help','ended':True}]
    speaker=Mock()
    speaker.speak.return_value={'tts_first_chunk_ms':1,'tts_total_ms':2}
    VoiceRunner(listener,stt,speaker,service,tmp_path/'metrics.jsonl').run()
    service.turn.assert_not_called()
    assert service.unrecognized.call_count==2
    assert any('phone digit' in call.args[0] for call in speaker.speak.call_args_list)


@pytest.mark.parametrize('phrase',['phone number is','phone numbers','phone number’s'])
def test_phone_replay_anchors_to_label_when_numeric_timestamp_collapses(phrase):
    from voice.adapters import WhisperTranscriber
    transcriber=object.__new__(WhisperTranscriber)
    transcriber.model=Mock()
    transcriber.phone_region='IN'
    transcriber._digit_suppression=[-1]
    transcriber.model.transcribe.return_value=(iter([SimpleNamespace(text='The number is nine nine zero zero one one two two three three.')]),None)
    words=[SimpleNamespace(word=' phone',start=1,end=1.2),SimpleNamespace(word=' number',start=1.2,end=1.4),
           SimpleNamespace(word=' is',start=1.4,end=1.5),SimpleNamespace(word=' 990012233.',start=2,end=2)]
    original=f'My reference is AB123456. My {phrase} 990012233.'
    result=transcriber._verify_phone(CapturedAudio([0]*48000),original,[SimpleNamespace(words=words)])
    assert result==f'My reference is AB123456. My {phrase} 9900112233.'
    assert len(transcriber.model.transcribe.call_args.args[0])>16000


@pytest.mark.parametrize('literal',['Thank you for watching this video.','nine zero zero zero one one one two two'])
def test_phone_replay_refuses_hallucinated_or_incomplete_number(literal):
    from voice.adapters import WhisperTranscriber
    from voice.interfaces import PhoneNumberUnclear
    transcriber=object.__new__(WhisperTranscriber)
    transcriber.model=Mock();transcriber.phone_region='IN';transcriber._digit_suppression=[-1]
    transcriber.model.transcribe.side_effect=lambda *a,**k:(iter([SimpleNamespace(text=literal)]),None)
    words=[SimpleNamespace(word=' phone',start=0,end=.2),SimpleNamespace(word=' 990012233.',start=.3,end=.3)]
    with pytest.raises(PhoneNumberUnclear):
        transcriber._verify_phone(CapturedAudio([0]*16000),'My phone number is 990012233.',[SimpleNamespace(words=words)])


@pytest.mark.parametrize('literal,raises',[
    ('Thank you for watching this video.',False),
    ('nine eight eight zero zero five five six six seven',True),
])
def test_valid_primary_phone_fallback_and_disagreement(literal,raises):
    from voice.adapters import WhisperTranscriber
    from voice.interfaces import PhoneNumberUnclear
    transcriber=object.__new__(WhisperTranscriber)
    transcriber.model=Mock();transcriber.phone_region='IN';transcriber._digit_suppression=[-1]
    transcriber.model.transcribe.side_effect=lambda *a,**k:(iter([SimpleNamespace(text=literal)]),None)
    words=[SimpleNamespace(word=' phone',start=0,end=.2),SimpleNamespace(word=' 9900112233.',start=.3,end=.3)]
    text='My phone number is 9900112233.'
    if raises:
        with pytest.raises(PhoneNumberUnclear):transcriber._verify_phone(CapturedAudio([0]*16000),text,[SimpleNamespace(words=words)])
    else:
        assert transcriber._verify_phone(CapturedAudio([0]*16000),text,[SimpleNamespace(words=words)])==text


def test_segmenter_keeps_preroll_and_waits_for_end_silence():
    segmenter = SpeechSegmenter(silence_frames=2, pre_roll_frames=2, min_speech_frames=2)
    assert segmenter.feed('old-silence', False) is None
    assert segmenter.feed('preroll', False) is None
    assert segmenter.feed('hello', True) is None
    assert segmenter.feed('there', True) is None
    assert segmenter.feed('end1', False) is None
    assert segmenter.feed('end2', False) == ['preroll','hello','there','end1','end2']
    assert not segmenter.started


def test_segmenter_ignores_noise_and_does_not_submit_truncated_speech():
    segmenter = SpeechSegmenter(silence_frames=1, min_speech_frames=3, max_frames=5)
    segmenter.feed('click', True)
    assert segmenter.feed('silence', False) is None
    assert not segmenter.started
    with pytest.raises(UtteranceTooLong):
        for _ in range(5):
            segmenter.feed('speech', True)


def test_transport_retries_same_message_id_and_marks_voice_channel():
    payloads = []
    def handler(request):
        if request.url.path == '/api/sessions':
            assert json.loads(request.content) == {'channel':'voice'}
            return httpx.Response(201, json={'session_id':'session', 'greeting':'Hello'})
        payloads.append(json.loads(request.content))
        if len(payloads) == 1:
            return httpx.Response(503, json={'detail':'busy'})
        return httpx.Response(200, json={'reply':'Booked', 'ended':False})
    service = VoiceService('http://test', client=httpx.Client(base_url='http://test', transport=httpx.MockTransport(handler)), pause=lambda _:None)
    service.start()
    assert service.turn('yes')['reply'] == 'Booked'
    assert payloads[0] == payloads[1]


def test_runner_two_silences_handoff_and_close(tmp_path):
    listener = Mock()
    listener.listen.side_effect = [TimeoutError(), TimeoutError()]
    service = Mock(session_id='test')
    service.start.return_value = 'Hello'
    service.unrecognized.side_effect = [
        {'reply':'Please try again', 'ended':False},
        {'reply':'Contact staff', 'ended':True,'handoff':True,'staff_phone':'+91 1234567890'}]
    speaker = Mock()
    speaker.speak.return_value = {'tts_first_chunk_ms':1, 'tts_total_ms':2}
    VoiceRunner(listener, Mock(), speaker, service, tmp_path/'metrics.jsonl').run()
    assert service.unrecognized.call_count == 2
    service.turn.assert_not_called()
    assert '9 1 1 2 3 4 5 6 7 8 9 0' in speaker.speak.call_args.args[0]
    service.close.assert_called_once()


def test_runner_uses_transcript_and_does_not_save_raw_audio(tmp_path):
    listener = Mock()
    listener.listen.return_value = CapturedAudio(samples=['private-audio'], endpoint_ms=480)
    stt = Mock()
    stt.transcribe.return_value = 'Opening hours please'
    speaker = Mock()
    speaker.speak.return_value = {'tts_first_chunk_ms':5,'tts_total_ms':10}
    service = Mock(session_id='test')
    service.start.return_value = 'Hello'
    service.turn.return_value = {'reply':'Open today', 'ended':True,'queue_wait_ms':2,'request_id':'request'}
    path = tmp_path/'metrics.jsonl'
    VoiceRunner(listener, stt, speaker, service, path).run()
    service.turn.assert_called_once_with('Opening hours please')
    data = path.read_text()
    assert 'private-audio' not in data and 'Opening hours' not in data
    assert json.loads(data)['endpoint_ms'] == 480


def test_runner_closes_session_after_uncertain_network_failure(tmp_path):
    listener = Mock()
    listener.listen.return_value = CapturedAudio(samples=[])
    stt = Mock()
    stt.transcribe.return_value = 'yes'
    service = Mock()
    service.turn.side_effect = httpx.ReadTimeout('uncertain result')
    with pytest.raises(httpx.ReadTimeout):
        VoiceRunner(listener, stt, Mock(), service, tmp_path/'metrics.jsonl').run()
    service.close.assert_called_once()
    service.turn.assert_called_once()


def test_piper_writes_first_chunk_before_synthesizing_next(monkeypatch):
    from voice.adapters import PiperSpeaker
    import threading
    events = []
    playing=threading.Event()
    second_ready=threading.Event()
    class Stream:
        def __init__(self, **kwargs): pass
        def start(self): events.append('start')
        def write(self, data):
            events.append('write:'+data.decode())
            if data==b'first':
                playing.set()
                assert second_ready.wait(2),'Later synthesis did not overlap first playback'
                events.append('first-playback-finished')
        def stop(self): events.append('stop')
        def close(self): events.append('close')
    def synthesize(text):
        events.append('synth1')
        yield SimpleNamespace(sample_rate=22050,sample_channels=1,audio_int16_bytes=b'first')
        assert playing.wait(2)
        events.append('synth2')
        second_ready.set()
        yield SimpleNamespace(sample_rate=22050,sample_channels=1,audio_int16_bytes=b'second')
    monkeypatch.setitem(sys.modules,'sounddevice',SimpleNamespace(RawOutputStream=Stream))
    speaker = object.__new__(PiperSpeaker)
    speaker.voice = SimpleNamespace(synthesize=synthesize)
    speaker.device = None
    result = speaker.speak('First sentence. Second sentence.')
    assert events.index('write:first') < events.index('synth2')
    assert events.index('synth2') < events.index('first-playback-finished')
    assert events[-2:] == ['stop','close']
    assert result['tts_first_chunk_ms'] >= 0
