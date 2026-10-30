import hashlib
import json
from decimal import Decimal

import pytest

openpyxl = pytest.importorskip("openpyxl")

from scripts.import_execution_excel import ORDER_DUE, import_report, read_report
from tender_app.execution_repository import ExecutionRepository


def workbook(path, archive=False):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Отгружено (архив)" if archive else "Отчет по выйграным тендерам "
    columns = ({"date":2,"dates":3,"due":4,"signed":5,"ordered":6,"placed":7,"balance":8,
                "supplier":10,"doc":11,"number":13,"stage":14,"transport":16,"location":17,
                "eta":18,"note":19,"contacts":20,"folder":21,"money":22} if archive else
               {"date":3,"dates":4,"due":5,"signed":6,"ordered":7,"placed":8,"shipped":9,"balance":10,
                "supplier":12,"doc":14,"number":16,"stage":17,"transport":18,"location":19,
                "eta":20,"note":21,"contacts":22,"folder":23,"money":24})
    headers = {"date":"Заказ покупателя.Дата","dates":"Даты отгрузки","due":ORDER_DUE,
               "signed":"Заказ покупателя.Подписан","ordered":"Количество","placed":"Заказ поставщику количество",
               "balance":"Долг по отгрузке количество","number":"Номер тендера","stage":"Статус",
               "transport":"Транспорт / номер машины","location":"Где груз сейчас","eta":"Ожидаемое прибытие",
               "note":"Заметки / история","contacts":"Контакты заказчика","folder":"Ссылка на папку с документацией"}
    for key, label in headers.items():
        ws.cell(5,columns[key],label)
    ws.cell(7,1,"Завод «Учебный»")
    ws.cell(8,1,"Договор №ДЕМО от 01.04.2026")
    ws.cell(8,1).alignment = openpyxl.styles.Alignment(indent=1)
    ws.cell(9,1,"1 от 22.04.2026")
    ws.cell(9,1).alignment = openpyxl.styles.Alignment(indent=2)
    for key, value in {"date":"05.05.2026","dates":"01.11.2026","due":"10.11.2026","signed":"Да",
                       "number":"ДЕМО-1","stage":"Закрыто" if archive else "В производстве",
                       "note":"История источника","contacts":"Учебный отдел","money":1000}.items():
        ws.cell(9,columns[key],value)
    ws.cell(9,columns["folder"],"Открыть папку").hyperlink = "file:///C:\\Документы\\№ДЕМО-1_Завод"
    ws.cell(10,1,"Вал · ДЕМО.101")
    ws.cell(10,1).alignment = openpyxl.styles.Alignment(indent=3)
    for key, value in {"ordered":4,"placed":4,"balance":2,"supplier":"Учебный поставщик","doc":"Приложение 1"}.items():
        ws.cell(10,columns[key],value)
    wb.save(path)
    return wb, ws, columns


def test_import_preserves_rows_history_dates_units_links_and_source(tmp_path):
    source = tmp_path / "Пример.xlsx"
    wb, ws, columns = workbook(source)
    ws.cell(10,26,"Дополнительная заметка за таблицей")
    wb.save(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    target = tmp_path / "Импорт" / "tenders.db"
    report = import_report(source,target)
    assert report["specifications"] == 1 and report["positions"] == 1
    repo = ExecutionRepository(target)
    try:
        spec = repo.specifications()[0]
        assert spec.date == "2026-04-22"  # Document date differs from sales-order date.
        assert spec.due == "2026-11-10" and spec.revised is None
        assert spec.positions[0].customer_shipped == Decimal("2")
        assert spec.positions[0].unit == "ед. не указана"
        assert "Дополнительная заметка" in spec.positions[0].note
        assert "История источника" in spec.history[0][1]
        assert "1000" in spec.history[0][1]  # Money retained as source text, not a financial model.
        assert repo.all()[0].folder_path == "C:\\Документы\\№ДЕМО-1_Завод"
    finally:
        repo.close()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    with pytest.raises(FileExistsError):
        import_report(source,target)


def test_archive_closed_with_outstanding_quantity_is_archived_without_completion(tmp_path):
    source = tmp_path / "Архив.xlsx"
    workbook(source,archive=True)
    entries, _, _, _ = read_report(source)
    spec = entries[0]["spec"]
    assert not spec.completed
    assert spec.archived
    assert spec.positions[0].remaining == Decimal("2")
    assert "Архивный лист" in spec.risk
    assert any("противоречит остатку" in notice for notice in entries[0]["notices"])
    target = tmp_path / "Архив" / "tenders.db"
    report = import_report(source, target)
    assert report["archived"] == 1
    repo = ExecutionRepository(target)
    assert repo.specifications()[0].archived
    assert not repo.specifications()[0].completed
    repo.close()


def test_unknown_shipping_stays_unknown_and_explicit_shipping_wins(tmp_path):
    source = tmp_path / "Пример.xlsx"
    wb, ws, columns = workbook(source)
    ws.cell(10,columns["balance"]).value = None
    wb.save(source)
    entries, _, _, _ = read_report(source)
    assert entries[0]["spec"].positions[0].customer_shipped is None
    ws.cell(10,columns["balance"],2)
    ws.cell(10,columns["shipped"],1)
    wb.save(source)
    entries, _, _, _ = read_report(source)
    assert entries[0]["spec"].positions[0].customer_shipped == 1
    assert any("противоречат" in notice for notice in entries[0]["notices"])


def test_missing_numbers_multiple_specs_and_different_suppliers(tmp_path):
    source = tmp_path / "Пример.xlsx"
    wb, ws, columns = workbook(source)
    for row in (11,12):
        for col in range(1,27):
            original = ws.cell(row-2,col)
            ws.cell(row,col,original.value)
        ws.cell(row,1).alignment = openpyxl.styles.Alignment(indent=row-9)
    ws.cell(11,1,"2 от 22.04.2026")
    ws.cell(12,columns["supplier"],"Второй учебный поставщик")
    wb.save(source)
    report = import_report(source,tmp_path / "new" / "tenders.db")
    assert report["tenders"] == 1 and report["specifications"] == 2
    ws.cell(9,columns["number"]).value = None
    ws.cell(9,columns["folder"]).hyperlink = None
    ws.cell(11,columns["number"]).value = None
    wb.save(source)
    report = import_report(source,tmp_path / "missing" / "tenders.db")
    assert report["tenders"] == 2


def test_invalid_required_quantity_fails_before_creating_database(tmp_path):
    source = tmp_path / "Пример.xlsx"
    wb, ws, columns = workbook(source)
    ws.cell(10,columns["ordered"],"=1+1")  # No cached result; never guess a formula result.
    wb.save(source)
    target = tmp_path / "bad" / "tenders.db"
    with pytest.raises(ValueError,match="положительного"):
        import_report(source,target)
    assert not target.exists()


def test_generated_short_subject_keeps_full_position_names(tmp_path):
    source = tmp_path / "Архив.xlsx"
    wb, ws, columns = workbook(source,archive=True)
    for col in range(1,25):
        ws.cell(11,col,ws.cell(10,col).value)
    ws.cell(11,1,"Вторая позиция с полным наименованием и чертежом ДЕМО.102")
    ws.cell(11,1).alignment = openpyxl.styles.Alignment(indent=3)
    wb.save(source)
    entries, _, _, _ = read_report(source)
    spec = entries[0]["spec"]
    assert spec.title == "Вал · ДЕМО.101 и ещё 1 поз."
    assert spec.positions[1].name == "Вторая позиция с полным наименованием и чертежом ДЕМО.102"
