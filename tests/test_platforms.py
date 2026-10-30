import json
from pathlib import Path
import sqlite3
import zipfile

import pytest

from tender_app.ai_config import AIError
from tender_app.execution_repository import ExecutionRepository
from tender_app.models import Tender
from tender_app.platform_clients import PlatformCard, PlatformStopped, save_response
from tender_app.platform_deadlines import labelled_deadline, parse_deadline, sections_from_text
from tender_app.platform_files import extract_archive, publish_file
from tender_app.platform_settings import PlatformSettings
from tender_app.platform_sync import enrich_new_tenders
from tender_app.settings import Settings
from tests.conftest import SEED


LABEL = 'Дата окончания подачи заявок'


@pytest.fixture
def storage(tmp_path):
    repository = ExecutionRepository(tmp_path / 'db' / 'tenders.db', SEED)
    yield repository
    repository.close()


def add(storage, tmp_path, number='123', platform='B2B'):
    folder = tmp_path / number
    folder.mkdir(exist_ok=True)
    return storage.save(Tender(number=number, platform=platform, folder_path=str(folder),
        deadline_date='2026-11-01', deadline_time='12:00', deadline_timezone='Europe/Moscow',
        deadline_raw='01.11.2026 12:00', comment='Ручной комментарий'))


@pytest.mark.parametrize(('value', 'day', 'clock'), [
    ('27.10.2026 00:30 UTC+5', '2026-10-26', '22:30'),
    ('27.10.2026 15:00 МСК', '2026-10-27', '15:00'),
    ('27.10.2026, 15:00 МСК', '2026-10-27', '15:00'),
    ('27 октября 2026 г., 15:00 МСК', '2026-10-27', '15:00'),
    ('2026-10-27T15:00:00Z', '2026-10-27', '18:00'),
    ('2026-10-27T15:00:00+05:00', '2026-10-27', '13:00'),
    ('27.10.2026', '2026-10-27', None),
])
def test_explicit_deadlines(value, day, clock):
    deadline = labelled_deadline({LABEL: value})
    assert (deadline.day, deadline.clock, deadline.warning) == (day, clock, '')


@pytest.mark.parametrize('value', ['27.10.2026 15:00', '31.02.2026 12:00 МСК', 'завтра', '27.10.2026 12:00 UTC+25'])
def test_unknown_zone_or_invalid_date_is_not_authoritative(value):
    result = parse_deadline(value)
    assert result.warning and result.day is None


def test_deadline_label_required_and_ambiguous_values_rejected():
    assert labelled_deadline({'Срок поставки': '27.10.2026 15:00 МСК'}).warning
    sections = sections_from_text('Срок поставки: 01.12.2026\nДата окончания подачи заявок:\n27.10.2026 15:00 МСК\n')
    assert labelled_deadline(sections).clock == '15:00'
    sections['Окончание приема предложений'] = '28.10.2026 12:00 МСК'
    assert labelled_deadline(sections).warning


def test_deadline_clock_on_next_line_and_repeated_same_value():
    sections = sections_from_text(LABEL + '\n27.10.2026\n12:25\nпо МСК\nСрок поставки: 01.12.2026')
    sections['Окончание приема предложений'] = '27.10.2026, 12:25 МСК'
    assert labelled_deadline(sections).clock == '12:25'


class FakeClient:
    def __init__(self, repository, before_read=None):
        self.repository = repository
        self.before_read = before_read
        self.calls = []
        self.closed = False

    def read(self, number, url):
        assert not self.repository.connection.in_transaction
        self.calls.append(number)
        if self.before_read:
            self.before_read(number)
        return PlatformCard(number, 'https://www.b2b-center.ru/market/tender-' + number + '/',
                            {LABEL: '28.10.2026 10:00 МСК'})

    def download(self, card, folder, say):
        assert not self.repository.connection.in_transaction
        folder.mkdir(exist_ok=True)
        path = folder / 'ТЗ.txt'
        path.write_text('Тестовый документ', encoding='utf-8')
        yield path

    def close(self):
        self.closed = True


