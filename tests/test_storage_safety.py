import os
import pytest


def test_csv_failure_preserves_previous_export(repo, tmp_path, monkeypatch):
    target = tmp_path / 'export.csv'
    target.write_bytes(b'previous export')

    def fail():
        raise OSError('read failed')

    monkeypatch.setattr(repo, 'all', fail)
    with pytest.raises(OSError):
        repo.export_csv(target)
    assert target.read_bytes() == b'previous export'
    assert not list(tmp_path.glob('*.tmp'))


def test_backup_rotation_preserves_unmanaged_database(repo):
    repo.backup_dir.mkdir(parents=True, exist_ok=True)
    unrelated = repo.backup_dir / 'important-archive.db'
    unrelated.write_bytes(b'unrelated database')
    os.utime(unrelated, (1, 1))
    for _ in range(15):
        repo.backup(manual=True)
    assert unrelated.read_bytes() == b'unrelated database'
    assert len(list(repo.backup_dir.glob('manual-*.db'))) == 14
