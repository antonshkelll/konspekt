"""Render demo widgets without opening audio or contacting any provider."""
import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase
from konspekt import config
from konspekt.app import MainWindow, SettingsDialog, STYLE

app = QApplication([])
for font in ("segoeui.ttf", "segoeuib.ttf"):
    QFontDatabase.addApplicationFont(str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / font))
app.setStyleSheet(STYLE)
out = Path(__file__).resolve().parent.parent / "output"
out.mkdir(exist_ok=True)
with patch.object(config, "load", lambda: dict(config.DEFAULTS)), patch.object(config, "save"):
    win = MainWindow()
    win.context.setPlainText("Демо · обсуждение запуска продукта. Цель: согласовать этапы и сроки.")
    win.attachments["План проекта.md"] = "Пилот: две недели. Метрика: время подготовки заметок."
    win.files_label.setText("План проекта.md · 1 файл контекста")
    win.on_text(0, "Собеседники", "Как предлагаете оценивать результат пилота?", True)
    win.on_text(0, "Я", "Давайте зафиксируем исходное время подготовки заметок.", True)
    win.ai_view.setMarkdown("**Пример подсказки · демонстрационные данные**\n\nПредлагаю сравнить время подготовки заметок до пилота и после него.\n\n- Период пилота: две недели.\n- Основная метрика: время подготовки заметок.\n- Источник: План проекта.md.\n\nУточнение: какой результат команда будет считать достаточным?")
    win.show()
    app.processEvents()
    win.grab().save(str(out / "beta-preview.png"))
    dlg = SettingsDialog(win.cfg, win)
    dlg.show()
    app.processEvents()
    dlg.grab().save(str(out / "settings-preview.png"))
    dlg.close()
    win.lines.clear()
    win.close()
