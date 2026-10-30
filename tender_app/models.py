from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
import re


MOSCOW = timezone(timedelta(hours=3), "МСК")
STATUSES = ("Интересный", "В работе", "Податься", "Подался", "Переторжка",
            "Ожидаем итоги", "Победа", "Проиграли", "Не участвуем", "Отменён")
ACTIVE = {"В работе", "Податься", "Переторжка"}
GROUPS = ("В работе", "Сегодня", "Завтра", "Просрочены", "Поданы / ожидание",
          "Интересные", "Закрытые", "Все")
SORTS = ("Ближайшие сверху", "Номер", "Заказчик")


def now_moscow():
    return datetime.now(MOSCOW).replace(tzinfo=None)


@dataclass
class Tender:
    id: int | None = None
    number: str = ""
    customer: str = ""
    title: str = ""
    url: str = ""
    platform: str = ""
    deadline_date: str | None = None
    deadline_time: str | None = None
    deadline_timezone: str | None = None
    deadline_raw: str | None = None
    deadline_needs_review: int = 0
    comment: str = ""
    action: str = ""
    status: str = "В работе"
    contacts: str = ""
    folder_path: str = ""
    original_folder_link: str = ""
    original_url_cell: str = ""
    import_issues: str = ""
    source_sheet: str = ""
    source_row: int = 0
    created_at: str = ""
    updated_at: str = ""

    def values(self):
        return asdict(self)


def deadline(tender):
    """Не угадываем повреждённые даты и не превращаем отсутствие времени в полночь."""
    try:
        day = date.fromisoformat(tender.deadline_date) if tender.deadline_date else None
        clock = time.fromisoformat(tender.deadline_time) if tender.deadline_time else None
        if clock and (not day or clock.tzinfo):
            return None, None
        return day, clock
    except (ValueError, TypeError):
        return None, None


def overdue(tender, now=None):
    now = now or now_moscow()
    day, clock = deadline(tender)
    return bool(day and (day < now.date() or
                         (day == now.date() and clock is not None and clock < now.time())))


def deadline_label(tender):
    day, clock = deadline(tender)
    if not day:
        return "Срок не указан"
    result = day.strftime("%d.%m.%Y") + (" · " + clock.strftime("%H:%M") + " МСК" if clock
                                         else " · время не указано")
    return result


def deadline_copy_text(tender):
    day, clock = deadline(tender)
    if not day:
        return ""
    return day.strftime("%d.%m.%Y") + (" " + clock.strftime("%H:%M") if clock else "")


def deadline_key(tender, now):
    day, clock = deadline(tender)
    if day is None:
        return 2, 0, 0, tender.id or 0
    seconds = clock.hour * 3600 + clock.minute * 60 + clock.second if clock else 86400
    if overdue(tender, now):
        return 0, -day.toordinal(), -seconds, tender.id or 0
    return 1, day.toordinal(), seconds, tender.id or 0


def filter_tenders(tenders, query="", group="В работе", status="Все", sort="Ближайшие сверху", now=None):
    now = now or now_moscow()
    query = query.strip().casefold()
    result = []
    for item in tenders:
        if query and query not in "\n".join((item.number, item.customer, item.title)).casefold():
            continue
        if status != "Все" and item.status != status:
            continue
        day, _ = deadline(item)
        if group == "В работе" and item.status not in ACTIVE:
            continue
        if group in ("Сегодня", "Завтра", "Просрочены"):
            if item.status not in ACTIVE:
                continue
            if group == "Просрочены" and not overdue(item, now):
                continue
            if group != "Просрочены" and day != now.date() + timedelta(days=group == "Завтра"):
                continue
        if group == "Поданы / ожидание" and item.status not in {"Подался", "Ожидаем итоги"}:
            continue
        if group == "Интересные" and item.status != "Интересный":
            continue
        if group == "Закрытые" and item.status not in set(STATUSES[6:]):
            continue
        result.append(item)
    if sort == "Номер":
        return sorted(result, key=lambda t: (t.number.casefold(), t.id or 0))
    if sort == "Заказчик":
        return sorted(result, key=lambda t: (t.customer.casefold(), t.id or 0))
    return sorted(result, key=lambda t: (t.status not in ACTIVE, deadline_key(t, now)))


def chat_search_query(text):
    """Recognize explicit local search commands; other text needs a model."""
    text = text.strip()
    match = re.fullmatch(r"(?:найди|найти|поиск)(?:\s+(.+))?", text, re.I | re.S)
    if match:
        query = (match.group(1) or "").strip().removeprefix("№").strip()
        if re.fullmatch(r"тендер(?:ы|ов|а|у)?", query, re.I):
            return ''
        from tender_app.record_search import number_references
        if not number_references(query):
            query = re.sub(r'^тендер(?:ы|ов|а|у)?\s+', '', query, flags=re.I)
        return query
    if re.fullmatch(r"№?\s*\d[\w./-]*", text):
        return text.removeprefix("№").strip()
    return None


def search_tenders(tenders, query):
    from tender_app.record_search import number_references, search_records
    query = query.strip().removeprefix("№").strip()
    if not query:
        return []
    if number_references(query):
        matched = {hit.tender_id for hit in search_records(tenders, [], query)}
        return [tender for tender in tenders if tender.id in matched]
    exact = [item for item in tenders if item.number.strip().casefold() == query.casefold()]
    return exact or filter_tenders(tenders, query=query, group="Все", sort="Номер")


def valid_web_url(value):
    try:
        if any(c.isspace() or ord(c) < 32 for c in value) or "\\" in value:
            return False
        parts = urlsplit(value)
        return (parts.scheme.lower() in {"http", "https"} and bool(parts.hostname)
                and parts.username is None and parts.password is None and parts.port != 0)
    except ValueError:
        return False


def resolve_folder(value, root):
    if not value.strip():
        raise ValueError("Папка не указана. Выберите папку документов.")
    path = Path(value)
    return (path if path.is_absolute() else Path(root) / path).resolve()
