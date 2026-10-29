"""Consent-gated voice notes; never passed to the business router or STT."""
import io
import wave
import time
from uuid import UUID
from fastapi import HTTPException
from fastapi.responses import Response
import local_audit as audit

MAX_AUDIO=16000*2*30+44


def save(session_id,note_id,data):
    import chat_api as api
    with api.lock:
        session=api.sessions.get(str(session_id))
        if session is None:raise HTTPException(404,'Conversation not found.')
        if not session.turn_lock.acquire(blocking=False):raise HTTPException(409,'A conversation turn is in progress.')
    try:
        if session.stopped or time.monotonic()-session.touched>api.TTL_SECONDS:raise HTTPException(409,'Conversation is no longer active.')
        if session.survey.get('status')!='awaiting_comment' or not session.survey.get('consented_at'):
            raise HTTPException(409,'Please accept the optional feedback invitation first.')
        try:
            with wave.open(io.BytesIO(data),'rb') as audio:
                if (audio.getnchannels(),audio.getsampwidth(),audio.getframerate())!=(1,2,16000):raise ValueError('Use mono 16 kHz PCM WAV.')
                frames=audio.readframes(480001)
                if not frames or len(frames)>960000 or len(frames)!=audio.getnframes()*2:raise ValueError('Use a complete recording up to 30 seconds.')
        except (wave.Error,EOFError,ValueError) as error:raise HTTPException(422,str(error) or 'Invalid WAV.')
        notes=session.survey.get('voice_notes',[])
        existing=next((n for n in notes if n['id']==str(note_id)),None)
        directory=audit.DATA_ROOT/'feedback_audio'/str(session_id)
        path=directory/(str(note_id)+'.wav')
        if existing:
            if path.read_bytes()!=data:raise HTTPException(409,'Note identifier already used.')
            return existing
        if len(notes)>=1:raise HTTPException(409,'One voice note per survey is supported.')
        directory.mkdir(parents=True,exist_ok=True)
        note={'id':str(note_id),'url':f'/api/sessions/{session_id}/feedback/voice-notes/{note_id}','seconds':len(frames)/32000,'at':audit.now()}
        path.write_bytes(data)
        audit.append(audit.DATA_ROOT/'feedback_notes.jsonl',{'session_id':str(session_id),'note':note})
        session.survey['voice_notes']=[*notes,note]
        session.touched=time.monotonic()
        return note
    finally:session.turn_lock.release()


def read(session_id,note_id):
    path=audit.DATA_ROOT/'feedback_audio'/str(session_id)/(str(note_id)+'.wav')
    if not path.is_file():raise HTTPException(404,'Voice note not found.')
    return Response(path.read_bytes(),media_type='audio/wav',headers={'Cache-Control':'no-store'})
