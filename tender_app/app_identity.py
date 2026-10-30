"""Shared Windows and Qt identity for the source and portable application."""
import sys
from pathlib import Path

from PySide6.QtGui import QIcon


APP_ID = "Rinako.TenderAssistant.Execution"
ICON_PATH = Path(__file__).resolve().parent / "assets" / "tender-assistant.ico"


def application_icon():
    return QIcon(str(ICON_PATH))


def set_windows_app_id():
    # A pythonw launch must have its own taskbar group rather than Python's icon.
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
