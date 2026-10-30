"""Local, strict workbook contract for employee onboarding. No network or writes on read."""
from dataclasses import dataclass, field
from datetime import date, datetime, time
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from tender_app.execution_models import Position, Specification, STAGES
from tender_app.import_1c import normalize_number, number_aliases
from tender_app.models import Tender, STATUSES, valid_web_url
from tender_app.repository import validate_tender


INTRO = 'Инструкция и примеры'
# field, visible header; order is the template's public interface.
SHEETS = {
    'Тендеры': (
        ('number', 'Номер тендера'), ('customer', 'Заказчик'), ('title', 'Название'),
        ('platform', 'Площадка'), ('url', 'Ссылка'), ('deadline_date', 'Дата подачи'),
        ('deadline_time', 'Время подачи МСК'), ('status', 'Статус'), ('contacts', 'Контакты'),
        ('comment', 'Комментарий'), ('action', 'Действие'), ('folder_path', 'Папка документов')),
    'Спецификации': (
        ('tender', 'Номер тендера'), ('code', 'Код спецификации'), ('number', 'Номер спецификации'),
        ('title', 'Название'), ('contract', 'Договор'), ('date', 'Дата спецификации'),
        ('due', 'Срок поставки'), ('revised', 'Перенос срока'), ('eta', 'Прибытие'),
        ('stage', 'Этап'), ('risk', 'Риски'), ('signed', 'Подписана'),
        ('completed', 'Завершена'), ('archived', 'В архиве')),
    'Позиции': (
        ('spec', 'Код спецификации'), ('code', 'Код позиции'), ('name', 'Наименование'),
        ('unit', 'Единица'), ('ordered', 'Заказано'), ('placed', 'Размещено'),
        ('customer_shipped', 'Отгружено заказчику'), ('supplier', 'Поставщик'),
        ('supplier_document', 'Документ поставщика'), ('stage', 'Этап'), ('due', 'Срок поставки'),
        ('eta', 'Прибытие'), ('transport', 'Транспорт'), ('location', 'Местонахождение'),
        ('note', 'Примечание'), ('comment', 'Комментарий'), ('working_note', 'Рабочая заметка')),
    'Заметки': (('kind', 'Объект'), ('code', 'Номер или код'), ('text', 'Текст'), ('stamp', 'Дата заметки')),
}
DATES = {'deadline_date', 'date', 'due', 'revised', 'eta', 'stamp'}
BOOLS = {'signed', 'completed', 'archived'}
NUMBERS = {'ordered', 'placed', 'customer_shipped'}
MAX_ROWS = 50_000


@dataclass
class Issue:
    sheet: str
    row: int
    field: str
    message: str
    warning: bool = False

    def __str__(self):
        return f'{"Предупреждение" if self.warning else "Ошибка"}: {self.sheet}, строка {self.row}, {self.field}: {self.message}'


@dataclass
class ExcelPlan:
    path: Path
    digest: str = ''
    database_token: str = ''
    tenders: dict = field(default_factory=dict)
    specs: dict = field(default_factory=dict)
    positions: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    skipped: set = field(default_factory=set)
    issues: list = field(default_factory=list)

    @property
    def valid(self):
        return not any(not issue.warning for issue in self.issues)

    def report(self):
        lines = [f'Файл: {self.path}', f'SHA-256: {self.digest}']
        for key, tender in self.tenders.items():
            skipped = key in self.skipped
            codes = {code for code, (parent, _) in self.specs.items() if parent == key}
            position_codes = {code for code, (parent, _) in self.positions.items() if parent in codes}
            notes = sum((kind == 'Тендер' and target == key) or
                        (kind == 'Спецификация' and target in codes) or
                        (kind == 'Позиция' and target in position_codes)
                        for kind, target, _, _ in self.notes)
            lines.append(f'{"Пропуск — уже существует" if skipped else "Добавить"}: {tender.number} | '
                         f'{tender.customer} | {tender.title} | спецификаций: {len(codes)}, '
                         f'позиций: {len(position_codes)}, заметок: {notes}')
        lines.append(f'Тендеров к добавлению: {len(self.tenders) - len(self.skipped)}; пропусков: {len(self.skipped)}.')
        lines.extend(str(issue) for issue in self.issues)
        if not self.valid:
            lines.append('Загрузка всего файла заблокирована. Исправьте ошибки и проверьте файл снова.')
        return '\n'.join(lines)


