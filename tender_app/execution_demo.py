"""Fictional, read-only screen data. Not a persistence or migration model."""
from datetime import date

from tender_app.models import Tender
from tender_app.execution_models import UNKNOWN_UNIT


DEMO_TODAY = date(2026, 10, 20)


def demo_data():
    tenders = [
        Tender(id=1, number="ДЕМО-101", customer="Завод «Северный пример»",
               title="Вал приводной по чертежу ДЕМО.101", status="Победа"),
        Tender(id=2, number="ДЕМО-202", customer="Комбинат «Учебный»",
               title="Комплектующие для модернизации транспортёра и ремонтные материалы",
               status="Победа"),
        Tender(id=3, number="ДЕМО-202", customer="Фабрика «Вымышленная»",
               title="Барабан натяжной", status="Победа"),
        Tender(id=4, number="ДЕМО-404", customer="Предприятие «Образец»",
               title="Ролики опорные", status="Победа"),
    ]
    for tender in tenders:
        tender.contacts = "Учебный отдел снабжения · demo@example.invalid"
        tender.folder_path = f"Документы/{tender.number} · пример пути, папка не создаётся"
        tender.comment = "[20.10.2026 09:00 МСК]\nПолучено уведомление о победе."

    def position(name, unit, ordered, placed, customer_shipped,
                 supplier, stage, note="", transport="Не назначен", location="У поставщика"):
        return dict(name=name, unit=unit, ordered=ordered, placed=placed,
                    customer_shipped=customer_shipped,
                    supplier=supplier, stage=stage, note=note, transport=transport,
                    location=location, supplier_document="Приложение ДЕМО-П/1", comment=note, working_note=note,
                    comments=[("20.10.2026 10:30 МСК", note)] if note else [])

    def specification(identifier, tender_id, number, title, stage, due, eta, positions,
                      signed=True, revised=None, risk="", completed=False):
        return dict(id=identifier, tender_id=tender_id, number=number, title=title,
                    stage=stage, due=due, eta=eta, positions=positions, signed=signed,
                    revised=revised, risk=risk, completed=completed, archived=completed,
                    date="2026-10-01", contract="Договор ДЕМО-Д/26 от 01.10.2026",
                    updated="20.10.2026 10:30 МСК",
                    history=[("20.10.2026 10:30 МСК", "Уточнены этапы позиций и прогноз прибытия."),
                             ("19.10.2026 15:00 МСК", "Спецификация добавлена. История тендера ведётся отдельно.")])

    specifications = [
        specification(101, 1, "СП-01", "Вал приводной", "В производстве", "2026-10-27",
                      "2026-10-26", [position("Вал приводной · ДЕМО.101", "шт", 1, 1, 0,
                                             "Мастерская «Пример»", "В производстве")]),
        specification(201, 2, "СП-02", "Комплектующие транспортёра", "Частично отгружено",
                      "2026-10-18", "2026-10-23", [
                          position("Шестерня цилиндрическая с упрочнённой поверхностью зубьев · ДЕМО.202.01",
                                   "шт", 10, 10, 4, "Завод «Макет»", "В пути",
                                   "4 шт отгружены заказчику; оставшиеся изделия частично в пути и у поставщика.",
                                   "Автомобиль ДЕМО-01", "Частично в пути"),
                          position("Втулка дистанционная для приводного узла · ДЕМО.202.02", "шт", 6, 6, 0,
                                   "Мастерская «Пример»", "Готово к отгрузке"),
                          position("Материал для восстановления посадочных поверхностей", "т", 2.5, 2.5, 0,
                                   "Поставщик «Учебный»", "В производстве"),
                      ], risk="Поставщик задерживает остаток. Продление запрошено, но не согласовано."),
        specification(202, 2, "СП-03", "Резервные узлы", "Договор на согласовании", "2026-10-22",
                      None, [position("Узел натяжения в сборе · ДЕМО.202.03", "компл", 2, None, None,
                                      "Не выбран", "Договор на согласовании", "Количество отгрузок пока не внесено.")],
                      signed=False, revised="2026-11-05"),
        specification(401, 4, "СП-04", "Ролики опорные", "Завершено", "2026-10-15",
                      "2026-10-14", [position("Ролик опорный · ДЕМО.404", "шт", 8, 8, 8,
                                             "Завод «Макет»", "Отгружено заказчику")], completed=True),
    ]
    specifications[-1]["history"].insert(0, ("20.10.2026 11:00 МСК", "Пользователь подтвердил завершение."))
    for spec in specifications:
        spec["comments"] = list(spec["history"])
    return tenders, specifications


def shown(value):
    return "не указано" if value is None else str(value).replace(".", ",")


def date_label(value):
    return date.fromisoformat(value).strftime("%d.%m.%Y") if value else "не указано"


def delivery_due(spec):
    from tender_app.execution_models import earliest_due
    return earliest_due(spec["due"], spec["revised"], (p.get("due") for p in spec["positions"]))


def is_overdue(spec, today=DEMO_TODAY):
    if spec["completed"] or spec["archived"]:
        return False
    dates = [p.get("due") or spec["revised"] or spec["due"] for p in spec["positions"]
             if p["customer_shipped"] is None or p["customer_shipped"] < p["ordered"]]
    if not spec["positions"]:
        dates = [spec["revised"] or spec["due"]]
    return any(value and date.fromisoformat(value) < today for value in dates)


def quantities(spec):
    lines = []
    for unit in dict.fromkeys(p["unit"] for p in spec["positions"]):
        positions = [p for p in spec["positions"] if p["unit"] == unit]
        if unit == UNKNOWN_UNIT:
            lines.append(f"{len(positions)} поз. · единицы не указаны")
            continue
        ordered = sum(p["ordered"] for p in positions)
        shipped = (None if any(p["customer_shipped"] is None for p in positions)
                   else sum(p["customer_shipped"] for p in positions))
        remaining = None if shipped is None else ordered - shipped
        lines.append(f"{shown(ordered)} / {shown(shipped)} / {shown(remaining)} {unit}")
    return "\n".join(lines)
