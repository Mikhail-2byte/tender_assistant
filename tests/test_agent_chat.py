"""V4 standalone agent, fictional data only; no network/model required."""
import json
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from tender_app.agent_chat import AgentHistory, AgentPage
from tender_app.execution_seed import create_demo_database
from tender_app.execution_repository import ExecutionRepository
from tender_app.execution_window import ExecutionWindow
from tender_app.settings import Settings

@pytest.fixture
def agent(tmp_path, app):
    create_demo_database(tmp_path/'fictional.db')
    repo=ExecutionRepository(tmp_path/'fictional.db')
    page=AgentPage(repo,tmp_path/'agent-chat.json')
    page.resize(960,700);page.show()
    yield page
    page.flush();page.close();repo.close();app.processEvents()

def test_unused_history_not_written(agent):
    assert agent.flush()
    assert not agent.history.path.exists()

def test_local_commands_read_only_and_search_links(agent):
    before=agent.repository.all(),agent.repository.specifications()
    for command in ['Сводка исполнения','Просроченные поставки','Сроки','Подачи сегодня']:
        reply,links=agent.local_reply(command)
        assert reply
    reply,links=agent.local_reply('Найди ДЕМО-101')
    assert {l['kind'] for l in links} == {'tender','spec'}
    opened=[];agent.tender_requested.connect(lambda i:opened.append(('tender',i)))
    agent.specification_requested.connect(lambda i:opened.append(('spec',i)))
    for link in links:agent.open_record(link)
    assert opened==[(l['kind'],l['id']) for l in links]
    assert before==(agent.repository.all(),agent.repository.specifications())

def test_free_form_honest_and_stored(agent):
    agent.chat.input.setPlainText('Напиши письмо заказчику');agent.send_message()
    messages=agent.history.active['messages']
    assert len(messages)==2
    assert 'не подключена' in messages[-1]['text']
    assert 'не обработан' in messages[-1]['text']
    assert AgentHistory(agent.history.path).active['messages']==messages

def test_drafts_and_chat_switch_restored(agent):
    agent.chat.input.setPlainText('первый черновик');agent.new_chat()
    agent.chat.input.setPlainText('второй черновик');agent.flush()
    agent.chat_selector.setCurrentIndex(1)
    assert agent.chat.input.toPlainText()=='первый черновик'
    assert AgentHistory(agent.history.path).active['draft']=='первый черновик'

def test_commands_do_not_overwrite_draft(agent):
    agent.chat.input.setPlainText('не отправлен')
    agent.run_quick('Сводка исполнения');agent.submit_search('ДЕМО-101')
    assert agent.chat.input.toPlainText()=='не отправлен'
    assert not agent.history.active['messages']

def test_enter_send_shift_enter_newline(agent):
    agent.chat.input.setFocus();agent.chat.input.setPlainText('Сводка')
    QTest.keyClick(agent.chat.input,Qt.Key.Key_Return,Qt.KeyboardModifier.ShiftModifier)
    assert '\n' in agent.chat.input.toPlainText()
    QTest.keyClick(agent.chat.input,Qt.Key.Key_Return)
    assert len(agent.history.active['messages'])==2
    agent.send_message();assert len(agent.history.active['messages'])==2

def test_stale_link_safe(agent):
    agent.open_record(dict(kind='spec',id=-999,label='stale'))
    assert 'больше не найдена' in agent.warning.text()

def test_save_failure_keeps_messages_and_retry(agent,monkeypatch):
    save=agent.history.save
    monkeypatch.setattr(agent.history,'save',lambda:(_ for _ in ()).throw(OSError('disk unavailable')))
    agent.run_quick('Сводка')
    assert len(agent.history.active['messages'])==2 and agent.history_dirty
    assert 'Не удалось сохранить' in agent.warning.text()
    monkeypatch.setattr(agent.history,'save',save)
    assert agent.flush() and not agent.history_dirty

def test_corrupt_history_never_overwritten(tmp_path):
    path=tmp_path/'agent-chat.json';path.write_text('{bad')
    h=AgentHistory(path)
    with pytest.raises(ValueError):h.save()
    assert h.warning and path.read_text()=='{bad'

def test_caps_no_pruning_and_long_draft(agent):
    draft='Я'*14000;agent.chat.input.setPlainText(draft)
    agent.send_message();assert agent.chat.input.toPlainText()==draft
    agent.flush();assert AgentHistory(agent.history.path).active['draft']==draft
    agent.history.active['messages']=[dict(role='user',text='x',links=[]) for _ in range(200)]
    agent.chat.input.setPlainText('Сводка');agent.send_message()
    assert len(agent.history.active['messages'])==200
    while len(agent.history.chats)<10:agent.history.create()
    ids=[c['id'] for c in agent.history.chats];agent.new_chat()
    assert [c['id'] for c in agent.history.chats]==ids

def test_fullpage_navigation_and_draft(tmp_path,app):
    create_demo_database(tmp_path/'fictional.db');repo=ExecutionRepository(tmp_path/'fictional.db')
    w=ExecutionWindow(repo,Settings(tmp_path/'settings.json'),tmp_path)
    w.resize(1024,740);w.show();w.navigation_buttons[2].click();QTest.qWait(50)
    assert w.pages.currentIndex()==2 and w.agent_page.isVisible()
    assert not w.agent_page.chat.model_state.wordWrap()
    w.agent_page.chat.input.setPlainText('мой черновик')
    w.pages.setCurrentIndex(1);w.pages.setCurrentIndex(2)
    assert w.agent_page.chat.input.toPlainText()=='мой черновик'
    assert w.agent_page.chat.scroll.horizontalScrollBar().maximum()==0
    w.close();repo.close()


def test_chat_header_labels_and_legacy_empty_title(agent):
    from PySide6.QtWidgets import QLabel
    assert agent.new_button.text() == "Новый чат"
    assert any(label.text() == "История чатов" for label in agent.findChildren(QLabel))
    assert agent.chat_selector.currentText() == "Без названия"
    agent.history.active['title'] = 'Новый диалог'
    agent.refresh_selector()
    assert agent.chat_selector.currentText() == "Без названия"
    agent.run_quick('Сводка исполнения')
    assert agent.chat_selector.currentText() == 'Сводка исполнения'