def database_token(repository):
    digest = sha256()
    for line in repository.connection.iterdump():
        digest.update(line.encode('utf-8'))
    return digest.hexdigest()


def save_template(path):
    """Template contains no formula and no employee data; examples live only in INTRO."""
    path = Path(path)
    if path.suffix.lower() != '.xlsx':
        raise ValueError('Выберите файл .xlsx.')
    book = Workbook()
    intro = book.active
    intro.title = INTRO
    instructions = [
        'Шаблон переноса базы — версия 1',
        'Заполняйте строки со второй на четырёх рабочих листах. Этот лист не импортируется.',
        'Номер тендера обязателен. Не меняйте заголовки. Номера и коды вводите как текст, сохраняя нули.',
        'Код спецификации и код позиции — ваши уникальные обозначения внутри этой книги (например С1 и П1).',
        'Связи: спецификация → номер тендера; позиция → код спецификации; заметка → номер тендера или код.',
        'Спецификации допустимы только у тендера со статусом «Победа». Статусы и этапы выбирайте из списка.',
        'Даты: ДД.ММ.ГГГГ или дата Excel. Время: ЧЧ:ММ, московское. Неизвестные даты и время оставляйте пустыми.',
        'Заказано обязательно и больше нуля. Размещено и отгружено: пусто означает «неизвестно», 0 — известный ноль.',
        'Подписана / Завершена / В архиве: Да или Нет (пусто = Нет). Завершение требует полной отгрузки.',
        'Если статус пуст — «В работе»; этап пуст — «Тендер выигран». Единицу позиции укажите обязательно.',
        'Папка документов необязательна. Укажите абсолютный путь; файлы не копируются и не изменяются.',
        'Если папка не найдена, путь сохраняется с предупреждением. Относительные пути — от корневой папки документов программы.',
        'Не используйте формулы: вставляйте значения. Дополнительные сведения переносите в комментарии и заметки.',
        'Новые столбцы и листы с данными запрещены. Все ошибки блокируют весь файл. Существующие номера пропускаются целиком.',
        'Примеры ниже вымышленные. Для импорта перенесите нужные примеры на рабочие листы; удалите ненужные.',
    ]
    for line in instructions:
        intro.append([line])
    examples = {
        'Тендеры': ['001-2026', 'Завод Пример', 'Поставка валов', '', '', '30.12.2026', '12:30', 'Победа'],
        'Спецификации': ['001-2026', 'С1', '01', 'Валы', 'Д-01', '01.10.2026', '30.12.2026', '', '', 'В производстве', '', 'Да', 'Нет', 'Нет'],
        'Позиции': ['С1', 'П1', 'Вал', 'шт', 10, 10, 4, 'Завод Пример', 'ЗП-01', 'В производстве'],
        'Заметки': ['Позиция', 'П1', 'Уточнить дату готовности', '28.10.2026'],
    }
    for title, columns in SHEETS.items():
        intro.append([])
        intro.append([f'Пример: {title}'])
        intro.append([label for _, label in columns])
        intro.append(examples[title])
        sheet = book.create_sheet(title)
        sheet.append([label for _, label in columns])
        sheet.freeze_panes = 'A2'
        sheet.auto_filter.ref = f'A1:{get_column_letter(len(columns))}1001'
        sheet.row_dimensions[1].height = 42
        for index, (name, label) in enumerate(columns, 1):
            letter = get_column_letter(index)
            sheet.column_dimensions[letter].width = 25 if name not in {'title', 'comment', 'text', 'working_note'} else 42
            sheet.cell(1, index).comment = Comment(
                f'{label}. Даты: ДД.ММ.ГГГГ; время: ЧЧ:ММ МСК. '
                'Номера и коды — текст. Подробности и примеры на первом листе.', 'Тендерный помощник')
            choices = (STATUSES if name == 'status' else STAGES if name == 'stage' else
                       ('Да', 'Нет') if name in BOOLS else
                       ('Тендер', 'Спецификация', 'Позиция') if name == 'kind' else None)
            if choices:
                validation = DataValidation(type='list', formula1='"' + ','.join(choices) + '"')
                validation.errorTitle = 'Выберите значение из списка'
                validation.error = 'Допустимые значения указаны в выпадающем списке.'
                validation.showErrorMessage = True
                sheet.add_data_validation(validation)
                validation.add(f'{letter}2:{letter}{MAX_ROWS + 1}')
            for row in range(2, 1002):
                cell = sheet.cell(row, index)
                cell.number_format = ('dd.mm.yyyy' if name in DATES else 'hh:mm' if name == 'deadline_time'
                                      else '0.######' if name in NUMBERS else '@')
                cell.font = Font(name='Arial', size=11, color='174EA6')
                cell.alignment = Alignment(vertical='top', wrap_text=True)
        for cell in sheet[1]:
            cell.font = Font(name='Arial', bold=True, color='FFFFFF', size=11)
            cell.fill = PatternFill('solid', fgColor='24435B')
            cell.alignment = Alignment(wrap_text=True, vertical='center')
    for row in intro:
        for cell in row:
            cell.font = Font(name='Arial', size=11)
    for index in range(1, 18):
        intro.column_dimensions[get_column_letter(index)].width = 24
    for row in range(1, len(instructions) + 1):
        intro.merge_cells(start_row=row, start_column=1, end_row=row, end_column=8)
        intro.cell(row, 1).alignment = Alignment(wrap_text=True, vertical='center')
        intro.row_dimensions[row].height = 34
    for row in range(len(instructions) + 1, intro.max_row + 1):
        intro.row_dimensions[row].height = 48
        for cell in intro[row]:
            cell.alignment = Alignment(wrap_text=True, vertical='top')
    # Exclusive creation prevents accidental destruction of a filled workbook.
    try:
        with path.open('xb') as stream:
            book.save(stream)
    finally:
        book.close()


