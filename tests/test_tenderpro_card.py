from bs4 import BeautifulSoup
import pytest

from tender_app.platform_clients import PlatformStopped, TenderProClient


HEADER = '<div id="tenderHeader"><span class="header1">Учебная закупка (id123)</span></div>'


def test_shell_uses_issued_common_link_and_labelled_deadline(monkeypatch):
    client = TenderProClient({})
    client.sid = 'session'
    pages = [
        (HEADER + '<div id="tenderForm"></div><a href="/view_tender.shtml?tab=files&amp;fileNonce">Документы</a>', 'entry'),
        (HEADER + '<a href="/view_tender.shtml?tab=common&amp;commonNonce">Общая информация</a>', 'files'),
        (HEADER + '''<table>
            <tr><td>Прием предложений до:</td><td>3 февраля 2026 23:00 MSK</td></tr>
            <tr><td>Закрыт:</td><td>9 февраля 2026</td></tr>
            <tr><td>Поставка до:</td><td>31 мая 2026</td></tr></table>''', 'common'),
    ]
    calls = []
    def page(url, referer=''):
        calls.append((url, referer))
        markup, path = pages.pop(0)
        return markup, client.BASE + '/' + path
    monkeypatch.setattr(client, 'page', page)
    try:
        card = client.read('123', '')
        assert calls == [('/api/tender/123/view', ''),
                         ('/view_tender.shtml?tab=files&fileNonce', client.BASE + '/entry'),
                         ('/view_tender.shtml?tab=common&commonNonce', client.BASE + '/files')]
        assert (card.deadline.day, card.deadline.clock, card.deadline.zone) == ('2026-02-03', '23:00', 'Europe/Moscow')
        assert 'Прием предложений до' in card.deadline.raw
        assert 'sid' not in card.url
    finally:
        client.close()


@pytest.mark.parametrize('markup', [
    HEADER.replace('id123', 'id1234'),
    '<p>Другая страница (id123)</p>',
    HEADER + '<h1>Тендер № 124</h1>',
])
def test_number_is_verified_in_explicit_header_only(markup):
    with pytest.raises(PlatformStopped, match='номер'):
        TenderProClient._verify_number(BeautifulSoup(markup, 'html.parser'), '123')


def test_each_followed_tab_must_match_number(monkeypatch):
    client = TenderProClient({})
    client.sid = 'session'
    pages = [HEADER + '<div id="tenderForm"></div><a href="/view?tab=files">Документы</a>',
             HEADER.replace('id123', 'id124') + '<a href="/view?tab=common">Общая информация</a>']
    monkeypatch.setattr(client, 'page', lambda *args: (pages.pop(0), client.BASE + '/view'))
    try:
        with pytest.raises(PlatformStopped, match='номер'):
            client.read('123', '')
    finally:
        client.close()


def test_cp1251_page_keeps_one_current_sid_and_bare_nonce(monkeypatch):
    client = TenderProClient({})
    client.sid = 'current'
    monkeypatch.setattr(client, '_wait', lambda: None)
    markup = '<meta charset="windows-1251"><h3>Ваши файлы к предложению</h3>'
    class Response:
        status_code = 200
        headers = {'Content-Type': 'text/html'}
        encoding = 'ISO-8859-1'
        url = client.BASE + '/view'
        closed = False
        def raise_for_status(self): pass
        def iter_content(self, *_): yield markup.encode('cp1251')
        def close(self): self.closed = True
    response = Response()
    def get(url, **kwargs):
        assert url == client.BASE + '/view?tab=files&bareNonce'
        assert kwargs['params'] == {'sid': 'current'}
        assert kwargs['headers']['Referer'] == client.BASE + '/entry'
        assert kwargs['allow_redirects'] is False
        return response
    monkeypatch.setattr(client.session, 'get', get)
    try:
        text, _ = client.page('/view?sid=old&tab=files&bareNonce&sid=other', client.BASE + '/entry')
        assert 'Ваши файлы к предложению' in text and response.closed
    finally:
        client.close()


def test_organizer_manifest_excludes_own_files_and_deduplicates():
    markup = HEADER + '''<div id="tenderSelect">Документы (1)</div>
        <a href="/api/file/10">Чертёж.jpg</a><a href="/api/file/10">Скачать</a>
        <h3><span>Ваши файлы</span> к предложению</h3>
        <a href="/api/file/20">Наше КП.pdf</a><a href="/api/file/21">Наш сертификат.pdf</a>'''
    assert TenderProClient._organizer_documents(BeautifulSoup(markup, 'html.parser')) == {'10': 'Чертёж.jpg'}


def test_document_count_mismatch_is_not_reported_as_empty_success():
    markup = '<div id="tenderSelect">Документы (2)</div><a href="/api/file/10">ТЗ.txt</a>'
    with pytest.raises(PlatformStopped, match='количеством'):
        TenderProClient._organizer_documents(BeautifulSoup(markup, 'html.parser'))


def test_conflicting_submission_rows_are_not_silently_overwritten(monkeypatch):
    client = TenderProClient({})
    client.sid = 'session'
    markup = HEADER + '''<table>
        <tr><td>Приём предложений до:</td><td>3 февраля 2026 23:00 MSK</td></tr>
        <tr><td>Приём предложений до:</td><td>4 февраля 2026 23:00 MSK</td></tr>
        <tr><td>Приём предложений до:</td><td>5 февраля 2026 23:00 MSK</td></tr>
        <tr><td>Приём предложений до:</td><td>3 февраля 2026 23:00 MSK</td></tr></table>'''
    monkeypatch.setattr(client, 'page', lambda *args: (markup, client.BASE + '/view'))
    try:
        card = client.read('123', '')
        assert card.deadline.warning and card.deadline.day is None
    finally:
        client.close()
