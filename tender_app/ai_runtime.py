"""Qt coordinator. One network job, isolated snapshots, no model-confirmed writes."""
from dataclasses import asdict
from copy import deepcopy
from threading import Event
from shiboken6 import isValid
from PySide6.QtCore import QObject,QThread,Signal,QTimer,Qt
from PySide6.QtGui import QPainter,QColor,QPen
from PySide6.QtWidgets import (QPushButton,QComboBox,QMessageBox,QDialog,QVBoxLayout,QHBoxLayout,QPlainTextEdit,QLabel)
from .ai_config import AIError,contains_secret,scrub
from .ai_models import ModelSettings,PROVIDERS
from .ai_client import ModelClient
from .ai_engine import Turn
from .ai_tools import Tools,snapshot,apply_proposal
from .theme import style_record_link
from .search_presentation import link_html
from .ai_usage import UsageLog, summary_text, usage_summary

class ModelChoice(QComboBox):
    """Explicit dropdown indicator; long model IDs never enlarge a helper's minimum width."""
    def setMinimumWidth(self,width):super().setMinimumWidth(0)
    def paintEvent(self,event):
        super().paintEvent(event)
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('#b7c9e8' if self.isEnabled() else '#667389'),1.5))
        x=self.width()-16;y=self.height()//2
        painter.drawLine(x-4,y-2,x,y+2);painter.drawLine(x,y+2,x+4,y-2)
        painter.end()

class Job(QThread):
    result=Signal(object);failed=Signal(str);progress=Signal(str)
    def __init__(self,run,parent=None):
        super().__init__(parent);self.run_task=run;self.cancelled=Event();self.client=None
    def stop(self):
        self.cancelled.set()
        if self.client:self.client.cancel.set()
    def run(self):
        try:
            result=self.run_task(self)
            if not self.cancelled.is_set():self.result.emit(result)
        except AIError as error:
            if not self.cancelled.is_set():self.failed.emit(str(error))
        except BaseException:
            if not self.cancelled.is_set():self.failed.emit('Не удалось выполнить запрос. Технические детали скрыты; ничего не записано.')

