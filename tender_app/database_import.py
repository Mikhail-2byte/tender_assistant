"""Atomic Excel import; deliberately does not invoke per-card save methods."""
from dataclasses import asdict

from tender_app.database_excel import database_token, prepare_excel
from tender_app.execution_repository import SPEC_FIELDS, POSITION_FIELDS
from tender_app.models import now_moscow
from tender_app.repository import COLUMNS


def _insert(connection, table, values):
    names = tuple(values)
    return connection.execute(
        f'INSERT INTO {table} ({",".join(names)}) VALUES ({",".join("?" for _ in names)})',
        tuple(values.values())).lastrowid


def commit_excel(repository, plan, documents_root=None, *, backup_current=True):
    if not plan.valid:
        raise ValueError('В файле есть ошибки. Исправьте их и повторите проверку.')
    # Re-read source rather than trust mutable UI objects or a changed workbook.
    fresh = prepare_excel(plan.path, repository, documents_root)
    if (not fresh.valid or fresh.digest != plan.digest or
            fresh.database_token != plan.database_token):
        raise ValueError('Файл или база изменились после проверки. Проверьте файл снова.')
    if len(fresh.tenders) == len(fresh.skipped):
        return fresh.report() + '\nНовых тендеров нет. База не изменена.'
    backup = repository.backup(manual=True) if backup_current else None
    stamp = now_moscow().isoformat(timespec='seconds') + '+03:00'
    connection = repository.connection
    tender_ids, spec_ids, position_ids = {}, {}, {}
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        if database_token(repository) != fresh.database_token:
            raise ValueError('База изменилась после проверки. Проверьте файл снова.')
        for key, tender in fresh.tenders.items():
            if key in fresh.skipped:
                continue
            data = asdict(tender)
            data['created_at'] = data['updated_at'] = stamp
            tender_ids[key] = _insert(connection, 'tenders', {n: data[n] for n in COLUMNS if n != 'id'})
        for code, (parent, spec) in fresh.specs.items():
            if parent not in tender_ids:
                continue
            data = asdict(spec)
            data.update(tender_id=tender_ids[parent], updated=stamp)
            spec_ids[code] = _insert(connection, 'execution_specs', {n: data[n] for n in SPEC_FIELDS})
            _insert(connection, 'execution_history', dict(spec_id=spec_ids[code], stamp=stamp,
                                                         message='Импорт из единого Excel-шаблона.'))
        ordinals = {}
        for code, (parent, position) in fresh.positions.items():
            if parent not in spec_ids:
                continue
            data = asdict(position)
            for name in ('ordered', 'placed', 'customer_shipped'):
                data[name] = str(data[name]) if data[name] is not None else None
            ordinal = ordinals.get(parent, 0)
            ordinals[parent] = ordinal + 1
            values = dict(spec_id=spec_ids[parent], ordinal=ordinal)
            values.update({n: data[n] for n in POSITION_FIELDS})
            position_ids[code] = _insert(connection, 'execution_positions', values)
            if position.comment:
                _insert(connection, 'execution_comments', dict(spec_id=spec_ids[parent],
                        position_id=position_ids[code], stamp=None, message=position.comment))
        for kind, target, note_stamp, text in fresh.notes:
            if kind == 'Тендер' and target in tender_ids:
                addition = (note_stamp[:10] + ': ' if note_stamp else '') + text
                connection.execute("UPDATE tenders SET comment=CASE WHEN comment='' THEN ? ELSE comment || char(10) || ? END WHERE id=?",
                                   (addition, addition, tender_ids[target]))
            elif kind == 'Спецификация' and target in spec_ids:
                _insert(connection, 'execution_comments', dict(spec_id=spec_ids[target], position_id=None,
                                                               stamp=note_stamp, message=text))
            elif kind == 'Позиция' and target in position_ids:
                spec_code = fresh.positions[target][0]
                _insert(connection, 'execution_comments', dict(spec_id=spec_ids[spec_code], position_id=position_ids[target],
                                                               stamp=note_stamp, message=text))
        # Freeze the same current text as the legacy view, including undated imports.
        for spec_id in spec_ids.values():
            repository._seed_registry_comment(('specification', spec_id), spec_id, None)
        for code, position_id in position_ids.items():
            parent, position = fresh.positions[code]
            repository._seed_registry_comment(('position', spec_ids[parent], position_id),
                                              spec_ids[parent], position_id, position.working_note)
        repository.check(connection)
    return (fresh.report() + f'\nЗагрузка завершена. Добавлено тендеров: {len(tender_ids)}, '
            f'спецификаций: {len(spec_ids)}, позиций: {len(position_ids)}.\n' +
            (f'Резервная копия: {backup}' if backup else 'Создана новая база; прежняя база не изменялась.'))
