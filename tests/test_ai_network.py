"""Bounded waiting and provider recovery, with no external API requests."""
import io
import json
from email.message import Message
from email.utils import formatdate
from urllib.error import HTTPError

import pytest

from tender_app.ai_config import AIError, Profile
from tender_app.ai_client import ModelClient
from tests.test_ai_usage import client_with_responses, reply


def limited(seconds):
    headers=Message();headers['Retry-After']=seconds
    return HTTPError('https://example.invalid',429,'private',headers,io.BytesIO(b'private'))


def test_retry_after_respected_with_progress_and_accounting(monkeypatch):
    client=client_with_responses(monkeypatch,[limited('3'),reply()])
    waits=[];progress=[];client.progress=progress.append
    monkeypatch.setattr(client.cancel,'wait',lambda seconds: waits.append(seconds) or False)
    client.complete([])
    assert waits==[3] and len(client.usage_events)==2
    assert any('3' in text and 'повтор' in text.lower() for text in progress)


def test_long_retry_after_is_not_shortened_or_replayed(monkeypatch):
    client=client_with_responses(monkeypatch,[limited('120')])
    with pytest.raises(AIError,match='120'):
        client.complete([])
    assert client.requests==1


def test_retry_after_http_date(monkeypatch):
    import tender_app.ai_client as module
    monkeypatch.setattr(module.time,'time',lambda:100000)
    client=client_with_responses(monkeypatch,[limited(formatdate(100010,usegmt=True)),reply()])
    waits=[];monkeypatch.setattr(client.cancel,'wait',lambda seconds:waits.append(seconds) or False)
    client.complete([])
    assert waits==[10]


def test_invalid_retry_after_uses_bounded_default(monkeypatch):
    client=client_with_responses(monkeypatch,[limited('NaN'),reply()])
    waits=[];monkeypatch.setattr(client.cancel,'wait',lambda seconds:waits.append(seconds) or False)
    client.complete([])
    assert waits==[5]


def test_cancel_during_backoff_never_sends_second_attempt(monkeypatch):
    client=client_with_responses(monkeypatch,[limited('3')])
    monkeypatch.setattr(client.cancel,'wait',lambda seconds:True)
    with pytest.raises(AIError,match='остановлен'):client.complete([])
    assert client.requests==1


def test_retry_wait_cannot_exceed_remaining_turn_budget(monkeypatch):
    import tender_app.ai_client as module
    client=client_with_responses(monkeypatch,[limited('10')])
    client.deadline=module.time.monotonic()+2
    with pytest.raises(AIError):client.complete([])
    assert client.requests==1


def test_openrouter_error_inside_http_200_is_handled_as_temporary(monkeypatch):
    client=client_with_responses(monkeypatch,[{'error':{'code':503,'message':'PRIVATE'}},reply()])
    assert json.loads(client.complete([]))['kind']=='answer'
    assert client.usage_events[0]['status']=='api_error'
    assert client.usage_events[0]['api_error_code']==503
    assert 'PRIVATE' not in json.dumps(client.usage_events)


def test_keepalive_cannot_extend_response_deadline(monkeypatch):
    import tender_app.ai_client as module
    clock=[0.0]
    monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    class Response:
        status=200;headers=Message();length=None
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read1(self,size):clock[0]+=1;return b' '
    class Opener:
        def open(self,*args,**kwargs):return Response()
    monkeypatch.setattr(module,'build_opener',lambda *args:Opener())
    client=ModelClient(Profile(provider='openrouter',model='test:free',timeout=5))
    with pytest.raises(AIError,match='ожидания|таймаут|ответ'):
        client.complete([])
    assert clock[0]<=5 and client.requests==1
    assert client.usage_events[0]['total_tokens'] is None


def test_cancel_during_body_stops_without_retry(monkeypatch):
    import tender_app.ai_client as module
    client=ModelClient(Profile(provider='openrouter',model='test:free'))
    class Response:
        status=200;headers=Message();length=None
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read1(self,size):client.cancel.set();return b' '
    class Opener:
        def open(self,*args,**kwargs):return Response()
    monkeypatch.setattr(module,'build_opener',lambda *args:Opener())
    with pytest.raises(AIError,match='остановлен'):client.complete([])
    assert client.requests==1


def test_http_200_auth_error_never_retried_or_exposed(monkeypatch):
    client=client_with_responses(monkeypatch,[{'error':{'code':401,'message':'PRIVATE'}}])
    with pytest.raises(AIError) as error:client.complete([])
    assert client.requests==1 and 'PRIVATE' not in str(error.value)


def test_second_temporary_error_stops_at_two_attempts(monkeypatch):
    client=client_with_responses(monkeypatch,[limited('3'),limited('3')])
    with pytest.raises(AIError):client.complete([])
    assert client.requests==2 and len(client.usage_events)==2
