"""Pure, read-only search shared by UI commands and model tools."""
from dataclasses import asdict, dataclass
import re


def normalized(text):
    return re.sub(r'\s+', ' ', str(text or '')).strip().casefold().replace('ё', 'е')


# Identifiers contain digits; words such as «спецификация подписана» are not references.
NUMBER = r'(?=[\w./-]*\d)[\w][\w./-]*'
REFERENCE = re.compile(r'(?<!\w)(?P<kind>прил(?:ожени[еяюи])?\.?|спец(?:ификаци[яиюи])?\.?|тендер(?:а|у|ом)?)\s*№?\s*(?P<number>' + NUMBER + r')', re.IGNORECASE)
APPLICATION = re.compile(r'(?<!\w)прил(?:ожени[еяюи])?\.?\s*№?\s*([^\s,;]+)', re.IGNORECASE)


@dataclass(frozen=True)
class NumberReference:
    kind: str
    number: str
    text: str


def number_references(text):
    return [NumberReference('application' if m['kind'].casefold().startswith('прил') else
                            'spec' if m['kind'].casefold().startswith('спец') else 'tender',
                            normalized(m['number']).rstrip('.'), m.group())
            for m in REFERENCE.finditer(text)]


def application_identifiers(document):
    document = str(document or '').strip()
    numbers = [m.group(1).rstrip('.') for m in APPLICATION.finditer(document)]
    if not numbers and re.fullmatch(r'\d[\w/.-]*', document):
        numbers = [document]
    return list(dict.fromkeys(numbers))


def document_number(value):
    value = re.split(r'\s+от(?:\s|$)', normalized(value), maxsplit=1)[0]
    value = re.sub(r'^(?:спецификация|спец\.?|тендер)\s*', '', value)
    return value.lstrip('№ ').rstrip('.')


def reference_matches(reference, tender, spec=None, position=None):
    if reference.kind == 'tender':
        return document_number(tender.number) == reference.number
    if reference.kind == 'spec':
        return spec is not None and document_number(spec.number) == reference.number
    positions = [position] if position else spec.positions if spec else []
    return any(reference.number in [normalized(n) for n in application_identifiers(p.supplier_document)] for p in positions)


@dataclass(frozen=True)
class SearchResult:
    kind: str
    tender_id: int
    spec_id: int | None
    position_id: int | None
    title: str
    context: str
    field: str
    snippet: str
    customer: str = ''
    tender_number: str = ''
    spec_number: str = ''
    position_name: str = ''
    document: str = ''
    archived: bool = False

    def link(self, query=''):
        return dict(kind='spec' if self.spec_id is not None else 'tender',
                    id=self.spec_id if self.spec_id is not None else self.tender_id,
                    label=(f'Открыть спецификацию {self.spec_number}' if self.spec_id is not None
                           else f'Открыть тендер № {self.tender_number}'),
                    **({'position_id': self.position_id} if self.position_id is not None else {}),
                    match=asdict(self), query=query)


def search_records(tenders, specifications, query, *, include_contacts=True):
    query = query.strip().removeprefix('№').strip()
    references = number_references(query)
    remainder = REFERENCE.sub(' ', query)
    words = [word for word in normalized(remainder).split() if word not in {'и', 'в', 'по', '№'}] if references else normalized(query).split()
    if not words and not references:
        return []
    results = []

    def add(kind, tender, spec, position, title, context, fields, number):
        texts = [(label, re.sub(r'\s+', ' ', str(value or '')).strip()) for label, value in fields]
        if not include_contacts:
            texts = [(label, value) for label, value in texts if label != 'Контакты']
        if references:
            if any(not reference_matches(ref, tender, spec, position) for ref in references):
                return
            # Typed queries return the requested entity, rather than all its children.
            target = 'Позиция' if any(r.kind == 'application' for r in references) else 'Спецификация' if any(r.kind == 'spec' for r in references) else 'Тендер'
            if kind != target:
                return
        combined = normalized(' '.join(value for _, value in texts))
        if not all(word in combined for word in words):
            return
        matching = [(label, value) for label, value in texts if any(w in normalized(value) for w in words)]
        if references:
            preferred = 'Приложение / документ' if target == 'Позиция' else 'Номер'
            matching = [(label, value) for label, value in texts if label == preferred]
        label, value = max(matching, key=lambda pair: sum(w in normalized(pair[1]) for w in words))
        match_words = words + [ref.number for ref in references]
        offsets = [normalized(value).find(w) for w in match_words if w in normalized(value)]
        offset = min(offsets, default=0)
        start = max(0, offset - 45)
        snippet = ('…' if start else '') + value[start:start + 170] + ('…' if len(value) > start + 170 else '')
        result = SearchResult(kind, tender.id, spec.id if spec else None,
            position.id if position else None, title, context, label, snippet,
            tender.customer, tender.number, spec.number if spec else '',
            position.name if position else '', position.supplier_document if position else '',
            bool(spec and spec.archived))
        rank = (0 if references or normalized(number) == normalized(query) or (position and normalized(query) in [normalized(n) for n in application_identifiers(position.supplier_document)]) else
                2 if label == 'История изменений' else 1)
        results.append((rank, result))

    for tender in tenders:
        add('Тендер', tender, None, None, f'№ {tender.number}',
            f'{tender.customer} · {tender.title}', [
                ('Номер', tender.number), ('Заказчик', tender.customer), ('Предмет', tender.title),
                ('Контакты', tender.contacts), ('История работы', tender.comment),
                ('Действие', tender.action), ('Площадка', tender.platform), ('Статус', tender.status),
            ], tender.number)
    by_id = {t.id: t for t in tenders}
    for spec in specifications:
        tender = by_id.get(spec.tender_id)
        if tender is None:
            continue
        context = f'{tender.customer} · Тендер № {tender.number}' + (' · Архив' if spec.archived else '')
        add('Спецификация', tender, spec, None, spec.number, context, [
            ('Номер', spec.number), ('Предмет', spec.title), ('Договор', spec.contract),
            ('Заказчик', tender.customer), ('Тендер', tender.number), ('Риск', spec.risk),
            ('Этап', spec.summary), ('Комментарии спецификации', ' '.join(message for _, message in spec.comments)),
            ('История изменений', ' '.join(message for _, message in spec.history)),
        ], spec.number)
        for position in spec.positions:
            add('Позиция', tender, spec, position, position.name,
                f'Спецификация {spec.number} · {context}', [
                    ('Позиция / чертёж', position.name), ('Изготовитель', position.supplier),
                    ('Приложение / документ', position.supplier_document), ('Примечание', position.note),
                    ('Транспорт', position.transport), ('Местоположение', position.location),
                    ('Комментарий', position.comment), ('Рабочая заметка', position.working_note), ('Этап', position.stage),
                    ('Комментарии позиции', ' '.join(message for _, message in position.comments)),
                ], position.name)
    return [result for _, result in sorted(results, key=lambda pair: pair[0])]
