import csv
import hashlib
import sqlite3
from contextlib import closing
from dataclasses import replace

import pytest

from tender_app.models import Tender
from tender_app.repository import COLUMNS, Repository, readonly, require_local
from tests.conftest import SEED


def test_seed_and_existing_data_preserved(repo):
    original_hash = hashlib.sha256(SEED.read_bytes()).hexdigest()
    original = repo.all()
    assert len(original) == 237
    chosen = original[0]
    saved = repo.save(replace(chosen, status="Подался"))
    assert saved.id == chosen.id
    for name in COLUMNS:
        if name not in {"status", "updated_at"}:
            assert getattr(saved, name) == getattr(chosen, name)
    repo.save(saved)
    assert len(repo.all()) == 237
    assert hashlib.sha256(SEED.read_bytes()).hexdigest() == original_hash
    assert repo.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_new_tender_and_missing_imported_number(repo):
    with pytest.raises(ValueError):
        repo.save(Tender())
    created = repo.save(Tender(number="0001-АБ-26", comment="Строка\nКириллица", folder_path="Тендеры/0001"))
    assert created.id and created.number == "0001-АБ-26"
    assert repo.save(created).id == created.id
    no_number = next(t for t in repo.all() if not t.number)
    assert repo.save(replace(no_number, action="Проверить номер")).id == no_number.id
    assert repo.duplicates("0001-аб-26")[0].id == created.id
    assert not repo.duplicates(created.number, created.id)


def test_backup_before_first_write_and_restore(repo):
    before = [t.values() for t in repo.all()]
    repo.save(replace(repo.all()[0], comment="Изменённый текст"))
    daily = list(repo.backup_dir.glob("daily-*.db"))
    assert len(daily) == 1
    with closing(readonly(daily[0])) as conn:
        conn.row_factory = sqlite3.Row
        assert [dict(row) for row in conn.execute("SELECT * FROM tenders ORDER BY id")] == before
    repo.save(replace(repo.all()[0], status="Победа"))
    assert len(list(repo.backup_dir.glob("daily-*.db"))) == 1
    manual = repo.backup(manual=True)
    safety = repo.restore(daily[0])
    assert manual.exists() and safety.exists()
    assert [t.values() for t in repo.all()] == before


def test_retention_and_invalid_restore(repo, tmp_path):
    for _ in range(17):
        repo.backup(manual=True)
    assert len(list(repo.backup_dir.glob("*.db"))) == 14
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not a database")
    before = repo.all()
    with pytest.raises(sqlite3.DatabaseError):
        repo.restore(bad)
    assert repo.all() == before


def test_export_is_complete(repo, tmp_path):
    repo.save(Tender(number="CSV-тест", comment='Текст; "кавычки"\nЕщё строка', deadline_date="2026-10-19"))
    target = tmp_path / "Экспорт.csv"
    repo.export_csv(target)
    assert target.read_bytes().startswith(b"\xef\xbb\xbf")
    with target.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter=";"))
    assert len(rows) == 238 and tuple(rows[0]) == COLUMNS
    assert rows[-1]["comment"] == 'Текст; "кавычки"\nЕщё строка'
    with pytest.raises(ValueError):
        repo.export_csv(repo.path)


def test_write_failure_rolls_back(repo):
    tender = repo.all()[0]
    repo.connection.execute("CREATE TRIGGER reject_update BEFORE UPDATE ON tenders BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        repo.save(replace(tender, comment="Не терять"))
    assert repo.all()[0] == tender


def test_reopen_and_network_path_rejected(repo):
    created = repo.save(Tender(number="Перезапуск"))
    with closing(Repository(repo.path)) as second:
        assert next(t for t in second.all() if t.id == created.id) == created
    with pytest.raises(ValueError):
        require_local(r"\\server\share\tenders.db")


def test_backup_failure_prevents_write(repo, monkeypatch):
    before = repo.all()[0]
    def fail(*args, **kwargs):
        raise PermissionError("Нет доступа к папке резервных копий")
    monkeypatch.setattr(repo, "backup", fail)
    with pytest.raises(PermissionError):
        repo.save(replace(before, action="Новое действие"))
    assert repo.all()[0] == before


def test_restore_oldest_of_fourteen_backups(repo):
    oldest = repo.backup(manual=True)
    original = repo.all()
    repo.save(replace(original[0], comment="Новая история"))
    for _ in range(12):
        repo.backup(manual=True)
    assert oldest.exists()
    repo.restore(oldest)
    assert repo.all() == original
    assert len(list(repo.backup_dir.glob("*.db"))) == 14
