"""B2B and Tender.Pro readers. No dependency on the source projects at runtime."""
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

from .platform_deadlines import labelled_deadline
from .platform_deadlines import PlatformDeadline
from .platform_files import MAX_FILE_BYTES, extract_archive, publish_file


class PlatformError(ValueError):
    pass


class PlatformStopped(PlatformError):
    """Authentication, blocking or protocol failure: stop this platform's queue."""


@dataclass
class PlatformCard:
    number: str
    url: str
    sections: dict
    documents: list = field(default_factory=list)
    referer: str = ''
    source_id: str = ''
    submission_deadline: PlatformDeadline | None = None

    @property
    def deadline(self):
        return self.submission_deadline if self.submission_deadline is not None else labelled_deadline(self.sections)


def trusted_url(url, hosts):
    parts = urlsplit(url)
    if (parts.scheme != 'https' or parts.hostname not in hosts or parts.username
            or parts.password or parts.port not in (None, 443)):
        raise PlatformStopped('Площадка вернула посторонний адрес. Обработка остановлена.')
    return url


def read_metadata(response, stopped):
    """Bound decompressed API/HTML responses, including chunked bodies."""
    limit = 8 * 1024 * 1024
    deadline = time.monotonic() + 120
    chunks, size = [], 0
    for chunk in response.iter_content(65536):
        if stopped():
            raise PlatformStopped('Обработка остановлена.')
        if time.monotonic() > deadline:
            raise PlatformStopped('Время получения ответа площадки превышено.')
        size += len(chunk)
        if size > limit:
            raise PlatformStopped('Площадка вернула слишком большой ответ (более 8 МиБ).')
        chunks.append(chunk)
    if stopped():
        raise PlatformStopped('Обработка остановлена.')
    return b''.join(chunks)


def save_response(response, folder, name, stopped):
    if response.status_code in (401, 403, 429):
        raise PlatformStopped('Площадка ограничила скачивание или требует повторного входа.')
    response.raise_for_status()
    if 'html' in response.headers.get('Content-Type', '').lower():
        raise PlatformStopped('Вместо документа получена страница входа или проверки доступа.')
    until = time.monotonic() + 180
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=folder, suffix='.part', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            size = 0
            for chunk in response.iter_content(65536):
                if stopped():
                    raise PlatformStopped('Загрузка остановлена.')
                if time.monotonic() > until:
                    raise PlatformError('Время скачивания документа превысило 3 минуты.')
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise PlatformError('Документ превышает 512 МиБ.')
                stream.write(chunk)
            stream.close()
            expected = response.headers.get('Content-Length', '')
            if (not size or (expected.isdigit() and not response.headers.get('Content-Encoding')
                             and size != int(expected))):
                raise PlatformError('Документ пуст или загружен не полностью.')
            return publish_file(temporary, folder, name)
        finally:
            stream.close()
            temporary.unlink(missing_ok=True)


