import pytest

from tender_app.platform_clients import PlatformError, PlatformStopped
from tender_app.platform_public import (UGMKClient, TektorgClient, UGMK_ATTACHMENTS,
                                       tektorg_documents, ugmk_documents)
from tender_app.platform_sync import enrich_new_tenders, platform_kind
from tests.test_platforms import storage, add


def test_tektorg_empty_search_does_not_stop_next_procedure(storage, tmp_path, monkeypatch):
    missing = add(storage, tmp_path, 'РН60605398', 'ТЭК-Торг')
    found = add(storage, tmp_path, 'ЗП6101073', 'ТЭК-Торг')
    client = TektorgClient()
    calls = []
    def response(method, url, **kwargs):
        number = kwargs['json']['params']['name']
        calls.append(number)
        if number == missing.number:
            return {'totalPages': 1, 'currentPage': 1, 'limitPage': 20, 'hash': '0' * 32}
        return {'total': 1, 'data': [{'id': 1, 'registryNumber': number, 'sectionAlias': 'zakupki'}]}
    monkeypatch.setattr(client, '_json', response)
    monkeypatch.setattr(client, '_card', lambda identifier, number: {
        'id': 1, 'registryNumber': number, 'sectionAlias': 'zakupki',
        'dates': {'dateEndRegistration': '2026-10-26T18:00:00+03:00'}})
    monkeypatch.setattr(client, 'download', lambda *args: iter(()))
    reports = enrich_new_tenders(storage, {missing.id, found.id}, lambda *args: client)
    assert calls == [missing.number, found.number]
    assert not reports[0]['deadline_applied'] and reports[1]['deadline_applied']
    assert any('не найдена' in message for message in reports[0]['messages'])
    saved = next(t for t in storage.all() if t.id == missing.id)
    assert (saved.deadline_date, saved.deadline_time) == (missing.deadline_date, missing.deadline_time)


@pytest.mark.parametrize('payload', [{}, {'hash': '0' * 32},
    {'totalPages': 2, 'currentPage': 1, 'limitPage': 20, 'hash': '0' * 32},
    {'totalPages': 1, 'currentPage': 1, 'limitPage': 20, 'hash': 'invalid'}])
def test_tektorg_unknown_search_response_still_stops_queue(monkeypatch, payload):
    client = TektorgClient()
    try:
        monkeypatch.setattr(client, '_json', lambda *args, **kwargs: payload)
        with pytest.raises(PlatformStopped):
            client.read('123', '')
    finally:
        client.close()


@pytest.mark.parametrize(('name', 'kind'), [('УГМК', 'ugmk'), ('ЭТП УГМК', 'ugmk'),
    ('etp.metal-it.ru', 'ugmk'), ('UGMK', 'ugmk'), ('ТЭК-Торг', 'tektorg'),
    ('ТЭК Торг', 'tektorg'), ('www.tektorg.ru', 'tektorg'), ('Другая', '')])
def test_platform_recognition(name, kind):
    assert platform_kind(name) == kind


def test_ugmk_uses_registered_number_not_internal_id(monkeypatch):
    client = UGMKClient()
    calls = []
    def graphql(operation, query, variables):
        calls.append((operation, variables))
        if operation == 'feed':
            return {'trades': {'total': 1, 'items': [{'id': 554128215, 'registeredNumber': '124729-ТТ'}]}}
        return {'trade': {'id': 554128215, 'registeredNumber': '124729-ТТ',
                          'bidSubmissionEndDate': 1780642800000, 'lots': []}}
    monkeypatch.setattr(client, '_graphql', graphql)
    card = client.read('№ 124729-ТТ', '')
    assert calls[0][1]['dto'] == {'fullTextString': '№ 124729-ТТ'}
    assert calls[1][1] == {'id': 554128215}
    assert card.number == '№ 124729-ТТ' and card.url.endswith('/trades/554128215/info')
    assert (card.deadline.day, card.deadline.clock, card.deadline.zone) == ('2026-06-05', '10:00', 'Europe/Moscow')
    assert '1780642800000' in card.deadline.raw
    client.close()


@pytest.mark.parametrize('raw', [None, '2026-10-27T12:00:00', 1780642800, True])
def test_unknown_ugmk_deadline_does_not_invent_timezone(monkeypatch, raw):
    client = UGMKClient()
    monkeypatch.setattr(client, '_graphql', lambda operation, *_:
        {'trades': {'total': 1, 'items': [{'id': 1, 'registeredNumber': '1-ТТ'}]}}
        if operation == 'feed' else {'trade': {'id': 1, 'registeredNumber': '1-ТТ', 'bidSubmissionEndDate': raw}})
    assert client.read('1-ТТ', '').deadline.warning
    client.close()


