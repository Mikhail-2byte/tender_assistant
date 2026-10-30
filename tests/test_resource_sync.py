from dataclasses import replace
from pathlib import Path

import pytest

from tender_app.execution_repository import ExecutionRepository
from tender_app.execution_seed import create_demo_database
from tender_app.resource_sync import needs_b2b_url, parse_b2b_results, sync_resources
from tender_app.models import Tender
from tests.test_import_1c import line


@pytest.fixture
def storage(tmp_path):
    path = tmp_path / 'test.db'
    create_demo_database(path)
    repo = ExecutionRepository(path)
    yield repo
    repo.close()


def test_real_search_response_uses_url_and_exact_number():
    payload = {'trades': [
        {'trade_id': '1234', 'url': '/market/wrong/tender-1234/'},
        {'trade_id': 123, 'url': '/market/shaft/tender-123/#tracking'},
    ]}
    assert parse_b2b_results(payload, '123') == 'https://www.b2b-center.ru/market/shaft/tender-123/'


@pytest.mark.parametrize('payload', [None, {}, {'trades': []},
    {'trades': [{'trade_id': 123, 'url': 'https://other.invalid/'}]},
    {'trades': [{'trade_id': 123, 'url': '/one'}, {'trade_id': 123, 'url': '/two'}]},
    {'trades': [{'trade_id': 123}]}, {'trades': [None]}])
def test_search_does_not_guess_when_response_is_unsafe_or_ambiguous(payload):
    with pytest.raises(ValueError):
        parse_b2b_results(payload, '123')


def test_repairs_previously_imported_cards_and_preserves_other_data(storage, tmp_path):
    root = tmp_path / 'Работа'
    root.mkdir()
    existing = root / '123 Вал'
    existing.mkdir()
    document = existing / 'Документ.txt'
    document.write_text('сохранить', encoding='utf-8')
    ids, _ = storage.import_1c(storage.preview_1c(line('123') + line('124')), (1, 2))
    before = {t.id: t for t in storage.all()}
    broken = 'https://www.b2b-center.ru/market/view.html?id=123'
    storage.connection.execute('UPDATE tenders SET url=? WHERE id=?', (broken, ids[0]))
    storage.connection.commit()
    counts, errors = sync_resources(storage, root,
        lookup=lambda number: f'https://www.b2b-center.ru/market/shaft/tender-{number}/', ids=set(ids))
    assert not errors
    assert counts == {'folder_path': 2, 'url': 2}
    saved = {t.id: t for t in storage.all()}
    assert saved[ids[0]].folder_path == str(existing)
    assert document.read_text(encoding='utf-8') == 'сохранить'
    assert (root / '124' / 'С ЭТП').is_dir()
    for identifier in ids:
        item = saved[identifier]
        assert replace(item, url=before[identifier].url,
                       folder_path=before[identifier].folder_path) == before[identifier]
    assert sync_resources(storage, root, lookup=lambda _: pytest.fail('No repeated fetch'),
                          ids=set(ids))[0] == {'folder_path': 0, 'url': 0}


def test_network_failure_still_binds_folders_and_preserves_manual_links(storage, tmp_path):
    ids, _ = storage.import_1c(storage.preview_1c(line('123') + line('124')), (1, 2))
    calls = []
    def fail(number):
        calls.append(number)
        raise ValueError('HTTP 403')
    counts, errors = sync_resources(storage, tmp_path, lookup=fail, ids=set(ids))
    assert counts == {'folder_path': 2, 'url': 0}
    assert len(calls) == 1 and 'HTTP 403' in errors[0]
    manual = Tender(number='123', platform='B2B', url='https://www.b2b-center.ru/market/my/tender-123/')
    assert not needs_b2b_url(manual)


def test_concurrent_resource_edit_is_not_overwritten(storage, tmp_path):
    ids, _ = storage.import_1c(storage.preview_1c(line('123')), (1,))
    identifier = ids[0]
    def lookup(number):
        with storage.connection:
            storage.connection.execute('UPDATE tenders SET url=?,contacts=? WHERE id=?',
                ('https://manual.invalid/', 'Новый контакт', identifier))
        return 'https://www.b2b-center.ru/market/tender-123/'
    counts, _ = sync_resources(storage, tmp_path, lookup=lookup, ids={identifier})
    saved = next(t for t in storage.all() if t.id == identifier)
    assert counts['url'] == 0
    assert saved.url == 'https://manual.invalid/' and saved.contacts == 'Новый контакт'


def test_duplicate_folder_names_are_reported_without_guessing(storage, tmp_path):
    ids, _ = storage.import_1c(storage.preview_1c(line('123', platform='Другая')), (1,))
    (tmp_path / '123 вал').mkdir()
    (tmp_path / '123_барабан').mkdir()
    counts, errors = sync_resources(storage, tmp_path, ids=set(ids))
    assert counts['folder_path'] == 0 and 'несколько' in errors[0]
    assert not (tmp_path / '123').exists()


def test_background_result_merges_resources_without_discarding_open_draft(window, app, monkeypatch, tmp_path):
    from PySide6.QtTest import QTest
    from tender_app.resource_sync import start_sync
    saved = window.repository.save(Tender(number='123', platform='B2B',
        import_issues='Источник: буфер 1С'))
    window.load_tender(saved)
    identifier = saved.id
    window.settings.values['import_documents_root'] = str(tmp_path)
    window.settings.values['ai_proxy'] = 'http://unreachable.invalid:1'
    window.edits['contacts'].setPlainText('Мой несохранённый контакт')
    window.note.setPlainText('Мой черновик записи')
    monkeypatch.setattr('tender_app.resource_sync.lookup_b2b',
        lambda number: f'https://www.b2b-center.ru/market/shaft/tender-{number}/')
    start_sync(window, {identifier})
    for _ in range(1000):
        app.processEvents()
        if window.resource_job is None:
            break
        QTest.qWait(5)
    assert window.resource_job is None
    assert window.edits['contacts'].toPlainText() == 'Мой несохранённый контакт'
    assert window.note.toPlainText() == 'Мой черновик записи'
    assert window.edits['url'].text().endswith('/tender-123/')
    assert window.edits['folder_path'].text() == str(tmp_path / '123')
    assert (tmp_path / '123' / 'С ЭТП').is_dir()
    assert window.dirty
    assert window.caption.objectName() == 'tenderNumber'
    assert window.caption.font().pointSizeF() > window.edits['number'].font().pointSizeF()


def test_cancelled_close_does_not_close_window_after_later_sync(window, app, monkeypatch):
    from PySide6.QtTest import QTest
    from tender_app.resource_sync import ResourceJob, start_sync
    monkeypatch.setattr(ResourceJob, 'run', lambda job: job.msleep(30))
    monkeypatch.setattr(window, 'guard_changes', lambda: False)
    start_sync(window)
    window.close()
    for _ in range(200):
        app.processEvents()
        if window.resource_job is None:
            break
        QTest.qWait(5)
    assert window.resource_job is None
    assert window.isVisible()
    assert not window.resource_sync_closing
