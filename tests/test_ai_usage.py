"""Accounting uses provider counters, including failed/abandoned turns; no network."""
import io
import json
from email.message import Message
from urllib.error import HTTPError

import pytest

from tender_app.ai_client import ModelClient, ModelReplyError
from tender_app.ai_config import AIError, Profile
from tender_app.ai_engine import Turn
from tender_app.ai_tools import Tools
from tender_app.ai_usage import UsageLog, token_usage, usage_summary


def client_with_responses(monkeypatch, responses, log=None):
    client = ModelClient(Profile(provider='groq', model='test/model', allow_data_collection=None))
    if log:
        client.usage_sink = log.append
    monkeypatch.setattr(client.cancel, 'wait', lambda seconds: False)
    replies = iter(responses)

    class Response(io.BytesIO):
        status = 200
        headers = Message()
        @property
        def length(self):return len(self.getbuffer())-self.tell()

    class Opener:
        def open(self, *args, **kwargs):
            reply = next(replies)
            if isinstance(reply, Exception):
                raise reply
            if callable(reply):
                reply = reply(client)
            return Response(json.dumps(reply).encode())

    monkeypatch.setattr('tender_app.ai_client.build_opener', lambda *args: Opener())
    return client


def reply(action=None, usage=True):
    result = {'choices': [{'message': {'content': json.dumps(action or {'kind': 'answer', 'text': 'Ответ'})},
                           'finish_reason': 'stop'}]}
    if usage:
        result['usage'] = {'prompt_tokens': 123, 'completion_tokens': 45, 'total_tokens': 168,
                           'prompt_tokens_details': {'cached_tokens': 10},
                           'completion_tokens_details': {'reasoning_tokens': 20},
                           'secret_unexpected_field': 'DO NOT STORE'}
    return result


def test_provider_usage_not_character_estimate_and_no_sensitive_data(tmp_path, monkeypatch):
    log = UsageLog(tmp_path/'ai-usage.jsonl')
    client = client_with_responses(monkeypatch, [reply()], log)
    client.complete([{'role': 'user', 'content': 'PRIVATE QUESTION'}])
    saved = json.loads(log.path.read_text(encoding='utf-8'))
    assert (saved['prompt_tokens'], saved['completion_tokens'], saved['total_tokens']) == (123, 45, 168)
    assert saved['cached_tokens'] == 10 and saved['reasoning_tokens'] == 20
    assert saved['input_characters'] == len('PRIVATE QUESTION')
    assert 'PRIVATE QUESTION' not in log.path.read_text() and 'DO NOT STORE' not in log.path.read_text()
    assert usage_summary(client.usage_events)['total_tokens'] == 168


def test_turn_accumulates_tool_calls_and_resets_for_next_question(tmp_path, monkeypatch):
    log = UsageLog(tmp_path/'usage.jsonl')
    client = client_with_responses(monkeypatch, [reply({'kind': 'read', 'tool': 'overview', 'args': {}}),
                                                reply(), reply()], log)
    Turn(client, Tools([], [], 'Сводка')).run('Сводка')
    first = client.turn_id
    assert usage_summary(client.usage_events) == dict(prompt_tokens=246, completion_tokens=90,
                                                     total_tokens=336, calls=2, unknown=0)
    Turn(client, Tools([], [], 'Вопрос')).run('Вопрос')
    assert first != client.turn_id and usage_summary(client.usage_events)['total_tokens'] == 168
    events = [json.loads(line) for line in log.path.read_text(encoding='utf-8').splitlines()]
    assert len(events) == 3 and events[0]['turn_id'] == events[1]['turn_id'] != events[2]['turn_id']


def test_retry_records_429_headers_and_unknown_usage(tmp_path, monkeypatch):
    headers = Message();headers['retry-after'] = '12';headers['x-ratelimit-remaining-tokens'] = '0'
    headers['x-ratelimit-limit-tokens'] = 'PRIVATE VALUE';headers['authorization'] = 'PRIVATE KEY'
    error = HTTPError('https://example.invalid', 429, 'PRIVATE ERROR', headers, io.BytesIO(b'PRIVATE BODY'))
    log = UsageLog(tmp_path/'usage.jsonl')
    client = client_with_responses(monkeypatch, [error, reply()], log)
    client.complete([{'role': 'user', 'content': 'Вопрос'}])
    assert [e['attempt'] for e in client.usage_events] == [1, 2]
    assert client.usage_events[0]['http_status'] == 429
    assert client.usage_events[0]['total_tokens'] is None
    assert client.usage_events[0]['limits'] == {'retry-after': '12', 'x-ratelimit-remaining-tokens': '0'}
    assert 'PRIVATE' not in log.path.read_text()
    assert usage_summary(client.usage_events)['unknown'] == 1
    assert 'известная часть' in log.report()


def test_usage_logged_before_malformed_completion_rejected(monkeypatch):
    result = reply();result['choices'][0]['finish_reason'] = 'length'
    client = client_with_responses(monkeypatch, [result])
    with pytest.raises(ModelReplyError):
        client.complete([])
    assert client.usage_events[0]['total_tokens'] == 168


def test_cancellation_after_response_preserves_reported_usage(monkeypatch):
    def cancelled(client):
        client.cancel.set()
        return reply()
    client = client_with_responses(monkeypatch, [cancelled])
    with pytest.raises(AIError, match='остановлен'):
        client.complete([])
    assert client.usage_events[0]['total_tokens'] == 168


def test_network_failure_is_unknown_and_cancel_before_send_not_counted(monkeypatch):
    client = client_with_responses(monkeypatch, [TimeoutError('PRIVATE'), TimeoutError('PRIVATE')])
    with pytest.raises(AIError):client.complete([])
    assert len(client.usage_events) == 2
    assert all(e['total_tokens'] is None for e in client.usage_events)
    client.begin_turn();client.cancel.set()
    with pytest.raises(AIError):client.complete([])
    assert not client.usage_events


@pytest.mark.parametrize('value', [None, -1, True, '123', 1.2])
def test_missing_or_invalid_counters_not_reported_as_zero(value):
    data = token_usage({'usage': {'prompt_tokens': value}})
    assert data['prompt_tokens'] is None and data['total_tokens'] is None
    assert token_usage({'usage': {'total_tokens': 0}})['total_tokens'] == 0


def test_journal_persists_across_restart_and_reports_corruption(tmp_path):
    path = tmp_path/'usage.jsonl';log = UsageLog(path)
    log.append(dict(endpoint='/chat/completions', prompt_tokens=1, completion_tokens=2, total_tokens=3))
    with path.open('a', encoding='utf-8') as stream:stream.write('broken\n')
    report = UsageLog(path).report()
    assert 'всего 3' in report and 'Не прочитано строк журнала: 1' in report


def test_journal_write_error_visible_and_does_not_break_request(tmp_path, monkeypatch):
    path = tmp_path/'not-a-directory';path.write_text('preserve')
    log = UsageLog(path/'usage.jsonl')
    client = client_with_responses(monkeypatch, [reply()], log)
    assert json.loads(client.complete([]))['text'] == 'Ответ'
    assert 'неполный' in log.warning and path.read_text() == 'preserve'
