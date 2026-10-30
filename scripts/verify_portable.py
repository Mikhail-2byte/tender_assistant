"""Verify only a freshly extracted execution build, never existing portable data."""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile


def verify():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from tender_app.execution_repository import ExecutionRepository
    from tender_app.execution_groups import contract_key

    name = "TenderAssistantExecutionPreview"
    archive = root / "dist" / f"{name}-portable.zip"
    artifacts = root / "artifacts" / "checks" / "portable"
    artifacts.mkdir(parents=True, exist_ok=True)
    protected = [root / "data" / "sample" / "tenders.db"]
    protected.extend((root / "data" / "source").rglob("*.xlsx"))
    for working in [root / "artifacts" / "execution-preview-data", *(root / "artifacts").glob("work-data-*")]:
        protected.extend(working.glob("*.db"))
        protected.extend(working.glob("*.json"))
        protected.extend((working / "backups").rglob("*.db"))
    for directory in (root / "tender_app" / "data", root / "dist" / "TenderAssistant", root / "dist" / "TenderAssistantChatPreview",
                      root / "dist" / name, root / "artifacts" / "legacy-releases"):
        protected.extend(directory.rglob("*.db"))
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected if p.is_file()}
    target = Path(tempfile.mkdtemp(prefix="Проверка ZIP с пробелами ", dir=artifacts))
    with ZipFile(archive) as package:
        assert not any("/data/" in entry for entry in package.namelist())
        assert not any(entry.endswith((".xlsx", ".csv")) for entry in package.namelist())
        package.extractall(target)
    portable = target / name
    env = os.environ.copy()
    env["PATH"] = str(Path(env["SYSTEMROOT"]) / "System32")
    for variable in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
        env.pop(variable, None)

    def launch():
        result = subprocess.run([str(portable / f"{name}.exe"), "--smoke-test"],
                                cwd=target, env=env, timeout=45)
        assert result.returncode == 0, result.returncode
        return json.loads((portable / "data" / "execution-smoke.json").read_text(encoding="utf-8"))

    initial = launch()
    assert initial['platform_runtime_ready'] is True
    assert initial["records"] == 4 and initial["specifications"] == 4
    assert initial["schema_version"] == 10 and initial["frozen"] is True
    assert initial["sections"] == ["Реестр", "Исполнение", "Агент", "Реестр исполнения"]
    assert initial["execution_registry_columns"] == 5
    assert initial["execution_registry_levels"] == ["contract", "position", "specification"]
    assert initial["without_specifications"] == 1
    assert initial["navigation"] == "contracts" and initial["contracts"] == 2
    database = portable / "data" / "tenders.db"
    assert Path(initial["database"]) == database
    repo = ExecutionRepository(database)
    try:
        assert all((portable / "Документы" / t.folder_path).is_dir() for t in repo.all())
        backup = repo.backup(manual=True)
        spec = repo.specifications()[0]
        tender = next(t for t in repo.all() if t.id == spec.tender_id)
        contract_owner = ("contract", contract_key(tender, {"contract": spec.contract}))
        repo.update_registry_comment(contract_owner, "Комментарий договора из portable", "")
        contract_comment = "Вторая запись договора\nДополнительная строка"
        repo.update_registry_comment(contract_owner, contract_comment, "Комментарий договора из portable", append=True)
        spec.positions[0].customer_shipped = Decimal("0.5")
        spec.positions[0].due = "2027-02-10"
        spec.positions[0].working_note = "Рабочая заметка для проверки"
        spec.positions[0].comment = "Проверка комментария к позиции"
        repo.save_specification(spec, "Проверка сохранения после распаковки")
        repo.set_position_comment(spec.id, spec.positions[0].id, "Второй комментарий")
        repo.add_specification_comment(spec.id, "Событие по спецификации")
        repo.set_position_shipped(spec.id, spec.positions[0].id, "0.75")
        repo.export_execution(portable / "data" / "проверка-экспорта.json")
        exported = json.loads((portable / "data" / "проверка-экспорта.json").read_text(encoding="utf-8"))
        assert exported["specifications"][0]["positions"][0]["customer_shipped"] == "0.75"
        assert exported["specifications"][0]["positions"][0]["due"] == "2027-02-10"
        assert exported["specifications"][0]["positions"][0]["working_note"] == "Рабочая заметка для проверки"
        assert exported["specifications"][0]["positions"][0]["comment"].startswith("Отгружено заказчику:")
        assert len(exported["specifications"][0]["positions"][0]["comments"]) == 4
        assert exported["specifications"][0]["comments"][0][1] == "Событие по спецификации"
        assert any(r["message"] == "Комментарий договора из portable" for r in exported["registry_comments"])
        assert any(r["message"] == contract_comment and r["stamp"] for r in exported["registry_comments"])
        assert repo.registry_comment(("position", spec.id, spec.positions[0].id)) == "Второй комментарий"
        text = '\t'.join(('1', '01.08.2026', '10.08.2026 12:30:07', '10',
                          'ZIP-NEW-001', 'Fictional customer', 'Tender', '', 'Нет',
                          'В работе', 'Fictional goods', 'tender.pro', 'Packaging test')) + '\n'
        plan = repo.preview_1c(text)
        added_ids, _ = repo.import_1c(plan, [1], portable / 'Документы')
        assert repo.unseen_tender_ids() == set(added_ids)
        assert repo.registry_comment(contract_owner) == contract_comment
    finally:
        repo.close()
    reopened = launch()
    assert reopened["history_entries"] == initial["history_entries"] + 3
    repo = ExecutionRepository(database)
    try:
        assert repo.unseen_tender_ids() == set(added_ids)
        assert repo.registry_comment(contract_owner) == contract_comment
        assert len(repo.registry_comment_entries(contract_owner)) == 2
        card = next(t for t in repo.all() if t.id == added_ids[0])
        repo.mark_tender_seen(card.id)
        assert repo.unseen_tender_ids() == set()
        assert next(t for t in repo.all() if t.id == card.id) == card
        assert repo.specifications()[0].positions[0].customer_shipped == Decimal("0.75")
        assert repo.specifications()[0].positions[0].due == "2027-02-10"
        assert repo.specifications()[0].positions[0].working_note == "Рабочая заметка для проверки"
        assert repo.specifications()[0].positions[0].comment.startswith("Отгружено заказчику:")
        assert len(repo.specifications()[0].positions[0].comments) == 4
        repo.restore(backup)
        repo.check(repo.connection)
        assert len(repo.all()) == 4 and repo.unseen_tender_ids() == set()
        assert repo.registry_comment(contract_owner) == ""
    finally:
        repo.close()
    restored = launch()
    assert restored["history_entries"] == initial["history_entries"]
    excel_check = subprocess.run([str(portable / f"{name}.exe"), '--smoke-test', '--check-database-import'],
                                 cwd=target, env=env, timeout=60)
    assert excel_check.returncode == 0, excel_check.returncode
    assert json.loads((portable / 'data' / 'execution-smoke.json').read_text(encoding='utf-8'))['excel_import_runtime']
    after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected if p.is_file()}
    assert before == after
    result = {"records": 4, "specifications": 4, "folders": 4, "schema_version": 10,
              "no_python_on_path": True, "save_reopen_restore_export": True,
              "unseen_import_reopen_view": True,
              "nested_execution_registry_and_contract_comments": True,
              "dated_append_comment_with_newline": True,
              "excel_template_atomic_import_runtime": True,
              "protected_files_unchanged": True, "fresh_extraction": str(target),
              "archive_bytes": archive.stat().st_size, "protected_hashes": after}
    (artifacts / "portable-verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--chat-preview", action="store_true", help="Compatibility alias")
    mode.add_argument("--execution-preview", action="store_true", help="Compatibility alias")
    parser.parse_args()
    verify()


if __name__ == "__main__":
    main()
