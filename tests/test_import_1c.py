"""All import tests use fictional TSV and temporary fictional databases."""
import csv
from dataclasses import replace
from io import StringIO
from pathlib import Path
import sqlite3
import time

import pytest

from tender_app.import_1c import (MAX_BYTES, MAX_RECORDS, ImportFormatError,
    StaleImportPlan, make_plan, normalize_number, number_aliases, parse_text)
from tender_app.models import Tender, overdue
from tender_app.repository import COLUMNS, Repository, readonly
from tender_app.execution_repository import ExecutionRepository
from tender_app.execution_seed import create_demo_database
from tender_app.ai_tools import snapshot, Tools


def line(number='000123/А-26', customer='ООО Учебный заказчик', deadline='10.08.2026 12:30:07',
         status='В работе', title='Поставка валов', platform='b2b-center', comment='По чертежу, 3 шт.'):
    fields = ['1','01.08.2026',deadline,'10',number,customer,'Тендер учебный','','Нет',status,title,platform,comment]
    stream = StringIO(newline='')
    csv.writer(stream,delimiter='\t',lineterminator='\r\n').writerow(fields)
    return stream.getvalue()


def parsed(text):
    return parse_text(text).rows[0]


@pytest.fixture
def storage(tmp_path):
    path = tmp_path/'Рабочая тестовая база'/'tenders.db'
    create_demo_database(path)
    repo = ExecutionRepository(path)
    yield repo
    repo.close()


def test_typical_and_all_fields():
    row=parsed(line())
    assert (row.number,row.customer,row.title,row.platform,row.comment)==('000123/А-26','ООО Учебный заказчик','Поставка валов','b2b-center','По чертежу, 3 шт.')
    assert row.source_status=='В работе'
    assert row.deadline_time=='12:30:07'
    tender=row.tender(__import__('datetime').datetime(2026,8,1))
    assert tender.status=='В работе' and tender.source_row==0 and tender.source_sheet==''
    assert not tender.url and not tender.contacts and not tender.folder_path
    assert tender.deadline_timezone=='Europe/Moscow'
    assert '01.08.2026' in tender.action


@pytest.mark.parametrize('status',['Подался','Отказ','Проиграли','Победа','Интересный','Интересные','На согласовании'])
def test_source_status_never_becomes_business_status(status):
    row=parsed(line(status=status))
    assert row.category=='new'
    assert row.tender(__import__('datetime').datetime.now()).status=='В работе'


def test_shifted_missing_and_ambiguous_anchor():
    fields=['1','01.08.2026','10.08.2026','10','111','Заказчик','x','Подался','срочно','РТС-тендер','вал']
    row=parsed('\t'.join(fields));assert (row.title,row.platform,row.comment)==('срочно','РТС-тендер','вал')
    row=parsed(line(status='неизвестный статус'));assert (row.title,row.platform,row.comment)==('Поставка валов','b2b-center','По чертежу, 3 шт.')
    row=parsed(line(customer='В работе'));assert row.customer=='В работе' and row.title=='Поставка валов'
    row=parsed(line(title='Победа'));assert row.category=='conflict'
    row=parsed(line(status='неизвестный',title='Победа'));assert row.category=='conflict'
    assert parsed('\t'.join(fields[:8])).category=='error'


def test_optional_comment():
    row=parsed(line().rstrip('\r\n').rsplit('\t',1)[0])
    assert row.category=='new' and row.comment==''


def test_quotes_newlines_crlf_blank_bom_and_empty_cells():
    text='\ufeff'+line(comment='Строка 1\r\nСтрока 2\t"цитата"')+'\r\n'+line('002',comment='')
    data=parse_text(text)
    assert not data.fatal and len(data.rows)==3
    assert data.rows[0].comment=='Строка 1\r\nСтрока 2\t"цитата"'
    assert data.rows[0].raw_fields[7]=='' and data.rows[0].line_start==1 and data.rows[0].line_end==2
    assert data.rows[1].category=='blank' and data.rows[2].number=='002'
    assert data.rows[2].line_start==4
    assert parsed(line().replace('\r\n','\n')).category=='new'


def test_empty_corrupted_and_headers_visible():
    assert parse_text('').fatal
    assert parse_text(' \t\n').fatal
    assert parsed('a\tb\tc').category=='error'
    assert parsed(line(number='')).category=='error'
    assert parsed(line(number='Номер')).category=='header'
    result=parse_text(line()+'"несбалансированная кавычка')
    assert result.fatal and result.rows[-1].category=='error'
    assert make_plan(line()+'"обрыв', [], 'x').fatal


def test_raw_number_nbsp_and_punctuation():
    row=parsed(line(number=' 000123/А-26.7\xa0  А '))
    assert row.raw_fields[4]==' 000123/А-26.7\xa0  А '
    assert row.number=='000123/А-26.7   А'
    assert normalize_number(row.number)=='000123/а-26.7 а'
    assert number_aliases('111\r\n112')=={'111','112','111 112'}
    assert number_aliases('001/А-26.7 X')=={'001/а-26.7 x'}
    assert parsed(line(number='111\n112')).category=='conflict'
    assert parsed(line(title='Дробильно-размольное&#x20;',comment='<img src=x>')).comment=='<img src=x>'


