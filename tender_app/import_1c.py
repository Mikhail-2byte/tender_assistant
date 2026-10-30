"""Local-only 1C TSV parsing and immutable add-only preview plans.

No clipboard, database writes, HTML decoding, filesystem or network operations here.
Physical line ranges are retained even for quoted multiline records.
"""
from collections import Counter, defaultdict
import csv
from dataclasses import dataclass, replace
from datetime import datetime
from io import StringIO
import re

from .models import Tender

MAX_BYTES = 5 * 1024 * 1024
MAX_RECORDS = 10_000
MAX_CELL_CHARS = 131_072
STATUSES_1C = frozenset(('в работе', 'подался', 'отказ', 'проиграли', 'победа',
                         'интересный', 'интересные', 'на согласовании'))
LABELS = {'new': 'Новый', 'existing': 'Уже существует', 'repeat': 'Повтор в буфере',
          'conflict': 'Конфликт / неоднозначность', 'error': 'Ошибка',
          'header': 'Заголовок — пропущен', 'blank': 'Пустая строка — пропущена'}


class ImportFormatError(ValueError):
    pass


class StaleImportPlan(ValueError):
    pass


def normalize_number(value):
    """Conservative comparison only; never split punctuation or fold Cyrillic/Latin."""
    return re.sub(r'\s+', ' ', str(value or '').replace('\xa0', ' ')).strip().casefold()


def number_aliases(value):
    # Historical multiline numbers: include full value AND exact individual lines.
    return frozenset(filter(None, (normalize_number(value),
        *(normalize_number(p) for p in str(value or '').splitlines()))))


def clean(value):
    return value.replace('\xa0', ' ').strip()


@dataclass(frozen=True)
class ImportRow:
    ordinal: int
    line_start: int
    line_end: int
    raw_fields: tuple[str, ...] = ()
    number: str = ''
    customer: str = ''
    title: str = ''
    platform: str = ''
    comment: str = ''
    source_status: str = ''
    deadline_raw: str = ''
    deadline_date: str | None = None
    deadline_time: str | None = None
    deadline_needs_review: int = 0
    category: str = 'new'
    messages: tuple[str, ...] = ()
    matches: tuple[int, ...] = ()

    @property
    def signature(self):
        # All original columns participate: ignored source data must not silently
        # turn differing records into full duplicates. Normalize only presentation.
        values = [clean(f).replace('\r\n', '\n').replace('\r', '\n') for f in self.raw_fields]
        if len(values) > 4:
            values[4] = normalize_number(values[4])
        return tuple(values)

    def tender(self, stamp):
        return Tender(number=self.number, customer=self.customer, title=self.title,
                      platform=self.platform, url=procedure_url(self.platform, self.number), comment=self.comment, status='В работе',
                      action=f'Добавлен из 1С · {stamp:%d.%m.%Y}',
                      deadline_date=self.deadline_date, deadline_time=self.deadline_time,
                      deadline_timezone='Europe/Moscow' if self.deadline_date else None,
                      deadline_raw=self.deadline_raw or None,
                      deadline_needs_review=self.deadline_needs_review,
                      import_issues='; '.join((
                          f'Источник: буфер 1С, строки {self.line_start}–{self.line_end}',
                          *self.messages)),
                      source_sheet='', source_row=0)


@dataclass(frozen=True)
class ParsedText:
    rows: tuple[ImportRow, ...]
    fatal: str = ''


@dataclass(frozen=True)
class ImportPlan:
    text: str
    rows: tuple[ImportRow, ...]
    database_digest: str
    fatal: str = ''

    @property
    def counts(self):
        found = Counter(r.category for r in self.rows)
        return {key: found[key] for key in LABELS}


