import io
import wave
from unittest.mock import Mock
from voice.greeting import recording


def test_recording_survives_process_cache_and_invalidates_business_voice_and_wording(tmp_path):
    for name in ['models/piper/en_US-lessac-high.onnx','models/piper/en_US-lessac-high.onnx.json','voice/adapters.py','voice/phrases.py','voice/speech_text.py']:
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('fixture')
    output=io.BytesIO()
    with wave.open(output,'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(22050);wav.writeframes(b'\0\0'*100)
    synth=Mock(return_value=output.getvalue());restaurant={'id':'private','name':'Private Restaurant'}
    a,data=recording(tmp_path,restaurant,synth);assert synth.call_count==1
    recording(tmp_path,restaurant,Mock(side_effect=AssertionError('Must use persisted recording')))
    b,_=recording(tmp_path,{**restaurant,'id':'other'},synth);assert a['version']!=b['version']
    c,_=recording(tmp_path,{**restaurant,'name':'Renamed Restaurant'},synth);assert a['version']!=c['version']
    (tmp_path/'models/piper/en_US-lessac-high.onnx').write_text('different voice')
    d,_=recording(tmp_path,restaurant,synth);assert a['version']!=d['version']
    assert synth.call_count==4 and data==output.getvalue()
    (tmp_path/'data/speech-greetings'/f'{d["version"]}.wav').write_bytes(b'broken')
    recording(tmp_path,restaurant,synth);assert synth.call_count==5
    cue,spoken=recording(tmp_path,restaurant,synth,text='One moment.')
    assert cue['text']=='One moment.' and cue['version']!=d['version'] and spoken==data
    recording(tmp_path,restaurant,Mock(side_effect=AssertionError('Cue must use persistent cache')),text='One moment.')