def test_only_explicit_new_ids_and_source_is_retained(storage, tmp_path):
    old = add(storage, tmp_path, '122')
    new = add(storage, tmp_path)
    client = FakeClient(storage)
    reports = enrich_new_tenders(storage, {new.id}, lambda *args: client)
    assert client.calls == ['123'] and client.closed
    saved = next(t for t in storage.all() if t.id == new.id)
    assert (saved.deadline_date, saved.deadline_time) == ('2026-10-28', '10:00')
    assert saved.deadline_raw == '01.11.2026 12:00' and saved.comment == new.comment
    assert next(t for t in storage.all() if t.id == old.id) == old
    row = storage.connection.execute('SELECT * FROM platform_checks').fetchone()
    assert row['source_1c'] == new.deadline_raw and row['applied'] == 1
    assert json.loads(row['files_json'])[0].endswith('ТЗ.txt')
    assert reports[0]['files'] == 1 and reports[0]['deadline_applied']
    assert enrich_new_tenders(storage, set(), lambda *args: pytest.fail('No client')) == []


def test_concurrent_deadline_and_url_edits_are_preserved(storage, tmp_path):
    tender = add(storage, tmp_path)
    def manual(number):
        with storage.connection:
            storage.connection.execute('UPDATE tenders SET deadline_time=?,url=? WHERE id=?',
                                       ('16:45', 'https://manual.invalid/', tender.id))
    client = FakeClient(storage, manual)
    reports = enrich_new_tenders(storage, {tender.id}, lambda *args: client)
    saved = next(t for t in storage.all() if t.id == tender.id)
    assert saved.deadline_time == '16:45' and saved.url == 'https://manual.invalid/'
    assert not reports[0]['deadline_applied']


def test_blocked_platform_keeps_partial_files_and_other_platform_continues(storage, tmp_path):
    tenders = [add(storage, tmp_path, '123'), add(storage, tmp_path, '124'),
               add(storage, tmp_path, '125', 'Тендер Про')]
    b2b = FakeClient(storage)
    pro = FakeClient(storage)
    original = b2b.download
    def partial(card, folder, say):
        yield from original(card, folder, say)
        raise PlatformStopped('Вход истёк')
    b2b.download = partial
    reports = enrich_new_tenders(storage, {t.id for t in tenders},
        lambda kind, *_: b2b if kind == 'b2b' else pro)
    assert b2b.calls == ['123'] and pro.calls == ['125']
    assert reports[0]['files'] == 1 and reports[0]['deadline_applied']
    assert not reports[1]['url'] and reports[2]['files'] == 1
    assert storage.connection.execute('SELECT COUNT(*) FROM platform_checks').fetchone()[0] == 3


def test_mismatch_never_changes_link_or_deadline(storage, tmp_path):
    tender = add(storage, tmp_path)
    client = FakeClient(storage)
    client.read = lambda *_: PlatformCard('999', 'https://www.b2b-center.ru/', {LABEL: '28.10.2026'})
    enrich_new_tenders(storage, {tender.id}, lambda *args: client)
    saved = next(t for t in storage.all() if t.id == tender.id)
    assert saved.url == tender.url and saved.deadline_date == tender.deadline_date


def test_unknown_zone_keeps_1c_and_marks_review(storage, tmp_path):
    tender = add(storage, tmp_path)
    client = FakeClient(storage)
    client.read = lambda *_: PlatformCard('123', 'https://www.b2b-center.ru/market/tender-123/', {LABEL: '28.10.2026 12:00'})
    enrich_new_tenders(storage, {tender.id}, lambda *args: client)
    saved = next(t for t in storage.all() if t.id == tender.id)
    assert saved.deadline_date == tender.deadline_date and saved.deadline_needs_review == 1


