import hashlib
import json
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QLockFile
import pytest

from tests.conftest import ROOT, SEED


@pytest.mark.parametrize("entry_point", ["run_app.py", "run_chat_preview.py", "run_execution_preview.py"])
def test_compatibility_launchers_open_current_application(tmp_path, entry_point):
    original_hash = hashlib.sha256(SEED.read_bytes()).hexdigest()
    data = tmp_path / "Данные проверки"
    result = subprocess.run(
        [sys.executable, str(ROOT / entry_point), "--smoke-test", "--data-dir", str(data)],
        cwd=tmp_path, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads((data / "execution-smoke.json").read_text(encoding="utf-8"))
    assert report["records"] == 4
    assert report["specifications"] == 4
    assert Path(report["database"]) == data / "tenders.db"
    assert report["agent_available"]
    assert report["sections"] == ["Реестр", "Исполнение", "Агент", "Реестр исполнения"]
    assert hashlib.sha256(SEED.read_bytes()).hexdigest() == original_hash


def test_second_instance_is_rejected_before_opening_database(tmp_path, app):
    data = tmp_path / "Данные реестра"
    data.mkdir()
    lock = QLockFile(str(data / "application.lock"))
    assert lock.tryLock(0)
    command = [sys.executable, str(Path(__file__).resolve().parents[1] / "run_app.py"),
               "--smoke-test", "--data-dir", str(data)]
    try:
        rejected = subprocess.run(command, capture_output=True, text=True, timeout=15)
        assert rejected.returncode == 1
        assert not (data / "tenders.db").exists()
    finally:
        lock.unlock()
    accepted = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert accepted.returncode == 0, accepted.stderr
    assert (data / "tenders.db").is_file()
    assert (data / "execution-smoke.json").is_file()
