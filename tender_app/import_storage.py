"""Atomic add-only 1C import. Every connection belongs to its calling thread."""
from contextlib import closing
import hashlib
import sqlite3
import struct
from pathlib import Path
import re

from .import_1c import ImportFormatError, StaleImportPlan, make_plan
from .models import now_moscow


def database_digest(connection, exclude_tender_ids=()):
    """Hash actual typed SQLite values, not SQL quote()/iterdump() text.

    SQLite quote(text) truncates at NUL. Such legacy values must still invalidate
    previews on ANY change. Length-framed typed values avoid that blind spot.
    Rowids preserve deterministic ordering in the application's ordinary tables;
    WITHOUT ROWID extension tables use sorted encoded rows as a safe fallback.
    """
    digest = hashlib.sha256()
    excluded = frozenset(exclude_tender_ids)

    def encode(value):
        if value is None:
            tag, raw = b'N', b''
        elif isinstance(value, int):
            tag, raw = b'I', str(value).encode('ascii')
        elif isinstance(value, float):
            tag, raw = b'R', struct.pack('!d', value)
        elif isinstance(value, str):
            tag, raw = b'T', value.encode('utf-8')
        elif isinstance(value, bytes):
            tag, raw = b'B', value
        else:
            raise ValueError('Неподдерживаемый тип SQLite при проверке плана.')
        return tag + len(raw).to_bytes(8, 'big') + raw

    def feed_row(row):
        digest.update(b'row')
        for value in row:
            digest.update(encode(value))
        digest.update(b'end')

    feed_row(('schema-v2', connection.execute('PRAGMA user_version').fetchone()[0]))
    schema = list(connection.execute(
        'SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name'))
    for row in schema:
        feed_row(row)
    for kind, name, _, _ in schema:
        if kind != 'table':
            continue
        identifier = '"' + name.replace('"', '""') + '"'
        feed_row(('table', name))
        try:
            cursor = connection.execute('SELECT rowid, * FROM ' + identifier + ' ORDER BY rowid')
        except sqlite3.OperationalError as exc:
            if 'no such column: rowid' not in str(exc).lower():
                raise
            rows = [b''.join(encode(value) for value in row)
                    for row in connection.execute('SELECT * FROM ' + identifier)]
            for raw in sorted(rows):
                digest.update(b'row' + raw + b'end')
        else:
            for row in cursor:
                if name == 'tenders' and row[0] in excluded:
                    continue
                feed_row(row)
    return digest.hexdigest()


def preview(repository, text):
    if repository.connection.in_transaction:
        raise ValueError('Завершите текущую операцию базы перед разбором.')
    try:
        repository.connection.execute('BEGIN')
        result = make_plan(text, repository.all(), database_digest(repository.connection))
    finally:
        repository.connection.rollback()
    return result


