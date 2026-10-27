from unittest.mock import Mock
from fastapi import FastAPI
from fastapi.testclient import TestClient
import voice_test_jobs as jobs


def test_background_test_start_and_duplicate_guard(monkeypatch):
    monkeypatch.setattr(jobs,'state',{'status':'idle','scenarios':[]})
    thread=Mock()
    monkeypatch.setattr(jobs,'Thread',Mock(return_value=thread))
    app=FastAPI();app.include_router(jobs.router)
    with TestClient(app) as client:
        response=client.post('/api/voice/tests')
        assert response.status_code==202
        assert len(response.json()['scenarios'])==len(jobs.CASES)
        thread.start.assert_called_once()
        assert client.post('/api/voice/tests').status_code==409
        assert client.get('/api/voice/tests').json()['status']=='running'
