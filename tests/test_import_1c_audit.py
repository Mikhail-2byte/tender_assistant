"""Adversarial regression tests from the second audit; only fictional local data."""
import csv
from dataclasses import replace
from io import StringIO
import random
import sqlite3
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QMimeData, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication, QListWidget, QMessageBox

from tender_app.import_1c import (MAX_BYTES, MAX_CELL_CHARS, ImportFormatError,
    StaleImportPlan, make_plan, parse_text, validate_text_size)
from tender_app.import_storage import database_digest
from tender_app.import_dialog import Import1CDialog, SourceEditor
from tender_app.models import Tender
from tender_app.repository import readonly
from tests.test_import_1c import line, storage
from tests.test_import_1c_ui import owner, show, wait, yes


@pytest.mark.parametrize('columns',[5,6,9])
def test_invalid_same_number_blocks_otherwise_valid_member(storage,columns):
    fields=['1','01.08.2026','10.08.2026','10','ОБЩИЙ-НОМЕР','Другой учебный заказчик','x','x','x']
    plan=storage.preview_1c(line('ОБЩИЙ-НОМЕР')+'\t'.join(fields[:columns])+'\n')
    assert [r.category for r in plan.rows]==['conflict','error']
    with pytest.raises(ImportFormatError):storage.import_1c(plan,[1])


def test_legacy_nul_suffix_change_invalidates_plan(storage):
    tender=storage.save(Tender(number='ДЕМО-NUL',comment='prefix\x00old'))
    plan=storage.preview_1c(line())
    before=database_digest(storage.connection)
    storage.save(replace(tender,comment='prefix\x00new'))
    assert database_digest(storage.connection)!=before
    with pytest.raises(StaleImportPlan):storage.import_1c(plan,[1])
    assert not any(t.number=='000123/А-26' for t in storage.all())


@pytest.mark.parametrize('effect',[
    "UPDATE tenders SET comment='changed by trigger' WHERE id=1;",
    "INSERT INTO tenders(number,source_sheet,source_row,created_at,updated_at) VALUES('HIDDEN','',0,'','');",
    "UPDATE tenders SET status='Победа' WHERE id=NEW.id;",
    "DELETE FROM tenders WHERE id=NEW.id;",
])
def test_trigger_side_effects_are_detected_and_whole_batch_rolled_back(storage,effect):
    storage.connection.execute("CREATE TRIGGER fictional_effect AFTER INSERT ON tenders BEGIN "+effect+" END")
    plan=storage.preview_1c(line()+line('002'))
    before=storage.all(),storage.specifications(),database_digest(storage.connection)
    with pytest.raises(StaleImportPlan):storage.import_1c(plan,[1,2])
    assert (storage.all(),storage.specifications(),database_digest(storage.connection))==before


def test_binary_typed_hash_nul_blob_float_and_without_rowid(storage):
    c=storage.connection
    c.execute('CREATE TABLE "extra" (key TEXT PRIMARY KEY, value, another) WITHOUT ROWID')
    with c:c.execute('INSERT INTO extra VALUES(?,?,?)',('demo',b'a\x00b','x\x00old'))
    first=database_digest(c)
    with c:c.execute('UPDATE extra SET another=?',('x\x00new',))
    assert database_digest(c)!=first
    backup=storage.backup(manual=True)
    copy=readonly(backup)
    try:assert database_digest(copy)==database_digest(c)
    finally:copy.close()
    with c:c.execute('UPDATE extra SET value=?',(1.5,))
    before=database_digest(c)
    with c:c.execute('UPDATE extra SET value=?',(1.6,))
    assert before!=database_digest(c)
    plan=storage.preview_1c(line())
    ids,_=storage.import_1c(plan,[1]);assert len(ids)==1


def test_sql_like_number_and_literal_markup_are_only_text(storage):
    number="000/А-26'); DROP TABLE tenders; --"
    plan=storage.preview_1c(line(number,comment='<script>alert(1)</script> &#x20;'))
    before=storage.all()
    ids,_=storage.import_1c(plan,[1])
    assert storage.all()[:-1]==before
    assert storage.all()[-1].number==number
    assert storage.all()[-1].comment=='<script>alert(1)</script> &#x20;'
    assert ids==(storage.all()[-1].id,)


