from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from voice.phrases import spoken_text,speech_segments,SHORTEN
from tests.test_customer_feedback import client,say

@pytest.mark.parametrize('text',list(SHORTEN)+[
 'Table for 4 on 2027-01-14 at 20:30. Is that correct?',
 'Your reservation is updated to 4 guests on 2027-01-14 at 20:30.',
 'Cancel the reservation on 2027-01-14 at 20:30? Please say yes to confirm.',
 'Thank you for calling Algo Garden. How can I help you today?'])
def test_canonical_transcript_matches_synthesized_segments(text):
 from voice.adapters import PiperSpeaker
 canonical=spoken_text(text)
 assert spoken_text(canonical)==canonical
 assert ' '.join(speech_segments(text))==canonical
 synth=Mock(side_effect=lambda text,**kwargs:iter([]))
 speaker=object.__new__(PiperSpeaker);speaker._cache=OrderedDict();speaker.voice=SimpleNamespace(synthesize=synth)
 for segment in speech_segments(canonical):list(speaker.chunks(segment))
 assert ' '.join(call.args[0] for call in synth.call_args_list)==canonical

def test_voice_api_returns_matching_greeting_reply_and_structured_output(client):
 info=client.post('/api/sessions',json={'channel':'voice'}).json()
 assert info['spoken_greeting']==spoken_text(info['greeting'])
 result=say(client,info['session_id'],'What are your opening hours?')
 assert result['spoken_reply']==spoken_text(result['reply'])
 assert result['structured']['output']['spoken_reply']==result['spoken_reply']
 assert 'Anything else I can help with?' in result['spoken_reply']

def test_chat_reply_is_unchanged(client):
 info=client.post('/api/sessions',json={'channel':'chat'}).json()
 result=say(client,info['session_id'],'What are your opening hours?')
 assert 'spoken_reply' not in result
 assert result['reply'].endswith('Is there anything else I can help you with?')
