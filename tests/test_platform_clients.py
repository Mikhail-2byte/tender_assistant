import pytest

from tender_app.platform_clients import PlatformCard, PlatformStopped, TenderProClient, trusted_url


def test_tenderpro_reads_labelled_deadline_and_verifies_number(monkeypatch):
    client = TenderProClient({})
    client.sid = 'test'
    markup = '''<title>Тендер № 123</title><table>
        <tr><td>Дата окончания подачи заявок (МСК)</td><td>28.10.2026 15:00</td></tr>
        <tr><td>Срок поставки</td><td>01.12.2026</td></tr></table>'''
    monkeypatch.setattr(client, 'page', lambda *_: (markup, 'https://www2.tender.pro/api/tender/123/view?sid=test'))
    card = client.read('123', '')
    assert card.number == '123' and 'sid' not in card.url
    assert card.url == 'https://www2.tender.pro/view_tender.shtml?tenderid=123'
    assert card.deadline.day == '2026-10-28' and card.deadline.clock == '15:00'
    with pytest.raises(PlatformStopped, match='номер'):
        client.read('124', '')
    client.close()


def test_tenderpro_download_keeps_organizer_files_only(monkeypatch, tmp_path):
    client = TenderProClient({})
    client.sid = 'test'
    monkeypatch.setattr(client, '_wait', lambda: None)
    def page(url, referer=''):
        if not referer:
            return '<title>Тендер № 123</title><a href="/view?tab=files&amp;nonce=once">Документы</a>', client.BASE + '/api/tender/123/view'
        assert 'nonce=once' in url
        return '''<title>Тендер № 123</title><a href="/api/file/10">ТЗ.txt</a>
                  <h3>Ваши файлы к предложению</h3><a href="/api/file/20">Наше КП.txt</a>''', client.BASE + '/view?tab=files'
    monkeypatch.setattr(client, 'page', page)
    urls = []
    class Response:
        status_code = 200
        headers = {'Content-Disposition': 'attachment; filename="ТЗ.txt"', 'Content-Length': '3'}
        url = client.BASE + '/api/file/10'
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def raise_for_status(self): pass
        def iter_content(self, size): yield b'abc'
    def get(url, **kwargs):
        urls.append(url)
        assert kwargs['headers']['Referer'].endswith('tab=files')
        return Response()
    monkeypatch.setattr(client.session, 'get', get)
    paths = list(client.download(PlatformCard('123', client.BASE, {}), tmp_path, lambda _: None))
    assert len(paths) == 1 and paths[0].read_bytes() == b'abc'
    assert urls == [client.BASE + '/api/file/10']
    client.close()


def test_auth_redirect_with_http_200_stops_queue(monkeypatch):
    client = TenderProClient({})
    monkeypatch.setattr(client, '_wait', lambda: None)
    class Response:
        status_code = 200
        url = client.BASE + '/view'
        text = "<script>window.location='/api/authorization'</script>"
        encoding = 'utf-8'
        def iter_content(self, *_): yield self.text.encode('utf-8')
        def raise_for_status(self): pass
        def close(self): pass
    monkeypatch.setattr(client.session, 'get', lambda *args, **kwargs: Response())
    with pytest.raises(PlatformStopped, match='истёк'):
        client.page('/view')
    client.close()


def test_server_login_error_never_echoes_credentials(monkeypatch):
    client = TenderProClient({'key': 'a-secret-key'})
    monkeypatch.setattr(client, '_wait', lambda: None)
    class Response:
        status_code = 200
        url = client.BASE
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def raise_for_status(self): pass
        def iter_content(self, *_): yield b'{"success":"false","result":{"error":["password=a-secret-password"]}}'
    monkeypatch.setattr(client.session, 'post', lambda *args, **kwargs: Response())
    with pytest.raises(PlatformStopped) as error:
        client.rpc('app.login', {})
    assert 'a-secret' not in str(error.value)
    client.close()


@pytest.mark.parametrize('url', ['https://other.invalid/file', 'http://www2.tender.pro/file',
                                 'https://user:password@www2.tender.pro/file'])
def test_untrusted_document_urls_rejected(url):
    with pytest.raises(PlatformStopped):
        trusted_url(url, TenderProClient.HOSTS)


def test_b2b_public_deadline_is_read_before_document_login(monkeypatch, tmp_path):
    from tender_app.platform_b2b import B2BClient, NEW_CARD_JS, LEGACY_CARD_JS
    monkeypatch.setattr('tender_app.platform_b2b.time.sleep', lambda _: None)
    class Locator:
        first = None
        def wait_for(self, **kwargs): pass
        def inner_text(self): return 'Дата окончания подачи заявок: 27.10.2026 12:25, по МСК'
        def all_text_contents(self): return ['Тендер № 4645898']
        def __init__(self): self.first = self
    class Page:
        url = 'https://www.b2b-center.ru/app/market-next/shaft/tender-4645898/'
        def goto(self, *args, **kwargs): return None
        def locator(self, *args): return Locator()
        def evaluate(self, script):
            assert script in (NEW_CARD_JS, LEGACY_CARD_JS)
            return {'number': '4645898', 'sections': {}, 'documents': []}
    client = B2BClient(tmp_path)
    client.context = object()
    client.page = Page()
    monkeypatch.setattr(client, '_check', lambda *args: None)
    monkeypatch.setattr(client, '_login', lambda *_: pytest.fail('Public metadata must not require login'))
    card = client.read('4645898', client.page.url)
    assert card.deadline.day == '2026-10-27' and card.deadline.clock == '12:25'
    client.page.url = 'https://www.b2b-center.ru/market/shaft/tender-4645898/'
    assert client.read('4645898', client.page.url).deadline.clock == '12:25'


def test_b2b_search_protocol_failure_stops_platform_before_browser(monkeypatch, tmp_path):
    from tender_app.platform_b2b import B2BClient
    monkeypatch.setattr('tender_app.platform_b2b.time.sleep', lambda _: None)
    def fail(_):
        raise ValueError('changed protocol')
    monkeypatch.setattr('tender_app.resource_sync.lookup_b2b', fail)
    client = B2BClient(tmp_path)
    with pytest.raises(PlatformStopped, match='Очередь остановлена'):
        client.read('123', '')
    assert client.context is None