@pytest.mark.parametrize('client_class', [UGMKClient, TektorgClient])
def test_ambiguous_registry_number_is_not_guessed(monkeypatch, client_class):
    client = client_class()
    if client_class is UGMKClient:
        monkeypatch.setattr(client, '_graphql', lambda *_: {'trades': {'total': 2, 'items': [
            {'id': 1, 'registeredNumber': '123'}, {'id': 2, 'registeredNumber': '123'}]}})
    else:
        monkeypatch.setattr(client, '_json', lambda *_, **kw: {'total': 2, 'data': [
            {'id': 1, 'registryNumber': '123'}, {'id': 2, 'registryNumber': '123'}]})
    with pytest.raises(PlatformError, match='однозначно'):
        client.read('123', '')
    client.close()


def test_ugmk_collects_every_procedure_and_lot_document():
    data = {field: [{'id': n, 'name': f'doc{n}.pdf', 'size': str(n), 'token': f'token{n}'}]
            for n, field in enumerate(UGMK_ATTACHMENTS)}
    data['lots'] = [{'otherAttachments': [data['otherAttachments'][0],
                    {'id': 99, 'name': 'lot.pdf', 'size': '99', 'token': 'lot-token'}]}]
    files = ugmk_documents(data)
    assert len(files) == 10 and len({d['name'] for d in files}) == 10
    assert files[-1]['url'].endswith('/lot-token/lot.pdf')


def test_tektorg_procedure_lot_and_explanations_but_no_after_win():
    item = {'id': 1, 'filename': 'ТЗ.pdf', 'size': 3, 'httpLink': 'https://api.tektorg.ru/fresh?token=a'}
    data = {'documents': [item, {'id': 4, 'filename': 'Договор.pdf', 'size': 4}],
            'lots': [{'documents': [item], 'docsExplain': [{'documents': [
                {'filename': 'Исправление.pdf', 'size': 5, 'httpLink': 'https://api.tektorg.ru/correction'}]}],
                'docsAfterWin': [{'filename': 'Не скачивать.pdf'}]}]}
    files = tektorg_documents(data)
    assert [d['name'] for d in files] == ['ТЗ.pdf', 'Договор.pdf', 'Исправление.pdf']
    assert files[0]['url'] == item['httpLink']
    assert files[1]['url'].endswith('/documents/procedure/4')


def test_tektorg_refreshes_token_before_download_and_converts_timezone(monkeypatch, tmp_path):
    client = TektorgClient()
    calls = []
    def json_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if method == 'POST':
            assert kwargs['json']['params']['name'] == 'РН123/1'
            return {'total': 1, 'data': [{'id': 55, 'registryNumber': 'РН123/1', 'sectionAlias': 'zakupki'}]}
        version = len([call for call in calls if call[0] == 'GET'])
        return {'id': 55, 'registryNumber': 'РН123/1', 'sectionAlias': 'zakupki',
                'dates': {'dateEndRegistration': '2026-10-27T00:30:00+05:00'},
                'documents': [{'id': 1, 'filename': 'ТЗ.txt', 'size': 3,
                               'httpLink': 'https://api.tektorg.ru/file?token=' + str(version)}]}
    monkeypatch.setattr(client, '_json', json_request)
    card = client.read('РН123/1', '')
    assert card.url == 'https://www.tektorg.ru/223-fz/procedures/55'
    assert (card.deadline.day, card.deadline.clock) == ('2026-10-26', '22:30')
    downloaded = []
    class Response:
        status_code = 200
        headers = {'Content-Length': '3'}
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def raise_for_status(self): pass
        def iter_content(self, *_): yield b'abc'
    def request(method, url, **kwargs):
        downloaded.append(url)
        return Response()
    monkeypatch.setattr(client, '_request', request)
    paths = list(client.download(card, tmp_path, lambda _: None))
    assert downloaded == ['https://api.tektorg.ru/file?token=2']
    assert paths[0].read_bytes() == b'abc'
    client.close()


@pytest.mark.parametrize('client_class', [UGMKClient, TektorgClient])
def test_incomplete_search_never_accepts_first_match(monkeypatch, client_class):
    client = client_class()
    if client_class is UGMKClient:
        pages = iter([{'trades': {'total': 2, 'items': [{'id': 1, 'registeredNumber': '123'}]}},
                      {'trades': {'total': 2, 'items': []}}])
        monkeypatch.setattr(client, '_graphql', lambda *_: next(pages))
    else:
        pages = iter([{'total': 2, 'data': [{'id': 1, 'registryNumber': '123'}]}, {'total': 2, 'data': []}])
        monkeypatch.setattr(client, '_json', lambda *_, **kwargs: next(pages))
    with pytest.raises(PlatformStopped, match='неполный'):
        client.read('123', '')
    client.close()