class AIService(QObject):
    def __init__(self,window):
        super().__init__(window);self.window=window;self.repository=window.repository
        self.settings=ModelSettings(window.settings.path.parent/'ai-settings.json',proxy=window.settings.get('ai_proxy',''))
        self.consents=set();self.jobs=[];self.active=None;self.sources={};self.pending_dialog=None
        self.client_factory=ModelClient
        self.usage_log=UsageLog(window.settings.path.parent/'ai-usage.jsonl')
        self.attach(window.assistant,lambda:{'kind':'tender','id':window.current.id} if window.current and window.current.id else None)
        self.attach(window.execution_assistant,self.execution_context)
        self.attach(window.agent_page.chat,lambda:None,window.agent_page)
        self.refresh_states()
    def execution_context(self):
        sid=self.window.execution.current_specification_id
        return {'kind':'spec','id':sid} if sid is not None else None
    def attach(self,panel,context,page=None):
        state={'context':context,'page':page,'history':[],'busy':False,'request':None}
        self.sources[panel]=state
        old=panel.model_selector;old.hide()
        try:old.currentIndexChanged.disconnect()
        except RuntimeError:pass
        select=ModelChoice();select.setObjectName('chatModel');panel.model_selector=select
        select.setStyleSheet('QComboBox {padding-right:28px;} QComboBox::down-arrow {image:none;}')
        select.setAccessibleName('Модель агента');select.setToolTip('Выбор сохранённой модели. Добавить модель: меню слева сверху → Настройки → Модели.')
        select.setMinimumWidth(0);select.setMaximumWidth(16777215)
        select.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon);select.setMinimumContentsLength(12)
        # Keep the indicator, model and Send together in every chat.
        footer=panel.composer.layout().itemAt(1).layout()
        footer.insertWidget(footer.count()-1,select)
        if page:
            select.setMaximumWidth(340)
        else:
            footer.setStretch(1,0)
            footer.setStretch(footer.indexOf(select),1)
        select.show()
        stop=QPushButton('Остановить');stop.hide();stop.clicked.connect(lambda:self.cancel(panel))
        usage=QPushButton('Расход AI');usage.setObjectName('aiUsage');usage.clicked.connect(self.show_usage)
        usage.setToolTip('Токены по данным API, отдельные обращения, ошибки и лимиты')
        accounting=QHBoxLayout();accounting.addWidget(usage);accounting.addStretch();accounting.addWidget(stop)
        panel.composer.layout().addLayout(accounting)
        state.update(select=select,stop=stop,usage=usage)
        select.currentIndexChanged.connect(lambda:self.select_model(panel))
        panel.ai_check=self.check_secret
        panel.ai_submit=lambda text:self.submit(panel,text)
        panel.input.setToolTip('Enter — отправить · Shift+Enter — новая строка. Модель выбирается под полем сообщения.')
        if page:
            page.ai_submit=lambda text:self.submit(panel,text)
            page.ai_scrub=lambda text:scrub(text,self.settings.secrets())
            page.history_note.setText('История на этом компьютере · изменения только после подтверждения')
        panel.examples_toggle.hide();panel.examples.hide()
    def check_secret(self,text):
        if contains_secret(text,self.settings.secrets()):
            QMessageBox.warning(self.window,'Секрет не отправлен','Похоже на API-ключ. Удалите его из черновика и используйте «Настройки → Модели». Сообщение не отправлено и не добавлено в переписку.')
            return False
        return True
    def select_model(self,panel):
        ident=self.sources[panel]['select'].currentData()
        if not ident or ident==self.settings.active:return
        if self.busy():self.refresh_states();return
        try:self.settings.select(ident)
        except AIError as error:QMessageBox.warning(self.window,'Модель не выбрана',str(error))
        self.refresh_states()
    def refresh_states(self):
        entries=self.settings.entries()
        for panel,state in self.sources.items():
            select=state['select'];select.blockSignals(True);select.clear()
            for provider,label in PROVIDERS.items():
                group=[e for e in entries if e['provider']==provider and e['model']]
                if not group:continue
                select.addItem(label,None);select.model().item(select.count()-1).setEnabled(False)
                for e in group:select.addItem(e['model'],e['id'])
            if not select.count():select.addItem('Модель не настроена',None)
            index=select.findData(self.settings.active) if self.settings.active else 0
            select.setCurrentIndex(max(0,index));select.blockSignals(False)
            select.setEnabled(bool(entries) and not self.busy())
            if state['busy']:continue
            p=self.settings.profile()
            self.status(panel,'ready' if p.api_key else 'offline',
                (PROVIDERS[p.provider]+' / '+p.model+' · готова к запросу') if p.api_key else
                'AI не настроен. Локальные команды доступны. Модели: меню → Настройки → Модели.')
    def status(self,panel,state,details):
        indicator=panel.model_state
        color={'ready':'#8bdbb3','working':'#edc184','error':'#f19b9f','offline':'#8993a3'}[state]
        indicator.clear();indicator.setFixedSize(10,10)
        indicator.setStyleSheet(f'background-color:{color};border:none;border-radius:5px;')
        indicator.setProperty('aiState',state)
        indicator.setToolTip(details);indicator.setAccessibleName(details)
        if state=='working':self.window.statusBar().showMessage(details)
        elif self.sources.get(panel,{}).get('request'):self.window.statusBar().showMessage(details,5000)
    def invalidate_consent(self):self.consents.clear()
    def new_client(self,p,purpose='dialog'):
        client=self.client_factory(p)
        client.usage_sink=self.usage_log.append;client.purpose=purpose
        return client
    def finish_usage(self,panel,client):
        events=getattr(client,'usage_events',[])
        summary=usage_summary(events)
        button=self.sources[panel]['usage']
        if summary['calls']:
            count=str(summary['total_tokens']) if not summary['unknown'] else str(summary['total_tokens'])+'+?'
            button.setText('Расход AI · '+count+' ток.')
            button.setToolTip('Последний вопрос: '+summary_text(events))
        if self.usage_log.warning:
            button.setText('Расход AI · журнал не сохранён')
            button.setToolTip(self.usage_log.warning)
    def show_usage(self):
        dialog=QDialog(self.window);dialog.setWindowTitle('Расход AI');dialog.resize(940,620)
        layout=QVBoxLayout(dialog)
        path=QLabel('Журнал: '+str(self.usage_log.path));path.setTextFormat(Qt.TextFormat.PlainText)
        path.setWordWrap(True);path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);layout.addWidget(path)
        report=QPlainTextEdit();report.setReadOnly(True);report.setPlainText(self.usage_log.report());layout.addWidget(report,1)
        row=QHBoxLayout();refresh=QPushButton('Обновить');refresh.clicked.connect(lambda:report.setPlainText(self.usage_log.report()))
        close=QPushButton('Закрыть');close.clicked.connect(dialog.accept);row.addWidget(refresh);row.addStretch();row.addWidget(close);layout.addLayout(row)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()
    def open_settings(self):
        from .ai_dialog import AISettingsDialog
        dialog=AISettingsDialog(self,self.window)
        try:
            dialog.exec()
        finally:
            for job in self.jobs:
                if getattr(job,'ui_dialog',None) is dialog:job.stop()
            dialog.deleteLater()
            self.refresh_states()
    def consent(self,p,parent,operation='Диалог'):
        signature=tuple(asdict(p).values())
        if signature in self.consents:return True
        details=(f'{operation}\nПровайдер: {PROVIDERS[p.provider]}\nМодель: {p.model}\n\n'
            'Вопрос, до 8 сообщений текущего диалога и запрошенные данные (включая контакты при запросе) отправятся провайдеру. Вся база и файлы автоматически не отправляются.\n\n'
            'Тарификация и политика обработки данных определяются вашим аккаунтом провайдера. Программа не переключает их и не гарантирует бесплатный запрос. Изменения в базе — только после вашего подтверждения.\n\n'
            'Разрешить запросы с этими настройками в текущем сеансе?')
        ok=QMessageBox.question(parent,'Подключение к AI',details,QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)==QMessageBox.StandardButton.Yes
        if ok:self.consents.add(signature)
        return ok
    def panel_busy(self,panel,busy):
        s=self.sources[panel];s['busy']=busy;panel.responding=busy;panel.update_send_button();s['stop'].setVisible(busy)
        for state in self.sources.values():state['select'].setEnabled(False if busy else bool(self.settings.entries()))
        page=s['page']
        if page:
            for w in (page.chat_selector,page.new_button,page.delete_button):w.setEnabled(not busy)
    def busy(self):return bool(self.jobs)
    def cancel(self,panel):
        s=self.sources[panel]
        if s['request']:
            s['request']['job'].stop();self.status(panel,'working','Остановка… ожидаю завершения сетевого запроса')
            s['stop'].setEnabled(False)
    def start(self,fn,on_result,on_error,on_finished):
        if self.busy():raise AIError('Другой запрос ещё завершается. Дождитесь ответа или таймаута.')
        job=Job(fn,self);self.jobs.append(job);self.active=job
        # Cancellation can arrive after the worker emitted its result but before
        # the GUI thread processes the queued signal.
        job.result.connect(lambda value: None if job.cancelled.is_set() else on_result(value))
        job.failed.connect(lambda message: None if job.cancelled.is_set() else on_error(message))
        def finish():
            try:
                on_finished(job.cancelled.is_set())
            finally:
                if job in self.jobs:self.jobs.remove(job)
                if self.active is job:self.active=None
                job.deleteLater()
        job.finished.connect(finish);job.start();return job
    def add_message(self,panel,role,text,links=None):
        state=self.sources[panel];page=state['page'];text=scrub(text,self.settings.secrets())
        message={'role':role,'text':text,'links':links or []}
        if page:
            chat=page.history.active
            if len(chat['messages'])>=page.history.MAX_MESSAGES:return
            if not chat['messages'] and role=='user':chat['title']=' '.join(text.split())[:48]
            chat['messages'].append(message);page.history_dirty=True;page.persist();page.refresh_selector();page.render_chat()
        else:
            state['history'].append(message);state['history']=state['history'][-20:]
            frame=panel.message('Вы' if role=='user' else 'Агент',text)
            for link in links or []:
                html=link_html(link)
                if html:
                    label=QLabel(html);label.setWordWrap(True);label.setMinimumWidth(0)
                    label.setTextFormat(Qt.TextFormat.RichText);frame.layout().addWidget(label)
                button=QPushButton(link['label']);style_record_link(button,link['kind']);frame.layout().addWidget(button)
                button.clicked.connect(lambda checked=False,l=link:self.open_link(l))
    def open_link(self,link):
        try:
            if link['kind']=='tender':self.window.open_execution_tender(link['id'])
            elif type(link.get('position_id')) is int:self.window.open_search_position(link['id'],link['position_id'])
            else:self.window.open_specification(link['id'])
        except (ValueError,StopIteration):QMessageBox.information(self.window,'Запись не найдена','Повторите поиск: запись могла измениться.')
    def submit(self,panel,text,model_id=None,retry=False):
        text=text.strip();s=self.sources[panel];page=s['page']
        if not text:return
        secrets=self.settings.secrets()
        if contains_secret(text,secrets):
            QMessageBox.warning(self.window,'Секрет не отправлен','Похоже на API-ключ. Он не добавлен в переписку. Удалите его из поля и используйте «Настройки → Модели».');return
        if len(text)>8000:QMessageBox.warning(self.window,'Длинный вопрос','Максимум 8000 символов; черновик сохранён.');return
        if self.busy():QMessageBox.information(self.window,'Запрос выполняется','Дождитесь завершения текущего запроса. Черновик не изменён.');return
        if page and len(page.history.active['messages'])+4>page.history.MAX_MESSAGES:page.show_warning('Диалог заполнен. Создайте новый; старые сообщения не удалялись.');return
        name=model_id or self.settings.active;p=self.settings.profile(name)
        if not p.api_key or not p.model:
            QMessageBox.information(self.window,'Модель не настроена','Откройте меню слева сверху → Настройки → Модели. Укажите провайдера, ключ и ID модели. Локальные команды работают без ключа.');return
        if not self.consent(p,self.window):return
        prior=deepcopy(page.history.active['messages'] if page else s['history'])
        ctx=deepcopy(s['context']())
        tenders,specs=snapshot(self.repository)
        tools=Tools(tenders,specs,text,ctx,p.allow_contacts,secrets)
        def run(job):
            client=self.new_client(p);job.client=client;client.progress=job.progress.emit
            if job.cancelled.is_set():client.cancel.set()
            return Turn(client,tools,prior,job.progress.emit).run(text)
        if not retry:
            if page:
                page.loading=True;panel.input.clear();page.loading=False;page.history.active['draft']=''
            else:panel.input.clear()
            self.add_message(panel,'user',text)
        self.panel_busy(panel,True);s['stop'].setEnabled(True);self.status(panel,'working','Подключается…')
        request={'text':text,'profile':name,'context':ctx,'job':None};s['request']=request
        def done(result):
            if s['request'] is not request:return
            self.add_message(panel,'assistant',result['text'],result.get('links',[]))
            self.status(panel,'ready','Ответ получен · '+PROVIDERS[p.provider]+' / '+result['model'])
            if result.get('proposal'):
                proposal=result['proposal'];self.add_message(panel,'assistant',proposal.preview)
                # Modal preview cannot be replaced by navigation or a second question.
                QTimer.singleShot(0,self,lambda:self.preview(proposal,panel))
        def error(message):
            self.add_message(panel,'assistant','Ошибка AI: '+scrub(message,secrets)+'\nНичего не записано. Запрос сохранён; черновик не изменён.')
            self.status(panel,'error','Ошибка · '+PROVIDERS[p.provider]+': '+scrub(message,secrets))
            frame=panel.message('Повтор запроса','Повтор отправит исходный вопрос. Новый черновик останется на месте.')
            again=QPushButton('Повторить');frame.layout().addWidget(again)
            def repeat(target):
                if self.busy():
                    QMessageBox.information(self.window,'Запрос завершается','Дождитесь завершения текущего запроса и повторите.');return
                if s['context']()!=ctx:
                    QMessageBox.information(self.window,'Контекст изменился','Выбрана другая карточка. Вернитесь к исходной или задайте новый вопрос.');return
                again.setEnabled(False)
                self.submit(panel,text,target,True)
                if not s['busy']:again.setEnabled(True)
            again.clicked.connect(lambda:repeat(self.settings.active))
        def finish(cancelled):
            self.finish_usage(panel,request['job'].client)
            self.panel_busy(panel,False);s['stop'].setEnabled(True)
            if cancelled:self.add_message(panel,'assistant','Запрос остановлен. Изменения не применялись. Провайдер мог уже обработать запрос и учесть его в лимите.');self.status(panel,'offline','Запрос остановлен')
            if s['request'] is request:s['request']=None
        job=self.start(run,done,error,finish);request['job']=job
        job.progress.connect(lambda text:self.status(panel,'working',text))
    def auxiliary(self,dialog,p):
        if not p.api_key:dialog.result.setText('Введите ключ. Проверка не выполнена.');return
        if not self.consent(p,dialog,'Проверка подключения'):return
        if self.busy():dialog.result.setText('Другой запрос ещё выполняется. Дождитесь завершения.');return
        dialog.result.setText('Проверяю…');dialog.test_button.setEnabled(False)
        def run(job):
            client=self.new_client(p,'connection_test');job.client=client;client.progress=job.progress.emit
            if job.cancelled.is_set():client.cancel.set()
            client.connect()
            reply=client.complete([{'role':'system','content':'Верни один JSON-объект {"kind":"answer","text":"Подключение работает"} без инструментов.'},{'role':'user','content':'Проверка связи без данных базы.'}])
            from .ai_client import strict_json
            data=strict_json(reply)
            if not isinstance(data,dict) or data.get('kind')!='answer' or not isinstance(data.get('text'),str):raise AIError('API доступен, но модель не соблюдает требуемый JSON-протокол.')
            return client.model
        def done(model):
            if isValid(dialog) and dialog.isVisible():dialog.result.setText('Подключение работает: '+PROVIDERS[p.provider]+' / '+model+'. Данные базы не передавались. Проверка не сохраняет изменения формы.')
        def error(msg):
            if isValid(dialog) and dialog.isVisible():dialog.result.setText('Ошибка: '+scrub(msg,(p.api_key,)))
        def finish(cancelled):
            if isValid(dialog) and dialog.isVisible():
                dialog.test_button.setEnabled(True)
                if cancelled:dialog.result.setText('Проверка остановлена.')
                if job.client:
                    dialog.result.setText(dialog.result.text()+'\n'+summary_text(getattr(job.client,'usage_events',[])))
                if self.usage_log.warning:dialog.result.setText(dialog.result.text()+'\n'+self.usage_log.warning)
        job=self.start(run,done,error,finish)
        job.ui_dialog=dialog
        job.progress.connect(dialog.result.setText)
    def preview(self,p,panel):
        if p.state!='pending':return
        if getattr(self.window,'_closed',False):p.state='cancelled';return
        dialog=QDialog(self.window);dialog.setWindowTitle('Предложение агента · ещё не записано');dialog.resize(720,510);layout=QVBoxLayout(dialog)
        label=QLabel('Проверьте карточку и изменения. Только кнопка «Применить» выполнит запись.');label.setWordWrap(True);layout.addWidget(label)
        text=QPlainTextEdit();text.setReadOnly(True);text.setPlainText(p.preview);layout.addWidget(text,1)
        row=QHBoxLayout();apply=QPushButton('Применить');cancel=QPushButton('Отменить');row.addStretch();row.addWidget(cancel);row.addWidget(apply);layout.addLayout(row)
        self.pending_dialog=dialog
        cancel.clicked.connect(dialog.reject)
        def confirm():
            apply.setEnabled(False)
            if not self.window.guard_changes():apply.setEnabled(True);return
            try:
                apply_proposal(self.repository,p)
            except Exception:
                self.add_message(panel,'assistant','Запись отклонена или не удалась. Возможно, карточка изменилась. Данные не перезаписаны; подготовьте новый предпросмотр.')
                dialog.reject()
                return
            message='Сохранено после подтверждения пользователя: '+p.label
            try:
                self.window.tenders=self.repository.all();self.window.refresh_execution()
                if self.window.current and self.window.current.id is not None:
                    fresh=next(t for t in self.window.tenders if t.id==self.window.current.id);self.window.load_tender(fresh)
            except Exception:
                message+='\nДанные сохранены, но интерфейс не обновлён. Перезапустите приложение; не повторяйте запись.'
            self.add_message(panel,'assistant',message)
            self.window.statusBar().showMessage(message,15000);dialog.accept()
        apply.clicked.connect(confirm)
        try:
            if dialog.exec()!=QDialog.DialogCode.Accepted and p.state=='pending':p.state='cancelled';self.add_message(panel,'assistant','Предложение отменено. Ничего не записано.')
        finally:
            self.pending_dialog=None
            dialog.deleteLater()
