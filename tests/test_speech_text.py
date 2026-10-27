import pytest
from voice.speech_text import natural_speech


def test_live_example_reads_as_natural_spoken_date_and_time():
    text=natural_speech('2026-10-28 at 19:00')
    assert text=='the twenty-eighth of October, twenty twenty-six at seven in the evening'


@pytest.mark.parametrize('raw,expected',[
    ('20:30','half past eight in the evening'),('08:05','eight oh five in the morning'),
    ('12:00','noon'),('00:00','midnight'),('23:00','eleven at night'),
    ('07:00 PM','seven in the evening'),('13:15','one fifteen in the afternoon'),
])
def test_spoken_time(raw,expected):
    assert natural_speech(raw)==expected


def test_phone_reference_and_bad_date_are_not_rewritten():
    text='Phone 6 1 2 6 6 6 1 4 2 3. Reference abc12345. Invalid 2026-02-30.'
    assert natural_speech(text)==text


def test_normalization_is_idempotent():
    text=natural_speech('2026-10-28 at 19:00')
    assert natural_speech(text)==text


def test_suggested_alternatives_have_spoken_ordinals_and_periods():
    result=natural_speech('Friday, October 30, 2026 at 07:00 PM; or Saturday, October 31, 2026 at 08:30 PM.')
    assert result=='Friday, the thirtieth of October, twenty twenty-six at seven in the evening; or Saturday, the thirty-first of October, twenty twenty-six at half past eight in the evening.'


@pytest.mark.parametrize('text',[
    'Table for 2 on 2026-10-28 at 19:00. Is that correct?',
    'Your table is booked for 2026-10-28 at 19:00.',
    'I can offer Friday, October 30, 2026 at 07:00 PM.',
    'Change 2026-10-28 at 19:00 to 2026-10-29 at 20:30. Is that correct?',
    'Your reservation is updated to 2 guests on 2026-10-29 at 20:30.',
])
def test_actual_piper_adapter_receives_spoken_text(text):
    from voice.adapters import PiperSpeaker
    from collections import OrderedDict
    from types import SimpleNamespace
    from unittest.mock import Mock
    speaker=object.__new__(PiperSpeaker);speaker._cache=OrderedDict()
    speaker.voice=SimpleNamespace(synthesize=Mock(return_value=iter([])))
    list(speaker.chunks(text))
    speaker.voice.synthesize.assert_called_once_with(natural_speech(text))
def test_audio_assertion_accepts_actual_afternoon_and_rejects_missing_periods():
    from test_voice_natural_dates import natural_audio_matches
    assert natural_audio_matches('October, half past one in the afternoon','October at half past one in the afternoon','2026-10-29')
    assert not natural_audio_matches('October, afternoon to evening','October in the evening','2026-10-29')
    assert not natural_audio_matches('October in the evening','','2026-10-29')

