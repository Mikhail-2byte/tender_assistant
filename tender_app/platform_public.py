"""Anonymous UGMK and TEK-Torg clients, adapted from Otbor_zakupok."""
from dataclasses import replace
from datetime import datetime, timezone
import html
import json
import re
import time
from urllib.parse import quote, unquote, urljoin, urlsplit

from .platform_clients import PlatformCard, PlatformError, PlatformStopped, read_metadata, save_response, trusted_url
from .platform_deadlines import PlatformDeadline, parse_deadline
from .platform_files import extract_archive


def number_key(value):
    return re.sub(r'\s+', '', str(value).strip().removeprefix('№')).casefold()


def exact_match(rows, number, field):
    matches = {str(row['id']): row for row in rows
               if isinstance(row, dict) and row.get('id') is not None
               and number_key(row.get(field, '')) == number_key(number)}
    if len(matches) != 1:
        raise PlatformError('Номер не найден однозначно на площадке; карточка из 1С сохранена без замены срока.')
    return next(iter(matches.values()))


def response_filename(header, fallback):
    try:
        header = header.encode('latin-1').decode('utf-8')
    except UnicodeError:
        pass
    header = html.unescape(header)
    match = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", header, re.I) or re.search(r'filename\s*=\s*"?([^";]+)', header, re.I)
    return unquote(match[1]).strip() if match else fallback


class PublicClient:
    def __init__(self, say=lambda _: None, stopped=lambda: False):
        import requests
        self.session = requests.Session()
        self.session.headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
        self.stopped, self.say = stopped, say

    def close(self):
        self.session.close()

    def _request(self, method, url, **kwargs):
        import requests
        kwargs.setdefault('stream', True)
        for _ in range(5):
            if self.stopped():
                raise PlatformStopped('Обработка остановлена.')
            time.sleep(0.2)  # Source clients' one-second pause, including file requests.
        trusted_url(url, self.HOSTS)
        for _ in range(4):
            try:
                response = self.session.request(method, url, timeout=(15, 60), allow_redirects=False, **kwargs)
            except requests.RequestException:
                raise PlatformStopped('Нет связи с площадкой. Очередь этой площадки остановлена.') from None
            if response.status_code in (301, 302, 303, 307, 308):
                target = urljoin(url, response.headers.get('Location', ''))
                response.close()
                if method != 'GET':
                    raise PlatformStopped('Площадка изменила адрес API. Очередь остановлена.')
                url = trusted_url(target, self.HOSTS)
                continue
            if response.status_code in (401, 403, 429):
                response.close()
                raise PlatformStopped('Площадка ограничила доступ или требует входа. Очередь остановлена.')
            if response.status_code == 404:
                response.close()
                raise PlatformError('Процедура или документ недоступны на площадке (404).')
            if response.status_code != 200:
                response.close()
                raise PlatformStopped('Площадка вернула ошибку запроса. Очередь остановлена.')
            return response
        raise PlatformStopped('Слишком много перенаправлений площадки.')

    def _json(self, method, url, **kwargs):
        with self._request(method, url, **kwargs) as response:
            raw = read_metadata(response, self.stopped)
            try:
                payload = json.loads(raw)
            except ValueError:
                raise PlatformStopped('Площадка вернула страницу вместо JSON. Очередь остановлена.') from None
        if not isinstance(payload, dict):
            raise PlatformStopped('Изменился формат ответа площадки. Очередь остановлена.')
        return payload

    def download(self, card, folder, say):
        seen = set()
        if not card.documents:
            say('В карточке нет доступной документации организатора.')
        for document in card.documents:
            if not document.get('url'):
                say('Площадка не выдала ссылку на документ: ' + document['name'])
                continue
            try:
                with self._request('GET', document['url'], stream=True) as response:
                    name = response_filename(response.headers.get('Content-Disposition', ''), document['name'])
                    path = save_response(response, folder, name, self.stopped)
                if path in seen:
                    continue
                seen.add(path)
                yield path
                say('Файл сохранён: ' + path.name)
                try:
                    warning = extract_archive(path)
                    if warning:
                        say(warning)
                except Exception:
                    say('Не удалось распаковать архив: ' + path.name + '. Оригинал сохранён.')
            except PlatformStopped:
                raise
            except Exception:
                say('Не удалось скачать документ: ' + document['name'] + '. Остальные файлы продолжаются.')


UGMK_ATTACHMENTS = ('purchaseDocumentationAttachments', 'qualificationDocumentationAttachments',
                   'contractRequirementsAttachments', 'initialPriceCalcAttachments', 'extraAttachments',
                   'contractDraftAttachments', 'technicalSpecificationAttachments',
                   'projectDocumentationAttachments', 'otherAttachments')
UGMK_FEED = '''query feed($dto: TradeQueryDtoInput, $limit: Int, $skip: Int) {
  trades(tradeQueryDto: $dto, limit: $limit, skip: $skip) {
    total items {id registeredNumber}
  }
}'''
UGMK_CARD = '''query card($id: Long) {
  trade(id: $id) {id registeredNumber bidSubmissionEndDate
    %s
    lots {id %s}
  }
}''' % ('\n'.join(name + ' {id name size token}' for name in UGMK_ATTACHMENTS),
         '\n'.join(name + ' {id name size token}' for name in UGMK_ATTACHMENTS[5:]))


def ugmk_documents(data):
    documents, seen = [], set()
    containers = [(data, UGMK_ATTACHMENTS)] + [(lot, UGMK_ATTACHMENTS[5:]) for lot in data.get('lots') or []]
    for container, fields in containers:
        for field in fields:
            for item in container.get(field) or []:
                key = (item.get('name'), item.get('size'))
                if key in seen:
                    continue
                seen.add(key)
                name, token = str(item.get('name') or 'Документ'), item.get('token')
                url = ('https://etp.metal-it.ru/files/get/' + quote(str(token), safe='') + '/' + quote(name, safe='')) if token else ''
                documents.append({'name': name, 'url': url})
    return documents


class UGMKClient(PublicClient):
    BASE = 'https://etp.metal-it.ru'
    HOSTS = ('etp.metal-it.ru',)

    def _graphql(self, operation, query, variables):
        payload = self._json('POST', self.BASE + '/graphql/' + operation,
            json={'operationName': operation, 'query': query, 'variables': variables})
        if payload.get('errors') or not isinstance(payload.get('data'), dict):
            raise PlatformStopped('УГМК отклонила запрос или изменила структуру API. Очередь остановлена.')
        return payload['data']

    def read(self, number, url):
        rows = []
        for skip in range(0, 200, 20):
            page = self._graphql('feed', UGMK_FEED, {'dto': {'fullTextString': number}, 'limit': 20, 'skip': skip}).get('trades')
            if not isinstance(page, dict) or not isinstance(page.get('items'), list) or type(page.get('total')) is not int:
                raise PlatformStopped('УГМК изменила формат поиска.')
            if page['total'] > 200:
                raise PlatformError('УГМК: слишком много результатов по номеру. Нужен полный реестровый номер.')
            if not page['items'] and len(rows) < page['total']:
                raise PlatformStopped('УГМК вернула неполный результат поиска. Сопоставление остановлено.')
            previous = {str(row.get('id')) for row in rows}
            rows.extend(page['items'])
            identifiers = {str(row.get('id')) for row in rows}
            if page['items'] and identifiers == previous:
                raise PlatformStopped('УГМК повторила страницу поиска. Сопоставление остановлено.')
            if len(identifiers) >= page['total']:
                break
        match = exact_match(rows, number, 'registeredNumber')
        data = self._graphql('card', UGMK_CARD, {'id': int(match['id'])}).get('trade')
        if not isinstance(data, dict) or str(data.get('id')) != str(match['id']) or number_key(data.get('registeredNumber', '')) != number_key(number):
            raise PlatformStopped('УГМК вернула карточку другого номера. Очередь остановлена.')
        raw = data.get('bidSubmissionEndDate')
        deadline = PlatformDeadline(raw=f'bidSubmissionEndDate={raw}', warning='УГМК не указала корректный срок подачи; срок 1С сохранён.')
        if type(raw) in (int, float) and 946684800000 <= raw <= 4133980800000:
            stamp = datetime.fromtimestamp(raw / 1000, timezone.utc).isoformat()
            deadline = replace(parse_deadline(stamp), raw=f'bidSubmissionEndDate={raw} ({stamp})')
        return PlatformCard(number, self.BASE + '/trades/' + str(match['id']) + '/info', {},
                            ugmk_documents(data), source_id=str(match['id']), submission_deadline=deadline)


TEKTORG_ALIASES = {'zakupki': '223-fz', '223-fz': '223-fz', 'market': 'market',
                   'interrao': 'inter_rao', 'inter_rao': 'inter_rao', 'rosneft': 'rosneft', 'rosnefttkp': 'rosnefttkp'}


def tektorg_documents(data):
    documents, seen = [], set()
    containers = [(data, True)]
    for lot in data.get('lots') or []:
        containers.append((lot, False))
        containers.extend((explanation, False) for explanation in lot.get('docsExplain') or [])
    for container, procedure in containers:
        for item in container.get('documents') or []:
            name, size = str(item.get('filename') or 'Документ'), item.get('size')
            url = item.get('httpLink') or ''
            if not url and procedure and item.get('id') is not None:
                url = 'https://api.tektorg.ru/open-api/documents/procedure/' + str(item['id'])
            key = (name, str(size)) if size else (name, str(item.get('id') or url))
            if key not in seen:
                seen.add(key)
                documents.append({'name': name, 'url': url})
    return documents


class TektorgClient(PublicClient):
    BASE = 'https://www.tektorg.ru'
    API = 'https://api.tektorg.ru/open-api'
    HOSTS = ('www.tektorg.ru', 'tektorg.ru', 'api.tektorg.ru')

    def _card(self, identifier, number):
        payload = self._json('GET', self.API + '/procedures/' + identifier)
        data = payload.get('data', payload)
        if not isinstance(data, dict) or str(data.get('id')) != identifier or number_key(data.get('registryNumber', '')) != number_key(number):
            raise PlatformStopped('ТЭК-Торг вернул карточку другого номера или изменил формат. Очередь остановлена.')
        return data

    def read(self, number, url):
        rows = []
        for page_number in range(1, 11):
            page = self._json('POST', self.BASE + '/api/getProcedures',
                json={'params': {'name': number, 'limit': 20, 'page': page_number}})
            # Verified empty search response: metadata only, unlike populated pages.
            if (page_number == 1 and set(page) == {'totalPages', 'currentPage', 'limitPage', 'hash'}
                    and type(page['totalPages']) is int and page['totalPages'] == 1
                    and type(page['currentPage']) is int and page['currentPage'] == 1
                    and type(page['limitPage']) is int and page['limitPage'] == 20
                    and isinstance(page['hash'], str) and re.fullmatch(r'[0-9a-f]{32}', page['hash'])):
                raise PlatformError('ТЭК-Торг: процедура не найдена; исходный срок сохранён.')
            if not isinstance(page.get('data'), list) or type(page.get('total')) is not int:
                raise PlatformStopped('ТЭК-Торг изменил формат поиска.')
            if page['total'] > 200:
                raise PlatformError('ТЭК-Торг: слишком много результатов. Нужен полный реестровый номер.')
            if not page['data'] and len(rows) < page['total']:
                raise PlatformStopped('ТЭК-Торг вернул неполный результат поиска. Сопоставление остановлено.')
            previous = {str(row.get('id')) for row in rows}
            rows.extend(page['data'])
            identifiers = {str(row.get('id')) for row in rows}
            if page['data'] and identifiers == previous:
                raise PlatformStopped('ТЭК-Торг повторил страницу поиска. Сопоставление остановлено.')
            if len(identifiers) >= page['total']:
                break
        match = exact_match(rows, number, 'registryNumber')
        identifier = str(match['id'])
        data = self._card(identifier, number)
        alias = TEKTORG_ALIASES.get(data.get('sectionAlias') or match.get('sectionAlias'))
        public_url = self.BASE + '/' + alias + '/procedures/' + identifier if alias else ''
        if not public_url and data.get('etpLink'):
            target = urlsplit(data['etpLink'])
            if target.hostname and (target.hostname == 'tektorg.ru' or target.hostname.endswith('.tektorg.ru')):
                public_url = trusted_url(data['etpLink'], (target.hostname,))
        if not public_url:
            self.say('ТЭК-Торг не предоставил публичную ссылку для этой секции; срок и документы будут получены по API.')
        raw = (data.get('dates') or {}).get('dateEndRegistration')
        deadline = replace(parse_deadline(str(raw or '')), raw='dateEndRegistration=' + str(raw or ''))
        return PlatformCard(number, public_url, {}, tektorg_documents(data),
                            source_id=identifier, submission_deadline=deadline)

    def download(self, card, folder, say):
        # Links carry expiring tokens: obtain them from a fresh card immediately before IO.
        fresh = self._card(card.source_id, card.number)
        yield from super().download(replace(card, documents=tektorg_documents(fresh)), folder, say)
