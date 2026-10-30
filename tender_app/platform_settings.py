"""Platform credentials reuse the application's Windows secret store."""
import hashlib
import json
import os
from pathlib import Path

from .ai_config import AIError, WindowsSecrets


class PlatformSettings:
    def __init__(self, settings, store=None):
        self.settings = settings
        self.store = store or WindowsSecrets()
        self.session = None
        token = hashlib.sha256(str(settings.path.resolve()).casefold().encode('utf-8')).hexdigest()[:24]
        self.target = 'TenderAssistant/Platforms/' + token

    @property
    def profile_dir(self):
        return Path(os.environ.get('LOCALAPPDATA', str(self.settings.path.parent))) / 'Rinako' / 'TenderAssistant' / 'b2b-profile'

    def credentials(self):
        if self.session is not None:
            return dict(self.session)
        if self.settings.get('platform_credentials_session_only', False):
            return {}
        try:
            data = json.loads(self.store.get(self.target) or '{}')
            if not isinstance(data, dict):
                return {}
            return {key: data[key] for key in ('login', 'password', 'key') if isinstance(data.get(key), str)}
        except (AIError, ValueError):
            return {}

    def save(self, credentials, session_only=False):
        values = {key: credentials.get(key, '') for key in ('login', 'password', 'key')}
        values['login'] = values['login'].strip()
        values['key'] = values['key'].strip()
        if any(not value for value in values.values()):
            raise ValueError('Для Tender.Pro нужны логин, пароль и ключ.')
        if session_only:
            # Remove old persisted credentials so they cannot silently reappear next run.
            try:
                self.store.delete(self.target)
            except AIError:
                pass
            self.settings.save(platform_credentials_session_only=True)
            self.session = values
            return 'Доступы Tender.Pro действуют только до закрытия приложения.'
        try:
            self.store.set(self.target, json.dumps(values, ensure_ascii=False))
            self.settings.save(platform_credentials_session_only=False)
            self.session = None
            return 'Доступы Tender.Pro сохранены в хранилище Windows.'
        except AIError:
            self.settings.save(platform_credentials_session_only=True)
            self.session = values
            return 'Хранилище Windows недоступно. Доступы действуют только до закрытия приложения.'


def platform_settings(owner):
    if not hasattr(owner, 'platform_settings'):
        owner.platform_settings = PlatformSettings(owner.settings)
    return owner.platform_settings


def show_platform_settings(owner):
    from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout,
                                  QLabel, QLineEdit, QMessageBox, QVBoxLayout)
    settings = platform_settings(owner)
    dialog = QDialog(owner)
    dialog.setWindowTitle('Площадки — B2B, Tender.Pro, УГМК, ТЭК-Торг')
    dialog.setMinimumWidth(520)
    layout = QVBoxLayout(dialog)
    label = QLabel('B2B-Center: при первой загрузке откроется Chrome/Edge.\n'
                   'Войдите вручную; следующий запуск использует сохранённый вход.\n'
                   'Tender.Pro: укажите доступы ниже.\n'
                   'УГМК и ТЭК-Торг: вход не требуется.\n'
                   'Новые тендеры обрабатываются после импорта из 1С.')
    label.setWordWrap(True)
    layout.addWidget(label)
    form = QFormLayout()
    values = settings.credentials()
    fields = {}
    for key, title in (('login', 'Логин Tender.Pro'), ('password', 'Пароль'), ('key', 'Ключ API')):
        edit = QLineEdit(values.get(key, ''))
        if key != 'login':
            edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(title, edit)
        fields[key] = edit
    layout.addLayout(form)
    session = QCheckBox('Хранить доступы только до закрытия приложения')
    session.setChecked(settings.session is not None)
    layout.addWidget(session)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
    buttons.button(QDialogButtonBox.StandardButton.Save).setText('Сохранить')
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Отмена')
    layout.addWidget(buttons)
    buttons.rejected.connect(dialog.reject)

    def save():
        try:
            message = settings.save({key: edit.text() for key, edit in fields.items()}, session.isChecked())
        except (ValueError, OSError) as error:
            QMessageBox.warning(dialog, 'Доступы не сохранены', str(error))
            return
        QMessageBox.information(dialog, 'Площадки', message)
        dialog.accept()

    buttons.accepted.connect(save)
    try:
        dialog.exec()

    finally:
        dialog.deleteLater()
