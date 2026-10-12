import asyncio
import errno
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.routes import api
from app.clients.lastfm_client import LastFMClient, MusicClientError
from app.core.config import Settings, get_settings
from app.db.database import get_db
from app.main import app


def test_env_path_does_not_depend_on_launch_directory(tmp_path, monkeypatch):
    expected = Path(__file__).resolve().parents[1] / '.env'
    monkeypatch.chdir(tmp_path)
    assert Settings.model_config['env_file'] == expected
    assert Settings().mock_mode == Settings(_env_file=expected).mock_mode


def test_live_mode_does_not_silently_use_mock_without_key(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, 'mock_mode', False)
    monkeypatch.setattr(settings, 'lastfm_api_key', '')
    assert LastFMClient()._is_mock() is False
    client = TestClient(app)
    health = client.get('/api/health').json()
    assert health == {'status': 'configuration_error', 'mock_mode': False}
    response = client.post('/api/recommend', json={'request_text': 'recommend music'})
    assert response.status_code == 503
    assert 'LASTFM_API_KEY' in response.json()['detail']


def test_recommendation_deadline_cancels_work_and_rolls_back(monkeypatch):
    class Database:
        rolled_back = False

        def rollback(self):
            self.rolled_back = True

    database = Database()
    cancelled = []

    def override():
        yield database

    async def slow_run(*args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    monkeypatch.setattr(get_settings(), 'recommendation_timeout_seconds', 0.01)
    monkeypatch.setattr(api.agent, 'run', slow_run)
    app.dependency_overrides[get_db] = override
    try:
        client = TestClient(app)
        response = client.post('/api/recommend', json={'request_text': 'recommend music'})
        assert response.status_code == 504
        assert cancelled and database.rolled_back
        assert client.get('/api/health').status_code == 200
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize('failure, expected', [
    ('permission', '套接字权限错误'),
    ('connect', '尚未验证 API Key'),
    ('proxy', '代理连接失败'),
    ('timeout', '连接 Last.fm 超时'),
    ('http', 'HTTP 503'),
])
def test_live_errors_identify_failure_without_exposing_key(monkeypatch, caplog, failure, expected):
    secret = 'test-key-must-not-be-disclosed'
    monkeypatch.setattr(get_settings(), 'lastfm_api_key', secret)
    original_client = httpx.AsyncClient

    def respond(request):
        if failure == 'http':
            return httpx.Response(503, request=request)
        if failure == 'permission':
            try:
                raise PermissionError(errno.EACCES, 'denied')
            except PermissionError as cause:
                raise httpx.ConnectError(str(request.url), request=request) from cause
        exception = {
            'connect': httpx.ConnectError,
            'proxy': httpx.ProxyError,
            'timeout': httpx.ConnectTimeout,
        }[failure]
        raise exception(str(request.url), request=request)

    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(respond)))
    with pytest.raises(MusicClientError, match=expected) as error:
        asyncio.run(LastFMClient()._request({'method': 'chart.getTopTracks'}))
    assert secret not in str(error.value)
    assert secret not in caplog.text