def _text(value):
    return '' if value is None else str(value).strip()


def _empty(value):
    return value is None or (isinstance(value, str) and not value.strip())


def _day(value):
    if value in (None, ''):
        return None
    if isinstance(value, datetime):
        if value.time() != time():
            raise ValueError('Укажите только дату без времени.')
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    for fmt in ('%d.%m.%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(str(value).strip(), fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError('Введите дату ДД.ММ.ГГГГ или оставьте поле пустым.')


def _clock(value):
    if value in (None, ''):
        return None
    if isinstance(value, time) and value.tzinfo is None and not value.second and not value.microsecond:
        return value.isoformat(timespec='minutes')
    try:
        return datetime.strptime(str(value).strip(), '%H:%M').strftime('%H:%M')
    except ValueError:
        raise ValueError('Введите время ЧЧ:ММ по Москве или оставьте поле пустым.') from None


def _flag(value):
    if value in (None, ''):
        return False
    if _text(value).casefold() not in {'да', 'нет'}:
        raise ValueError('Выберите Да или Нет.')
    return _text(value).casefold() == 'да'


def prepare_excel(path, repository=None, documents_root=None):
    plan = ExcelPlan(Path(path).resolve())
    if repository:
        plan.database_token = database_token(repository)
    try:
        if plan.path.suffix.lower() != '.xlsx':
            raise ValueError('Поддерживается только .xlsx из шаблона программы.')
        if plan.path.stat().st_size > 25 * 1024 * 1024:
            raise ValueError('Файл больше 25 МБ. Разделите перенос на несколько книг.')
        payload = plan.path.read_bytes()
        with ZipFile(BytesIO(payload)) as archive:
            if sum(info.file_size for info in archive.infolist()) > 150 * 1024 * 1024:
                raise ValueError('Распакованный Excel больше 150 МБ. Разделите книгу.')
        plan.digest = sha256(payload).hexdigest()
        book = load_workbook(BytesIO(payload), read_only=True, data_only=False, keep_links=False)
        try:
            rows = _read_rows(book, plan)
        finally:
            book.close()
        _build_records(rows, plan, documents_root)
        if not plan.tenders:
            plan.issues.append(Issue('Тендеры', 2, 'Номер тендера', 'Нет тендеров. Заполните рабочий лист.'))
        if repository:
            aliases = {}
            for tender in repository.all():
                for alias in number_aliases(tender.number):
                    aliases.setdefault(alias, set()).add(tender.id)
            for key, tender in plan.tenders.items():
                matches = set().union(*(aliases.get(alias, set()) for alias in number_aliases(tender.number)))
                if len(matches) > 1:
                    plan.issues.append(Issue('Тендеры', tender.source_row, 'Номер тендера',
                                             'Найдено несколько карточек. Устраните неоднозначность в базе.'))
                elif matches:
                    plan.skipped.add(key)
    except Exception as error:
        plan.issues.append(Issue('Файл', 0, 'Книга', f'Не удалось прочитать файл: {error}'))
    return plan


def _read_rows(book, plan):
    result = {name: [] for name in SHEETS}
    for title in SHEETS:
        if title not in book.sheetnames:
            plan.issues.append(Issue(title, 1, 'Лист', 'Лист отсутствует. Используйте полный шаблон.'))
    for sheet in book:
        if sheet.title == INTRO:
            continue
        if sheet.max_row > MAX_ROWS + 1 or sheet.max_column > 100:
            plan.issues.append(Issue(sheet.title, 1, 'Размер листа', f'Допустимо до {MAX_ROWS} строк и 100 столбцов.'))
            continue
        iterator = sheet.iter_rows()
        headers = next(iterator, ())
        expected = dict((label, name) for name, label in SHEETS.get(sheet.title, ()))
        mapping = {}
        for index, cell in enumerate(headers):
            label = _text(cell.value)
            if not label:
                continue
            if label not in expected or expected[label] in mapping.values():
                plan.issues.append(Issue(sheet.title, 1, label, 'Неизвестный или повторный заголовок. Перенесите сведения в штатные поля шаблона.'))
            else:
                mapping[index] = expected[label]
        if sheet.title in SHEETS:
            for label, name in expected.items():
                if name not in mapping.values():
                    plan.issues.append(Issue(sheet.title, 1, label, 'Верните столбец из шаблона.'))
        for row_number, cells in enumerate(iterator, 2):
            if all(_empty(cell.value) for cell in cells):
                continue
            data = {}
            failed = False
            for index, cell in enumerate(cells):
                if _empty(cell.value):
                    continue
                label = _text(headers[index].value) if index < len(headers) else ''
                if index not in mapping:
                    plan.issues.append(Issue(sheet.title, row_number, label or get_column_letter(index + 1),
                                             'Данные вне шаблона. Перенесите их в комментарии или заметки.'))
                    failed = True
                    continue
                name = mapping[index]
                try:
                    if cell.data_type in {'f', 'e'}:
                        raise ValueError('Замените формулу или ошибку Excel обычным значением.')
                    if len(str(cell.value)) > 32_767:
                        raise ValueError('Ячейка слишком длинная. Разделите текст на заметки.')
                    data[name] = (_day(cell.value) if name in DATES else _clock(cell.value) if name == 'deadline_time'
                                  else _flag(cell.value) if name in BOOLS else _text(cell.value))
                except ValueError as error:
                    plan.issues.append(Issue(sheet.title, row_number, label, str(error)))
                    failed = True
            if not failed and sheet.title in result:
                result[sheet.title].append((row_number, data))
    return result


def _build_records(rows, plan, documents_root):
    for row, data in rows['Тендеры']:
        try:
            tender = Tender(**data, source_sheet='Тендеры', source_row=row)
            tender.import_issues = 'Источник: единый Excel-шаблон'
            tender.status = tender.status or 'В работе'
            validate_tender(tender)
            if tender.status not in STATUSES:
                raise ValueError('Статус: выберите значение из списка.')
            if tender.url and not valid_web_url(tender.url):
                raise ValueError('Ссылка: укажите полный адрес http:// или https://.')
            key = normalize_number(tender.number)
            if any(number_aliases(tender.number) & number_aliases(t.number) for t in plan.tenders.values()):
                raise ValueError('Номер тендера повторяется или неоднозначен внутри файла. Оставьте одну строку.')
            if tender.deadline_date:
                tender.deadline_timezone = 'Europe/Moscow'
            if tender.folder_path:
                folder = Path(tender.folder_path)
                if not folder.is_absolute():
                    folder = Path(documents_root or plan.path.parent) / folder
                if not folder.is_dir():
                    plan.issues.append(Issue('Тендеры', row, 'Папка документов',
                                             f'Папка не найдена; путь будет сохранён: {tender.folder_path}', True))
            plan.tenders[key] = tender
        except (ValueError, TypeError) as error:
            plan.issues.append(Issue('Тендеры', row, 'Карточка', str(error)))
    for row, original in rows['Спецификации']:
        data = dict(original)
        try:
            code = data.pop('code', '')
            parent = normalize_number(data.pop('tender', ''))
            if not code or code in plan.specs:
                raise ValueError('Код спецификации: заполните уникальный код.')
            if parent not in plan.tenders:
                raise ValueError('Номер тендера: укажите номер из листа «Тендеры».')
            if plan.tenders[parent].status != 'Победа':
                raise ValueError('Для спецификации тендер должен иметь статус «Победа». Исправьте статус или связь.')
            spec = Specification(**data, tender_id=0)
            if not spec.number.strip():
                raise ValueError('Номер спецификации обязателен.')
            plan.specs[code] = (parent, spec)
        except (ValueError, TypeError) as error:
            plan.issues.append(Issue('Спецификации', row, 'Спецификация', str(error)))
    for row, original in rows['Позиции']:
        data = dict(original)
        try:
            code = data.pop('code', '')
            parent = data.pop('spec', '')
            if not code or code in plan.positions:
                raise ValueError('Код позиции: заполните уникальный код.')
            if parent not in plan.specs:
                raise ValueError('Код спецификации: укажите код из листа «Спецификации».')
            for name in ('name', 'unit', 'ordered'):
                if not data.get(name):
                    raise ValueError(f'{dict(SHEETS["Позиции"])[name]}: обязательное поле.')
            position = Position(**data)
            position.validate()
            plan.positions[code] = (parent, position)
            plan.specs[parent][1].positions.append(position)
        except (ValueError, TypeError) as error:
            plan.issues.append(Issue('Позиции', row, 'Позиция', str(error)))
    for row, data in rows['Спецификации']:
        code = data.get('code')
        if code in plan.specs:
            try:
                plan.specs[code][1].validate()
            except ValueError as error:
                plan.issues.append(Issue('Спецификации', row, 'Спецификация', str(error)))
    for row, data in rows['Заметки']:
        kind, target, text = data.get('kind'), data.get('code', ''), data.get('text', '')
        if kind == 'Тендер':
            target = normalize_number(target)
        owners = {'Тендер': plan.tenders, 'Спецификация': plan.specs, 'Позиция': plan.positions}
        if kind not in owners or target not in owners.get(kind, {}) or not text:
            plan.issues.append(Issue('Заметки', row, 'Объект / номер или код / текст',
                                     'Выберите объект, укажите существующий номер или код и непустой текст.'))
        else:
            stamp = data.get('stamp')
            plan.notes.append((kind, target, stamp + 'T00:00:00+03:00' if stamp else None, text))
