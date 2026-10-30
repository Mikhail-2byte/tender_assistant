import json
import sqlite3
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

import tender_app.execution_repository as execution_storage
from tender_app.execution_models import Position, Specification
from tender_app.execution_repository import ExecutionRepository
from tender_app.models import Tender
from tender_app.repository import Repository
from tests.conftest import SEED


@pytest.fixture
def execution_repo(tmp_path):
    repo = ExecutionRepository(tmp_path / "Исполнение" / "tenders.db", SEED)
    yield repo
    repo.close()


def new_spec(repo, **kwargs):
    tender = repo.save(Tender(number="ДЕМО-1", customer="Вымышленный завод", status="Победа"))
    return Specification(tender_id=tender.id, number="СП-1", due="2026-10-10",
                         positions=[Position(name="Вал", ordered=Decimal("2.5"))], **kwargs)


def test_migration_preserves_all_old_values_and_reopen_is_idempotent(tmp_path):
    path = tmp_path / "tenders.db"
    old = Repository(path, SEED)
    before = [t.values() for t in old.all()]
    old.close()
    migrated = ExecutionRepository(path)
    assert [t.values() for t in migrated.all()] == before
    assert migrated.specifications() == []
    assert migrated.connection.execute("PRAGMA user_version").fetchone()[0] == 10
    migrated.close()
    restored_old = Repository(tmp_path / "before-execution-v2.db")
    assert [t.values() for t in restored_old.all()] == before
    restored_old.close()
    reopened = ExecutionRepository(path)
    assert [t.values() for t in reopened.all()] == before
    reopened.close()
    with pytest.raises(ValueError, match="версия"):
        Repository(path)


def test_failed_migration_rolls_back_and_can_be_retried(tmp_path, monkeypatch):
    path = tmp_path / "tenders.db"
    original = Repository(path, SEED)
    before = [t.values() for t in original.all()]
    original.close()
    with monkeypatch.context() as patch:
        patch.setattr(execution_storage, "SCHEMA", (*execution_storage.SCHEMA[:2], "INVALID SQL"))
        with pytest.raises(sqlite3.Error):
            ExecutionRepository(path)
    original = Repository(path)
    assert [t.values() for t in original.all()] == before
    assert original.connection.execute("SELECT name FROM sqlite_master WHERE name='execution_specs'").fetchone() is None
    original.close()
    migrated = ExecutionRepository(path)
    migrated.check(migrated.connection)
    migrated.close()


def test_roundtrip_history_foreign_keys_backup_restore_export(execution_repo, tmp_path):
    repo = execution_repo
    original = new_spec(repo)
    first = repo.save_specification(original, "Подписана спецификация")
    first.positions[0].customer_shipped = Decimal("1.25")
    first.positions[0].placed = Decimal("2.5")
    second = repo.save_specification(first, "Частичная отгрузка")
    assert second.positions[0].remaining == Decimal("1.25")
    assert len(second.history) == 2
    assert second.history[0][1] == "Частичная отгрузка"
    backup = repo.backup(manual=True)
    second.risk = "Задержка"
    repo.save_specification(second)
    repo.restore(backup)
    assert repo.specifications()[0].risk == ""
    assert len(repo.specifications()[0].history) == 2
    output = tmp_path / "Экспорт.json"
    repo.export_execution(output)
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["schema_version"] == 10
    assert data["specifications"][0]["tender_id"] == original.tender_id
    assert data["specifications"][0]["positions"][0]["customer_shipped"] == "1.25"
    assert "Подписана спецификация" in output.read_text(encoding="utf-8")
    with pytest.raises(sqlite3.IntegrityError):
        with repo.connection:
            repo.connection.execute("DELETE FROM tenders WHERE id=?", (original.tender_id,))
    path = repo.path
    reopened = ExecutionRepository(path)
    assert reopened.specifications()[0] == repo.specifications()[0]
    reopened.close()