@pytest.mark.parametrize('raw,day,clock,review',[
    ('10.08.2026','2026-08-10',None,0),('10.08.2026 00:00','2026-08-10','00:00',0),
    ('10.08.2026 00:00:09','2026-08-10','00:00:09',0),('10.08.2026 09:12','2026-08-10','09:12',0),
    ('31.02.2026',None,None,1),('не дата',None,None,1),('',None,None,0),
    ('10.08.2026 24:00',None,None,1),('2026-08-10',None,None,1)])
def test_deadline(raw,day,clock,review):
    row=parsed(line(deadline=raw))
    assert (row.deadline_date,row.deadline_time,row.deadline_needs_review)==(day,clock,review)
    assert row.deadline_raw==raw
    if not raw:assert not overdue(row.tender(__import__('datetime').datetime.now()))


def test_buffer_duplicates_and_conflicts():
    plan=make_plan(line()+line()+line('002')+line('002',comment='Отличие'),[], 'x')
    assert [r.category for r in plan.rows]==['new','repeat','conflict','conflict']
    assert sum(plan.counts.values())==4


def test_existing_closed_alias_and_multiple_matches(storage):
    first=storage.save(Tender(number='001\n002',customer='Учебный',platform='ЭТП',status='Проиграли'))
    plan=storage.preview_1c(line('002',customer='Учебный',platform='ЭТП'))
    assert plan.rows[0].category=='existing' and plan.rows[0].matches==(first.id,)
    assert storage.duplicates(' 002 ')[0].id==first.id
    storage.save(Tender(number='002',customer='Другой'))
    assert storage.preview_1c(line('002')).rows[0].category=='conflict'
    assert storage.preview_1c(line('ДЕМО-202')).rows[0].category=='conflict'
    assert storage.preview_1c(line('ДЕМО-101')).rows[0].category=='conflict'


def state(storage):
    return storage.all(),storage.specifications(),list(storage.connection.iterdump())


def test_preview_cancel_snapshot_backup_atomic_commit_and_idempotency(storage):
    before=state(storage)
    files=set(storage.path.parent.rglob('*'))
    plan=storage.preview_1c(line()+line('002')+line())
    assert state(storage)==before and set(storage.path.parent.rglob('*'))==files
    ids,backup=storage.import_1c(plan,[1,2])
    assert len(ids)==2 and Path(backup).is_file() and Path(backup).name.startswith('manual-')
    copy=ExecutionRepository.open_existing_for_import(backup,read_only=True)
    try:assert copy.all()==before[0] and copy.specifications()==before[1]
    finally:copy.close()
    assert storage.all()[:-2]==before[0] and storage.specifications()==before[1]
    assert storage.all()[-2].created_at.endswith('+03:00')
    fresh=storage.preview_1c(plan.text)
    assert fresh.counts['new']==0
    files=set(storage.path.parent.rglob('*'))
    with pytest.raises(ImportFormatError):storage.import_1c(fresh,[])
    with pytest.raises(StaleImportPlan):storage.import_1c(plan,[1,2])
    assert len(storage.all())==len(before[0])+2 and set(storage.path.parent.rglob('*'))==files
    tenders,specs=snapshot(storage)
    answer=Tools(tenders,specs,'Найди 000123/А-26').read('search',{'query':'000123/А-26'})
    assert '000123/А-26' in answer


def test_backup_failure_and_mid_batch_rollback(storage,monkeypatch):
    plan=storage.preview_1c(line()+line('002'))
    before=state(storage)
    with monkeypatch.context() as patch:
        def fail(*a,**k):raise PermissionError('Вымышленная ошибка backup')
        patch.setattr(storage,'backup',fail)
        with pytest.raises(PermissionError):storage.import_1c(plan,[1,2])
    assert state(storage)==before
    storage.connection.execute("CREATE TRIGGER fail_second BEFORE INSERT ON tenders WHEN NEW.number='002' BEGIN SELECT RAISE(ABORT,'fictional'); END")
    plan=storage.preview_1c(plan.text)
    before=state(storage)
    with pytest.raises(sqlite3.IntegrityError):storage.import_1c(plan,[1,2])
    assert state(storage)==before


def test_changed_database_and_backup_race_cancel_whole_batch(storage,monkeypatch):
    plan=storage.preview_1c(line())
    other=ExecutionRepository.open_existing_for_import(storage.path)
    other.save(Tender(number='ПАРАЛЛЕЛЬНЫЙ'))
    other.close()
    before=state(storage)
    with pytest.raises(StaleImportPlan):storage.import_1c(plan,[1])
    assert state(storage)==before
    plan=storage.preview_1c(line())
    backup=storage.backup
    def racing(*a,**kw):
        target=backup(*a,**kw)
        other=ExecutionRepository.open_existing_for_import(storage.path)
        try:other.save(Tender(number='ПОСЛЕ-БЭКАПА'))
        finally:other.close()
        return target
    monkeypatch.setattr(storage,'backup',racing)
    with pytest.raises(StaleImportPlan):storage.import_1c(plan,[1])
    assert not any(t.number=='000123/А-26' for t in storage.all())


