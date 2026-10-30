from pathlib import Path

import pytest

from tender_app.import_1c import procedure_url
from tender_app.import_storage import import_folder


def test_import_folder_creates_and_reuses_number_with_description(tmp_path):
    existing = tmp_path / '123 Барабан'
    existing.mkdir()
    document = existing / 'чертёж.txt'
    document.write_text('сохранить', encoding='utf-8')
    created = []
    assert import_folder(tmp_path, '123', created) == existing
    assert (existing / 'С ЭТП').is_dir()
    assert (existing / 'Запрос_123.xlsx').is_file()
    assert document.read_text(encoding='utf-8') == 'сохранить'
    assert not (tmp_path / '123').exists()
    folder = import_folder(tmp_path, '124', created)
    assert folder == tmp_path / '124'
    assert (folder / 'С ЭТП').is_dir()
    assert import_folder(tmp_path, '124', []) == folder


def test_request_workbook_matches_tensers_and_preserves_existing_file(tmp_path):
    from openpyxl import load_workbook
    from tender_app.request_workbook import create_request_workbook

    target = create_request_workbook(tmp_path, '001/А-26')
    assert target.name == 'Запрос_001_А-26.xlsx'
    workbook = load_workbook(target)
    try:
        sheet = workbook.active
        assert sheet.title == 'Sheet1'
        assert sheet['A2'].value == 'Коммерческое предложение'
        assert sheet['B5'].value == '产品名称\nНаименование'
        assert sheet['M7'].value == '=L7*K7'
        assert sheet['O7'].value == '=K7*N7'
        assert sheet['S7'].value == '=IFERROR(L7/N7,"")'
        assert sheet['M8'].value == '=SUM(M7:M7)'
        assert sheet['B5'].fill.fgColor.rgb == '000070C0'
        assert 'B8:L8' in sheet.merged_cells
        assert workbook.calculation.fullCalcOnLoad
    finally:
        workbook.close()
    target.write_bytes(b'edited by user')
    assert create_request_workbook(tmp_path, '001/А-26') is None
    assert target.read_bytes() == b'edited by user'


def test_request_save_failure_leaves_no_partial_file(tmp_path, monkeypatch):
    from tender_app.request_workbook import create_request_workbook
    def fail(*args):
        raise OSError('Ошибка записи')
    monkeypatch.setattr('openpyxl.Workbook.save', fail)
    with pytest.raises(OSError, match='Ошибка записи'):
        create_request_workbook(tmp_path, '123')
    assert list(tmp_path.iterdir()) == []


def test_import_folder_rejects_ambiguous_matches(tmp_path):
    (tmp_path / '123 Барабан').mkdir()
    (tmp_path / '123_вал').mkdir()
    with pytest.raises(ValueError, match='несколько'):
        import_folder(tmp_path, '123', [])
    assert not (tmp_path / '123').exists()


@pytest.mark.parametrize(('platform', 'number', 'url'), [
    ('B2B-Center', '123', ''),
    ('Тендер Про', 'id123', 'https://www.tender.pro/view_tender.shtml?tenderid=123'),
    ('Tender.pro', '123', 'https://www.tender.pro/view_tender.shtml?tenderid=123'),
    ('Другая ЭТП', '123', ''), ('B2B', '123/45', ''),
])
def test_procedure_opening_urls(platform, number, url):
    assert procedure_url(platform, number) == url


def test_import_commits_folders_and_urls_and_rolls_back_failed_batch(tmp_path):
    from tender_app.execution_seed import create_demo_database
    from tender_app.execution_repository import ExecutionRepository
    from tests.test_import_1c import line
    database = tmp_path / 'test.db'
    create_demo_database(database)
    repo = ExecutionRepository(database)
    root = tmp_path / 'Работа'
    root.mkdir()
    existing = root / '123 Барабан'
    existing.mkdir()
    try:
        plan = repo.preview_1c(line('123'))
        ids, _ = repo.import_1c(plan, (1,), root)
        tender = next(t for t in repo.all() if t.id == ids[0])
        assert Path(tender.folder_path) == existing
        assert not tender.url  # Obtained later from the real B2B search response.
        assert (existing / 'С ЭТП').is_dir()
        (root / '125 вал').mkdir()
        (root / '125_барабан').mkdir()
        before = repo.all()
        plan = repo.preview_1c(line('124') + line('125'))
        with pytest.raises(ValueError, match='несколько'):
            repo.import_1c(plan, (1, 2), root)
        assert repo.all() == before
        assert not (root / '124').exists()
        assert existing.is_dir()
        # Rollback also removes a new blank from a pre-existing folder,
        # while preserving documents and an already edited request.
        existing_request = existing / 'Запрос_123.xlsx'
        existing_request.write_bytes(b'user request')
        other = root / '126 Деталь'
        other.mkdir()
        document = other / 'чертеж.txt'
        document.write_bytes(b'user document')
        plan = repo.preview_1c(line('126') + line('125'))
        with pytest.raises(ValueError, match='несколько'):
            repo.import_1c(plan, (1, 2), root)
        assert list(other.iterdir()) == [document]
        assert document.read_bytes() == b'user document'
        assert existing_request.read_bytes() == b'user request'
        assert repo.all() == before
    finally:
        repo.close()
