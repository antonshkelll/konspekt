"""Главное окно Konspekt: расшифровка встречи слева, ИИ-панель справа."""
import html
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QShortcut, QKeySequence, QPainter, QColor, QPen
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QPushButton, QSplitter, QTextBrowser, QVBoxLayout, QWidget, QFileDialog,
    QSystemTrayIcon, QMenu, QScrollArea, QSlider,
)

from . import account, config, llm
from .capture import Recorder, list_microphones
from .context import read_context, compose_context
from .privacy import protect_window

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
QLabel#brand { font-size: 24pt; font-weight: 700; color: #f1f3ff; }
QLabel#eyebrow { color: #a7a5ff; font-size: 9pt; font-weight: 600; }
QPushButton#secondary { border-color: #5854b8; color: #cbc9ff; }
QSplitter::handle { background: #14151a; width: 8px; }
"""


def resource(name: str) -> str:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return str(base / "assets" / name)


class Bridge(QObject):
    """Переносит события из фоновых потоков в поток интерфейса."""
    text = Signal(int, str, str, bool)
    status = Signal(int, str)
    ai_delta = Signal(object, str)
    ai_done = Signal(object, str)


class SettingsDialog(QDialog):
    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self.setMinimumWidth(600)
        self.resize(650, 740)
        self.cfg = cfg
        outer = QVBoxLayout(self)
        area = QScrollArea()
        area.setWidgetResizable(True)
        panel = QWidget()
        form = QFormLayout(panel)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        area.setWidget(panel)
        outer.addWidget(area)

        self.provider = QComboBox()
        for label, value in (("DeepSeek · быстрый ответ, платный API", "deepseek"), ("Groq · бесплатная квота", "groq"), ("Ollama · своя модель", "ollama"), ("OpenRouter", "openrouter")):
            self.provider.addItem(label, value)
        self.provider.setCurrentIndex(max(0, self.provider.findData(cfg["llm_provider"])))
        self.stt_provider = QComboBox()
        self.stt_provider.addItem("Groq Whisper · фрагменты по 8 секунд", "groq")
        self.stt_provider.addItem("Deepgram · потоковая расшифровка", "deepgram")
        self.stt_provider.setCurrentIndex(max(0, self.stt_provider.findData(cfg["stt_provider"])))
        self.groq = QLineEdit(cfg["groq_key"])
        self.groq.setEchoMode(QLineEdit.Password)
        self.groq_model = QLineEdit(cfg["groq_model"])
        self.deepseek = QLineEdit(cfg["deepseek_key"])
        self.deepseek.setEchoMode(QLineEdit.Password)
        self.deepseek_model = QLineEdit(cfg["deepseek_model"])
        self.ollama_url = QLineEdit(cfg["ollama_url"])
        self.ollama_model = QLineEdit(cfg["ollama_model"])
        self.protection = QCheckBox("Исключать окна из захвата экрана (Windows)")
        self.protection.setChecked(cfg["capture_protection"])
        self.taskbar = QCheckBox("Убирать окно из панели задач (доступ через трей)")
        self.taskbar.setChecked(cfg["skip_taskbar"])
        form.addRow("Провайдер ответов", self.provider)
        form.addRow("Распознавание речи", self.stt_provider)
        form.addRow("Ключ DeepSeek", self.deepseek)
        form.addRow("Модель DeepSeek", self.deepseek_model)
        form.addRow("Ключ Groq", self.groq)
        form.addRow("Модель Groq", self.groq_model)
        form.addRow("Адрес Ollama /v1", self.ollama_url)
        form.addRow("Модель Ollama", self.ollama_model)

        self.dg = QLineEdit(cfg["deepgram_key"])
        self.dg.setEchoMode(QLineEdit.Password)
        self.orr = QLineEdit(cfg["openrouter_key"])
        self.orr.setEchoMode(QLineEdit.Password)
        self.model = QLineEdit(cfg["llm_model"])
        self.fast = QLineEdit(cfg["fast_model"])
        self.stt = QLineEdit(cfg["stt_model"])
        self.dg_proxy = QCheckBox("Использовать системный прокси для Deepgram")
        self.dg_proxy.setChecked(cfg["deepgram_use_proxy"])

        self.mic = QComboBox()
        self.mic.addItem("Микрофон по умолчанию", None)
        rec = Recorder(cfg, lambda *a: None, lambda *a: None)
        try:
            for idx, name in list_microphones(rec.p):
                self.mic.addItem(name, idx)
        except (OSError, LookupError):
            self.mic.setToolTip("Аудиоустройства недоступны; проверь подключение микрофона.")
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
        form.addRow(self.dg_proxy)
        form.addRow("Микрофон", self.mic)
        form.addRow(self.system)
        form.addRow(self.hint)
        form.addRow(self.protection)
        form.addRow(self.taskbar)
        note = QLabel("Речь отправляется провайдеру распознавания, контекст — провайдеру ответов.\nЗащиту окна проверь в своей программе созвонов.\nКлючи сохраняются локально в config.json; не публикуй его.")
        note.setWordWrap(True)
        form.addRow(note)
        check = QPushButton("Проверить API")
        check.clicked.connect(self.check_keys)
        self.check_result = QLabel("")
        self.check_result.setObjectName("muted")
        self.check_result.setWordWrap(True)
        form.addRow(check)
        form.addRow(self.check_result)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        def refresh_fields():
            selected = self.provider.currentData()
            for field, provider in ((self.deepseek, "deepseek"), (self.deepseek_model, "deepseek"),
                                    (self.groq, "groq"), (self.groq_model, "groq"),
                                    (self.ollama_url, "ollama"), (self.ollama_model, "ollama"),
                                    (self.orr, "openrouter"), (self.model, "openrouter"), (self.fast, "openrouter")):
                form.setRowVisible(field, selected == provider or (field is self.groq and self.stt_provider.currentData() == "groq"))
            for field in (self.dg, self.stt, self.dg_proxy):
                form.setRowVisible(field, self.stt_provider.currentData() == "deepgram")
        self.provider.currentIndexChanged.connect(refresh_fields)
        self.stt_provider.currentIndexChanged.connect(refresh_fields)
        refresh_fields()

    def check_keys(self):
        self.check_result.setText("Проверяю…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        lines = []
        try:
            import httpx
            snapshot = dict(self.cfg, llm_provider=self.provider.currentData(),
                            groq_key=self.groq.text().strip(), groq_model=self.groq_model.text().strip(),
                            deepseek_key=self.deepseek.text().strip(), deepseek_model=self.deepseek_model.text().strip(),
                            ollama_url=self.ollama_url.text().strip(), ollama_model=self.ollama_model.text().strip(),
                            openrouter_key=self.orr.text().strip())
            url, key, _, service = llm.connection(snapshot)
            r = httpx.get(url + "/models", headers={"Authorization": f"Bearer {key}"} if key else {}, timeout=10)
            lines.append(f"{service}: API доступен; генерацию ещё нужно проверить." if r.status_code == 200 else account.explain_http(service, r.status_code))
        except Exception:
            lines.append("API ответов недоступен: проверь адрес и соединение.")
        checks = []
        if self.provider.currentData() == "deepseek":
            checks.append((account.check_deepseek, self.deepseek.text().strip()))
        if self.stt_provider.currentData() == "deepgram":
            checks.append((account.check_deepgram, self.dg.text().strip()))
        if self.provider.currentData() == "openrouter":
            checks.append((account.check_openrouter, self.orr.text().strip()))
        for fn, key in checks:
            try:
                lines.append(fn(key))
            except Exception as e:  # noqa: BLE001
                lines.append(f"Не удалось проверить: {e}")
        QApplication.restoreOverrideCursor()
        self.check_result.setText("\n".join(lines))

    def apply(self):
        self.cfg.update(
            llm_provider=self.provider.currentData(), stt_provider=self.stt_provider.currentData(),
            deepseek_key=self.deepseek.text().strip(), deepseek_model=self.deepseek_model.text().strip() or config.DEFAULTS["deepseek_model"],
            groq_key=self.groq.text().strip(), groq_model=self.groq_model.text().strip() or config.DEFAULTS["groq_model"],
            ollama_url=self.ollama_url.text().strip(), ollama_model=self.ollama_model.text().strip() or config.DEFAULTS["ollama_model"],
            capture_protection=self.protection.isChecked(), skip_taskbar=self.taskbar.isChecked(),
            deepgram_key=self.dg.text().strip(),
            openrouter_key=self.orr.text().strip(),
            llm_model=self.model.text().strip() or config.DEFAULTS["llm_model"],
            fast_model=self.fast.text().strip() or config.DEFAULTS["fast_model"],
            stt_model=self.stt.text().strip() or config.DEFAULTS["stt_model"],
            deepgram_use_proxy=self.dg_proxy.isChecked(),
            mic_device=self.mic.currentData(),
            capture_system=self.system.isChecked(),
            consent_hint=self.hint.isChecked(),
        )
        config.save(self.cfg)


class FocusWindow(QDialog):
    """Clear text over a translucent background; drag the header to move."""
    def __init__(self, parent):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._drag_offset = None
        self.background_alpha = 64

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor(14, 16, 24, self.background_alpha))
        painter.setPen(QPen(QColor(170, 170, 210, 90), 1))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 12, 12)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.position().y() < 55:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)


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
        self._record_generation = 0
        self._ai_busy = False
        self._last_auto = 0.0
        self._last_auto_line = 0
        self._last_final = 0.0
        self._auto_pending = False
        self._auto_buffer = ""
        self._manual_until = 0.0
        self._request_started = 0.0
        self._first_delta = False
        self.attachments = {}
        self.history = []
        self.focus_window = None
        self._demo_active = False
        self.answer_render = QTimer(self)
        self.answer_render.setSingleShot(True)
        self.answer_render.timeout.connect(self.render_answer)

        self.setWindowTitle("Konspekt — студия встреч · Demo 2")
        self.setWindowIcon(QIcon(resource("icon.ico")))
        self.resize(1220, 800)
        self._build()
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        menu = QMenu(self)
        menu.addAction("Открыть Konspekt", self.restore_window)
        menu.addAction("Остановить запись", lambda: self.stop_record() if self.recorder else None)
        menu.addAction("Выход", self.close)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.restore_window() if reason == QSystemTrayIcon.DoubleClick else None)
        self.tray.setToolTip("Konspekt · запись выключена")
        self.tray.show()
        QTimer.singleShot(0, self.apply_privacy)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(250)
        self.shortcuts = [QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.ask_free),
                          QShortcut(QKeySequence("Ctrl+Shift+K"), self, activated=self.open_focus),
                          QShortcut(QKeySequence("Escape"), self, activated=self.cancel_answer)]

    # ---------- интерфейс ----------
    def _build(self):
        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(14, 12, 14, 12)
        heading = QHBoxLayout()
        titles = QVBoxLayout()
        eyebrow = QLabel("PERSONAL WORKSPACE / DEMO")
        eyebrow.setObjectName("eyebrow")
        brand = QLabel("Konspekt")
        brand.setObjectName("brand")
        titles.addWidget(eyebrow)
        titles.addWidget(brand)
        heading.addLayout(titles)
        heading.addStretch()
        self.session_badge = QLabel("Локальная LLM  ·  встречи и тренировка")
        self.session_badge.setObjectName("muted")
        heading.addWidget(self.session_badge)
        demo = QPushButton("Посмотреть демо")
        demo.setObjectName("secondary")
        demo.clicked.connect(self.load_demo)
        heading.addWidget(demo)
        lay.addLayout(heading)

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
        privacy = QHBoxLayout()
        self.privacy_label = QLabel("Бета · запись включается вручную")
        self.privacy_label.setObjectName("muted")
        privacy.addWidget(self.privacy_label)
        privacy.addStretch()
        tray_btn = QPushButton("Свернуть в трей")
        tray_btn.clicked.connect(self.hide_to_tray)
        privacy.addWidget(tray_btn)
        lay.addLayout(privacy)

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
        files = QHBoxLayout()
        attach = QPushButton("＋ Файлы контекста")
        attach.clicked.connect(self.attach_files)
        remove = QPushButton("Убрать файлы")
        remove.clicked.connect(self.clear_files)
        files.addWidget(attach)
        files.addWidget(remove)
        rl.addLayout(files)
        self.files_label = QLabel("Файлы не прикреплены")
        self.files_label.setWordWrap(True)
        rl.addWidget(self.files_label)
        self.auto = QCheckBox("Автоподсказки после реплики собеседника")
        self.auto.setToolTip("Ollama / DeepSeek: после короткой паузы. Groq: раз в 30 с. OpenRouter: раз в 15 с. Облачные автозапросы расходуют API-баланс.")
        rl.addWidget(self.auto)
        practice = QHBoxLayout()
        for title, preset in (("Подсказка", "assist"), ("Разбор ответа", "coach"), ("Вопрос для тренировки", "practice")):
            button = QPushButton(title)
            button.clicked.connect(lambda _=False, k=preset: self.ask_preset(k))
            practice.addWidget(button)
        rl.addLayout(practice)

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
        self.ai_timing = QLabel("Ответ по мере генерации · скорость зависит от выбранной модели")
        self.ai_timing.setObjectName("muted")
        rl.addWidget(self.ai_timing)
        actions = QHBoxLayout()
        for title, action in (("Копировать", lambda: QApplication.clipboard().setText(self.answer)),
                              ("Стоп · Esc", self.cancel_answer),
                              ("Компактно", self.open_focus)):
            button = QPushButton(title)
            button.clicked.connect(action)
            actions.addWidget(button)
        rl.addLayout(actions)
        self.history_picker = QComboBox()
        self.history_picker.addItem("История ответов этой встречи", None)
        self.history_picker.activated.connect(self.restore_answer)
        rl.addWidget(self.history_picker)

        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([600, 500])
        lay.addWidget(split)
        self.setCentralWidget(root)

    def _tick(self):
        if self.recorder:
            sec = int(time.time() - self.started_at)
            self.clock.setText(f"{sec // 60:02d}:{sec % 60:02d}")
            now = time.monotonic()
            interval = {"deepseek": 1, "ollama": 1, "groq": 30}.get(self.cfg["llm_provider"], 15)
            if (self.auto.isChecked() and not self._ai_busy and self.lines and self._auto_pending
                    and now >= self._manual_until and not self.interim
                    and now - self._last_auto >= interval
                    and now - self._last_final >= (0.35 if self.cfg["llm_provider"] in ("deepseek", "ollama") else 1.5)):
                batch = self._auto_buffer[:1800]
                boundary = batch.rfind("\n")
                if boundary >= 600:
                    batch = batch[:boundary + 1]
                self._auto_buffer = self._auto_buffer[len(batch):]
                self._auto_pending = bool(self._auto_buffer)
                self._last_auto = now
                self._last_auto_line = len(self.lines)
                self.run_ai(llm.PRESETS["assist"], batch, self.cfg["fast_model"], automatic=True)
        if self.focus_window and self.focus_window.isVisible():
            self.focus_state.setText("● Микрофон + системный звук · автоответы" if self.recorder else "Запись выключена")

    # ---------- запись ----------
    def toggle_record(self):
        if self._demo_active:
            QMessageBox.information(self, "Демонстрация", "Сначала нажми «Новая встреча», чтобы начать настоящую запись.")
            return
        if self.recorder:
            self.stop_record()
            return
        key_name = "groq_key" if self.cfg["stt_provider"] == "groq" else "deepgram_key"
        if not self.cfg[key_name]:
            QMessageBox.information(self, "Нужен ключ", "Укажи ключ провайдера речи в настройках.")
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
        self._record_generation += 1
        generation = self._record_generation
        self.recorder = Recorder(dict(self.cfg), lambda *args: self.bridge.text.emit(generation, *args),
                                 lambda msg: self.bridge.status.emit(generation, msg))
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
        self.tray.setToolTip("Konspekt · ● идёт запись")
        self.auto.setChecked(True)
        self.open_focus()
        self.status.setText("")

    def stop_record(self):
        if not self.recorder:
            return
        rec, self.recorder = self.recorder, None
        threading.Thread(target=rec.close, daemon=True).start()
        self.interim.clear()
        self.rec_btn.setText("●  Продолжить запись")
        self.rec_btn.setProperty("active", False)
        self.rec_btn.style().polish(self.rec_btn)
        self.live.setText("")
        self.tray.setToolTip("Konspekt · запись выключена")
        self.render_transcript()

    def on_text(self, generation: int, speaker: str, text: str, final: bool):
        if generation != self._record_generation:
            return
        if final:
            self.interim.pop(speaker, None)
            self.lines.append((time.time(), speaker, text))
            self._last_final = time.monotonic()
            self._auto_buffer += f"{speaker}: {text}\n"
            self._auto_pending = True
        else:
            self.interim[speaker] = text
        self.render_transcript()

    def on_status(self, generation: int, msg: str):
        if generation != self._record_generation:
            return
        self.status.setText(msg)
        if msg.startswith("Groq STT") and self.recorder:
            self.stop_record()
            self.auto.setChecked(False)

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

    def run_ai(self, question: str, transcript: str, model: str, automatic=False):
        if self._demo_active:
            self.status.setText("Это демонстрационные данные. Нажми «Новая встреча» для работы с моделью.")
            return
        try:
            url, key, selected_model, service = llm.connection(self.cfg)
        except ValueError as exc:
            self.auto.setChecked(False)
            self.status.setText(str(exc))
            return
        if service != "Ollama" and not key:
            self.auto.setChecked(False)
            if automatic:
                self.status.setText("Автоподсказки выключены: укажи ключ API в настройках.")
                return
            QMessageBox.information(self, "Нужен ключ", "Укажи ключ провайдера ответов в настройках.")
            self.open_settings()
            return
        self.cfg["meeting_context"] = self.context.toPlainText()
        config.save(self.cfg)
        compact = service == "Groq" or (automatic and service == "Ollama")
        context = compose_context(self.cfg["meeting_context"], self.attachments, compact=compact)
        messages = llm.build_messages(transcript, context, question, compact=compact)
        if service == "Groq":
            self.status.setText("Groq: последние 2000 символов разговора и фрагменты файлов; всего до 3500 символов контекста.")
        self.archive_answer()
        self.answer = f"**{question}**\n\n"
        self.ai_view.setMarkdown(self.answer + "…")
        if service != "OpenRouter":
            model = selected_model
        self._ai_busy = True
        self._request_started = time.monotonic()
        self._first_delta = False
        self.ai_timing.setText(f"{service} · ожидаю первый текст…")
        if not automatic:
            self._manual_until = time.monotonic() + 30
        self._last_auto = time.monotonic()
        # Новый запрос отменяет предыдущий: старый поток увидит чужой токен и замолчит.
        token = self._ai_token = object()

        def work():
            cancelled = lambda: self._ai_token is not token  # noqa: E731
            try:
                llm.stream_answer(key, model, messages,
                                  lambda d: None if cancelled() else self.bridge.ai_delta.emit(token, d),
                                  cancelled, url, service, max_tokens=180 if automatic else 500)
                if not cancelled():
                    self.bridge.ai_done.emit(token, "")
            except Exception as e:  # noqa: BLE001
                if not cancelled():
                    self.bridge.ai_done.emit(token, str(e))

        threading.Thread(target=work, daemon=True).start()

    def on_ai_delta(self, token, delta: str):
        if token is not self._ai_token:
            return
        if not self._first_delta:
            self._first_delta = True
            self.ai_timing.setText(f"Первый текст через {time.monotonic() - self._request_started:.1f} с после запроса")
        self.answer += delta
        if not self.answer_render.isActive():
            self.answer_render.start(50)

    def on_ai_done(self, token, error: str):
        if token is not self._ai_token:
            return
        self._ai_busy = False
        self._manual_until = 0
        if error:
            self.auto.setChecked(False)
            self.answer += f"\n\n_Ошибка: {error}_"
        self.answer_render.stop()
        self.render_answer()

    def render_answer(self):
        self.ai_view.setMarkdown(self.answer)
        self.ai_view.verticalScrollBar().setValue(self.ai_view.verticalScrollBar().maximum())
        if self.focus_window and self.focus_window.isVisible():
            self.focus_view.setMarkdown(self.answer)

    def cancel_answer(self):
        if not self._ai_busy:
            return
        self._ai_token = None
        self._ai_busy = False
        self.auto.setChecked(False)
        self.answer_render.stop()
        self.render_answer()
        self.ai_timing.setText("Ответ остановлен · сервер может завершать уже принятый запрос")

    def archive_answer(self):
        if not self.answer.strip():
            return
        self.history.insert(0, self.answer)
        self.history = self.history[:20]
        self.history_picker.clear()
        self.history_picker.addItem("История ответов этой встречи", None)
        for index, value in enumerate(self.history):
            self.history_picker.addItem(value.splitlines()[0].strip("* ")[:65], index)

    def restore_answer(self, index):
        selected = self.history_picker.itemData(index)
        if selected is None:
            return
        if self._ai_busy:
            self.cancel_answer()
        # The current answer stays recoverable when navigating history.
        current = self.answer
        self.answer = self.history[selected]
        self.history[selected] = current
        self.history_picker.setItemText(index, current.splitlines()[0].strip("* ")[:65])
        self.render_answer()

    def open_focus(self):
        if self.focus_window is None:
            self.focus_window = FocusWindow(self)
            self.focus_window.setWindowTitle("Konspekt · фокус")
            self.focus_window.resize(480, 560)
            layout = QVBoxLayout(self.focus_window)
            header = QHBoxLayout()
            header.addWidget(QLabel("KONSPEKT · перетащи за верхний край"))
            close = QPushButton("×")
            close.setMaximumWidth(35)
            close.clicked.connect(self.focus_window.close)
            header.addWidget(close)
            layout.addLayout(header)
            self.focus_state = QLabel("Запись выключена")
            layout.addWidget(self.focus_state)
            self.focus_view = QTextBrowser()
            self.focus_view.setObjectName("focusAnswer")
            layout.addWidget(self.focus_view)
            pin = QCheckBox("Поверх остальных окон")
            pin.toggled.connect(self.pin_focus)
            pin.setChecked(True)
            layout.addWidget(pin)
            self.opacity_label = QLabel()
            layout.addWidget(self.opacity_label)
            self.opacity_slider = QSlider(Qt.Horizontal)
            self.opacity_slider.setRange(10, 95)
            self.opacity_slider.setValue(max(10, min(95, int(self.cfg["overlay_alpha"]))))
            self.opacity_slider.valueChanged.connect(self.set_overlay_alpha)
            self.opacity_slider.sliderReleased.connect(lambda: config.save(self.cfg))
            layout.addWidget(self.opacity_slider)
            stop = QPushButton("Остановить запись")
            stop.clicked.connect(self.stop_record)
            layout.addWidget(stop)
            self.set_overlay_alpha(self.opacity_slider.value())
        self.focus_view.setMarkdown(self.answer or "Подсказка появится здесь после запроса.")
        self.focus_window.show()
        protect_window(int(self.focus_window.winId()), self.cfg["capture_protection"])
        self.focus_window.raise_()

    def pin_focus(self, enabled):
        self.focus_window.setWindowFlag(Qt.WindowStaysOnTopHint, enabled)
        self.focus_window.show()
        protect_window(int(self.focus_window.winId()), self.cfg["capture_protection"])

    def set_overlay_alpha(self, value):
        value = max(10, min(95, int(value)))
        self.cfg["overlay_alpha"] = value
        self.opacity_label.setText(f"Плотность фона: {value}% · буквы остаются непрозрачными")
        alpha = round(255 * value / 100)
        self.focus_window.background_alpha = alpha
        self.focus_window.update()
        self.focus_window.setStyleSheet(
            "QDialog { background: transparent; }"
            "QLabel, QCheckBox, QTextBrowser, QSlider { background: transparent; color: white; }"
            "QTextBrowser#focusAnswer { border: none; font-size: 14pt; padding: 8px; }"
            "QPushButton { background: rgba(35,38,52,180); color: white; }")

    def load_demo(self):
        if self.recorder or self.lines or self._ai_busy or self.answer.strip():
            self.status.setText("Демо доступно в пустой встрече. Сохрани заметки и нажми «Новая встреча».")
            return
        if self.attachments:
            self.status.setText("Перед демонстрацией убери прикреплённые личные файлы.")
            return
        self._demo_previous_context = self.context.toPlainText()
        self._demo_active = True
        self.auto.setChecked(False)
        self.session_badge.setText("ДЕМОНСТРАЦИЯ · вымышленные данные")
        self.context.setPlainText("Тренировка продуктового интервью. Пилот: две недели. Бюджет: 42 000 ₽.")
        self.attachments["Демо-бриф.md"] = "Пилот длится две недели. Бюджет 42000 рублей. Цель — сократить время подготовки заметок."
        self.files_label.setText("Демо-бриф.md · демонстрационный контекст")
        self.on_text(self._record_generation, "Собеседники", "Как проверим, что пилот принёс пользу?", True)
        self.on_text(self._record_generation, "Я", "Предлагаю сравнить время подготовки заметок до и после пилота.", True)
        self.answer = ("**Пример подсказки · заранее подготовленное демо**\n\n"
                       "Предлагаю измерить время подготовки заметок до пилота и после него.\n\n"
                       "- **Срок:** две недели.\n- **Бюджет:** 42 000 ₽.\n"
                       "- **Критерий успеха:** согласуем целевое сокращение времени.\n\n"
                       "Источник: Демо-бриф.md.\n\n**Уточни:** какой результат команда считает достаточным?")
        self.render_answer()
        self.ai_timing.setText("Демо без записи и запросов к API · время генерации не измеряется")

    # ---------- прочее ----------
    def open_settings(self):
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec():
            dlg.apply()
            self.apply_privacy()

    def attach_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Контекст для ИИ", "", "Документы (*.txt *.md *.csv *.docx *.pdf)")
        for path in paths:
            try:
                text = read_context(path)
                name = Path(path).name
                remaining = sum(len(t) for n, t in self.attachments.items() if n != name)
                if name not in self.attachments and len(self.attachments) >= 5:
                    raise ValueError("Можно прикрепить до 5 файлов.")
                if remaining + len(text) > 16000:
                    raise ValueError("Общий лимит вложений — 16 000 символов.")
                self.attachments[name] = text
            except Exception as exc:
                QMessageBox.warning(self, "Не удалось прочитать файл", f"{Path(path).name}: {exc}")
        self.files_label.setText(" · ".join(self.attachments) or "Файлы не прикреплены")

    def clear_files(self):
        self.attachments.clear()
        self.files_label.setText("Файлы не прикреплены")

    def restore_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def hide_to_tray(self):
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.hide()
            self.tray.showMessage("Konspekt", "Открыть и остановить запись можно через значок в трее.")

    def apply_privacy(self):
        enabled = self.cfg["skip_taskbar"] and QSystemTrayIcon.isSystemTrayAvailable()
        self.setWindowFlag(Qt.Tool, enabled)
        self.show()
        protected = protect_window(int(self.winId()), self.cfg["capture_protection"])
        self.privacy_label.setText("Защита от захвата включена · проверь в приложении созвона" if self.cfg["capture_protection"] and protected else "Защита от захвата выключена" if protected else "Windows не применила защиту от захвата")

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
        if (self.lines or self.answer or self.history) and QMessageBox.question(
                self, "Новая встреча", "Очистить расшифровку? Несохранённые заметки пропадут."
        ) != QMessageBox.Yes:
            return
        if self.recorder:
            self.stop_record()
        self.lines.clear()
        self.interim.clear()
        self.answer = ""
        self.answer_render.stop()
        self.history.clear()
        self.history_picker.clear()
        self.history_picker.addItem("История ответов этой встречи", None)
        if self._demo_active:
            self.context.setPlainText(self._demo_previous_context)
        self._demo_active = False
        self.session_badge.setText("Локальная LLM  ·  встречи и тренировка")
        if self.focus_window:
            self.focus_view.clear()
        self._ai_token = None
        self._ai_busy = False
        self._record_generation += 1
        self._last_auto_line = 0
        self._auto_pending = False
        self._auto_buffer = ""
        self._manual_until = 0
        self.clear_files()
        self.started_at = 0.0
        self.clock.setText("")
        self.rec_btn.setText("●  Начать запись")
        self.transcript.clear()
        self.ai_view.clear()

    def closeEvent(self, event):
        self._ai_token = None
        if self.recorder:
            self.recorder.close()
        self.cfg["meeting_context"] = self._demo_previous_context if self._demo_active else self.context.toPlainText()
        config.save(self.cfg)
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Konspekt")
    app.setStyleSheet(STYLE)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