def parse_deadline(raw):
    value = clean(raw)
    if not value:
        return None, None, 0, ('Срок не указан.',)
    pattern = r'(\d{2}\.\d{2}\.\d{4})(?: (\d{2}:\d{2})(:\d{2})?)?'
    match = re.fullmatch(pattern, value)
    if match:
        fmt = '%d.%m.%Y' + (' %H:%M' if match[2] else '') + (':%S' if match[3] else '')
        try:
            dt = datetime.strptime(value, fmt)
            clock = dt.strftime('%H:%M:%S' if match[3] else '%H:%M') if match[2] else None
            return dt.date().isoformat(), clock, 0, (() if clock else ('Время не указано.',))
        except ValueError:
            pass
    return None, None, 1, ('Некорректный срок сохранится как исходный текст; требуется проверка.',)


def parse_fields(fields, ordinal, first, last):
    base = ImportRow(ordinal, first, last, tuple(fields))
    if not fields or not any(f.strip() for f in fields):
        return replace(base, category='blank')
    number = clean(fields[4]) if len(fields) > 4 else ''
    base = replace(base, number=number, customer=clean(fields[5]) if len(fields) > 5 else '',
                   deadline_raw=fields[2] if len(fields) > 2 else '')
    if len(fields) < 6:
        return replace(base, category='error', messages=('Меньше 6 столбцов; раскладка 1С не распознана.',))
    if any('\x00' in field for field in fields):
        return replace(base, category='error', messages=('Нулевой символ в исходных данных; скопируйте строку заново.',))
    if normalize_number(number) in {'номер', 'номер тендера'}:
        return replace(base, category='header')
    if not number or normalize_number(number) in STATUSES_1C | {'да', 'нет'}:
        return replace(base, category='error', messages=('Отсутствует допустимый номер тендера.',))
    if len(number.splitlines()) > 1 or any(ord(c) < 32 for c in number):
        return replace(base, category='conflict', messages=('Несколько номеров или управляющие символы в новом номере; требуется уточнение.',))
    # Never treat customer/number/date columns as status. A supported anchor must
    # have title and platform positions after it (comment may be omitted).
    anchors = [i for i in range(6, len(fields)) if clean(fields[i]).casefold() in STATUSES_1C]
    candidates = [i for i in anchors if i + 2 < len(fields)]
    if len(candidates) > 1 or (candidates and candidates[0] >= 10):
        return replace(base, category='conflict', messages=('Неоднозначное положение статуса/наименования; раскладка не угадывается.',))
    if candidates:
        idx = candidates[0]
        title, platform = clean(fields[idx+1]), clean(fields[idx+2])
        comment = clean(fields[idx+3]) if idx+3 < len(fields) else ''
        source_status = clean(fields[idx])
    elif any(i < 10 for i in anchors):
        return replace(base, category='error', messages=('После статуса недостаточно столбцов наименования и ЭТП.',))
    else:
        if len(fields) < 12:
            return replace(base, category='error', messages=('Без статуса требуются поля 11 и 12; комментарий — поле 13.',))
        title, platform = clean(fields[10]), clean(fields[11])
        comment = clean(fields[12]) if len(fields) > 12 else ''
        source_status = ''
    day, clock, review, warnings = parse_deadline(fields[2])
    warnings += (() if base.customer else ('Заказчик не указан.',))
    warnings += (() if title else ('Наименование не указано.',))
    warnings += (() if platform else ('ЭТП не указана.',))
    return replace(base, title=title, platform=platform, comment=comment,
                   source_status=source_status, deadline_date=day, deadline_time=clock,
                   deadline_needs_review=review, messages=warnings)


def validate_text_size(text):
    if not isinstance(text, str):
        raise ImportFormatError('Ожидается текст таблицы 1С.')
    if len(text) > MAX_BYTES:
        raise ImportFormatError('Буфер больше 5 МиБ UTF-8. Разделите таблицу на пакеты.')
    try:
        size = len(text.encode('utf-8'))
    except UnicodeEncodeError:
        raise ImportFormatError('Повреждённый Unicode в тексте. Скопируйте таблицу заново.') from None
    if size > MAX_BYTES:
        raise ImportFormatError('Буфер больше 5 МиБ UTF-8. Разделите таблицу на пакеты.')
    return size


