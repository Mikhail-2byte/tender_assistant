from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from tender_app.models import now_moscow


STAGES = ("Тендер выигран", "Договор на согласовании", "Спецификация подписана",
          "Размещено у поставщика", "В производстве", "Готово к отгрузке", "В пути",
          "Отгружено заказчику", "Приостановлено")
UNKNOWN_UNIT = "ед. не указана"


def quantity(value, label="Количество", optional=False):
    if value is None or str(value).strip() == "":
        if optional:
            return None
        raise ValueError(f"{label}: укажите количество.")
    try:
        result = Decimal(str(value).strip().replace(",", "."))
    except InvalidOperation:
        raise ValueError(f"{label}: введите число, например 2,5.") from None
    if not result.is_finite() or result < 0 or result > Decimal("999999999999"):
        raise ValueError(f"{label}: нужно неотрицательное конечное число до 999999999999.")
    if result.as_tuple().exponent < -6:
        raise ValueError(f"{label}: допускается не более 6 знаков после запятой.")
    return result


def shown(value):
    return "не указано" if value is None else format(value, "f").replace(".", ",")


def validate_date(value, label):
    if not value:
        return
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError(f"{label}: ожидается дата ГГГГ-ММ-ДД или пустое поле.") from None


@dataclass
class Position:
    id: int | None = None
    name: str = ""
    unit: str = "шт"
    ordered: Decimal = Decimal("1")
    placed: Decimal | None = None
    customer_shipped: Decimal | None = None
    supplier: str = ""
    supplier_document: str = ""
    stage: str = STAGES[0]
    note: str = ""
    transport: str = ""
    location: str = ""
    eta: str | None = None
    due: str | None = None
    comment: str = ""
    working_note: str = ""
    comments: list[tuple[str | None, str]] = field(default_factory=list)

    @property
    def remaining(self):
        return None if self.customer_shipped is None else self.ordered - self.customer_shipped

    def validate(self):
        self.name = self.name.strip()
        self.unit = self.unit.strip()
        if not self.name.strip() or not self.unit.strip():
            raise ValueError("У позиции нужны наименование и единица измерения.")
        self.ordered = quantity(self.ordered, "Заказано")
        self.placed = quantity(self.placed, "Размещено", optional=True)
        self.customer_shipped = quantity(self.customer_shipped, "Отгружено заказчику", optional=True)
        if not self.ordered:
            raise ValueError("Заказанное количество должно быть больше нуля.")
        if any(value is not None and value > self.ordered for value in (self.placed, self.customer_shipped)):
            raise ValueError("Размещённое и отгруженное количество не может превышать заказанное.")
        if self.placed is not None and self.customer_shipped is not None and self.customer_shipped > self.placed:
            raise ValueError("Отгружено больше, чем размещено у поставщика. Проверьте количества.")
        if self.stage not in STAGES:
            raise ValueError("Выберите известный этап позиции.")
        validate_date(self.eta, "Прибытие позиции")
        validate_date(self.due, "Срок поставки позиции")


@dataclass
class Specification:
    id: int | None = None
    tender_id: int | None = None
    number: str = ""
    title: str = ""
    date: str | None = None
    contract: str = ""
    signed: bool = False
    due: str | None = None
    revised: str | None = None
    eta: str | None = None
    stage: str = STAGES[0]
    risk: str = ""
    completed: bool = False
    archived: bool = False
    updated: str = ""
    positions: list[Position] = field(default_factory=list)
    history: list[tuple[str, str]] = field(default_factory=list)
    comments: list[tuple[str | None, str]] = field(default_factory=list)

    @property
    def summary(self):
        if self.completed:
            return "Завершено"
        if not self.positions:
            return self.stage
        if all(p.remaining == 0 for p in self.positions):
            return "Отгружено заказчику"
        if (any(p.customer_shipped is not None and p.customer_shipped > 0 for p in self.positions)
                and any(p.remaining is None or p.remaining > 0 for p in self.positions)):
            return "Частично отгружено"
        stages = {p.stage for p in self.positions}
        if len(stages) == 1:
            return self.positions[0].stage
        return "Разные этапы"

    @property
    def delivery_due(self):
        return earliest_due(self.due, self.revised, (p.due for p in self.positions))

    def overdue(self, today=None):
        if self.completed or self.archived:
            return False
        today = today or now_moscow().date()
        dates = [p.due or self.revised or self.due for p in self.positions
                 if p.remaining is None or p.remaining > 0]
        if not self.positions:
            dates = [self.revised or self.due]
        return any(value and date.fromisoformat(value) < today for value in dates)

    def quantities(self):
        lines = []
        for unit in dict.fromkeys(p.unit for p in self.positions):
            positions = [p for p in self.positions if p.unit == unit]
            if unit == UNKNOWN_UNIT:
                lines.append(f"{len(positions)} поз. · единицы не указаны")
                continue
            ordered = sum(p.ordered for p in positions)
            shipped = (None if any(p.customer_shipped is None for p in positions)
                       else sum(p.customer_shipped for p in positions))
            lines.append(f"{shown(ordered)} / {shown(shipped)} / {shown(None if shipped is None else ordered - shipped)} {unit}")
        return "\n".join(lines) or "Позиций пока нет"

    def validate(self):
        if not self.number.strip() or self.tender_id is None:
            raise ValueError("Укажите номер спецификации и связанный тендер.")
        for name, label in (("date", "Дата спецификации"), ("due", "Первоначальный срок"),
                            ("revised", "Согласованный перенос"), ("eta", "Прибытие")):
            validate_date(getattr(self, name), label)
        if self.revised and not self.due:
            raise ValueError("Перед переносом укажите первоначальный договорный срок.")
        if self.stage not in STAGES:
            raise ValueError("Выберите известный этап спецификации.")
        for position in self.positions:
            position.validate()
        if self.completed and (not self.positions or any(p.remaining != 0 for p in self.positions)):
            raise ValueError("Для завершения внесите все позиции и отгрузку заказчику без остатка.")


def earliest_due(due, revised, position_dates):
    dates = list(position_dates)
    if not dates:
        return revised or due
    return min((value or revised or due for value in dates if value or revised or due), default=None)


def delivery_days(start, end):
    if not start or not end:
        return "не указано"
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days
    return f"{days} кал. дн." if days >= 0 else "несогласованные даты"