def test_new_registry_numbers_integrate_and_preserve_1c(storage, tmp_path, monkeypatch):
    ugmk = add(storage, tmp_path, '123-ТТ', 'УГМК')
    tek = add(storage, tmp_path, 'РН123_1', 'ТЭК-Торг')
    old = add(storage, tmp_path, '124-ТТ', 'УГМК')
    clients = {'ugmk': UGMKClient(), 'tektorg': TektorgClient()}
    from tender_app.platform_clients import PlatformCard
    from tender_app.platform_deadlines import parse_deadline
    def read(number, _):
        return PlatformCard(number, '', {}, submission_deadline=parse_deadline('2026-10-27T12:00:00+03:00'))
    for client in clients.values():
        monkeypatch.setattr(client, 'read', read)
        monkeypatch.setattr(client, 'download', lambda *_: iter(()))
    reports = enrich_new_tenders(storage, {ugmk.id, tek.id}, lambda kind, *_: clients[kind])
    assert len(reports) == 2 and all(r['deadline_applied'] for r in reports)
    assert next(t for t in storage.all() if t.id == old.id) == old
    for tender in storage.all():
        if tender.id in {ugmk.id, tek.id}:
            assert tender.deadline_date == '2026-10-27' and tender.deadline_raw == ugmk.deadline_raw


def test_missing_public_link_preserves_manual_url(storage, tmp_path, monkeypatch):
    tender = add(storage, tmp_path, '123', 'ТЭК-Торг')
    tender.url = 'https://www.tektorg.ru/existing'
    storage.save(tender)
    client = TektorgClient()
    from tender_app.platform_clients import PlatformCard
    monkeypatch.setattr(client, 'read', lambda *_: PlatformCard('123', '', {}))
    monkeypatch.setattr(client, 'download', lambda *_: iter(()))
    enrich_new_tenders(storage, {tender.id}, lambda *_: client)
    assert next(t for t in storage.all() if t.id == tender.id).url == tender.url


def test_1c_import_accepts_registry_numbers_and_only_new_rows(storage, tmp_path):
    from tests.test_import_1c import line
    root = tmp_path / 'Документы'
    root.mkdir()
    text = line('124729-ТТ', platform='УГМК') + line('РН123/1', platform='ТЭК-Торг')
    plan = storage.preview_1c(text)
    ids, _ = storage.import_1c(plan, (1, 2), root)
    added = [t for t in storage.all() if t.id in ids]
    assert {t.number for t in added} == {'124729-ТТ', 'РН123/1'}
    from pathlib import Path
    assert all((Path(t.folder_path) / 'С ЭТП').is_dir() for t in added)
    assert storage.preview_1c(text).counts['new'] == 0


@pytest.mark.parametrize('status', [401, 403, 429])
def test_public_access_restriction_stops_queue(monkeypatch, status):
    monkeypatch.setattr('tender_app.platform_public.time.sleep', lambda _: None)
    client = UGMKClient()
    class Response:
        status_code = status
        def close(self): pass
    monkeypatch.setattr(client.session, 'request', lambda *args, **kwargs: Response())
    with pytest.raises(PlatformStopped, match='Очередь остановлена'):
        client._request('GET', client.BASE + '/document')
    client.close()


def test_document_redirect_cannot_send_token_to_another_host(monkeypatch):
    monkeypatch.setattr('tender_app.platform_public.time.sleep', lambda _: None)
    client = TektorgClient()
    calls = []
    class Response:
        status_code = 302
        headers = {'Location': 'https://external.invalid/document?token=private'}
        def close(self): pass
    def request(*args, **kwargs):
        calls.append(args)
        return Response()
    monkeypatch.setattr(client.session, 'request', request)
    with pytest.raises(PlatformStopped, match='посторонний'):
        client._request('GET', client.API + '/document')
    assert len(calls) == 1
    client.close()


def test_failure_of_one_document_keeps_other_downloads(monkeypatch, tmp_path):
    client = UGMKClient()
    from tender_app.platform_clients import PlatformCard
    card = PlatformCard('123-ТТ', '', {}, documents=[{'name': 'removed.txt', 'url': client.BASE + '/removed'},
                                                    {'name': 'ТЗ.txt', 'url': client.BASE + '/available'}])
    class Response:
        status_code = 200
        headers = {'Content-Length': '3'}
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def raise_for_status(self): pass
        def iter_content(self, *_): yield b'abc'
    def request(method, url, **kwargs):
        if url.endswith('/removed'):
            raise PlatformError('404')
        return Response()
    monkeypatch.setattr(client, '_request', request)
    messages = []
    paths = list(client.download(card, tmp_path, messages.append))
    assert len(paths) == 1 and paths[0].read_bytes() == b'abc'
    assert any('removed.txt' in message for message in messages)
    client.close()
