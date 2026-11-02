"""Persistent public welcome recording; no customer text or model generation."""
import hashlib
import io
import json
import os
import wave
from functools import lru_cache
from pathlib import Path
from threading import Lock
from uuid import uuid4

_lock = Lock()


@lru_cache(maxsize=16)
def _digest(path, size, modified):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def recording(root, restaurant, synthesize, cache_root=None, *, text=None):
    from voice.phrases import spoken_text
    text=spoken_text(text if text is not None else f"Thank you for calling {restaurant['name']}. How can I help you today?")
    # Exact business, wording, voice weights/config and synthesis code. A restart
    # reuses audio; a renamed restaurant or changed voice gets a new recording.
    paths=['models/piper/en_US-lessac-high.onnx','models/piper/en_US-lessac-high.onnx.json',
           'voice/adapters.py','voice/phrases.py','voice/speech_text.py']
    fingerprints={}
    for name in paths:
        p=root/name;stat=p.stat()
        fingerprints[name]=_digest(str(p),stat.st_size,stat.st_mtime_ns)
    key=hashlib.sha256(json.dumps({'business':restaurant['id'],'text':text,'voice':fingerprints},sort_keys=True).encode()).hexdigest()
    directory=cache_root or root/'data/speech-greetings'
    path=directory/(key+'.wav')
    def valid(data):
        try:
            with wave.open(io.BytesIO(data),'rb') as audio:
                return audio.getnchannels()==1 and audio.getsampwidth()==2 and audio.getnframes()>0
        except (wave.Error,EOFError):return False
    with _lock:
        data=path.read_bytes() if path.exists() else b''
        if not valid(data):
            data=synthesize(text)
            if not valid(data):raise RuntimeError('Welcome recording could not be prepared.')
            directory.mkdir(parents=True,exist_ok=True)
            temporary=directory/(key+'.'+str(uuid4())+'.tmp')
            try:
                temporary.write_bytes(data);os.replace(temporary,path)
            finally:
                temporary.unlink(missing_ok=True)
    return {'text':text,'version':key},data
