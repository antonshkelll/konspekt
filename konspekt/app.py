"""Главное окно Konspekt: расшифровка встречи слева, ИИ-панель справа."""
import html
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QPushButton, QSplitter, QTextBrowser, QVBoxLayout, QWidget,
)

from . import account, config, llm
from .capture import Recorder, list_microphones

CATCHUP_SECONDS = 5 * 60

STYLE = """
QWidget { background: #14151a; color: #e6e7eb; font-family: 'Segoe UI'; font-size: 10.5pt; }
QTextBrowser, QPlainTextEdit, QLineEdit, QComboBox {
    background: #1d1f26; border: 1px solid #2c2f3a; border-radius: 8px; padding: 6px;
}
QPushButton {
    background: #262935; border: 1px solid #333747; border-radius: 8px; padding: 7px 14px;
}
QPushButton:hover { background: #303446; }
QPushButton:disabled { color: #6b6f7d; }
QPushButton#record { background: #5b5bf0; border: none; font-weight: 600; }
QPushButton#record:hover { background: #6d6df5; }
QPushButton#record[active="true"] { background: #e5484d; }
QLabel#muted { color: #8b8fa0; }
QLabel#live { color: #ff6b6f; font-weight: 600; }
QSplitter::handle { background: #14151a; width: 8px; }
"""


def resource(name: str) -> str:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return str(base / "assets" / name)


class Bridge(QObject):
    """Переносит события из фоновых потоков в поток интерфейса."""
    text = Signal(str, str, bool)
    status = Signal(str)
    ai_delta = Signal(str)
    ai_done = Signal(str)


