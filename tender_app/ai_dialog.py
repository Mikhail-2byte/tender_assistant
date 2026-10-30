"""One settings entry point; provider, password field and explicit model ID only."""
import os
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,
    QComboBox,QLineEdit,QPushButton,QMessageBox,QFrame,QScrollArea,QWidget,QListWidget,QStackedWidget)
from .ai_config import Profile, AIError
from .ai_models import PROVIDERS,KeyStorageError

class AISettingsDialog(QDialog):
    def __init__(self,service,parent=None):
        super().__init__(parent);self.service=service;self.config=service.settings
        self.setWindowTitle('Настройки');self.resize(800,610);self.setMinimumSize(660,500)
        self.setModal(True);self.changed=False;self.loading=False;self.ident=None;self.saved={}
        root=QVBoxLayout(self);root.setContentsMargins(22,20,22,20);root.setSpacing(18)
        title=QLabel('Настройки');title.setStyleSheet('font-size:18px;font-weight:600;');root.addWidget(title)
        body=QHBoxLayout();body.setSpacing(24);root.addLayout(body,1)
        nav=QListWidget();nav.addItem('Модели');nav.setCurrentRow(0);nav.setFixedWidth(150);nav.setStyleSheet('QListWidget {background:transparent;border:none;} QListWidget::item {padding:10px;border-radius:6px;} QListWidget::item:selected {background:#293345;color:#ecf0f6;}');nav.setFocusPolicy(Qt.FocusPolicy.NoFocus);body.addWidget(nav)
        self.settings_pages=QStackedWidget();body.addWidget(self.settings_pages,1)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QFrame.Shape.NoFrame);self.settings_pages.addWidget(scroll)
        if hasattr(service.window.repository, 'import_excel'):
            from tender_app.database_dialog import DatabaseSettingsPage
            nav.addItem('База данных')
            self.database_page=DatabaseSettingsPage(service.window,self)
            self.settings_pages.addWidget(self.database_page)
        nav.currentRowChanged.connect(self.settings_pages.setCurrentIndex)
        self.navigation=nav
        content=QWidget();scroll.setWidget(content);self.content=QVBoxLayout(content);self.content.setContentsMargins(0,0,0,0);self.content.setSpacing(12)
        heading=QLabel('Модели');heading.setStyleSheet('font-size:18px;font-weight:600;');self.content.addWidget(heading)
        intro=QLabel('Добавьте провайдера, API-ключ и ID модели.\nПереключать модели можно прямо у поля сообщения.');intro.setWordWrap(True);intro.setObjectName('fieldLabel');self.content.addWidget(intro)
        self.cards=QWidget();self.cards_layout=QVBoxLayout(self.cards);self.cards_layout.setContentsMargins(0,0,0,0);self.cards_layout.setSpacing(8);self.content.addWidget(self.cards)
        self.add_button=QPushButton('+ Добавить модель');self.add_button.clicked.connect(self.add_model);self.content.addWidget(self.add_button)
        self.editor=QFrame();self.editor.setObjectName('modelEditor');el=QVBoxLayout(self.editor);el.setContentsMargins(16,16,16,16);el.setSpacing(10)
        self.editor_title=QLabel('Новая модель');self.editor_title.setStyleSheet('font-weight:600;');el.addWidget(self.editor_title)
        form=QFormLayout();form.setVerticalSpacing(12);el.addLayout(form)
        self.provider=QComboBox()
        for code,label in PROVIDERS.items():self.provider.addItem(label,code)
        form.addRow('Провайдер',self.provider)
        self.key=QLineEdit();self.key.setEchoMode(QLineEdit.EchoMode.Password);self.key.setAccessibleName('API-ключ');self.key.setPlaceholderText('Введите ключ');form.addRow('API-ключ',self.key)
        self.key_status=QLabel();self.key_status.setWordWrap(True);self.key_status.setObjectName('fieldLabel');form.addRow('',self.key_status)
        self.model=QLineEdit();self.model.setAccessibleName('ID модели');self.model.setPlaceholderText('Например: openai/gpt-oss-120b');form.addRow('Модель / ID',self.model)
        self.model.setMaxLength(180)
        row=QHBoxLayout();self.test_button=QPushButton('Проверить подключение');self.save_button=QPushButton('Сохранить');self.cancel_button=QPushButton('К списку');row.addWidget(self.test_button);row.addStretch();row.addWidget(self.cancel_button);row.addWidget(self.save_button);el.addLayout(row)
        self.result=QLabel();self.result.setWordWrap(True);el.addWidget(self.result);self.content.addWidget(self.editor)
        note=QLabel('Ключ хранится отдельно от базы и чатов. Тарифы и политика обработки данных задаются в кабинете провайдера. Проверка подключения делает API-запрос без данных базы и может расходовать лимит.');note.setWordWrap(True);note.setObjectName('fieldLabel');self.content.addWidget(note)
        self.content.addStretch(1)
        row=QHBoxLayout();row.addStretch();close=QPushButton('Готово');close.clicked.connect(self.reject);row.addWidget(close);root.addLayout(row)
        self.provider.currentIndexChanged.connect(self.provider_changed);self.key.textChanged.connect(self.mark_changed);self.model.textChanged.connect(self.mark_changed)
        self.save_button.clicked.connect(self.save);self.test_button.clicked.connect(self.network);self.cancel_button.clicked.connect(self.cancel_edit)
        self.setStyleSheet('QFrame#modelEditor {border:1px solid #414b5c;border-radius:10px;} QFrame#modelCard {border:1px solid #343e4d;border-radius:8px;}')
        self.refresh_cards();self.editor.hide()
        if not self.config.entries():self.edit_model()
    def refresh_cards(self):
        while self.cards_layout.count():
            w=self.cards_layout.takeAt(0).widget()
            if w:w.deleteLater()
        for provider,label in PROVIDERS.items():
            entries=[e for e in self.config.entries() if e['provider']==provider]
            if not entries:continue
            for e in entries:
                card=QFrame();card.setObjectName('modelCard');row=QHBoxLayout(card);row.setContentsMargins(12,12,12,12)
                texts=QVBoxLayout();provider_label=QLabel(label+(' · выбрана' if e['id']==self.config.active else ''));provider_label.setStyleSheet('font-weight:600;');texts.addWidget(provider_label)
                model=QLabel(e['model'] or 'Укажите ID модели');model.setTextFormat(Qt.TextFormat.PlainText);model.setWordWrap(True);model.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);texts.addWidget(model)
                row.addLayout(texts,1);edit=QPushButton('Изменить');edit.clicked.connect(lambda checked=False,i=e['id']:self.edit_model(i));row.addWidget(edit);self.cards_layout.addWidget(card)
    def discard_ok(self):
        return not self.changed or QMessageBox.question(self,'Несохранённые настройки','Отбросить изменения в форме модели?',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)==QMessageBox.StandardButton.Yes
    def edit_model(self,ident=None):
        if not self.discard_ok():return
        self.loading=True;self.ident=ident;entry=self.config.entry(ident);self.saved={}
        self.provider.setCurrentIndex(self.provider.findData(entry['provider']) if entry else 0);self.key.clear();self.model.setText(entry['model'] if entry else '')
        self.editor_title.setText('Изменить модель' if entry else 'Новая модель');self.load_provider_key();self.loading=False;self.changed=False
        self.result.setText(self.config.warning or '');self.cards.hide();self.add_button.hide();self.editor.show()
    def add_model(self):self.edit_model()
    def load_provider_key(self):
        provider=self.provider.currentData();entry=self.config.entry(self.ident)
        if not entry or entry['provider']!=provider:entry=next((e for e in self.config.entries() if e['provider']==provider and self.config.key(e)),None)
        key=self.config.key(entry) if entry else ''
        self.saved={'provider':provider,'key':key,'session_only':bool(entry and (entry['credential'] in self.config.session or entry['key_mode']=='session'))}
        self.key.setPlaceholderText('Оставьте пустым, чтобы использовать сохранённый ключ' if key else 'Введите API-ключ')
        self.key_status.setText(('Ключ задан · только на этот сеанс' if self.saved['session_only'] else 'Ключ задан · защищённое хранилище Windows') if key else 'Ключ не задан')
        self.model.setPlaceholderText('Например: openai/gpt-oss-120b' if provider=='groq' else 'Например: vendor/model:free')
    def provider_changed(self,*_):
        if self.loading:return
        self.key.clear();self.model.clear();self.load_provider_key();self.mark_changed()
    def mark_changed(self,*_):
        if not self.loading:self.changed=True;self.result.setText('')
    def value(self):
        key=self.key.text().strip() or self.saved.get('key','')
        if not self.model.text().strip():raise AIError('Укажите точный ID модели.')
        provider=self.provider.currentData();model=self.model.text().strip()
        return Profile(provider=provider,model=model,api_key=key,proxy=self.config.proxy,
            allow_paid=not(provider=='openrouter' and model.endswith(':free')),allow_data_collection=None,allow_contacts=True).validate()
    def save(self):
        try:
            p=self.value();session_only=os.name!='nt' or self.saved.get('session_only',False)
            try:ident=self.config.save_model(p.provider,p.model,p.api_key,self.ident,session_only)
            except KeyStorageError:
                if session_only or self.config.warning:raise
                # Secure storage may be unavailable on managed Windows. No plaintext fallback.
                if QMessageBox.question(self,'Ключ не сохранён','Не удалось сохранить настройки с ключом в хранилище Windows. Использовать ключ только до закрытия программы?',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
                ident=self.config.save_model(p.provider,p.model,p.api_key,self.ident,True)
            self.service.invalidate_consent();self.changed=False;self.edit_model(ident);self.refresh_cards();self.service.refresh_states()
            self.result.setText('Сохранено. '+('Ключ действует только до закрытия программы.' if self.saved['session_only'] else 'Ключ защищён хранилищем Windows.'))
        except (AIError,OSError) as error:self.result.setText(str(error) if isinstance(error,AIError) else 'Не удалось сохранить настройки. Проверьте доступ к папке.')
    def network(self):
        try:p=self.value()
        except AIError as error:self.result.setText(str(error));return
        self.service.auxiliary(self,p)
    def cancel_edit(self):
        if not self.discard_ok():return
        self.changed=False;self.key.clear();self.saved={};self.editor.hide();self.cards.show();self.add_button.show()
    def reject(self):
        if not self.discard_ok():return
        self.key.clear();self.saved={};super().reject()