def test_new_nul_field_rejected_but_legacy_values_are_not_rewritten():
    result=parse_text(line(comment='text\x00invisible'))
    assert result.rows[0].category=='error' and 'Нулевой символ' in result.rows[0].messages[0]


def test_number_space_normalization_is_shared_by_repeat_detection():
    plan=make_plan(line('001\xa0  А')+line('001 А'),[],'test')
    assert [r.category for r in plan.rows]==['new','repeat']


def test_cell_limit_cannot_be_disabled_by_other_modules():
    before=csv.field_size_limit()
    try:
        csv.field_size_limit(MAX_CELL_CHARS*4)
        result=parse_text(line(comment='x'*(MAX_CELL_CHARS+1)))
        assert result.fatal and result.rows[-1].category=='error'
    finally:csv.field_size_limit(before)


def test_unicode_and_exact_byte_limits_are_explicit():
    assert validate_text_size('x'*MAX_BYTES)==MAX_BYTES
    assert validate_text_size('Я'*(MAX_BYTES//2))==MAX_BYTES
    for text in ['x'*(MAX_BYTES+1),'Я'*(MAX_BYTES//2+1),'x\ud800']:
        with pytest.raises(ImportFormatError):validate_text_size(text)


def test_random_standard_tsv_roundtrips_without_boundary_loss():
    rng=random.Random(20261023)
    alphabet='абвXYZ09 <>;&\"\t\r\n\xa0'
    for i in range(800):
        comment=''.join(rng.choice(alphabet) for _ in range(rng.randrange(0,90)))
        title='Товар '+''.join(rng.choice(alphabet) for _ in range(rng.randrange(0,30)))
        fields=['1','01.08.2026','10.08.2026 00:00:09','10',f'00{i}/А-26','Учебный заказчик',
                'Тендер учебный','','Нет','В работе',title,'ЭТП',comment]
        stream=StringIO(newline='');csv.writer(stream,delimiter='\t',lineterminator='\r\n').writerow(fields)
        result=parse_text(stream.getvalue())
        assert not result.fatal and len(result.rows)==1
        row=result.rows[0]
        assert row.category=='new' and row.raw_fields==tuple(fields)
        assert row.title==title.replace('\xa0',' ').strip()
        assert row.comment==comment.replace('\xa0',' ').strip()
        assert row.deadline_time=='00:00:09'


@pytest.mark.parametrize('selection',[[[]],[{}],[None],[True]])
def test_noninteger_selection_fails_before_backup(storage,selection):
    plan=storage.preview_1c(line())
    files=set(storage.path.parent.rglob('*'))
    with pytest.raises(ImportFormatError):storage.import_1c(plan,selection)
    assert set(storage.path.parent.rglob('*'))==files


def test_overlimit_clipboard_rejected_before_document_replacement(owner,app,monkeypatch):
    dialog=show(owner,line(),app)
    old=dialog.source.toPlainText()
    monkeypatch.setattr(QApplication,'clipboard',lambda:SimpleNamespace(text=lambda:'x'*(MAX_BYTES+1)))
    def forbid(*a):raise AssertionError('oversized text reached Qt document')
    monkeypatch.setattr(dialog.source,'setPlainText',forbid)
    dialog.read_clipboard()
    assert dialog.plan is None and not dialog.confirm.isEnabled() and not dialog.busy
    assert dialog.source.toPlainText()==old and '5 МиБ' in dialog.summary.text()
    dialog.reject()


def test_ordinary_paste_size_accounts_for_replaced_selection(app,monkeypatch):
    import tender_app.import_dialog as module
    monkeypatch.setattr(module,'MAX_BYTES',128)
    editor=SourceEditor();editor.setPlainText('x'*100)
    rejected=[];editor.rejected.connect(rejected.append)
    cursor=editor.textCursor();cursor.select(QTextCursor.SelectionType.Document);editor.setTextCursor(cursor)
    mime=QMimeData();mime.setText('Я'*50)
    editor.insertFromMimeData(mime)
    assert editor.toPlainText()=='Я'*50 and not rejected
    cursor=editor.textCursor();cursor.movePosition(QTextCursor.MoveOperation.End);editor.setTextCursor(cursor)
    mime.setText('z'*30);editor.insertFromMimeData(mime)
    assert rejected and editor.toPlainText()=='Я'*50
    editor.close()


def test_read_failure_after_commit_is_reported_as_committed(owner,app,monkeypatch):
    dialog=show(owner,line(),app);yes(monkeypatch)
    with monkeypatch.context() as patch:
        def fail():raise sqlite3.OperationalError('fictional refresh read failure')
        patch.setattr(owner.repository,'all',fail)
        dialog.confirm_import();wait(dialog,app)
        assert len(dialog.added_ids)==1 and 'Пакет уже сохранён' in dialog.summary.text()
        assert not dialog.confirm.isEnabled() and not dialog._jobs and not owner.import_busy
    assert any(t.id==dialog.added_ids[0] for t in owner.repository.all())
    dialog.reject()


def test_agent_job_blocks_import_before_any_clipboard_read(owner,monkeypatch):
    monkeypatch.setattr(owner.ai_service,'busy',lambda:True)
    messages=[]
    monkeypatch.setattr(QMessageBox,'information',lambda *a,**kw:messages.append(a[2]))
    monkeypatch.setattr(QApplication,'clipboard',lambda: (_ for _ in ()).throw(AssertionError('clipboard read')))
    owner.open_import_1c()
    assert messages and 'Буфер не читался' in messages[0]
    assert getattr(owner,'_import_dialog',None) is None


def test_general_guard_blocks_competing_changes_while_import_is_busy(owner):
    owner.import_busy=True
    try:assert not owner.guard_changes()
    finally:owner.import_busy=False


def test_open_selected_added_card_without_resetting_search(owner,app,monkeypatch):
    dialog=show(owner,line()+line('002'),app);yes(monkeypatch)
    owner.search.setText('Нет таких результатов')
    dialog.confirm_import();wait(dialog,app)
    def choose():
        picker=QApplication.activeModalWidget()
        listing=picker.findChild(QListWidget)
        assert listing and listing.count()==2
        listing.setCurrentRow(1);picker.accept()
    QTimer.singleShot(0,choose)
    dialog.open_results()
    assert owner.current.id==dialog.added_ids[1]
    assert owner.search.text()=='Нет таких результатов'
    assert not dialog.isVisible()


def test_tooltips_cannot_interpret_source_as_rich_text(owner,app,monkeypatch):
    markup='<img src="file:///fictional-never-read.png"> &#x20;'
    dialog=show(owner,line(customer=markup,comment=markup),app)
    assert '<img' not in dialog.table.item(0,4).toolTip()
    assert '&lt;img' in dialog.table.item(0,4).toolTip()
    assert '&amp;#x20;' in dialog.table.item(0,4).toolTip()
    yes(monkeypatch);dialog.confirm_import();wait(dialog,app)
    item=next(owner.list.item(i) for i in range(owner.list.count())
              if owner.list.item(i).data(__import__('PySide6.QtCore',fromlist=['Qt']).Qt.ItemDataRole.UserRole)==dialog.added_ids[0])
    assert '<img' not in item.toolTip() and '&lt;img' in item.toolTip()
    dialog.reject()


def test_imported_html_like_number_stays_literal_in_card_caption(owner,app,monkeypatch):
    from PySide6.QtCore import Qt
    number='<b>000-А</b>'
    dialog=show(owner,line(number),app);yes(monkeypatch)
    dialog.confirm_import();wait(dialog,app)
    tender=next(t for t in owner.repository.all() if t.id==dialog.added_ids[0])
    owner.load_tender(tender)
    assert owner.caption.text()=='№ '+number
    assert owner.caption.textFormat()==Qt.TextFormat.PlainText
    dialog.reject()


@pytest.mark.parametrize('separator',['\n','\r','\r\n','\x85','\u2028','\u2029'])
def test_all_unicode_line_separators_in_new_number_require_review(separator):
    row=parse_text(line('001'+separator+'002')).rows[0]
    assert row.category=='conflict'