class SettingsDialog(QDialog):
    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self.setMinimumWidth(460)
        self.cfg = cfg
        form = QFormLayout(self)

        self.dg = QLineEdit(cfg["deepgram_key"])
        self.dg.setEchoMode(QLineEdit.Password)
        self.orr = QLineEdit(cfg["openrouter_key"])
        self.orr.setEchoMode(QLineEdit.Password)
        self.model = QLineEdit(cfg["llm_model"])
        self.fast = QLineEdit(cfg["fast_model"])
        self.stt = QLineEdit(cfg["stt_model"])

        self.mic = QComboBox()
        self.mic.addItem("Микрофон по умолчанию", None)
        rec = Recorder(cfg, lambda *a: None, lambda *a: None)
        try:
            for idx, name in list_microphones(rec.p):
                self.mic.addItem(name, idx)
        finally:
            rec.close()
        pos = self.mic.findData(cfg["mic_device"])
        self.mic.setCurrentIndex(max(pos, 0))

        self.system = QCheckBox("Записывать звук собеседников (системный звук)")
        self.system.setChecked(cfg["capture_system"])
        self.hint = QCheckBox("Напоминать предупредить участников о расшифровке")
        self.hint.setChecked(cfg["consent_hint"])

        form.addRow("Ключ Deepgram", self.dg)
        form.addRow("Ключ OpenRouter", self.orr)
        form.addRow("Основная модель", self.model)
        form.addRow("Быстрая модель", self.fast)
        form.addRow("Модель речи", self.stt)
        form.addRow("Микрофон", self.mic)
        form.addRow(self.system)
        form.addRow(self.hint)
        check = QPushButton("Проверить ключи и баланс")
        check.clicked.connect(self.check_keys)
        self.check_result = QLabel("")
        self.check_result.setObjectName("muted")
        self.check_result.setWordWrap(True)
        form.addRow(check)
        form.addRow(self.check_result)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def check_keys(self):
        self.check_result.setText("Проверяю…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        lines = []
        for fn, key in ((account.check_deepgram, self.dg.text().strip()),
                        (account.check_openrouter, self.orr.text().strip())):
            try:
                lines.append(fn(key))
            except Exception as e:  # noqa: BLE001
                lines.append(f"Не удалось проверить: {e}")
        QApplication.restoreOverrideCursor()
        self.check_result.setText("\n".join(lines))

    def apply(self):
        self.cfg.update(
            deepgram_key=self.dg.text().strip(),
            openrouter_key=self.orr.text().strip(),
            llm_model=self.model.text().strip() or config.DEFAULTS["llm_model"],
            fast_model=self.fast.text().strip() or config.DEFAULTS["fast_model"],
            stt_model=self.stt.text().strip() or config.DEFAULTS["stt_model"],
            mic_device=self.mic.currentData(),
            capture_system=self.system.isChecked(),
            consent_hint=self.hint.isChecked(),
        )
        config.save(self.cfg)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg = config.load()
        self.bridge = Bridge()
        self.bridge.text.connect(self.on_text)
        self.bridge.status.connect(self.on_status)
        self.bridge.ai_delta.connect(self.on_ai_delta)
        self.bridge.ai_done.connect(self.on_ai_done)

        self.recorder: Recorder | None = None
        self.lines: list[tuple[float, str, str]] = []  # (время, кто, текст)
        self.interim: dict[str, str] = {}
        self.started_at = 0.0
        self.answer = ""
        self._ai_token = None

        self.setWindowTitle("Konspekt — помощник для встреч")
        self.setWindowIcon(QIcon(resource("icon.ico")))
        self.resize(1100, 680)
        self._build()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)

    # ---------- интерфейс ----------
    def _build(self):
        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(14, 12, 14, 12)

        top = QHBoxLayout()
        self.rec_btn = QPushButton("●  Начать запись")
        self.rec_btn.setObjectName("record")
        self.rec_btn.clicked.connect(self.toggle_record)
        self.live = QLabel("")
        self.live.setObjectName("live")
        self.clock = QLabel("")
        self.clock.setObjectName("muted")
        export = QPushButton("Сохранить заметки")
        export.clicked.connect(self.export)
        clear = QPushButton("Новая встреча")
        clear.clicked.connect(self.new_meeting)
        settings = QPushButton("Настройки")
        settings.clicked.connect(self.open_settings)
        top.addWidget(self.rec_btn)
        top.addWidget(self.live)
        top.addWidget(self.clock)
        top.addStretch()
        for b in (clear, export, settings):
            top.addWidget(b)
        lay.addLayout(top)

        split = QSplitter(Qt.Horizontal)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("Расшифровка"))
        self.transcript = QTextBrowser()
        self.transcript.setPlaceholderText("Нажми «Начать запись» — здесь появятся реплики участников.")
        ll.addWidget(self.transcript)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        ll.addWidget(self.status)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(QLabel("О чём встреча (необязательно)"))
        self.context = QPlainTextEdit(self.cfg["meeting_context"])
        self.context.setPlaceholderText("Повестка, участники, термины проекта — так ИИ лучше поймёт разговор.")
        self.context.setMaximumHeight(90)
        rl.addWidget(self.context)

        presets = QHBoxLayout()
        for label, key in (("Итоги и задачи", "summary"), ("Что я пропустил?", "catchup"),
                           ("Письмо после встречи", "followup")):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, k=key: self.ask_preset(k))
            presets.addWidget(b)
        rl.addLayout(presets)

        ask = QHBoxLayout()
        self.question = QLineEdit()
        self.question.setPlaceholderText("Спроси что-нибудь о встрече…")
        self.question.returnPressed.connect(self.ask_free)
        send = QPushButton("Спросить")
        send.clicked.connect(self.ask_free)
        ask.addWidget(self.question)
        ask.addWidget(send)
        rl.addLayout(ask)

        self.ai_view = QTextBrowser()
        self.ai_view.setOpenExternalLinks(True)
        self.ai_view.setPlaceholderText("Здесь появятся ответы ИИ.")
        rl.addWidget(self.ai_view)

        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([600, 500])
        lay.addWidget(split)
        self.setCentralWidget(root)

    def _tick(self):
        if self.recorder:
            sec = int(time.time() - self.started_at)
            self.clock.setText(f"{sec // 60:02d}:{sec % 60:02d}")

    # ---------- запись ----------
    def toggle_record(self):
        if self.recorder:
            self.stop_record()
            return
        if not self.cfg["deepgram_key"]:
            QMessageBox.information(self, "Нужен ключ", "Укажи ключ Deepgram в настройках.")
            self.open_settings()
            return
        if self.cfg["consent_hint"]:
            box = QMessageBox(self)
            box.setWindowTitle("Перед началом")
            box.setText("Предупреди участников, что ведёшь расшифровку встречи.")
            never = QCheckBox("Больше не напоминать")
            box.setCheckBox(never)
            box.exec()
            if never.isChecked():
                self.cfg["consent_hint"] = False
                config.save(self.cfg)
        self.recorder = Recorder(self.cfg, self.bridge.text.emit, self.bridge.status.emit)
        try:
            self.recorder.start()
        except Exception as e:  # noqa: BLE001
            self.recorder.close()
            self.recorder = None
            QMessageBox.warning(self, "Не удалось начать", f"Ошибка: {e}")
            return
        if not self.started_at:
            self.started_at = time.time()
        self.rec_btn.setText("■  Остановить")
        self.rec_btn.setProperty("active", True)
        self.rec_btn.style().polish(self.rec_btn)
        self.live.setText("● Идёт расшифровка")
        self.status.setText("")

    def stop_record(self):
        rec, self.recorder = self.recorder, None
        threading.Thread(target=rec.close, daemon=True).start()
        self.interim.clear()
        self.rec_btn.setText("●  Продолжить запись")
        self.rec_btn.setProperty("active", False)
        self.rec_btn.style().polish(self.rec_btn)
        self.live.setText("")
        self.render_transcript()

    def on_text(self, speaker: str, text: str, final: bool):
        if final:
            self.interim.pop(speaker, None)
            self.lines.append((time.time(), speaker, text))
        else:
            self.interim[speaker] = text
        self.render_transcript()

    def on_status(self, msg: str):
        self.status.setText(msg)

    def render_transcript(self):
        bar = self.transcript.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 20
        rows = []
        for ts, who, text in self.lines:
            rows.append(self._row(ts, who, text, "#e6e7eb"))
        for who, text in self.interim.items():
            rows.append(self._row(time.time(), who, text, "#7d8191"))
        self.transcript.setHtml("".join(rows))
        if at_bottom:
            bar.setValue(bar.maximum())

    @staticmethod
    def _row(ts: float, who: str, text: str, color: str) -> str:
        who_color = "#8f8ff7" if who == "Я" else "#4fc3a1"
        t = datetime.fromtimestamp(ts).strftime("%H:%M")
        return (f'<p style="margin:0 0 8px 0"><span style="color:#5d6070">{t}</span> '
                f'<b style="color:{who_color}">{who}:</b> '
                f'<span style="color:{color}">{html.escape(text)}</span></p>')

    def transcript_text(self, since: float = 0) -> str:
        return "\n".join(
            f"[{datetime.fromtimestamp(ts):%H:%M}] {who}: {text}"
            for ts, who, text in self.lines if ts >= since
        )

    # ---------- ИИ ----------
    def ask_preset(self, key: str):
        if key == "catchup":
            self.run_ai(llm.PRESETS[key], self.transcript_text(time.time() - CATCHUP_SECONDS),
                        self.cfg["fast_model"])
        else:
            self.run_ai(llm.PRESETS[key], self.transcript_text(), self.cfg["llm_model"])

    def ask_free(self):
        q = self.question.text().strip()
        if q:
            self.question.clear()
            self.run_ai(q, self.transcript_text(), self.cfg["llm_model"])

    def run_ai(self, question: str, transcript: str, model: str):
        if not self.cfg["openrouter_key"]:
            QMessageBox.information(self, "Нужен ключ", "Укажи ключ OpenRouter в настройках.")
            self.open_settings()
            return
        self.cfg["meeting_context"] = self.context.toPlainText()
        config.save(self.cfg)
        messages = llm.build_messages(transcript, self.cfg["meeting_context"], question)
        self.answer = f"**{question}**\n\n"
        self.ai_view.setMarkdown(self.answer + "…")
        key = self.cfg["openrouter_key"]
        # Новый запрос отменяет предыдущий: старый поток увидит чужой токен и замолчит.
        token = self._ai_token = object()

        def work():
            cancelled = lambda: self._ai_token is not token  # noqa: E731
            try:
                llm.stream_answer(key, model, messages,
                                  lambda d: None if cancelled() else self.bridge.ai_delta.emit(d),
                                  cancelled)
                if not cancelled():
                    self.bridge.ai_done.emit("")
            except Exception as e:  # noqa: BLE001
                if not cancelled():
                    self.bridge.ai_done.emit(str(e))

        threading.Thread(target=work, daemon=True).start()

    def on_ai_delta(self, delta: str):
        self.answer += delta
        self.ai_view.setMarkdown(self.answer)
        self.ai_view.verticalScrollBar().setValue(self.ai_view.verticalScrollBar().maximum())

    def on_ai_done(self, error: str):
        if error:
            self.answer += f"\n\n_Ошибка: {error}_"
            self.ai_view.setMarkdown(self.answer)

    # ---------- прочее ----------
    def open_settings(self):
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec():
            dlg.apply()

    def export(self):
        if not self.lines:
            QMessageBox.information(self, "Пусто", "Пока нечего сохранять.")
            return
        config.NOTES_DIR.mkdir(parents=True, exist_ok=True)
        start = datetime.fromtimestamp(self.lines[0][0])
        path = config.NOTES_DIR / f"Встреча {start:%Y-%m-%d %H-%M}.md"
        parts = [f"# Встреча {start:%d.%m.%Y %H:%M}\n"]
        if self.context.toPlainText().strip():
            parts.append(f"## Контекст\n\n{self.context.toPlainText().strip()}\n")
        if self.answer:
            parts.append(f"## Заметки ИИ\n\n{self.answer.strip()}\n")
        parts.append("## Расшифровка\n\n" + self.transcript_text().replace("\n", "  \n") + "\n")
        path.write_text("\n".join(parts), encoding="utf-8")
        self.status.setText(f"Сохранено: {path}")

    def new_meeting(self):
        if self.lines and QMessageBox.question(
                self, "Новая встреча", "Очистить расшифровку? Несохранённые заметки пропадут."
        ) != QMessageBox.Yes:
            return
        if self.recorder:
            self.stop_record()
        self.lines.clear()
        self.interim.clear()
        self.answer = ""
        self._ai_token = None
        self.started_at = 0.0
        self.clock.setText("")
        self.rec_btn.setText("●  Начать запись")
        self.transcript.clear()
        self.ai_view.clear()

    def closeEvent(self, event):
        if self.recorder:
            self.recorder.close()
        self.cfg["meeting_context"] = self.context.toPlainText()
        config.save(self.cfg)
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Konspekt")
    app.setStyleSheet(STYLE)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
