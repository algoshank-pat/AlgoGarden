import time
from uuid import uuid4
import httpx


class VoiceService:
    def __init__(self, base_url: str, client=None, pause=time.sleep):
        self.client = client or httpx.Client(base_url=base_url, timeout=360)
        self.pause = pause
        self.session_id = None

    def start(self) -> str:
        response = self.client.post('/api/sessions', json={'channel': 'voice'})
        response.raise_for_status()
        data = response.json()
        self.session_id = data['session_id']
        return data['greeting']

    def warmup(self):
        response=self.client.post('/api/voice/warmup',timeout=120)
        response.raise_for_status()
        return response.json()

    def turn(self, text: str, input_kind: str = 'text') -> dict:
        payload = {'request_id': str(uuid4()), 'text': text, 'input_kind': input_kind}
        # Retries keep the same ID. Never submit a new booking request after a
        # timeout whose outcome is unknown.
        for attempt in range(3):
            try:
                response = self.client.post(f'/api/sessions/{self.session_id}/turns', json=payload)
            except httpx.TransportError:
                if attempt == 2:
                    raise
                self.pause(2)
                continue
            if response.status_code in (409, 503) and attempt < 2:
                self.pause(2)
                continue
            response.raise_for_status()
            return response.json()
        raise RuntimeError('No response received')

    def unrecognized(self) -> dict:
        return self.turn('[No intelligible speech detected]', input_kind='unrecognized_audio')

    def close(self):
        if self.session_id:
            try:
                self.client.delete(f'/api/sessions/{self.session_id}', timeout=2)
            except httpx.HTTPError:
                pass
        self.client.close()
