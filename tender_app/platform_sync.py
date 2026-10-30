"""Enrich only the IDs committed by this import. Network never runs in a transaction."""
import json
from pathlib import Path
import re

from .models import now_moscow
from .platform_clients import PlatformStopped
from .platform_deadlines import PlatformDeadline
from .resource_sync import is_b2b


DEADLINE_FIELDS = ('deadline_date', 'deadline_time', 'deadline_timezone', 'deadline_needs_review')


def platform_kind(platform):
    if is_b2b(platform):
        return 'b2b'
    compact = re.sub(r'[\W_]', '', platform.casefold())
    if any(marker in compact for marker in ('угмк', 'ugmk', 'metalitru')):
        return 'ugmk'
    if any(marker in compact for marker in ('тэкторг', 'tektorg', 'текторг')):
        return 'tektorg'
    if any(marker in compact for marker in ('tenderpro', 'тендерпро')):
        return 'tenderpro'
    return ''


def record_check(repository, tender, kind, card, deadline, paths, messages):
    """Compare snapshot fields atomically. Keep source_1c and all manual fields intact."""
    repository.backup()
    stamp = now_moscow().isoformat(timespec='seconds') + '+03:00'
    with repository.connection:
        repository.connection.execute('BEGIN IMMEDIATE')
        current = repository.connection.execute('SELECT * FROM tenders WHERE id=?', (tender.id,)).fetchone()
        if current is None:
            return False, None
        same_identity = (current['number'], current['platform']) == (tender.number, tender.platform)
        applied = False
        if same_identity and card is not None:
            if card.url and current['url'] == tender.url:
                repository.connection.execute('UPDATE tenders SET url=?,updated_at=? WHERE id=?', (card.url, stamp, tender.id))
            if (deadline.day and not deadline.warning
                    and all(current[name] == getattr(tender, name) for name in DEADLINE_FIELDS)):
                repository.connection.execute('''UPDATE tenders SET deadline_date=?,deadline_time=?,
                    deadline_timezone=?,deadline_needs_review=0,updated_at=? WHERE id=?''',
                    (deadline.day, deadline.clock, deadline.zone, stamp, tender.id))
                applied = True
            elif deadline.warning and all(current[name] == getattr(tender, name) for name in DEADLINE_FIELDS):
                repository.connection.execute('UPDATE tenders SET deadline_needs_review=1 WHERE id=?', (tender.id,))
            elif deadline.day:
                messages.append('Срок изменён вручную во время загрузки; автоматическая замена пропущена.')
        elif not same_identity:
            messages.append('Номер или площадка изменены вручную; данные площадки не применены.')
        cursor = repository.connection.execute('''INSERT INTO platform_checks(tender_id,platform,checked_at,source_1c,
            url,deadline_raw,deadline_date,deadline_time,deadline_timezone,applied,files_json,messages_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
            (tender.id, kind, stamp, tender.deadline_raw, card.url if card else tender.url,
             deadline.raw, deadline.day, deadline.clock, deadline.zone, int(applied),
             json.dumps([str(path) for path in paths], ensure_ascii=False), json.dumps(messages, ensure_ascii=False)))
    return applied, cursor.lastrowid


def enrich_new_tenders(repository, ids, client_factory, say=lambda text: None, stopped=lambda: False):
    if not ids:
        return []
    clients, blocked, reports = {}, set(), []
    # A snapshot prevents later edits from being treated as the baseline for this run.
    tenders = [tender for tender in repository.all() if tender.id in ids]
    try:
        for tender in tenders:
            if stopped():
                break
            kind = platform_kind(tender.platform)
            if not kind:
                continue
            messages, paths, card, applied = [], [], None, False
            check_id = None
            deadline = PlatformDeadline()

            def report(message):
                messages.append(message)
                # Record completed files even if a later file stops the platform.
                say(f'№ {tender.number}: {message}')

            try:
                if kind in blocked:
                    raise PlatformStopped('Пропущено: очередь этой площадки остановлена. Уже добавленный тендер сохранён.')
                if kind in ('ugmk', 'tektorg'):
                    number = tender.number.strip()
                    if not number or len(number) > 100 or any(ord(char) < 32 for char in number):
                        raise ValueError('Для поиска нужен один полный реестровый номер процедуры.')
                else:
                    match = re.fullmatch(r'(?:id)?([0-9]+)' if kind == 'tenderpro' else r'([0-9]+)', tender.number.strip(), re.I)
                    if not match:
                        raise ValueError('Для поиска нужен один точный числовой номер процедуры.')
                    number = match[1]
                if not tender.folder_path or not Path(tender.folder_path).is_dir():
                    raise ValueError('Папка тендера недоступна; загрузка не выполнялась.')
                client = clients.get(kind)
                if client is None:
                    client = clients[kind] = client_factory(kind, report, stopped)
                label = {'b2b': 'B2B-Center', 'tenderpro': 'Tender.Pro', 'ugmk': 'УГМК', 'tektorg': 'ТЭК-Торг'}[kind]
                say(f'№ {tender.number}: открываю карточку {label}…')
                card = client.read(number, tender.url)
                if card.number != number:
                    card = None
                    raise PlatformStopped('Площадка вернула карточку другого номера.')
                deadline = card.deadline
                report(deadline.warning or ('Срок площадки: ' + deadline.day + (' ' + deadline.clock + ' МСК' if deadline.clock else ' · время не указано')))
                # Persist confirmed metadata before file IO; a download failure cannot lose it.
                applied, check_id = record_check(repository, tender, kind, card, deadline, [], messages)
                # download yields completed paths to preserve partial success on interruption.
                for path in client.download(card, Path(tender.folder_path) / 'С ЭТП', report):
                    paths.append(path)
            except PlatformStopped as error:
                blocked.add(kind)
                report(str(error))
            except ValueError as error:
                report(str(error))
            except Exception:
                blocked.add(kind)
                report('Не удалось завершить запрос площадки. Очередь площадки остановлена; импорт сохранён.')
            # One final audit entry carries complete messages and partial file results.
            if check_id is None:
                record_check(repository, tender, kind, None, deadline, paths, messages)
            else:
                with repository.connection:
                    repository.connection.execute('UPDATE platform_checks SET files_json=?,messages_json=? WHERE id=?',
                        (json.dumps([str(path) for path in paths], ensure_ascii=False),
                         json.dumps(messages, ensure_ascii=False), check_id))
            result = {'id': tender.id, 'number': tender.number, 'platform': kind,
                      'url': card.url if card else '', 'deadline_applied': applied,
                      'files': len(paths), 'messages': list(messages)}
            reports.append(result)
            say(f'№ {tender.number}: ссылка {"получена" if card and card.url else "не подтверждена"}; '
                f'срок {"установлен" if applied else "не заменён"}; файлов {len(paths)}.')
    finally:
        for client in clients.values():
            try:
                client.close()
            except Exception:
                pass
    return reports
