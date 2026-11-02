import json
import chat_api as api
from tests.test_customer_feedback import client,say


def test_capture_state_is_correlated_content_free_and_locally_saved(client):
 sid=client.post('/api/sessions',json={'channel':'voice'}).json()['session_id']
 result=say(client,sid,'What are your opening hours?')
 payload={'request_id':result['request_id'],'phase':'playing','track_enabled':False,'track_live':True,'manually_muted':True,'audio_context':'running','audio_frames_received':True,'transcript':'must never be saved'}
 assert client.post(f'/api/sessions/{sid}/capture-observations',json=payload).status_code==200
 rows=api.audit.read(api.audit.CALLS_FILE.with_name('voice_capture.jsonl'))
 assert rows[-1]['session_id']==sid and rows[-1]['request_id']==result['request_id']
 assert 'transcript' not in rows[-1] and rows[-1]['track_enabled'] is False
 payload['request_id']='00000000-0000-0000-0000-000000000001'
 assert client.post(f'/api/sessions/{sid}/capture-observations',json=payload).status_code==409
