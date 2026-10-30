import pytest

from tender_app.platform_clients import PlatformStopped, TenderProClient
from tender_app.platform_public import UGMKClient


class Response:
    status_code = 200
    headers = {}
    encoding = 'utf-8'
    url = TenderProClient.BASE + '/view'
    text = 'safe'

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def raise_for_status(self):
        pass

    def close(self):
        self.closed = True

    def iter_content(self, *_):
        yield b'{"data":{}}'

    def json(self):
        raise AssertionError('Unbounded response.json() must not be used')


def test_tenderpro_never_follows_redirect_with_session_token(monkeypatch):
    client = TenderProClient({})
    monkeypatch.setattr(client, '_wait', lambda: None)

    def get(*args, **kwargs):
        assert kwargs.get('allow_redirects') is False
        assert kwargs.get('stream') is True
        return Response()

    monkeypatch.setattr(client.session, 'get', get)
    try:
        client.page('/view')
    finally:
        client.close()


def test_public_json_is_streamed_with_limit_and_closed(monkeypatch):
    client = UGMKClient()
    response = Response()
    response.iter_content = lambda *_: iter([b'x' * (8 * 1024 * 1024 + 1)])
    monkeypatch.setattr(client, '_request', lambda *args, **kwargs: response)
    try:
        with pytest.raises(PlatformStopped, match='больш'):
            client._json('GET', client.BASE)
        assert response.closed
    finally:
        client.close()


def test_public_json_honors_cancellation(monkeypatch):
    client = UGMKClient(stopped=lambda: True)
    response = Response()
    monkeypatch.setattr(client, '_request', lambda *args, **kwargs: response)
    try:
        with pytest.raises(PlatformStopped, match='останов'):
            client._json('GET', client.BASE)
        assert response.closed
    finally:
        client.close()
