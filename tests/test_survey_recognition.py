from types import SimpleNamespace
from pathlib import Path
import numpy as np
import pytest
from voice import survey_recognition as recognition

@pytest.mark.parametrize('word',['good','bad','skip'])
def test_short_answer_acoustic_second_pass(monkeypatch,word):
    calls=[]
    def transcribe(samples,**kwargs):
        calls.append(kwargs)
        return iter([SimpleNamespace(text=word,avg_logprob=-.1,no_speech_prob=.01)]),None
    monkeypatch.setattr(recognition,'_model',SimpleNamespace(transcribe=transcribe))
    assert recognition.recognize(np.full(8000,.1,dtype=np.float32),'Cool.',Path('.'))==word
    assert len(calls)==1

@pytest.mark.parametrize('text,score,silence',[('good',-2,.01),('good',-.1,.9),('good or bad',-.1,.01),('not good',-.1,.01)])
def test_uncertain_audio_never_forces_answer(monkeypatch,text,score,silence):
    monkeypatch.setattr(recognition,'_model',SimpleNamespace(transcribe=lambda *a,**k:(iter([SimpleNamespace(text=text,avg_logprob=score,no_speech_prob=silence)]),None)))
    assert recognition.recognize(np.full(8000,.1,dtype=np.float32),'Cool.',Path('.'))=='Cool.'

def test_silence_and_existing_answer_bypass_second_pass(monkeypatch):
    monkeypatch.setattr(recognition,'_model',SimpleNamespace(transcribe=lambda *a,**k:pytest.fail('Should not decode')))
    assert recognition.recognize(np.zeros(8000,dtype=np.float32),'',Path('.'))==''
    assert recognition.recognize(np.full(8000,.1,dtype=np.float32),'Bad.',Path('.'))=='Bad.'
