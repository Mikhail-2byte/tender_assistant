"""Qt integration with fake model clients. Never sends external requests."""
import json,time
from threading import Event
import pytest
from PySide6.QtCore import QTimer,QCoreApplication,QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox,QPushButton
from tender_app.execution_window import ExecutionWindow
from tender_app.execution_seed import create_demo_database
from tender_app.execution_repository import ExecutionRepository
from tender_app.settings import Settings
from tender_app.ai_config import Profile,AIError
from tender_app.ai_dialog import AISettingsDialog

KEY='gsk_FICTIONAL_UI_TEST_KEY_1234'
class Client:
    calls=[]
    def __init__(self,profile):self.config=profile;self.model='fake/test-model';self.available_models=['fake/test-model'];self.cancel=Event()
    def begin_turn(self):pass
    def connect(self):self.calls.append(('connect',self.config.provider));return self.model
    def complete(self,messages):self.calls.append(('complete',self.config.provider));return json.dumps({'kind':'answer','text':'Тестовый ответ без сетевого запроса'})

@pytest.fixture
def aiwin(tmp_path,app):
    create_demo_database(tmp_path/'fictional.db');repo=ExecutionRepository(tmp_path/'fictional.db');w=ExecutionWindow(repo,Settings(tmp_path/'settings.json'),tmp_path)
    w.resize(1366,768);w.show();w.pages.setCurrentIndex(2);QTest.qWait(30)
    service=w.ai_service;service.settings.save_model('groq','fake/test-model',KEY,session_only=True)
    service.settings.save_model('openrouter','fake/test-model','sk-or-FICTIONAL_UI_KEY_12345',session_only=True)
    Client.calls=[];service.client_factory=Client;service.consent=lambda *a,**kw:True;service.refresh_states()
    yield w
    for j in list(service.jobs):j.stop()
    for _ in range(300):
        if not service.busy():break
        QTest.qWait(10)
    w.dirty=False;w.close();w.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents();repo.close()

def finish(w):
    for _ in range(300):
        QTest.qWait(10)
        if not w.ai_service.jobs:return
    pytest.fail('network worker did not finish')

def test_ui_real_path_stores_answer_and_uses_selected_model(aiwin):
    w=aiwin;p=w.agent_page;p.chat.input.setPlainText('Объясни ситуацию');p.send_message();finish(w)
    assert len(p.history.active['messages'])==2
    assert p.history.active['messages'][-1]['text']=='Тестовый ответ без сетевого запроса'
    assert p.chat.model_state.text()==''
    assert p.chat.model_state.property('aiState')=='ready'
    assert 'Ответ получен' in p.chat.model_state.toolTip()
    assert Client.calls==[('connect','groq'),('complete','groq')]
    assert not p.chat.responding and p.chat_selector.isEnabled()