def commit_import(repository, plan, selected, documents_root=None, *, mark_unseen=False):
    """Backup -> lock -> revalidate full plan -> insert -> single commit.

    No migration, daily-only backup, per-item commit, or updates of existing cards. A changed unrelated DB table also invalidates
    the plan intentionally. A verified backup digest closes the backup/lock race.
    """
    from .repository import COLUMNS, readonly, validate_tender
    selected = tuple(selected)
    if (plan.fatal or not selected or any(type(i) is not int for i in selected)
            or len(selected) != len(set(selected))):
        raise ImportFormatError('Нет допустимого выбора или план недействителен.')
    allowed = {r.ordinal: r for r in plan.rows if r.category == 'new'}
    if any(i not in allowed for i in selected):
        raise ImportFormatError('Можно добавлять только новые строки.')
    if repository.connection.in_transaction:
        raise ValueError('В соединении уже выполняется другая операция.')
    # Reject already-consumed/stale plans before even creating another backup.
    try:
        repository.connection.execute('BEGIN')
        before_backup = database_digest(repository.connection)
    finally:
        repository.connection.rollback()
    if before_backup != plan.database_digest:
        raise StaleImportPlan('База изменилась. Обновите предпросмотр; пакет не записан.')
    backup = repository.backup(manual=True)
    with closing(readonly(backup)) as copy:
        repository.check(copy)
        backup_digest = database_digest(copy)
    result = []
    expected = {}
    created_folders = []
    try:
        repository.connection.execute('BEGIN IMMEDIATE')
        current_digest = database_digest(repository.connection)
        if current_digest != plan.database_digest or current_digest != backup_digest:
            raise StaleImportPlan('База изменилась после предпросмотра или резервной копии. Обновите предпросмотр; пакет не записан.')
        fresh = make_plan(plan.text, repository.all(), current_digest)
        if fresh != plan:
            raise StaleImportPlan('План изменён или устарел. Выполните новый разбор.')
        folders = folder_index(documents_root) if documents_root is not None else None
        stamp = now_moscow()
        for ordinal in selected:
            tender = allowed[ordinal].tender(stamp)
            if documents_root is not None:
                tender.folder_path = str(import_folder(documents_root, tender.number, created_folders, folders))
            validate_tender(tender)
            values = tender.values()
            values['created_at'] = values['updated_at'] = stamp.isoformat(timespec='seconds') + '+03:00'
            names = COLUMNS[1:]
            if mark_unseen:
                names = (*names, 'is_unseen')
                values['is_unseen'] = 1
            cursor = repository.connection.execute(
                'INSERT INTO tenders (' + ','.join(names) + ') VALUES (' + ','.join('?' for _ in names) + ')',
                [values[n] for n in names])
            identifier = cursor.lastrowid
            values['id'] = identifier
            result.append(identifier)
            expected[identifier] = values
        # Triggers/extensions must not silently alter existing business rows or
        # transform the selected cards. Original schema has no such side effects.
        if len(set(result)) != len(result):
            raise StaleImportPlan('База переиспользовала ID при вставке. Пакет отменён.')
        for identifier, values in expected.items():
            names = (*COLUMNS, 'is_unseen') if mark_unseen else COLUMNS
            row = repository.connection.execute('SELECT ' + ','.join(names) +
                ' FROM tenders WHERE id=?', (identifier,)).fetchone()
            if row is None or dict(row) != values:
                raise StaleImportPlan('Правило базы изменило импортируемую карточку. Пакет отменён.')
        if database_digest(repository.connection, result) != current_digest:
            raise StaleImportPlan('Правило базы изменило другие записи. Пакет полностью отменён.')
        repository.connection.commit()
    except BaseException:
        repository.connection.rollback()
        # Remove only request files and empty directories created by this attempt.
        for folder in reversed(created_folders):
            try:
                if folder.is_dir():
                    folder.rmdir()
                else:
                    folder.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    return tuple(result), str(backup)


def import_folder(root, number, created, index=None):
    root = Path(root).resolve() if index is None else Path(root)
    if not root.is_dir():
        raise ValueError(f'Рабочая папка не найдена: {root}. Выберите «Рабочая папка…» в окне импорта.')
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', number).strip().rstrip('.')
    if not name or name.upper().split('.')[0] in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1,10)), *(f'LPT{i}' for i in range(1,10))}:
        raise ValueError(f'Номер не подходит для имени папки: {number}')
    exact, prefixes = folder_index(root) if index is None else index
    matches = exact.get(name.casefold(), []) or prefixes.get(name.casefold(), [])
    if len(matches) > 1:
        raise ValueError(f'Для номера {number} найдено несколько папок. Уточните имена папок перед импортом.')
    folder = matches[0] if matches else root / name
    prepare_tender_folder(folder, number, created)
    exact[name.casefold()] = [folder]
    return folder


def prepare_tender_folder(folder, number, created):
    """Prepare the common manual/import kit, tracking only newly created entries."""
    from .request_workbook import create_request_workbook
    folder = Path(folder)
    if not folder.exists():
        folder.mkdir()
        created.append(folder)
    subfolder = folder / 'С ЭТП'
    if not subfolder.exists():
        subfolder.mkdir()
        created.append(subfolder)
    request = create_request_workbook(folder, number)
    if request is not None:
        created.append(request)
    return folder


def folder_index(root):
    root = Path(root)
    if not root.is_dir():
        raise ValueError(f'Рабочая папка не найдена: {root}. Выберите «Рабочая папка…» в окне импорта.')
    exact, prefixes = {}, {}
    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        name = folder.name.casefold()
        exact.setdefault(name, []).append(folder)
        for position, char in enumerate(name):
            if char in ' _':
                prefixes.setdefault(name[:position], []).append(folder)
    return exact, prefixes