def test_reject_changed_plan_invalid_selection_and_fatal(storage):
    plan=storage.preview_1c(line())
    with pytest.raises(StaleImportPlan):storage.import_1c(replace(plan,rows=(replace(plan.rows[0],comment='подмена'),)),[1])
    for selected in ([2],[1,1],[True],[]):
        with pytest.raises(ImportFormatError):storage.import_1c(plan,selected)
    bad=storage.preview_1c(line()+'"незакрыто')
    with pytest.raises(ImportFormatError):storage.import_1c(bad,[1])


def test_invalid_deadline_saved_as_raw_and_seconds_retained(storage):
    plan=storage.preview_1c(line('BAD',deadline='31.02.2026')+line('SEC',deadline='10.08.2026 01:02:03'))
    storage.import_1c(plan,[1,2])
    bad,sec=storage.all()[-2:]
    assert bad.deadline_raw=='31.02.2026' and bad.deadline_needs_review==1
    assert bad.deadline_date is None and bad.deadline_time is None
    assert sec.deadline_time=='01:02:03'


def test_limits_are_explicit_and_never_truncate():
    with pytest.raises(ImportFormatError,match='5 МиБ'):parse_text('Я'*(MAX_BYTES//2+1))
    with pytest.raises(ImportFormatError,match='10 000'):parse_text('\n'*(MAX_RECORDS+1))
    assert parse_text(line(comment='x'*140_000)).fatal


def test_large_batch(storage):
    text=''.join(line(f'ПАКЕТ-{i:05}') for i in range(10_000))
    plan=storage.preview_1c(text)
    assert len(plan.rows)==10_000 and plan.counts['new']==10_000
    ids,_=storage.import_1c(plan,range(1,10_001))
    assert len(ids)==10_000 and len(set(ids))==10_000
    assert storage.preview_1c(text).counts['new']==0


def test_existing_worker_open_does_not_seed_or_migrate(storage,tmp_path):
    with pytest.raises(FileNotFoundError):ExecutionRepository.open_existing_for_import(tmp_path/'missing.db')
    assert not (tmp_path/'missing.db').exists()
    with pytest.raises(ValueError):Repository.open_existing_for_import(storage.path)
    assert storage.connection.execute('PRAGMA user_version').fetchone()[0]==10


def test_fallback_comment_status_word_is_not_an_anchor():
    row=parsed(line(status='неизвестный',comment='Победа'))
    assert row.category=='new' and row.comment=='Победа' and row.platform=='b2b-center'


def test_malformed_tail_kept_as_one_explicit_diagnostic_range():
    text=line()+'"сломано"x\tcell\nещё\tстрока\n'
    parsed_text=parse_text(text)
    assert parsed_text.fatal
    assert parsed_text.rows[-1].line_end==3
    assert 'ещё' in parsed_text.rows[-1].raw_fields[0]


def test_parallel_imports_only_one_packet_commits(storage,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    plan=storage.preview_1c(line()+line('002'))
    barrier=Barrier(2)
    backup=ExecutionRepository.backup
    def synchronized(self,*a,**kw):
        result=backup(self,*a,**kw)
        barrier.wait(timeout=10)
        return result
    monkeypatch.setattr(ExecutionRepository,'backup',synchronized)
    def run():
        repo=ExecutionRepository.open_existing_for_import(storage.path)
        try:
            try:return ('ok',repo.import_1c(plan,[1,2]))
            except StaleImportPlan:return ('stale',None)
        finally:repo.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(run) for _ in range(2)]
        results=[f.result(timeout=20)[0] for f in futures]
    assert sorted(results)==['ok','stale']
    assert sum(t.number=='000123/А-26' for t in storage.all())==1


def test_corrupt_backup_blocks_import(storage,monkeypatch,tmp_path):
    plan=storage.preview_1c(line())
    before=state(storage)
    bad=tmp_path/'invalid-backup.db';bad.write_bytes(b'fictional broken backup')
    monkeypatch.setattr(storage,'backup',lambda **kw:bad)
    with pytest.raises(sqlite3.DatabaseError):storage.import_1c(plan,[1])
    assert state(storage)==before


def test_full_repeats_still_marked_when_card_already_exists(storage):
    storage.save(Tender(number='ВЫМЫШЛЕННЫЙ',customer='ООО Учебный заказчик',platform='b2b-center'))
    text=line('ВЫМЫШЛЕННЫЙ')+line('ВЫМЫШЛЕННЫЙ')
    plan=storage.preview_1c(text)
    assert [row.category for row in plan.rows]==['existing','repeat']
    assert plan.counts['new']==0