def test_usage_visible_persisted_and_kept_out_of_model_history(aiwin):
    from tender_app.models import now_moscow
    from tender_app.ai_usage import token_usage
    from PySide6.QtWidgets import QDialog,QPlainTextEdit
    w=aiwin;service=w.ai_service
    class Metered(Client):
        def __init__(self,p):super().__init__(p);self.usage_events=[]
        def complete(self,messages):
            result=super().complete(messages)
            event=dict(time=now_moscow().isoformat(),turn_id='test-turn',endpoint='/chat/completions',
                       provider='groq',model=self.model,purpose=self.purpose,status='ok',http_status=200)
            event.update(token_usage({'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120}}))
            self.usage_events.append(event);self.usage_sink(event)
            return result
    service.client_factory=Metered
    p=w.agent_page;p.chat.input.setPlainText('Вопрос для учёта');p.send_message();finish(w)
    button=service.sources[p.chat]['usage']
    assert button.text()=='Расход AI · 120 ток.' and 'вход 100' in button.toolTip()
    assert len(p.history.active['messages'])==2
    assert not any('Токены' in m['text'] or 'токены' in m['text'] for m in p.history.active['messages'])
    assert service.usage_log.path.exists()
    captured=[]
    def inspect():
        dialog=w.findChildren(QDialog)[-1]
        captured.append(dialog.findChild(QPlainTextEdit).toPlainText());dialog.accept()
    QTimer.singleShot(20,inspect);button.click()
    assert 'всего 120' in captured[0] and 'test-turn' in captured[0]


def test_usage_after_model_error_and_cancel_remains_visible(aiwin):
    w=aiwin;service=w.ai_service
    class FailedAfterUsage(Client):
        def __init__(self,p):super().__init__(p);self.usage_events=[]
        def complete(self,messages):
            event=dict(endpoint='/chat/completions',prompt_tokens=10,completion_tokens=5,total_tokens=15)
            self.usage_events.append(event);self.usage_sink(event)
            raise AIError('Тестовая ошибка после чтения')
    service.client_factory=FailedAfterUsage
    p=w.agent_page;p.chat.input.setPlainText('Вопрос');p.send_message();finish(w)
    assert service.sources[p.chat]['usage'].text()=='Расход AI · 15 ток.'
    assert 'всего 15' in service.usage_log.report()
    class CancelAfterUsage(FailedAfterUsage):
        def complete(self,messages):
            try:super().complete(messages)
            except AIError:pass
            time.sleep(.15)
            return json.dumps({'kind':'answer','text':'Поздний ответ'})
    service.client_factory=CancelAfterUsage
    p.chat.input.setPlainText('Ещё вопрос');p.send_message()
    for _ in range(100):
        job=service.sources[p.chat]['request']['job']
        if job.client and job.client.usage_events:break
        QTest.qWait(1)
    service.cancel(p.chat);finish(w)
    assert 'остановлен' in p.history.active['messages'][-1]['text']
    assert service.sources[p.chat]['usage'].text()=='Расход AI · 15 ток.'
    assert 'всего 30' in service.usage_log.report()

def test_no_consent_no_network_and_draft_preserved(aiwin):
    w=aiwin;w.ai_service.consent=lambda *a,**kw:False;p=w.agent_page;p.chat.input.setPlainText('Вопрос')
    p.send_message();assert not Client.calls and not p.history.active['messages'];assert p.chat.input.toPlainText()=='Вопрос'

def test_secret_draft_redacted_and_not_sent(aiwin,monkeypatch):
    w=aiwin;monkeypatch.setattr(QMessageBox,'warning',lambda *a:None);p=w.agent_page;p.chat.input.setPlainText(KEY);p.flush();p.send_message()
    assert not Client.calls and not p.history.active['messages']
    assert KEY not in p.history.path.read_text() and KEY not in p.history.active['draft']

def test_async_remains_responsive_new_draft_not_lost(aiwin):
    w=aiwin
    class Slow(Client):
        def complete(self,m):time.sleep(.15);return super().complete(m)
    w.ai_service.client_factory=Slow;p=w.agent_page;p.chat.input.setPlainText('Вопрос');p.send_message()
    p.chat.input.setPlainText('Новый черновик');QTest.qWait(40)
    assert p.chat.responding and p.chat.input.toPlainText()=='Новый черновик'
    finish(w);assert p.chat.input.toPlainText()=='Новый черновик'
    assert p.history.active['draft']=='Новый черновик'

def test_cancel_drops_late_reply_without_db_write(aiwin):
    w=aiwin;before=w.repository.all()
    class Slow(Client):
        def complete(self,m):time.sleep(.15);return super().complete(m)
    w.ai_service.client_factory=Slow;p=w.agent_page;p.chat.input.setPlainText('Вопрос');p.send_message();w.ai_service.cancel(p.chat);finish(w)
    assert 'остановлен' in p.history.active['messages'][-1]['text']
    assert not any('Тестовый ответ' in m['text'] for m in p.history.active['messages'])
    assert w.repository.all()==before


def test_cancel_drops_result_already_queued_by_finished_worker(aiwin):
    results, errors, completions = [], [], []
    service = aiwin.ai_service
    job = service.start(lambda _: 'late result', results.append, errors.append, completions.append)
    assert job.wait(1000)
    assert results == []
    job.stop()
    finish(aiwin)
    assert results == [] and errors == []
    assert completions == [True]

def test_context_captured_and_followup_has_history(aiwin):
    w=aiwin;seen=[]
    class Capture(Client):
        def complete(self,m):seen.append(m);time.sleep(.03);return super().complete(m)
    w.ai_service.client_factory=Capture;w.pages.setCurrentIndex(0);original=w.current.id
    w.assistant.input.setPlainText('Расскажи о нём');w.assistant.send_message()
    assert w.assistant.model_state.property('aiState')=='working'
    assert w.assistant.model_state.text()==''
    finish(w)
    assert w.ai_service.sources[w.assistant]['history'][-1]['text']=='Тестовый ответ без сетевого запроса'
    assert any('"id": '+str(original) in m['content'] for m in seen[-1] if 'UI-контекст' in m['content'])
    w.assistant.input.setPlainText('А подробнее?');w.assistant.send_message();finish(w)
    assert any(m['role']=='user' and m['content']=='Расскажи о нём' for m in seen[-1])

def test_error_retry_selected_model_preserves_new_draft(aiwin):
    w=aiwin
    class Fails(Client):
        def complete(self,m):
            if self.config.provider=='groq':raise AIError('API отклонил ключ.')
            return super().complete(m)
    w.ai_service.client_factory=Fails;p=w.agent_page;p.chat.input.setPlainText('Вопрос');p.send_message();finish(w)
    assert p.chat.model_state.property('aiState')=='error'
    assert p.chat.model_state.text()==''
    assert 'API отклонил ключ' in p.chat.model_state.toolTip()
    p.chat.input.setPlainText('Новый черновик')
    router=next(e for e in w.ai_service.settings.entries() if e['provider']=='openrouter')
    w.ai_service.settings.select(router['id']);w.ai_service.refresh_states()
    button=next(b for b in p.chat.findChildren(QPushButton) if b.text()=='Повторить');button.click();finish(w)
    assert p.chat.input.toPlainText()=='Новый черновик'
    assert any(call==('connect','openrouter') for call in Client.calls)
    assert 'Тестовый ответ' in p.history.active['messages'][-1]['text']

def test_ai_settings_dialog_masks_key_and_session_saved(aiwin):
    w=aiwin;d=AISettingsDialog(w.ai_service,w);d.edit_model(w.ai_service.settings.active);d.show();QTest.qWait(20)
    assert d.key.echoMode()==d.key.EchoMode.Password and d.key.text()==''
    assert 'задан' in d.key_status.text();d.key.setText('gsk_FICTIONAL_REPLACEMENT_KEY_123')
    d.save();assert not d.changed
    assert 'FICTIONAL' not in w.ai_service.settings.path.read_text()
    d.reject()

def test_api_probe_no_database_payload(aiwin):
    w=aiwin;seen=[]
    class Probe(Client):
        def complete(self,m):seen.extend(m);return super().complete(m)
    w.ai_service.client_factory=Probe
    d=AISettingsDialog(w.ai_service,w);d.edit_model(w.ai_service.settings.active);d.show()
    w.ai_service.auxiliary(d,d.value());finish(w)
    assert Client.calls==[('connect','groq'),('complete','groq')]
    assert 'Подключение работает' in d.result.text()
    assert not any(t.customer in json.dumps(seen,ensure_ascii=False) for t in w.repository.all())
    d.changed=False;d.reject()

def test_proposal_cancel_not_automatic_write(aiwin):
    w=aiwin;t=w.current;before=w.repository.all();w.pages.setCurrentIndex(0)
    class Propose(Client):
        def complete(self,m):return json.dumps({'kind':'propose','tool':'update','args':{'kind':'tender','id':t.id,'changes':{'status':'Победа'}}})
    w.ai_service.client_factory=Propose
    def reject_preview():
        if w.ai_service.pending_dialog:w.ai_service.pending_dialog.reject()
        else:QTimer.singleShot(10,reject_preview)
    QTimer.singleShot(10,reject_preview)
    w.assistant.input.setPlainText('Измени статус на Победа');w.assistant.send_message();finish(w);QTest.qWait(30)
    assert w.repository.all()==before

def test_confirmed_ui_proposal_written_and_reflected(aiwin):
    w=aiwin;t=w.current;w.pages.setCurrentIndex(0)
    class Propose(Client):
        def complete(self,m):return json.dumps({'kind':'propose','tool':'update','args':{'kind':'tender','id':t.id,'changes':{'contacts':'Контакт AI'}}})
    w.ai_service.client_factory=Propose
    def confirm_preview():
        dialog=w.ai_service.pending_dialog
        if dialog:next(b for b in dialog.findChildren(QPushButton) if b.text()=='Применить').click()
        else:QTimer.singleShot(10,confirm_preview)
    QTimer.singleShot(10,confirm_preview)
    w.assistant.input.setPlainText('Измени контакты на Контакт AI');w.assistant.send_message();finish(w);QTest.qWait(30)
    assert next(ti for ti in w.repository.all() if ti.id==t.id).contacts=='Контакт AI'
    assert w.edits['contacts'].toPlainText()=='Контакт AI' and not w.dirty


def test_confirmed_write_is_not_reported_as_rejected_when_refresh_fails(aiwin, monkeypatch):
    w = aiwin
    tender = w.current
    from tender_app.ai_tools import Tools, snapshot
    tenders, specs = snapshot(w.repository)
    proposal = Tools(tenders, specs, 'Измени контакты на Сохранённый контакт',
                     {'kind': 'tender', 'id': tender.id}, True, ()).propose(
        'update', {'kind': 'tender', 'id': tender.id, 'changes': {'contacts': 'Сохранённый контакт'}})
    messages = []
    monkeypatch.setattr(w.ai_service, 'add_message', lambda panel, role, text, *args: messages.append(text))
    monkeypatch.setattr(w, 'refresh_execution', lambda: (_ for _ in ()).throw(OSError('refresh failed')))
    from PySide6.QtWidgets import QDialog

    def confirm(dialog):
        next(b for b in dialog.findChildren(QPushButton) if b.text() == 'Применить').click()
        return dialog.result()

    monkeypatch.setattr(QDialog, 'exec', confirm)
    w.ai_service.preview(proposal, w.assistant)
    assert next(t for t in w.repository.all() if t.id == tender.id).contacts == 'Сохранённый контакт'
    assert proposal.state == 'done'
    assert any('сохранен' in text.lower().replace('ё', 'е') for text in messages)
    assert not any('не перезаписаны' in text for text in messages)

def test_model_answer_text_does_not_perform_a_write(aiwin):
    w=aiwin;before=w.repository.all();p=w.agent_page
    class Claims(Client):
        def complete(self,m):return json.dumps({'kind':'answer','text':'Сохранено моделью'})
    w.ai_service.client_factory=Claims;p.chat.input.setPlainText('Объясни');p.send_message();finish(w)
    assert p.history.active['messages'][-1]['text']=='Сохранено моделью'
    assert w.repository.all()==before
