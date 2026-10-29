"""Acoustic second pass for short, consented survey answers only."""
import re
import numpy as np
_model=None


def candidate(text):
    clean=' '.join(re.sub(r'[^a-z ]',' ',text.lower()).split())
    return clean if clean in {'good','bad','skip'} else None


def recognize(samples,primary,root):
    if candidate(primary) or len(samples)>16000*3 or len(primary.split())>3:
        return primary
    # Never turn silence/noise or a longer sentence into a forced survey choice.
    if len(samples)<1600 or float(np.sqrt(np.mean(samples*samples)))<.008:
        return primary
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model=WhisperModel('small',device='cpu',compute_type='int8',cpu_threads=4,
                            download_root=str(root/'models/whisper'),local_files_only=True)
    padded=np.pad(samples,(4000,8000))
    segments,_=_model.transcribe(padded,language='en',beam_size=5,temperature=0,
        initial_prompt='A customer answers a survey: good, bad, or skip.',
        condition_on_previous_text=False,vad_filter=False)
    segments=list(segments)
    text=' '.join(segment.text.strip() for segment in segments)
    answer=candidate(text)
    if answer and segments and all(segment.avg_logprob>=-0.9 and segment.no_speech_prob<.5 for segment in segments):
        return answer
    return primary