class TenderProClient:
    BASE = 'https://www2.tender.pro'
    HOSTS = ('www2.tender.pro', 'www.tender.pro', 'tender.pro')

    def __init__(self, credentials, say=lambda text: None, stopped=lambda: False):
        import requests
        self.session = requests.Session()
        self.session.headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
        self.credentials = credentials
        self.stopped = stopped
        self.sid = None

    def close(self):
        self.session.close()

    def _wait(self):
        for _ in range(10):
            if self.stopped():
                raise PlatformStopped('Обработка остановлена.')
            time.sleep(0.1)

    def _check(self, response):
        trusted_url(response.url, self.HOSTS)
        if 300 <= response.status_code < 400:
            raise PlatformStopped('Tender.Pro изменил адрес запроса. Обработка остановлена; повторите вход.')
        if response.status_code in (401, 403, 429):
            raise PlatformStopped('Tender.Pro ограничил доступ или требует входа.')
        response.raise_for_status()

    def rpc(self, method, params, keyed=True):
        self._wait()
        params = dict(params)
        if keyed:
            params['_key'] = self.credentials['key']
        envelope = {'id': 77, 'jsonrpc': '2.0', 'method': method, 'params': params, 'lang': 'ru'}
        if self.sid:
            envelope['sid'] = self.sid
        with self.session.post(self.BASE + '/api/v1/' + method + '/', json=envelope,
                               timeout=(15, 60), stream=True, allow_redirects=False) as response:
            self._check(response)
            raw = read_metadata(response, self.stopped)
            try:
                payload = json.loads(raw)
            except ValueError:
                raise PlatformStopped('Tender.Pro изменил ответ API или требует входа.') from None
        if not isinstance(payload, dict) or payload.get('success') != 'true':
            # Never expose server-supplied errors: login failures may echo passwords.
            raise PlatformStopped('Tender.Pro отказал в запросе. Проверьте доступы в «Площадки…».')
        result = payload.get('result')
        if not isinstance(result, dict):
            raise PlatformStopped('Tender.Pro изменил структуру ответа.')
        return result.get('data')

    def login(self):
        from bs4 import BeautifulSoup
        if not all(self.credentials.get(key) for key in ('login', 'password', 'key')):
            raise PlatformStopped('Настройте логин, пароль и ключ Tender.Pro в меню «Площадки…».')
        self._wait()
        with self.session.get(self.BASE + '/api/authorization', timeout=(15, 60),
                              stream=True, allow_redirects=False) as response:
            self._check(response)
            field = BeautifulSoup(read_metadata(response, self.stopped), 'html.parser').find('input', attrs={'name': '_ip'})
        if field is None:
            raise PlatformStopped('Tender.Pro изменил форму входа.')
        data = self.rpc('app.login', {'login': self.credentials['login'], 'psw': self.credentials['password'],
            '_ip': field.get('value', ''), '_cook': self.session.cookies.get('TPROv1', ''), 'protege_id': None}, False)
        if isinstance(data, list):
            data = data[0] if len(data) == 1 else None
        if not isinstance(data, dict) or not data.get('sid') or not data.get('cookie'):
            raise PlatformStopped('Tender.Pro не подтвердил вход.')
        self.sid = data['sid']
        self.session.cookies.clear()
        self.session.cookies.set('TPROv1', data['cookie'], domain='.tender.pro', path='/')

    def page(self, url, referer=''):
        self._wait()
        url = trusted_url(urljoin(self.BASE, url), self.HOSTS)
        # Preserve the bare one-time tab token, but send exactly one current sid.
        parts = urlsplit(url)
        url = parts._replace(query='&'.join(part for part in parts.query.split('&')
            if part and unquote(part.split('=', 1)[0]).casefold() != 'sid')).geturl()
        response = self.session.get(url, params={'sid': self.sid},
            headers={'Referer': referer} if referer else {}, timeout=(15, 60),
            stream=True, allow_redirects=False)
        try:
            self._check(response)
            from bs4 import UnicodeDammit
            raw = read_metadata(response, self.stopped)
            # Legacy tabs declare windows-1251 in HTML, without an HTTP charset.
            # requests defaults to Latin-1 in that case, corrupting labels and file boundaries.
            content_type = getattr(response, 'headers', {}).get('Content-Type', '')
            encodings = [response.encoding] if 'charset=' in content_type.lower() and response.encoding else []
            text = UnicodeDammit(raw, known_definite_encodings=encodings, is_html=True).unicode_markup
            if text is None:
                raise PlatformStopped('Tender.Pro: не удалось прочитать кодировку карточки.')
            if '/api/authorization' in response.url or re.search(r'window\.location\s*=\s*[\'\"]/api/authorization', text[:2000]):
                raise PlatformStopped('Вход Tender.Pro истёк. Обработка площадки остановлена.')
            return text, response.url
        finally:
            response.close()

    @staticmethod
    def _verify_number(soup, number):
        identifiers = set()
        for node in soup.select('h1,h2,title'):
            identifiers.update(re.findall(r'(?:тендер|закупка|процедура)\s*(?:№|N|#)?\s*(\d+)(?!\d)',
                                         node.get_text(' ', strip=True), re.I))
        for node in soup.select('#tenderHeader .header1'):
            match = re.search(r'\(\s*id(\d+)\s*\)\s*$', node.get_text(' ', strip=True), re.I)
            if match:
                identifiers.add(match[1])
        if identifiers != {number}:
            raise PlatformStopped('Tender.Pro: номер карточки не подтверждён. Ссылка и срок не изменены.')

    @staticmethod
    def _tab_link(soup, tab):
        return next((a['href'] for a in soup.find_all('a', href=True)
                     if parse_qs(urlsplit(a['href']).query).get('tab') == [tab]), None)

    def _card_page(self, number, url, referer=''):
        from bs4 import BeautifulSoup
        text, canonical = self.page(url, referer)
        soup = BeautifulSoup(text, 'html.parser')
        self._verify_number(soup, number)
        return soup, canonical

    def read(self, number, url):
        if not self.sid:
            self.login()
        soup, canonical = self._card_page(number, '/api/tender/' + number + '/view')
        form = soup.select_one('#tenderForm')
        if form is not None and not form.get_text(strip=True):
            # The new entry page is only a shell. Legacy common needs a one-time link
            # issued on another tab, not a guessed URL or an unlabelled API date.
            common = self._tab_link(soup, 'common')
            if not common:
                bridge = next((link for tab in ('files', 'stages', 'goods1')
                               if (link := self._tab_link(soup, tab))), None)
                if bridge:
                    soup, canonical = self._card_page(number, bridge, canonical)
                    common = self._tab_link(soup, 'common')
            if not common:
                raise PlatformStopped('Tender.Pro: ссылка на общую информацию не найдена. Срок не изменён.')
            soup, canonical = self._card_page(number, common, canonical)
        sections = {}
        for ordinal, row in enumerate(soup.select('tr')):
            cells = row.find_all(['td', 'th'], recursive=False)
            if len(cells) == 2:
                label = cells[0].get_text(' ', strip=True)
                if label and len(label) < 200:
                    value = cells[1].get_text(' ', strip=True)
                    if label in sections and sections[label] != value:
                        label += f' (строка {ordinal + 1})'
                    sections[label] = value
        from .platform_deadlines import sections_from_text
        sections.update(sections_from_text(soup.get_text('\n', strip=True)))
        # Redirects can end on a legacy URL with tenderid and a private sid.
        # Keep the procedure number while discarding every session parameter.
        public_url = self.BASE + '/view_tender.shtml?tenderid=' + number
        return PlatformCard(number, public_url, sections)

    def download(self, card, folder, say):
        soup, referer = self._card_page(card.number, '/api/tender/' + card.number + '/view')
        tab = self._tab_link(soup, 'files')
        if tab is None:
            say('Документация организатора отсутствует: вкладки «Документы» нет.')
            return []
        soup, referer = self._card_page(card.number, urljoin(referer, tab), referer)
        documents = self._organizer_documents(soup)
        say('Документов организатора: ' + str(len(documents)) + '.')
        paths = []
        for identifier, title in documents.items():
            self._wait()
            try:
                with self.session.get(self.BASE + '/api/file/' + identifier, params={'sid': self.sid},
                        headers={'Referer': referer}, stream=True, timeout=(15, 60), allow_redirects=False) as response:
                    self._check(response)
                    disposition = response.headers.get('Content-Disposition', '')
                    try:
                        disposition = disposition.encode('latin-1').decode('utf-8')
                    except (UnicodeError, ValueError):
                        pass
                    import html
                    disposition = html.unescape(disposition)
                    match = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.I) or re.search(r'filename="?([^";]+)', disposition, re.I)
                    name = unquote(match[1]) if match else (title or 'файл-' + identifier)
                    path = save_response(response, folder, name, self.stopped)
                if path in paths:
                    continue
                paths.append(path)
                say('Файл сохранён: ' + path.name)
                yield path
                try:
                    warning = extract_archive(path)
                    if warning:
                        say(warning)
                except Exception:
                    say('Не удалось распаковать архив: ' + path.name + '. Оригинал сохранён.')
            except PlatformStopped:
                raise
            except Exception:
                say('Не удалось скачать документ ' + identifier + '. Остальные файлы продолжаются.')

    @staticmethod
    def _organizer_documents(soup):
        boundary = soup.find(lambda node: node.name in ('h1', 'h2', 'h3', 'h4', 'b', 'strong', 'div', 'td', 'span')
            and re.fullmatch(r'Ваши\s+файлы\s+к\s+предложению\s*:?', node.get_text(' ', strip=True), re.I))
        documents = {}
        for link in soup.descendants:
            if link is boundary:
                break
            if getattr(link, 'name', None) != 'a' or not link.get('href'):
                continue
            match = re.fullmatch(r'/api/file/(\d+)/?', urlsplit(link['href']).path)
            if match:
                trusted_url(urljoin(TenderProClient.BASE, link['href']), TenderProClient.HOSTS)
                if not documents.get(match[1]):
                    documents[match[1]] = link.get_text(' ', strip=True)
        tabs = soup.select_one('#tenderSelect')
        count = re.search(r'Документы\s*\((\d+)\)', tabs.get_text(' ', strip=True), re.I) if tabs else None
        if count and int(count[1]) != len(documents):
            raise PlatformStopped('Tender.Pro: список документов не совпадает с количеством во вкладке.')
        return documents
