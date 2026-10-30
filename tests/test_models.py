from datetime import datetime

import pytest

from tender_app.models import Tender, deadline_label, filter_tenders, overdue, resolve_folder, valid_web_url


NOW = datetime(2026, 10, 19, 12, 30)


def item(number, day=None, clock=None, status="В работе"):
    return Tender(number=number, deadline_date=day, deadline_time=clock, status=status)


def test_cyrillic_and_combined_filters():
    entries = [Tender(number="001-АБ", customer="Цемрос", status="Податься"),
               Tender(number="02", title="Вкладыши", status="Победа")]
    assert filter_tenders(entries, "ЦЕМРОС")[0].number == "001-АБ"
    assert filter_tenders(entries, "вКЛАДЫШИ", "Все")[0].number == "02"
    assert not filter_tenders(entries, "ЦЕМРОС", status="Победа")
    assert not filter_tenders(entries, "ЦЕМРОС", "Закрытые")


def test_dates_filters_and_sort():
    entries = [item("old", "2025-01-01"), item("tomorrow", "2026-10-20", "10:00"),
               item("unknown"), item("today-date", "2026-10-19"),
               item("elapsed", "2026-10-19", "11:00"), item("later", "2026-10-19", "13:00"),
               item("bad", "2026-99-99"), item("closed", "2026-10-19", status="Победа")]
    result = filter_tenders(entries, now=NOW)
    assert [t.number for t in result] == ["elapsed", "old", "later", "today-date", "tomorrow", "unknown", "bad"]
    assert {t.number for t in filter_tenders(entries, group="Сегодня", now=NOW)} == {"today-date", "elapsed", "later"}
    assert [t.number for t in filter_tenders(entries, group="Завтра", now=NOW)] == ["tomorrow"]
    assert [t.number for t in filter_tenders(entries, group="Просрочены", now=NOW)] == ["elapsed", "old"]
    assert not overdue(entries[3], NOW)
    assert "время не указано" in deadline_label(entries[3])
    assert "00:00" not in deadline_label(entries[3])
    assert deadline_label(entries[2]) == "Срок не указан"
    assert deadline_label(entries[6]) == "Срок не указан"


def test_midnight_boundary():
    before = datetime(2026, 10, 19, 23, 59)
    after = datetime(2026, 10, 20, 0, 1)
    entries = [item("day", "2026-10-19"), item("zero", "2026-10-20", "00:00")]
    assert not any(overdue(t, before) for t in entries)
    assert all(overdue(t, after) for t in entries)


@pytest.mark.parametrize("url", ["file:///C:/Windows", "javascript:alert(1)", "https://", "https://a:bad", "https://a:99999", "https://user:pass@a", "https://a b", "https://a\\b"])
def test_reject_unsafe_urls(url):
    assert not valid_web_url(url)


@pytest.mark.parametrize("url", ["https://www.b2b-center.ru/market/?id=123", "http://example.org", "https://пример.рф/path"])
def test_allow_web_urls(url):
    assert valid_web_url(url)


def test_paths_do_not_depend_on_cwd(tmp_path, monkeypatch):
    root = tmp_path / "Корень документов"
    monkeypatch.chdir(tmp_path.parent)
    assert resolve_folder("Тендеры/001-АБ", root) == root / "Тендеры" / "001-АБ"
    absolute = tmp_path / "Другая папка"
    assert resolve_folder(str(absolute), root) == absolute
    with pytest.raises(ValueError):
        resolve_folder("", root)


def test_active_overdue_stays_above_future_and_closed():
    entries = [item('future', '2026-10-28'), item('expired', '2026-10-23', '10:00'),
               item('closed', '2026-10-22', status='Победа')]
    result = filter_tenders(entries, group='Все', now=datetime(2026, 10, 23, 13))
    assert [t.number for t in result] == ['expired', 'future', 'closed']
