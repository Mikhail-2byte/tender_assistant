"""Repair local folder bindings and fetch B2B URLs, without fetching tender data."""
import json
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urldefrag, urljoin, urlsplit
from urllib.request import Request, build_opener

from PySide6.QtCore import QThread, QTimer, Signal, QDate

from .import_storage import folder_index, import_folder


BASE = 'https://www.b2b-center.ru'


class B2BNotFound(ValueError):
    pass


def is_b2b(platform):
    return any(marker in platform.casefold() for marker in ('b2b', 'в2в'))


def needs_b2b_url(tender):
    if not is_b2b(tender.platform) or not re.fullmatch(r'[0-9]+', tender.number.strip()):
        return False
    if not tender.url.strip():
        return True
    parts = urlsplit(tender.url)
    return (parts.hostname in ('b2b-center.ru', 'www.b2b-center.ru')
            and parts.path in ('/market/', '/market/view.html')
            and parse_qs(parts.query).get('id') == [tender.number.strip()])


def parse_b2b_results(payload, number):
    # Same search response and exact-number matching as Tensers/b2b_links.py.
    if not isinstance(payload, dict) or not isinstance(payload.get('trades'), list):
        raise ValueError('B2B изменил формат ответа поиска.')
    urls = []
    for trade in payload['trades']:
        if not isinstance(trade, dict):
            raise ValueError('B2B изменил формат списка процедур.')
        if str(trade.get('trade_id', '')).strip() != number:
            continue
        relative = trade.get('url')
        if not isinstance(relative, str) or not relative.strip():
            raise ValueError('B2B не вернул адрес найденной процедуры.')
        url = urldefrag(urljoin(BASE + '/', relative.strip()))[0]
        parts = urlsplit(url)
        if (parts.scheme != 'https' or parts.hostname not in ('b2b-center.ru', 'www.b2b-center.ru')
                or parts.username or parts.password or parts.port not in (None, 443)):
            raise ValueError('B2B вернул посторонний адрес процедуры.')
        urls.append(url)
    if len(urls) != 1:
        raise B2BNotFound('B2B: номер не найден или найдено несколько процедур.')
    return urls[0]


def lookup_b2b(number):
    query = urlencode({'query': number, 'macro_trade_type': 'buy', 'tab': 'all'})
    request = Request(BASE + '/site/api/v1/market-search/?' + query,
                      headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                               'Accept': 'application/json'})
    opener = build_opener()
    try:
        with opener.open(request, timeout=12) as response:
            if 'json' not in response.headers.get('Content-Type', '').lower():
                raise ValueError('B2B требует проверку в браузере; поиск ссылок остановлен.')
            raw = response.read(8 * 1024 * 1024 + 1)
    except HTTPError as error:
        raise ValueError(f'B2B: HTTP {error.code}; поиск ссылок остановлен.') from error
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError('B2B вернул слишком большой ответ.')
    return parse_b2b_results(json.loads(raw), number)


def sync_resources(repository, root, lookup=lookup_b2b, stopped=lambda: False,
                   pause=lambda: None, ids=None):
    """Patch only missing folders/known bad URLs; compare old values before writing."""
    tenders = [t for t in repository.all() if (ids is None or t.id in ids)
               and 'Источник: единый Excel-шаблон' not in t.import_issues]
    changes, errors = [], []
    index = folder_index(root) if Path(root).is_dir() else None
    network_available = True
    for tender in tenders:
        if stopped():
            break
        if not tender.folder_path.strip() and index is not None:
            try:
                # Old seed cards: bind existing folders only. Imported cards may create one.
                name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', tender.number).strip().rstrip('.')
                matches = index[0].get(name.casefold(), []) or index[1].get(name.casefold(), [])
                imported = 'Источник: буфер 1С' in tender.import_issues
                if matches or imported:
                    folder = import_folder(root, tender.number, [], index)
                    changes.append((tender.id, 'folder_path', tender.folder_path, str(folder)))
            except (OSError, ValueError) as error:
                errors.append(f'№ {tender.number}: {error}')
        if needs_b2b_url(tender) and network_available:
            try:
                pause()
                if stopped():
                    break
                url = lookup(tender.number.strip())
                changes.append((tender.id, 'url', tender.url, url))
            except Exception as error:
                # Stop this batch on any network/protocol problem; keep folder results.
                errors.append(f'№ {tender.number}: {error}')
                network_available = isinstance(error, B2BNotFound)
    if index is None and any(not t.folder_path.strip() for t in tenders):
        errors.append(f'Рабочая папка недоступна: {root}. Выберите её в окне «Из 1С».')
    counts = {'folder_path': 0, 'url': 0}
    if changes:
        repository.backup(manual=True)
        with repository.connection:
            for identifier, field, old, new in changes:
                # Fields come only from the two constants above, never from network data.
                cursor = repository.connection.execute(
                    f'UPDATE tenders SET {field}=? WHERE id=? AND {field}=?', (new, identifier, old))
                counts[field] += cursor.rowcount
    return counts, errors


