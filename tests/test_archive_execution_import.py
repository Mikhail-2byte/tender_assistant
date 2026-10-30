import json
from dataclasses import replace

from PySide6.QtCore import QLockFile
import pytest

from scripts.archive_execution_import import ARCHIVE_SHEET, archive_source_records
from tender_app.execution_models import Position, Specification
from tender_app.execution_repository import ExecutionRepository
from tender_app.models import Tender
from tests.conftest import SEED


def test_archive_import_uses_spec_mapping_not_shared_tender_and_is_idempotent(tmp_path):
    path = tmp_path / "tenders.db"
    repo = ExecutionRepository(path, SEED)
    tender = repo.save(Tender(number="ДЕМО", customer="Пример", status="Победа", source_sheet=ARCHIVE_SHEET))
    archived = repo.save_specification(Specification(tender_id=tender.id, number="СП-1", positions=[Position(name="Вал")]))
    working = repo.save_specification(replace(archived, id=None, number="СП-2", positions=[Position(name="Втулка")]))
    mapping = [{"sheet": ARCHIVE_SHEET, "specification_id": archived.id},
               {"sheet": "Текущие тендеры", "specification_id": working.id}]
    with repo.connection:
        repo.connection.execute("INSERT INTO app_meta(key,value) VALUES (?,?)", ("execution_import_mapping", json.dumps(mapping)))
    before = repo.specifications()
    repo.close()
    assert archive_source_records(path)["archived_now"] == 1
    assert archive_source_records(path)["archived_now"] == 0
    repo = ExecutionRepository(path)
    after = repo.specifications()
    assert after[0].archived and not after[0].completed
    assert after[0].positions == before[0].positions and after[1] == before[1]
    repo.set_archived(archived.id, False)
    repo.close()
    assert archive_source_records(path)["archived_now"] == 0  # Manual return survives a repeated run.


def test_archive_import_respects_application_lock(tmp_path):
    path = tmp_path / "tenders.db"
    lock = QLockFile(str(tmp_path / "application.lock"))
    assert lock.tryLock(0)
    try:
        with pytest.raises(RuntimeError, match="Закройте"):
            archive_source_records(path)
        assert not path.exists()
    finally:
        lock.unlock()