def test_publish_preserves_user_files_and_skips_identical(tmp_path):
    source = tmp_path / 'download.part'
    source.write_bytes(b'new')
    folder = tmp_path / 'documents'
    folder.mkdir()
    (folder / 'ТЗ.pdf').write_bytes(b'user original')
    result = publish_file(source, folder, 'ТЗ.pdf')
    assert result.name == 'ТЗ (1).pdf'
    assert (folder / 'ТЗ.pdf').read_bytes() == b'user original'
    assert publish_file(source, folder, 'ТЗ.pdf') == result


def test_zip_keeps_original_and_does_not_overwrite_extraction(tmp_path):
    archive = tmp_path / 'Документы.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('Чертежи/Вал.txt', 'drawing')
    assert extract_archive(archive) == ''
    extracted = tmp_path / 'Документы (zip)' / 'Чертежи' / 'Вал.txt'
    extracted.write_text('user edit')
    extract_archive(archive)
    assert extracted.read_text() == 'user edit' and archive.is_file()


@pytest.mark.parametrize('name', ['../outside.txt', 'C:/outside.txt', '..\\outside.txt', 'x:y.txt', 'NUL.txt'])
def test_zip_rejects_unsafe_members(tmp_path, name):
    archive = tmp_path / 'unsafe.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr(name, 'unsafe')
    with pytest.raises(ValueError):
        extract_archive(archive)
    assert archive.is_file() and not (tmp_path / 'unsafe (zip)').exists()


def test_7z_extracts_safely(tmp_path):
    import py7zr
    archive = tmp_path / 'Документы.7z'
    with py7zr.SevenZipFile(archive, 'w') as z:
        z.writestr(b'example', 'folder/drawing.txt')
    extract_archive(archive)
    assert (tmp_path / 'Документы (7z)' / 'folder' / 'drawing.txt').read_bytes() == b'example'


def test_interrupted_response_leaves_no_document(tmp_path):
    class Response:
        status_code = 200
        headers = {'Content-Length': '10'}
        def raise_for_status(self): pass
        def iter_content(self, size):
            yield b'abc'
    with pytest.raises(ValueError, match='не полностью'):
        save_response(Response(), tmp_path, 'ТЗ.pdf', lambda: False)
    assert list(tmp_path.iterdir()) == []


def test_credentials_never_written_to_settings_and_fallback_is_session_only(tmp_path):
    class Store:
        def set(self, *_): raise AIError('unavailable')
        def get(self, *_): raise AIError('unavailable')
    settings = Settings(tmp_path / 'settings.json')
    access = PlatformSettings(settings, Store())
    values = {'login': 'test-user', 'password': 'test-password', 'key': 'test-key'}
    assert 'до закрытия' in access.save(values)
    assert access.credentials() == values
    text = settings.path.read_text(encoding='utf-8')
    assert all(secret not in text for secret in values.values())
    assert PlatformSettings(settings, Store()).credentials() == {}


def test_v8_migration_backup_rollback_and_restore(tmp_path, monkeypatch):
    path = tmp_path / 'tenders.db'
    repo = ExecutionRepository(path, SEED)
    before = repo.all()
    repo.close()
    with sqlite3.connect(path) as connection:
        connection.execute('DROP TABLE platform_checks')
        connection.execute('ALTER TABLE tenders DROP COLUMN is_unseen')
        connection.execute('DROP TABLE execution_registry_comments')
        connection.execute('PRAGMA user_version=7')
    (tmp_path / 'before-execution-v8.db').unlink()
    (tmp_path / 'before-execution-v9.db').unlink()
    (tmp_path / 'before-execution-v10.db').unlink()
    check = ExecutionRepository.check_structure
    def fail(self, connection, version):
        if version == 8:
            raise ValueError('failed check')
        return check(self, connection, version)
    with monkeypatch.context() as patch:
        patch.setattr(ExecutionRepository, 'check_structure', fail)
        with pytest.raises(ValueError, match='failed check'):
            ExecutionRepository(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 7
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='platform_checks'").fetchone() is None
    repo = ExecutionRepository(path)
    assert repo.all() == before
    backup = tmp_path / 'before-execution-v8.db'
    original = backup.read_bytes()
    repo.restore(backup)
    assert repo.all() == before and backup.read_bytes() == original
    repo.close()