def test_same_numbers_and_changed_result_preserve_separate_links(execution_repo):
    repo = execution_repo
    a, b = new_spec(repo), new_spec(repo)
    first = repo.save_specification(a)
    second = repo.save_specification(b)
    repo.save_specification(replace(first, id=None, positions=[Position(name="Втулка")]))
    tender = next(t for t in repo.all() if t.id == first.tender_id)
    repo.save(replace(tender, status="Проиграли"))
    assert len(repo.specifications()) == 3
    first.risk = "Результат пересмотрен"
    assert repo.save_specification(first).risk
    with pytest.raises(ValueError, match="выигранному"):
        repo.save_specification(replace(first, id=None))
    second.positions[0].id = first.positions[0].id
    with pytest.raises(ValueError, match="другой спецификации"):
        repo.save_specification(second)


@pytest.mark.parametrize("field,value", [("ordered", "-1"), ("ordered", "0"), ("ordered", "NaN"),
                                         ("ordered", "Infinity"), ("ordered", "1.1234567"),
                                         ("placed", "3"), ("customer_shipped", "3")])
def test_invalid_quantities_do_not_write(execution_repo, field, value):
    spec = new_spec(execution_repo)
    setattr(spec.positions[0], field, value)
    with pytest.raises(ValueError):
        execution_repo.save_specification(spec)
    assert execution_repo.specifications() == []


def test_mixed_units_unknowns_deadlines_and_manual_completion():
    spec = Specification(tender_id=1, number="СП", due="2026-10-10", revised="2026-11-10", positions=[
        Position(name="Вал", ordered=Decimal("10"), placed=Decimal("10"), customer_shipped=Decimal("4"), stage="В пути"),
        Position(name="Материал", unit="т", ordered=Decimal("2.5"), stage="В производстве"),
    ])
    spec.validate()
    assert spec.summary == "Частично отгружено"
    assert spec.quantities() == "10 / 4 / 6 шт\n2,5 / не указано / не указано т"
    assert not spec.overdue(date(2026, 10, 20))
    spec.revised = None
    assert spec.overdue(date(2026, 10, 20))
    spec.due = None
    assert not spec.overdue(date(2026, 10, 20))
    spec.completed = True
    with pytest.raises(ValueError, match="без остатка"):
        spec.validate()
    for position in spec.positions:
        position.customer_shipped = position.ordered
    spec.positions[0].placed = Decimal("10")
    spec.validate()
    assert spec.summary == "Завершено"
    spec.completed = False
    assert spec.summary == "Отгружено заказчику"
    assert not spec.overdue(date(2026, 10, 20))


