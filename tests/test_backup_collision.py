from datetime import datetime
from types import SimpleNamespace

from tender_app.repository import readonly


def test_manual_backups_remain_distinct_when_clock_returns_same_time(repo, monkeypatch):
    monkeypatch.setattr('tender_app.repository.datetime',
        SimpleNamespace(now=lambda: datetime(2026, 10, 23, 12, 0)))
    first = repo.backup(manual=True)
    with repo.connection:
        repo.connection.execute('UPDATE tenders SET contacts=? WHERE id=1', ('Новый контакт',))
    second = repo.backup(manual=True)
    assert first != second
    old, new = readonly(first), readonly(second)
    try:
        assert old.execute('SELECT contacts FROM tenders WHERE id=1').fetchone()[0] != 'Новый контакт'
        assert new.execute('SELECT contacts FROM tenders WHERE id=1').fetchone()[0] == 'Новый контакт'
    finally:
        old.close()
        new.close()
