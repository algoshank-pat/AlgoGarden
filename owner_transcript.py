"""Owner transcript assembly and evidence-based response provenance."""
def provenance(result, events=()):
    calls=(result.get('structured') or {}).get('calls',[])
    components={c.get('component','') for c in calls}
    names={e.get('name','') for e in events}
    labels=[]
    reason=(result.get('routing') or {}).get('reason','')
    if reason in {'pending_booking_status','current_call_booking_status','reservation_return','conversation_memory'}:
        labels.append('Memory')
    if 'search_knowledge' in components or any(n.startswith('search_knowledge.') for n in names):
        labels.append('RAG')
    if any(n.startswith('mcp.lookup.') for n in names):
        labels.append('MCP')
    if 'ollama.chat' in components or any(n.startswith('ollama.chat.') for n in names):
        labels.append('LLM')
    if not labels:
        labels.append('Rule / template' if result.get('routing') or calls else 'Not recorded')
    return labels


def lifecycle(audit, session_id, channel, kind, text):
    audit.append(audit.CALLS_FILE.with_name('conversation_lifecycle.jsonl'),
                 {'session_id':str(session_id),'channel':channel,'at':audit.now(),
                  'kind':kind,'text':text})


def transcript(turns, lifecycle_rows):
    rows=[]
    for row in lifecycle_rows:
        rows.append({'role':'assistant' if row['kind']=='greeting' else 'system',
                     'at':row['at'],'text':row['text'],'sources':['Rule / template'],
                     'kind':row['kind']})
    for turn in turns:
        rows.append({'role':'user','at':turn['at'],'text':turn.get('caller_text',''),
                     'kind':'message'})
        result=turn.get('result') or {}
        rows.append({'role':'assistant','at':turn['at'],
                     'text':result.get('spoken_reply') if turn.get('channel')=='voice' and result.get('spoken_reply') else result.get('reply',''),
                     'sources':result.get('response_sources') or provenance(result,turn.get('events',[])),
                     'kind':'message'})
    return sorted(rows,key=lambda row:row['at'])