def test_failed_save_is_atomic_and_keeps_editor_ids(execution_repo):
    repo = execution_repo
    spec = new_spec(repo)
    with repo.connection:
        repo.connection.execute("""CREATE TRIGGER fail_position BEFORE INSERT ON execution_positions
                                  BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        repo.save_specification(spec, "Запись не должна остаться")
    assert spec.id is None
    assert spec.positions[0].id is None
    assert repo.specifications() == []
    assert repo.connection.execute("SELECT count(*) FROM execution_history").fetchone()[0] == 0
    with repo.connection:
        repo.connection.execute("DROP TRIGGER fail_position")
    saved = repo.save_specification(spec)
    assert saved.id and saved.positions[0].id


def test_backup_failure_prevents_migration_and_save(tmp_path, monkeypatch):
    path = tmp_path / "tenders.db"
    original = Repository(path, SEED)
    original.close()
    with monkeypatch.context() as patch:
        def denied(*args):
            raise PermissionError("backup denied")
        patch.setattr(execution_storage.os, "replace", denied)
        with pytest.raises(PermissionError):
            ExecutionRepository(path)
    original = Repository(path)
    original.close()
    repo = ExecutionRepository(path)
    spec = new_spec(repo)
    monkeypatch.setattr(repo, "backup", denied)
    with pytest.raises(PermissionError):
        repo.save_specification(spec)
    assert repo.specifications() == []
    repo.close()


def test_unknown_units_are_not_summed_in_model_or_registry():
    from dataclasses import asdict
    from tender_app.execution_demo import quantities
    from tender_app.execution_models import UNKNOWN_UNIT
    spec = Specification(tender_id=1,number="СП",positions=[
        Position(name="Материал",unit=UNKNOWN_UNIT,ordered=Decimal("2.5")),
        Position(name="Вал",unit=UNKNOWN_UNIT,ordered=Decimal("1")),
    ])
    assert spec.quantities() == "2 поз. · единицы не указаны"
    assert quantities(asdict(spec)) == spec.quantities()


def test_archive_and_return_preserve_quantities_status_and_history(execution_repo, tmp_path):
    repo = execution_repo
    spec = repo.save_specification(new_spec(repo), "Исходная запись")
    assert spec.overdue(date(2026, 10, 20))
    repo.set_archived(spec.id, True)
    archived = repo.specifications()[0]
    assert archived.archived and not archived.completed
    assert not archived.overdue(date(2026, 10, 20))
    assert archived.positions == spec.positions and archived.stage == spec.stage
    assert archived.history[-1] == spec.history[0]
    repo.set_archived(spec.id, True)
    assert len(repo.specifications()[0].history) == 2
    repo.set_archived(spec.id, False)
    returned = repo.specifications()[0]
    assert not returned.archived and returned.overdue(date(2026, 10, 20))
    assert returned.positions == spec.positions
    output = tmp_path / "archive.json"
    repo.export_execution(output)
    assert json.loads(output.read_text(encoding="utf-8"))["specifications"][0]["archived"] is False


def test_v2_migration_preserves_data_backup_and_old_backup_restore(tmp_path):
    from contextlib import closing
    from tender_app.repository import readonly

    path = tmp_path / "tenders.db"
    repo = ExecutionRepository(path, SEED)
    saved = repo.save_specification(new_spec(repo))
    repo.close()
    # A fixture in the preceding schema, including existing execution data.
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE execution_comments")
        connection.execute("ALTER TABLE execution_positions DROP COLUMN comment")
        connection.execute("ALTER TABLE execution_specs DROP COLUMN archived")
        connection.execute("ALTER TABLE execution_positions DROP COLUMN working_note")
        connection.execute("ALTER TABLE execution_positions DROP COLUMN due")
        connection.execute("DROP TABLE platform_checks")
        connection.execute("ALTER TABLE tenders DROP COLUMN is_unseen")
        connection.execute("DROP TABLE IF EXISTS execution_registry_comments")
        connection.execute("PRAGMA user_version=2")
        (path.parent / "before-execution-v10.db").unlink(missing_ok=True)
    (tmp_path / "before-execution-v3.db").unlink()
    (tmp_path / "before-execution-v4.db").unlink()
    (tmp_path / "before-execution-v5.db").unlink()
    (tmp_path / "before-execution-v6.db").unlink()
    (tmp_path / "before-execution-v7.db").unlink()
    (tmp_path / "before-execution-v8.db").unlink()
    (tmp_path / "before-execution-v9.db").unlink()
    repo = ExecutionRepository(path)
    assert repo.specifications()[0] == saved
    backup = tmp_path / "before-execution-v3.db"
    with closing(readonly(backup)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    repo.set_archived(saved.id, True)
    repo.restore(backup)
    assert repo.specifications()[0] == saved
    repo.close()


def test_v3_migration_failure_rolls_back_and_retries(tmp_path, monkeypatch):
    path = tmp_path / "tenders.db"
    repo = ExecutionRepository(path, SEED)
    repo.close()
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE execution_comments")
        connection.execute("ALTER TABLE execution_positions DROP COLUMN comment")
        connection.execute("ALTER TABLE execution_specs DROP COLUMN archived")
        connection.execute("ALTER TABLE execution_positions DROP COLUMN working_note")
        connection.execute("ALTER TABLE execution_positions DROP COLUMN due")
        connection.execute("DROP TABLE platform_checks")
        connection.execute("ALTER TABLE tenders DROP COLUMN is_unseen")
        connection.execute("DROP TABLE IF EXISTS execution_registry_comments")
        connection.execute("PRAGMA user_version=2")
        (path.parent / "before-execution-v10.db").unlink(missing_ok=True)
    (tmp_path / "before-execution-v3.db").unlink()
    (tmp_path / "before-execution-v4.db").unlink()
    (tmp_path / "before-execution-v5.db").unlink()
    (tmp_path / "before-execution-v6.db").unlink()
    (tmp_path / "before-execution-v7.db").unlink()
    (tmp_path / "before-execution-v8.db").unlink()
    (tmp_path / "before-execution-v9.db").unlink()
    with monkeypatch.context() as patch:
        original_check = ExecutionRepository.check_structure
        def fail(self, connection, version):
            if version == 3:
                raise ValueError("verification failed")
            return original_check(self, connection, version)
        patch.setattr(ExecutionRepository, "check_structure", fail)
        with pytest.raises(ValueError, match="verification failed"):
            ExecutionRepository(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert "archived" not in {r[1] for r in connection.execute("PRAGMA table_info(execution_specs)")}
    repo = ExecutionRepository(path)
    repo.close()


def test_position_comment_keeps_source_and_quantities_and_is_atomic(execution_repo):
    repo = execution_repo
    spec = new_spec(repo)
    spec.positions[0].note = "Исходная строка Excel"
    saved = repo.save_specification(spec)
    position = saved.positions[0]
    repo.set_position_comment(saved.id, position.id, "На складе, готовим документы\nМашина завтра")
    updated = repo.specifications()[0]
    assert updated.positions[0].comment.startswith("На складе")
    assert updated.positions[0].note == position.note
    assert updated.positions[0].ordered == position.ordered
    assert updated.positions[0].customer_shipped == position.customer_shipped
    assert len(updated.history) == 2
    with pytest.raises(ValueError, match="Позиция"):
        repo.set_position_comment(saved.id + 100, position.id, "Чужая позиция")
    with repo.connection:
        repo.connection.execute("CREATE TRIGGER fail_comment BEFORE INSERT ON execution_history BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(sqlite3.IntegrityError):
        repo.set_position_comment(saved.id, position.id, "Не сохраняется")
    assert repo.specifications()[0] == updated


def test_v4_comment_migration_preserves_archive_and_old_notes_and_restores_v3(tmp_path, monkeypatch):
    path = tmp_path / "tenders.db"
    repo = ExecutionRepository(path, SEED)
    spec = new_spec(repo)
    spec.positions[0].note = "Источник"
    saved = repo.save_specification(spec)
    repo.set_archived(saved.id, True)
    original = repo.specifications()[0]
    repo.close()

    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE execution_comments")
        connection.execute("ALTER TABLE execution_positions DROP COLUMN comment")
        connection.execute("ALTER TABLE execution_positions DROP COLUMN working_note")
        connection.execute("ALTER TABLE execution_positions DROP COLUMN due")
        connection.execute("DROP TABLE platform_checks")
        connection.execute("ALTER TABLE tenders DROP COLUMN is_unseen")
        connection.execute("DROP TABLE IF EXISTS execution_registry_comments")
        connection.execute("PRAGMA user_version=3")
        (path.parent / "before-execution-v10.db").unlink(missing_ok=True)
    (tmp_path / "before-execution-v4.db").unlink()
    (tmp_path / "before-execution-v5.db").unlink()
    (tmp_path / "before-execution-v6.db").unlink()
    (tmp_path / "before-execution-v7.db").unlink()
    (tmp_path / "before-execution-v8.db").unlink()
    (tmp_path / "before-execution-v9.db").unlink()
    with monkeypatch.context() as patch:
        original_check = ExecutionRepository.check_structure
        def fail(self, connection, version):
            if version == 4:
                raise ValueError("verification failed")
            return original_check(self, connection, version)
        patch.setattr(ExecutionRepository, "check_structure", fail)
        with pytest.raises(ValueError, match="verification failed"):
            ExecutionRepository(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert "comment" not in {r[1] for r in connection.execute("PRAGMA table_info(execution_positions)")}
    repo = ExecutionRepository(path)
    assert repo.specifications()[0] == original
    repo.set_position_comment(saved.id, original.positions[0].id, "Новый комментарий")
    repo.restore(tmp_path / "before-execution-v4.db")
    assert repo.specifications()[0] == original
    repo.close()


def test_comments_are_append_only_isolated_and_export_all_entries(execution_repo, tmp_path):
    repo = execution_repo
    spec = new_spec(repo)
    spec.positions.append(Position(name="Вал"))  # Same names still have separate IDs.
    saved = repo.save_specification(spec)
    a, b = saved.positions
    repo.set_position_comment(saved.id, a.id, "Готово")
    repo.set_position_comment(saved.id, a.id, "Готово")
    repo.set_position_comment(saved.id, b.id, "В работе")
    repo.add_specification_comment(saved.id, "Заказчик согласовал отгрузку")
    result = repo.specifications()[0]
    assert [text for _, text in result.positions[0].comments] == ["Готово", "Готово"]
    assert [text for _, text in result.positions[1].comments] == ["В работе"]
    assert result.comments[0][1] == "Заказчик согласовал отгрузку"
    for stamp, _ in result.positions[0].comments:
        assert stamp.endswith("+03:00")
    backup = repo.backup(manual=True)
    repo.add_specification_comment(saved.id, "Поздняя запись")
    repo.restore(backup)
    assert repo.specifications()[0] == result
    output = tmp_path / "comments.json"
    repo.export_execution(output)
    exported = json.loads(output.read_text(encoding="utf-8"))
    assert len(exported["specifications"][0]["positions"][0]["comments"]) == 2
    assert exported["specifications"][0]["comments"][0][1] == "Заказчик согласовал отгрузку"


def test_v5_migrates_previous_position_history_and_rolls_back_on_failure(tmp_path, monkeypatch):
    path = tmp_path / "tenders.db"
    repo = ExecutionRepository(path, SEED)
    saved = repo.save_specification(new_spec(repo))
    pid = saved.positions[0].id
    repo.set_position_comment(saved.id, pid, "Изготовление началось")
    repo.set_position_comment(saved.id, pid, "Изделие готово")
    previous = repo.specifications()[0]
    for item in previous.positions:
        item.working_note = item.comment
    with repo.connection:
        repo.connection.execute("INSERT INTO execution_history(spec_id,stamp,message) VALUES (?,?,?)",
                                (saved.id, "2026-10-20T12:00:00+03:00", "Согласовали поставку"))
    repo.close()
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE execution_comments")
        connection.execute("ALTER TABLE execution_positions DROP COLUMN working_note")
        connection.execute("ALTER TABLE execution_positions DROP COLUMN due")
        connection.execute("DROP TABLE platform_checks")
        connection.execute("ALTER TABLE tenders DROP COLUMN is_unseen")
        connection.execute("DROP TABLE IF EXISTS execution_registry_comments")
        connection.execute("PRAGMA user_version=4")
        (path.parent / "before-execution-v10.db").unlink(missing_ok=True)
    (tmp_path / "before-execution-v5.db").unlink()
    (tmp_path / "before-execution-v6.db").unlink()
    (tmp_path / "before-execution-v7.db").unlink()
    (tmp_path / "before-execution-v8.db").unlink()
    (tmp_path / "before-execution-v9.db").unlink()
    with monkeypatch.context() as patch:
        original_check = ExecutionRepository.check_structure
        def fail(self, connection, version):
            if version == 5:
                raise ValueError("verification failed")
            return original_check(self, connection, version)
        patch.setattr(ExecutionRepository, "check_structure", fail)
        with pytest.raises(ValueError, match="verification failed"):
            ExecutionRepository(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='execution_comments'").fetchone() is None
    repo = ExecutionRepository(path)
    current = repo.specifications()[0]
    assert current.positions == previous.positions
    assert current.comments == [("2026-10-20T12:00:00+03:00", "Согласовали поставку")]
    repo.set_position_comment(saved.id, pid, "Дополнение")
    repo.restore(tmp_path / "before-execution-v5.db")
    assert repo.specifications()[0] == current
    repo.close()


def test_migration_preserves_last_comment_without_inventing_missing_timestamp(tmp_path):
    path = tmp_path / "tenders.db"
    repo = ExecutionRepository(path, SEED)
    spec = repo.save_specification(new_spec(repo))
    repo.set_position_comment(spec.id, spec.positions[0].id, "Первый комментарий")
    repo.close()
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE execution_positions SET comment='latest without timestamp'")
        connection.execute("DROP TABLE execution_comments")
        connection.execute("ALTER TABLE execution_positions DROP COLUMN working_note")
        connection.execute("ALTER TABLE execution_positions DROP COLUMN due")
        connection.execute("DROP TABLE platform_checks")
        connection.execute("ALTER TABLE tenders DROP COLUMN is_unseen")
        connection.execute("DROP TABLE IF EXISTS execution_registry_comments")
        connection.execute("PRAGMA user_version=4")
        (path.parent / "before-execution-v10.db").unlink(missing_ok=True)
    (tmp_path / "before-execution-v5.db").unlink()
    (tmp_path / "before-execution-v6.db").unlink()
    (tmp_path / "before-execution-v7.db").unlink()
    (tmp_path / "before-execution-v8.db").unlink()
    (tmp_path / "before-execution-v9.db").unlink()
    repo = ExecutionRepository(path)
    comments = repo.specifications()[0].positions[0].comments
    assert comments[0] == (None, "latest without timestamp")
    assert comments[1][1] == "Первый комментарий"
    repo.close()


def test_partial_shipments_update_cumulative_total_remaining_and_event_history(execution_repo):
    repo = execution_repo
    spec = new_spec(repo)
    spec.positions[0].ordered = spec.positions[0].placed = Decimal("4")
    saved = repo.save_specification(spec)
    pid = saved.positions[0].id
    repo.set_position_shipped(saved.id, pid, "2")
    half = repo.specifications()[0]
    assert half.positions[0].customer_shipped == 2 and half.positions[0].remaining == 2
    assert half.summary == "Частично отгружено"
    assert half.positions[0].comments[0][0].endswith("+03:00")
    assert "не указано → 2" in half.positions[0].comments[0][1]
    repo.set_position_shipped(saved.id, pid, "2,0")
    assert repo.specifications()[0] == half
    repo.set_position_shipped(saved.id, pid, "4")
    full = repo.specifications()[0]
    assert full.positions[0].remaining == 0 and full.summary == "Отгружено заказчику"
    assert len(full.positions[0].comments) == 2
    assert not full.overdue(date(2026, 10, 20))
    assert not full.archived
    repo.set_position_shipped(saved.id, pid, "3,5")
    corrected = repo.specifications()[0]
    assert corrected.positions[0].remaining == Decimal("0.5")
    for value in ("5", "-1", "NaN", "1.1234567"):
        with pytest.raises(ValueError):
            repo.set_position_shipped(saved.id, pid, value)
        assert repo.specifications()[0] == corrected
    with repo.connection:
        repo.connection.execute("CREATE TRIGGER fail_shipment BEFORE INSERT ON execution_history BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(sqlite3.IntegrityError):
        repo.set_position_shipped(saved.id, pid, "1")
    assert repo.specifications()[0] == corrected
    with pytest.raises(ValueError, match="Позиция"):
        repo.set_position_shipped(saved.id + 100, pid, "2")


@pytest.mark.parametrize("dates, expected", [
    (["2026-10-15", "2027-02-10"], "2026-10-15"),
    ([None, "2027-02-10"], "2026-11-10"),
    ([None, None], "2026-11-10"),
    ([], "2026-11-10"),
])
def test_position_delivery_dates_and_preview_agree(dates, expected):
    from dataclasses import asdict
    from tender_app.execution_demo import delivery_due, is_overdue
    spec = Specification(tender_id=1, number="СП", due="2026-10-10", revised="2026-11-10",
                         positions=[Position(name="Вал", due=value) for value in dates])
    assert spec.delivery_due == delivery_due(asdict(spec)) == expected
    assert spec.overdue(date(2026, 10, 20)) == is_overdue(asdict(spec), date(2026, 10, 20))
    if dates and dates[0] == "2026-10-15":
        assert spec.overdue(date(2026, 10, 20))
        spec.positions[0].customer_shipped = spec.positions[0].ordered
        assert not spec.overdue(date(2026, 10, 20))
        assert not is_overdue(asdict(spec), date(2026, 10, 20))


def test_delivery_days_and_missing_dates():
    from tender_app.execution_models import delivery_days
    assert delivery_days("2026-10-01", "2026-10-31") == "30 кал. дн."
    assert delivery_days("2026-10-01", "2026-10-01") == "0 кал. дн."
    assert delivery_days("2026-10-31", "2026-10-01") == "несогласованные даты"
    assert delivery_days(None, "2026-10-01") == "не указано"
    assert delivery_days("2026-10-01", None) == "не указано"
    assert Specification(number="СП", positions=[Position(name="Вал")]).delivery_due is None


def test_position_due_round_trip_and_validation(execution_repo):
    spec = new_spec(execution_repo)
    spec.positions[0].due = "2027-02-10"
    saved = execution_repo.save_specification(spec)
    assert saved.positions[0].due == "2027-02-10"
    saved.positions[0].due = "31.02.2027"
    with pytest.raises(ValueError, match="Срок поставки позиции"):
        execution_repo.save_specification(saved)


def test_v6_migration_preserves_data_restores_v5_and_retries_on_failure(tmp_path, monkeypatch):
    path = tmp_path / "tenders.db"
    repo = ExecutionRepository(path, SEED)
    saved = repo.save_specification(new_spec(repo))
    repo.set_position_comment(saved.id, saved.positions[0].id, "Отгружено 1 шт.")
    previous = repo.specifications()
    for spec in previous:
        for item in spec.positions:
            item.working_note = item.comment
    repo.close()

    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE execution_positions DROP COLUMN working_note")
        connection.execute("ALTER TABLE execution_positions DROP COLUMN due")
        connection.execute("DROP TABLE platform_checks")
        connection.execute("ALTER TABLE tenders DROP COLUMN is_unseen")
        connection.execute("DROP TABLE IF EXISTS execution_registry_comments")
        connection.execute("PRAGMA user_version=5")
        (path.parent / "before-execution-v10.db").unlink(missing_ok=True)
    (tmp_path / "before-execution-v6.db").unlink()
    (tmp_path / "before-execution-v7.db").unlink()
    (tmp_path / "before-execution-v8.db").unlink()
    (tmp_path / "before-execution-v9.db").unlink()
    with monkeypatch.context() as patch:
        original = ExecutionRepository.check_structure
        def fail(self, connection, version):
            if version == 6:
                raise ValueError("verification failed")
            return original(self, connection, version)
        patch.setattr(ExecutionRepository, "check_structure", fail)
        with pytest.raises(ValueError, match="verification failed"):
            ExecutionRepository(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
        assert "due" not in {r[1] for r in connection.execute("PRAGMA table_info(execution_positions)")}
    backup = tmp_path / "before-execution-v6.db"
    backup_bytes = backup.read_bytes()
    repo = ExecutionRepository(path)
    assert repo.specifications() == previous
    spec = repo.specifications()[0]
    spec.positions[0].due = "2027-02-10"
    repo.save_specification(spec)
    repo.restore(backup)
    assert repo.specifications() == previous
    assert backup.read_bytes() == backup_bytes
    repo.close()


def test_quick_fields_keep_note_separate_from_history_and_are_atomic(execution_repo):
    repo = execution_repo
    spec = repo.save_specification(new_spec(repo))
    pid = spec.positions[0].id
    original_quantity = spec.positions[0].ordered
    for field, value in (("working_note", "Отгружено 1 из 2"), ("due", "2027-02-10"),
                         ("supplier", "Завод"), ("transport", "Авто")):
        assert repo.update_position_field(spec.id, pid, field, value)
        assert not repo.update_position_field(spec.id, pid, field, value)
    repo.set_position_comment(spec.id, pid, "Новое событие")
    updated = repo.specifications()[0].positions[0]
    assert updated.working_note == "Отгружено 1 из 2"
    assert updated.ordered == original_quantity
    assert updated.comment == "Новое событие"
    repo.delete_execution_comment(spec.id, pid, repo.comment_entries(spec.id, pid)[0][0])
    assert repo.specifications()[0].positions[0].working_note == updated.working_note
    with pytest.raises(ValueError):
        repo.update_position_field(spec.id, pid, "due", "31.02.2027")
    assert repo.specifications()[0].positions[0].due == "2027-02-10"
    repo.connection.execute("CREATE TRIGGER fail_quick BEFORE INSERT ON execution_history BEGIN SELECT RAISE(ABORT,'test'); END")
    before = repo.specifications()
    with pytest.raises(sqlite3.IntegrityError):
        repo.update_position_field(spec.id, pid, "working_note", "Не сохраняется")
    assert repo.specifications() == before


def test_v7_note_migration_preserves_history_and_restores_v6(tmp_path, monkeypatch):
    path = tmp_path / "tenders.db"
    repo = ExecutionRepository(path, SEED)
    spec = repo.save_specification(new_spec(repo))
    pid = spec.positions[0].id
    repo.set_position_comment(spec.id, pid, "Текущая заметка")
    old_comments = repo.specifications()[0].positions[0].comments
    repo.close()
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE execution_positions DROP COLUMN working_note")
        connection.execute("DROP TABLE platform_checks")
        connection.execute("ALTER TABLE tenders DROP COLUMN is_unseen")
        connection.execute("DROP TABLE IF EXISTS execution_registry_comments")
        connection.execute("PRAGMA user_version=6")
        (path.parent / "before-execution-v10.db").unlink(missing_ok=True)
    (tmp_path / "before-execution-v7.db").unlink()
    (tmp_path / "before-execution-v8.db").unlink()
    (tmp_path / "before-execution-v9.db").unlink()
    with monkeypatch.context() as patch:
        original = ExecutionRepository.check_structure
        def fail(self, connection, version):
            if version == 7:
                raise ValueError("verification failed")
            return original(self, connection, version)
        patch.setattr(ExecutionRepository, "check_structure", fail)
        with pytest.raises(ValueError, match="verification failed"):
            ExecutionRepository(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 6
        assert "working_note" not in {r[1] for r in connection.execute("PRAGMA table_info(execution_positions)")}
    repo = ExecutionRepository(path)
    migrated = repo.specifications()[0].positions[0]
    assert migrated.working_note == "Текущая заметка"
    assert migrated.comments == old_comments
    repo.update_position_field(spec.id, pid, "working_note", "Новая заметка")
    backup = tmp_path / "before-execution-v7.db"
    backup_bytes = backup.read_bytes()
    repo.restore(backup)
    assert repo.specifications()[0].positions[0] == migrated
    assert backup.read_bytes() == backup_bytes
    repo.close()
