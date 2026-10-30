import hashlib
import json
import subprocess
import sys

from PySide6.QtCore import QLockFile

from tests.conftest import ROOT, SEED


def test_execution_launch_is_separate_and_persists_on_reopen(tmp_path):
    before = hashlib.sha256(SEED.read_bytes()).hexdigest()
    data = tmp_path / "Отдельная проба"
    command = [sys.executable, str(ROOT / "run_execution.py"), "--smoke-test", "--data-dir", str(data)]
    first = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert first.returncode == 0, first.stderr
    initial = json.loads((data / "execution-smoke.json").read_text(encoding="utf-8"))
    assert initial["records"] == 4 and initial["specifications"] == 4
    assert initial["maximized"] is True
    assert initial["schema_version"] == 10
    second = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert second.returncode == 0, second.stderr
    assert json.loads((data / "execution-smoke.json").read_text(encoding="utf-8")) == initial
    assert hashlib.sha256(SEED.read_bytes()).hexdigest() == before


def test_execution_launch_rejects_source_seed_and_main_deployment(tmp_path):
    for data in (SEED.parent, tmp_path / "TenderAssistant" / "data"):
        result = subprocess.run([sys.executable, str(ROOT / "run_execution.py"), "--smoke-test", "--data-dir", str(data)],
                                cwd=tmp_path, capture_output=True, text=True, timeout=15)
        assert result.returncode == 1
        assert "пробной версии" in result.stderr
    assert not (tmp_path / "TenderAssistant").exists()


def test_execution_second_instance_rejected_before_database_creation(tmp_path, app):
    data = tmp_path / "Данные"
    data.mkdir()
    lock = QLockFile(str(data / "application.lock"))
    assert lock.tryLock(0)
    try:
        result = subprocess.run([sys.executable, str(ROOT / "run_execution.py"), "--smoke-test", "--data-dir", str(data)],
                                capture_output=True, text=True, timeout=15)
        assert result.returncode == 1
        assert not (data / "tenders.db").exists()
    finally:
        lock.unlock()