def parse_text(text):
    validate_text_size(text)
    # csv.field_size_limit is process-global; default is deliberate: fail clearly,
    # never truncate an oversized single cell or change it in a worker thread.
    reader = csv.reader(StringIO(text.removeprefix('\ufeff'), newline=''),
                        delimiter='\t', quotechar='"', doublequote=True, strict=True)
    rows = []
    previous = 0
    try:
        for fields in reader:
            if len(rows) >= MAX_RECORDS:
                raise ImportFormatError('Больше 10 000 записей. Разделите таблицу на пакеты.')
            if any(len(field) > MAX_CELL_CHARS for field in fields):
                raise csv.Error('Размер ячейки больше 131 072 символов')
            rows.append(parse_fields(fields, len(rows)+1, previous+1, reader.line_num))
            previous = reader.line_num
    except csv.Error as exc:
        message = f'Повреждённый TSV или слишком длинная ячейка: {exc}. Импорт всего пакета заблокирован.'
        remaining = ''.join(text.splitlines(keepends=True)[previous:])
        rows.append(ImportRow(len(rows)+1, previous+1, max(previous+1, len(text.splitlines())),
                              raw_fields=(remaining,), category='error', messages=(message,)))
        return ParsedText(tuple(rows), message)
    if not text.strip('\ufeff \t\r\n\xa0'):
        return ParsedText(tuple(rows), 'Буфер пуст. Скопируйте таблицу из 1С.')
    return ParsedText(tuple(rows))


def make_plan(text, existing, database_digest):
    parsed = parse_text(text)
    rows = list(parsed.rows)
    index = defaultdict(list)
    for tender in existing:
        for alias in number_aliases(tender.number):
            index[alias].append(tender)
    groups = defaultdict(list)
    for i, row in enumerate(rows):
        if row.category not in {'blank', 'header'} and normalize_number(row.number):
            groups[normalize_number(row.number)].append(i)
    for key, indices in groups.items():
        invalid = [i for i in indices if rows[i].category != 'new']
        if invalid:
            for i in indices:
                if rows[i].category == 'new':
                    rows[i] = replace(rows[i], category='conflict', messages=(*rows[i].messages,
                        'В буфере есть повреждённая или неоднозначная строка с тем же номером. Исправьте всю группу.'))
            continue
        variants = {rows[i].signature for i in indices}
        matches = index.get(key, [])
        for pos, i in enumerate(indices):
            row = rows[i]
            ids = tuple(t.id for t in matches)
            if len(variants) > 1:
                category, message = 'conflict', 'Один номер с различающимися данными в буфере; вся группа заблокирована.'
            elif len(matches) > 1:
                category, message = 'conflict', 'В базе несколько совпадений номера/алиаса.'
            elif matches:
                t = matches[0]
                differ = [name for name in ('customer', 'platform')
                          if normalize_number(getattr(row, name)) != normalize_number(getattr(t, name))]
                if differ:
                    category, message = 'conflict', 'Совпадает номер, отличается заказчик или ЭТП; требуется уточнение.'
                else:
                    category, message = 'existing', 'Карточка уже существует; никаких обновлений.'
                    changed = [label for name, label in (('title','наименование'), ('comment','комментарий'),
                        ('deadline_date','дата'), ('deadline_time','время')) if getattr(row,name) != getattr(t,name)]
                    if changed:
                        message += ' Отличаются: ' + ', '.join(changed) + '.'
                    if pos:
                        category = 'repeat'
                        message = f'Полный повтор записи {rows[indices[0]].ordinal} в буфере. ' + message
            elif pos:
                category, message = 'repeat', f'Полный повтор записи {rows[indices[0]].ordinal} в буфере.'
            else:
                continue
            rows[i] = replace(row, category=category, matches=ids, messages=(*row.messages, message))
    return ImportPlan(text, tuple(rows), database_digest, parsed.fatal)


def procedure_url(platform, number):
    """Public opening URLs only; no card or document retrieval."""
    platform = platform.casefold().replace(' ', '')
    if any(marker in platform for marker in ('tender.pro', 'tenderpro', 'тендерпро')):
        match = re.fullmatch(r'(?:id)?([0-9]+)', number, re.I)
        if match:
            return f'https://www.tender.pro/view_tender.shtml?tenderid={match[1]}'
    return ''