class ResourceJob(QThread):
    progress = Signal(str)

    def __init__(self, owner, ids=None, enrich=False):
        super().__init__(owner)
        self.repository_class = type(owner.repository)
        self.path = owner.repository.path
        self.root = owner.settings.get('import_documents_root', r'C:\Работа\В работе')
        self.ids = ids
        self.enrich = enrich
        self.reports = []
        self.log = []
        if enrich:
            from .platform_settings import platform_settings
            settings = platform_settings(owner)
            self.credentials = settings.credentials()
            self.profile_dir = settings.profile_dir
        self.result = ({'folder_path': 0, 'url': 0}, [])

    def run(self):
        from threading import Event
        repository = None
        try:
            repository = self.repository_class.open_existing_for_import(self.path)
            if self.enrich:
                from .platform_sync import enrich_new_tenders
                from .platform_clients import TenderProClient
                from .platform_b2b import B2BClient
                from .platform_public import UGMKClient, TektorgClient

                def say(message):
                    self.log.append(message)
                    self.progress.emit(message)

                def factory(kind, report, stopped):
                    if kind == 'b2b':
                        return B2BClient(self.profile_dir, say, stopped)
                    if kind == 'ugmk':
                        return UGMKClient(say, stopped)
                    if kind == 'tektorg':
                        return TektorgClient(say, stopped)
                    return TenderProClient(self.credentials, say, stopped)

                self.reports = enrich_new_tenders(repository, self.ids, factory, say, self.isInterruptionRequested)
            else:
                self.result = sync_resources(repository, self.root,
                    lookup=lookup_b2b,
                    stopped=self.isInterruptionRequested, pause=lambda: Event().wait(1.5), ids=self.ids)
        except Exception as error:
            self.result = ({'folder_path': 0, 'url': 0}, [str(error)])
        finally:
            if repository:
                repository.close()


def start_sync(owner, ids=None, *, enrich=False):
    if getattr(owner, '_closed', False) or getattr(owner, 'resource_sync_closing', False):
        return
    if getattr(owner, 'import_busy', False):
        return
    previous = getattr(owner, 'resource_job', None)
    if previous is not None:
        pending = getattr(owner, 'resource_sync_queue', [])
        pending.append((None if ids is None else set(ids), enrich))
        owner.resource_sync_queue = pending
        return
    if enrich and not ids:
        return
    job = ResourceJob(owner, ids, enrich)
    owner.resource_job = job
    job.progress.connect(lambda text: owner.statusBar().showMessage(text))
    if enrich:
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QPlainTextEdit, QPushButton, QLabel
        previous_dialog = getattr(owner, 'platform_progress_dialog', None)
        if previous_dialog is not None:
            previous_dialog.hide()
            previous_dialog.deleteLater()
        dialog = QDialog(owner)
        dialog.setWindowTitle('Получение с площадок')
        dialog.resize(740, 460)
        layout = QVBoxLayout(dialog)
        progress_label = QLabel('Тендеры уже добавлены. Получаю ссылки, сроки и документы…')
        layout.addWidget(progress_label)
        output = QPlainTextEdit()
        output.setReadOnly(True)
        layout.addWidget(output)
        close = QPushButton('Скрыть')
        close.clicked.connect(dialog.hide)
        layout.addWidget(close)
        job.progress.connect(output.appendPlainText)
        job.finished.connect(lambda: progress_label.setText('Обработка завершена. Подробности — ниже.'))
        owner.platform_progress_dialog = dialog
        dialog.show()
    owner.statusBar().showMessage('Получение ссылок, сроков и документов…' if enrich else 'Привязка папок и поиск ссылок B2B…')

    def refresh_result():
        from dataclasses import replace
        counts, errors = job.result
        owner.resource_sync_errors = errors
        owner.tenders = owner.repository.all()
        if owner.current:
            saved = next((t for t in owner.tenders if t.id == owner.current.id), None)
            if saved:
                # Merge resources only, including into an open draft. Preserve typed fields.
                changes = {}
                owner.loading = True
                for field in ('folder_path', 'url'):
                    old, new = getattr(owner.current, field), getattr(saved, field)
                    if owner.edits[field].text() == old:
                        owner.edits[field].setText(new)
                        owner.loaded_text[field] = new
                    changes[field] = new
                if job.enrich:
                    from .platform_sync import DEADLINE_FIELDS
                    if not owner.deadline_changed:
                        day, clock = saved.deadline_date, saved.deadline_time
                        owner.date.setDate(QDate.fromString(day, 'yyyy-MM-dd') if day else QDate())
                        owner.time.setText(clock or '')
                        owner.change_deadline()
                    changes.update({field: getattr(saved, field) for field in DEADLINE_FIELDS})
                owner.current = replace(owner.current, **changes)
                owner.loading = False
                owner.update_resource_buttons()
                owner.mark_dirty()
        owner.refresh_list()
        owner.platform_reports = job.reports
        if enrich:
            for error in errors:
                output.appendPlainText(error)
            owner.statusBar().showMessage(f'Обработка площадок завершена: {len(job.reports)} тендеров. Подробности в окне результата.', 30000)
        else:
            owner.statusBar().showMessage(
                f'Папок привязано: {counts["folder_path"]}; ссылок B2B: {counts["url"]}. '
                + ('Не завершено: ' + '; '.join(errors) if errors else ''), 30000)
    def finished():
        try:
            refresh_result()
        except Exception:
            # The worker has already committed. Never report a failed import or
            # strand the queue because a widget could not be refreshed.
            owner.statusBar().showMessage(
                'Обработка завершена, но интерфейс не обновлён. Данные сохранены; '
                'перезапустите приложение, не повторяйте импорт.', 30000)
        finally:
            owner.loading = False
            owner.resource_job = None
            job.deleteLater()
        if getattr(owner, 'resource_sync_closing', False):
            owner.resource_sync_closing = False
            owner.resource_sync_queue = []
            owner.resource_sync_pending = False
            owner.close()
        elif getattr(owner, 'resource_sync_queue', []):
            pending_ids, pending_enrich = owner.resource_sync_queue.pop(0)
            QTimer.singleShot(0, owner, lambda: start_sync(owner, pending_ids, enrich=pending_enrich))
        elif getattr(owner, 'resource_sync_pending', False):
            owner.resource_sync_pending = False
            QTimer.singleShot(0, owner, lambda: start_sync(owner))

    job.finished.connect(finished)
    job.start()
