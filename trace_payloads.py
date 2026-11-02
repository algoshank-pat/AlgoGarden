"""Bounded developer trace payloads; credentials and labelled contact fields are removed."""
import json
import os
import re
from structured_io import json_value

_REDACT={'phone','phone_number','name','customer_name','guest_name','email','audio','samples','audio_int16_bytes','thinking','reasoning'}

def enabled():return os.getenv('LANGSMITH_PAYLOAD_MODE','summary').lower()=='developer'

def sanitize(value,depth=0):
    if depth>8:return '[depth limit]'
    value=json_value(value)
    if isinstance(value,dict):
        return {k:('[redacted]' if k.lower() in _REDACT or any(word in k.lower() for word in ('api_key','password','authorization','secret','access_token')) else sanitize(v,depth+1)) for k,v in list(value.items())[:60]}
    if isinstance(value,list):return [sanitize(v,depth+1) for v in value[:40]]
    if isinstance(value,str):
        # Structured content can itself be encoded JSON (e.g. model output).
        try:
            parsed=json.loads(value)
            if isinstance(parsed,(dict,list)):return sanitize(parsed,depth+1)
        except (ValueError,TypeError):pass
        key=os.getenv('LANGSMITH_API_KEY','')
        if key:value=value.replace(key,'[redacted]')
        value=re.sub(r'\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b','[email redacted]',value,flags=re.I)
        value=re.sub(r'(?<!\w)\+?\d(?:[ ().-]*\d){9,14}(?!\w)','[phone redacted]',value)
        value=re.sub(r'\b(?:sk-proj-|ghp_|github_pat_|lsv2_)[A-Za-z0-9_-]+','[credential redacted]',value)
        return value[:4000]+('[truncated]' if len(value)>4000 else '')
    return value

def payload(value):
    try:return sanitize(value)
    except Exception:return {'payload_unavailable':True}
