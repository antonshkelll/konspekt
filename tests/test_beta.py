import json
import os
import tempfile
import time
import unittest
import io
import wave
from array import array
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import httpx
from PySide6.QtWidgets import QApplication, QMessageBox
from konspekt import account, config, llm, diagnostics, chatgpt
from konspekt.context import read_context, compose_context
from konspekt.app import MainWindow, SettingsDialog
from konspekt.groq_stt import GroqSource
from konspekt.capture import Source
from unittest.mock import MagicMock


class AudioTests(unittest.TestCase):
    def test_deepgram_avoids_system_proxy_by_default(self):
        for enabled in (False, True):
            cfg = dict(config.DEFAULTS, deepgram_key="test", deepgram_use_proxy=enabled)
            src = Source(MagicMock(), {"defaultSampleRate": 16000, "maxInputChannels": 1, "index": 0},
                         "Я", cfg, lambda *a: None, lambda *a: None)
            with patch("konspekt.capture.connect") as connect, patch("konspekt.capture.threading.Thread"):
                src.start()
                self.assertIs(connect.call_args.kwargs["proxy"], True if enabled else None)
    def test_stereo_final_fragment_is_flushed_as_mono_wav(self):
        out, status = [], []
        src = GroqSource(None, {"defaultSampleRate": 100, "maxInputChannels": 2},
                         "Собеседники", dict(config.DEFAULTS, groq_key="test"),
                         lambda *args: out.append(args), status.append)
        class Stream:
            count = 0
            def read(self, *args, **kwargs):
                self.count += 1
                if self.count == 3:
                    src._stop.set()
                return array("h", [200, 600] * 10).tobytes()
        src._stream = Stream()
        src._capture()
        self.assertTrue(src._capture_done.is_set())
        with patch("konspekt.groq_stt.httpx.Client") as client:
            post = client.return_value.__enter__.return_value.post
            post.return_value = httpx.Response(200, json={"text": "вопрос"})
            src._transcribe()
            wav_data = post.call_args.kwargs["files"]["file"][1]
            with wave.open(io.BytesIO(wav_data)) as wav:
                self.assertEqual(wav.getnchannels(), 1)
                self.assertEqual(wav.getnframes(), 30)
                self.assertEqual(set(array("h", wav.readframes(30))), {400})
        self.assertEqual(out, [("Собеседники", "вопрос", True)])
        self.assertFalse(status)

    def test_stt_quota_signals_stop(self):
        status = []
        src = GroqSource(None, {"defaultSampleRate": 100, "maxInputChannels": 1},
                         "Я", dict(config.DEFAULTS, groq_key="test"), lambda *a: None, status.append)
        src._queue.put(array("h", [400] * 100).tobytes())
        src._capture_done.set()
        with patch("konspekt.groq_stt.httpx.Client") as client:
            client.return_value.__enter__.return_value.post.return_value = httpx.Response(429)
            src._transcribe()
        self.assertTrue(src._stop.is_set())
        self.assertTrue(status[0].startswith("Groq STT"))


class ContextTests(unittest.TestCase):
    def test_docx_and_limits(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "brief.docx"
            with ZipFile(p, "w") as z:
                z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>Бюджет 100</w:t></w:r></w:p></w:document>')
            self.assertEqual(read_context(str(p)), "Бюджет 100")
            p = Path(folder) / "long.txt"
            p.write_text("a" * 16001)
            with self.assertRaises(ValueError):
                read_context(str(p))

    def test_compact_keeps_every_file(self):
        text = compose_context("a" * 4000, {"one.md": "первый" * 500, "two.md": "второй" * 500}, True)
        self.assertLessEqual(len(text), 3500)
        for part in ("one.md", "two.md", "первый", "второй"):
            self.assertIn(part, text)


class LLMTests(unittest.TestCase):
    def test_subscription_route_never_calls_api_key_transport(self):
        cfg = dict(config.DEFAULTS, llm_provider="chatgpt", chatgpt_model="available-slug", openai_key="must-not-be-used")
        url, key, model, service = llm.connection(cfg)
        self.assertEqual(key, "")
        with patch.object(chatgpt, "call", return_value={"ok":True}) as ipc, patch.object(llm.httpx, "stream") as paid:
            llm.stream_answer(key, model, [{"role":"system","content":"instructions"},{"role":"user","content":"meeting"}], lambda _:None, lambda:False, url, service)
            payload = ipc.call_args.args[0]
            self.assertEqual(payload["instructions"], "instructions")
            self.assertEqual(payload["input"], [{"role":"user","content":"meeting"}])
            self.assertNotIn("max_output_tokens", payload)
            self.assertNotIn("openai_key", payload)
            paid.assert_not_called()

    def test_subscription_failure_does_not_fallback(self):
        with patch.object(chatgpt, "stream", side_effect=RuntimeError("sharing_not_enabled")), patch.object(llm.httpx, "stream") as paid:
            with self.assertRaises(RuntimeError):
                llm.stream_answer("", "model", [], lambda _:None, lambda:False, service="ChatGPT")
            paid.assert_not_called()

    def test_openai_stream_wire_contract(self):
        cfg = dict(config.DEFAULTS, llm_provider="openai", openai_key="synthetic-key")
        url, key, model, service = llm.connection(cfg)
        self.assertEqual(url, "https://api.openai.com/v1")
        self.assertEqual(model, "gpt-6-luna")
        with patch.object(llm.httpx, "stream") as stream:
            response = stream.return_value.__enter__.return_value
            response.status_code = 200
            response.iter_lines.return_value = ['data: {"choices":[{"delta":{"content":"готово"}}]}', 'data: [DONE]']
            out = []
            llm.stream_answer(key, model, [], out.append, lambda: False, url, service, max_tokens=120)
            body = stream.call_args.kwargs["json"]
            self.assertEqual(body["max_completion_tokens"], 120)
            self.assertNotIn("max_tokens", body)
            self.assertEqual(body["reasoning_effort"], "none")
            self.assertFalse(body["store"])
            self.assertEqual(out, ["готово"])

    def test_probe_requires_key_and_redacts_failures(self):
        cfg = dict(config.DEFAULTS, llm_provider="openai", openai_key="")
        with patch.object(llm, "stream_answer") as request:
            self.assertFalse(diagnostics.probe(cfg)["ok"])
            request.assert_not_called()
        cfg["openai_key"] = "secret-test-key"
        with patch.object(llm, "stream_answer", side_effect=RuntimeError("error secret-test-key")):
            report = diagnostics.probe(cfg)
        self.assertFalse(report["ok"])
        self.assertNotIn("secret-test-key", report["message"])

    def test_probe_measures_real_callback(self):
        cfg = dict(config.DEFAULTS, llm_provider="openai", openai_key="test")
        def respond(key, model, messages, delta, cancel, url, service, **kwargs):
            self.assertNotIn("Расшифровка", messages[1]["content"])
            delta("42000 рублей, 2 недели")
        with patch.object(llm, "stream_answer", respond):
            report = diagnostics.probe(cfg)
        self.assertTrue(report["ok"])
        self.assertGreaterEqual(report["first_text_s"], 0)
        self.assertGreaterEqual(report["total_s"], report["first_text_s"])

    def test_deepseek_empty_balance_is_not_reported_as_ready(self):
        with patch.object(account.httpx, "get", return_value=httpx.Response(200, json={"is_available": False})):
            result = account.check_deepseek("test")
        self.assertIn("недостаточно", result)

    def test_local_r1_has_room_to_finish_reasoning(self):
        with patch.object(llm.httpx, "stream") as stream:
            response = stream.return_value.__enter__.return_value
            response.status_code = 200
            response.iter_lines.return_value = ['data: {"choices":[{"delta":{"content":"ok"}}]}', 'data: [DONE]']
            llm.stream_answer("", "deepseek-r1:1.5b", [], lambda _: None, lambda: False,
                              "http://127.0.0.1:11434/v1", "Ollama", max_tokens=100)
            body = stream.call_args.kwargs["json"]
            self.assertEqual(body["reasoning_effort"], "none")
            self.assertEqual(body["max_tokens"], 2048)
            self.assertNotIn("Authorization", stream.call_args.kwargs["headers"])
            self.assertEqual(stream.call_args.kwargs["timeout"].read, 180)

    def run_stream(self, data, status=200):
        out = []
        def stream(*args, **kwargs):
            self.request = (args, kwargs)
            return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(status, text=data))).stream(*args, **kwargs)
        with patch.object(llm.httpx, "stream", stream):
            llm.stream_answer("key", "model", [], out.append, lambda: False,
                              "https://example.test/v1", "Test")
        return out

    def test_sse(self):
        self.assertEqual(self.run_stream('data: {"choices":[{"delta":{"content":"Привет"}}]}\n\ndata: [DONE]\n\n'), ["Привет"])
        self.assertEqual(self.request[0][1], "https://example.test/v1/chat/completions")

    def test_quota_and_stream_errors(self):
        for data, code in (("quota", 429), ('data: {"error":{}}\n', 200), ("data: [DONE]\n", 200), ('data: {"choices":[{"delta":{"content":"x"}}]}\n', 200)):
            with self.subTest(code=code, data=data), self.assertRaises(RuntimeError):
                self.run_stream(data, code)

    def test_ollama_tunnel_and_no_plain_remote(self):
        cfg = dict(config.DEFAULTS, llm_provider="ollama")
        self.assertEqual(llm.connection(cfg)[1], "")
        cfg["ollama_url"] = "http://remote.test:11434/v1"
        with self.assertRaises(ValueError):
            llm.connection(cfg)

    def test_deepseek_non_thinking_stream_request(self):
        cfg = dict(config.DEFAULTS, llm_provider="deepseek", deepseek_key="test-key")
        url, key, model, service = llm.connection(cfg)
        self.assertEqual((url, service, model), ("https://api.deepseek.com", "DeepSeek", "deepseek-flash"))
        out = []
        with patch.object(llm.httpx, "stream") as stream:
            response = stream.return_value.__enter__.return_value
            response.status_code = 200
            response.iter_lines.return_value = ['data: {"choices":[{"delta":{"content":"ответ"}}]}', 'data: [DONE]']
            llm.stream_answer(key, model, [], out.append, lambda: False, url, service)
            self.assertEqual(stream.call_args.kwargs["json"]["thinking"], {"type": "disabled"})
            self.assertTrue(stream.call_args.kwargs["json"]["stream"])
        self.assertEqual(out, ["ответ"])


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.loader = patch.object(config, "load", lambda: dict(config.DEFAULTS))
        self.saver = patch.object(config, "save")
        self.loader.start()
        self.saver.start()
        self.win = MainWindow()

    def tearDown(self):
        self.win.lines.clear()
        self.win.recorder = None
        self.win.close()
        self.loader.stop()
        self.saver.stop()

    def test_stale_signals_ignored(self):
        token = object()
        self.win._ai_token = token
        self.win.on_ai_delta(object(), "old")
        self.assertEqual(self.win.answer, "")
        self.win.on_ai_delta(token, "new")
        self.assertEqual(self.win.answer, "new")
        self.win._record_generation = 2
        self.win.on_text(1, "Я", "old", True)
        self.assertFalse(self.win.lines)
        self.win.on_text(2, "Я", "new", True)
        self.assertEqual(len(self.win.lines), 1)

    def test_auto_only_new_lines_and_not_busy(self):
        self.win.recorder = object()
        self.win.auto.setChecked(True)
        self.win.on_text(0, "Собеседники", "Сколько стоит?", True)
        self.win._last_final = time.monotonic() - 5
        with patch.object(self.win, "run_ai") as ask:
            self.win._ai_busy = True
            self.win._tick()
            ask.assert_not_called()
            self.win._ai_busy = False
            self.win._tick()
            ask.assert_called_once()
            self.win._last_auto = 0
            self.win._tick()
            ask.assert_called_once()

    def test_new_meeting_rejects_old_audio(self):
        self.win.attachments["brief.txt"] = "data"
        self.win.new_meeting()
        self.win.on_text(0, "Я", "old", True)
        self.assertFalse(self.win.lines)
        self.assertFalse(self.win.attachments)

    def test_background_answer_reaches_ui_with_attached_context(self):
        self.win.cfg["deepseek_key"] = "test"
        self.win.cfg["llm_provider"] = "deepseek"
        self.win.attachments["brief.md"] = "Бюджет 100"
        def answer(key, model, messages, on_delta, cancel, url, service, **kwargs):
            self.assertIn("brief.md", messages[1]["content"])
            self.assertEqual(service, "DeepSeek")
            on_delta("Готовый ответ")
        with patch.object(llm, "stream_answer", answer):
            self.win.run_ai("Каков бюджет?", "", "ignored")
            deadline = time.monotonic() + 2
            while self.win._ai_busy and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
        self.assertFalse(self.win._ai_busy)
        self.assertIn("Готовый ответ", self.win.ai_view.toPlainText())

    def test_demo_never_calls_llm_and_preserves_real_context(self):
        self.win.context.setPlainText("Моя повестка")
        self.win.load_demo()
        self.assertTrue(self.win._demo_active)
        with patch.object(llm, "stream_answer") as request:
            self.win.run_ai("вопрос", "", "model")
            request.assert_not_called()
        self.assertIsNone(self.win.recorder)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            self.win.new_meeting()
        self.assertEqual(self.win.context.toPlainText(), "Моя повестка")
        self.assertFalse(self.win.attachments)
        self.assertFalse(self.win._demo_active)

    def test_demo_close_does_not_persist_demo_context(self):
        self.win.context.setPlainText("Личная повестка")
        self.win.load_demo()
        self.win.close()
        self.assertEqual(self.win.cfg["meeting_context"], "Личная повестка")

    def test_cancel_rejects_queued_answer_and_flushes_text(self):
        token = self.win._ai_token = object()
        self.win._ai_busy = True
        self.win.on_ai_delta(token, "Первый текст")
        self.win.cancel_answer()
        self.win.on_ai_delta(token, "не должен появиться")
        self.assertEqual(self.win.answer, "Первый текст")
        self.assertIn("Первый текст", self.win.ai_view.toPlainText())
        self.assertFalse(self.win._ai_busy)

    def test_history_swap_and_compact_view(self):
        self.win.answer = "Первый ответ"
        self.win.archive_answer()
        self.win.answer = "Второй ответ"
        self.win.restore_answer(1)
        self.assertEqual(self.win.answer, "Первый ответ")
        self.assertEqual(self.win.history[0], "Второй ответ")
        self.win.open_focus()
        self.win.render_answer()
        self.assertEqual(self.win.focus_view.toPlainText(), "Первый ответ")
        self.win.focus_window.close()

    def test_both_speakers_are_sent_and_backlog_is_not_dropped(self):
        self.win.recorder = object()
        self.win.auto.setChecked(True)
        self.win.on_text(0, "Я", "мой ответ " * 300, True)
        self.win.on_text(0, "Собеседники", "следующий вопрос?", True)
        expected = self.win._auto_buffer
        self.win._last_final = time.monotonic() - 5
        self.win._ai_busy = True
        with patch.object(self.win, "run_ai") as ask:
            self.win._tick()
            ask.assert_not_called()
            self.assertEqual(self.win._auto_buffer, expected)
            self.win._ai_busy = False
            while self.win._auto_pending:
                self.win._last_auto = 0
                self.win._tick()
            sent = "".join(call.args[1] for call in ask.call_args_list)
        self.assertEqual(sent, expected)
        self.assertIn("Я:", sent)
        self.assertIn("Собеседники:", sent)

    def test_overlay_alpha_does_not_fade_text(self):
        self.win.open_focus()
        self.win.set_overlay_alpha(15)
        self.assertEqual(self.win.cfg["overlay_alpha"], 15)
        self.assertEqual(self.win.focus_window.windowOpacity(), 1.0)
        self.app.processEvents()
        image = self.win.focus_window.grab().toImage()
        self.assertAlmostEqual(image.pixelColor(5, 100).alpha(), 38, delta=2)
        self.win.set_overlay_alpha(80)
        self.app.processEvents()
        self.assertAlmostEqual(self.win.focus_window.grab().toImage().pixelColor(5,100).alpha(), 204, delta=2)
        self.assertIn("color: white", self.win.focus_window.styleSheet())
        self.win.focus_window.close()

    def test_openai_settings_and_background_probe(self):
        with patch("konspekt.app.Recorder"), patch("konspekt.app.list_microphones", return_value=[]):
            dlg = SettingsDialog(dict(config.DEFAULTS))
        dlg.provider.setCurrentIndex(dlg.provider.findData("openai"))
        dlg.openai.setText("test-key")
        with patch.object(diagnostics, "probe", return_value={"ok": True, "message": "Synthetic PASS"}), patch.object(account, "check_deepgram", return_value="STT"):
            dlg.check_keys(live=True)
            deadline = time.monotonic() + 2
            while dlg._checking and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
        self.assertFalse(dlg._checking)
        self.assertIn("Synthetic PASS", dlg.check_result.text())
        dlg.apply()
        self.assertEqual(dlg.cfg["llm_provider"], "openai")
        self.assertEqual(dlg.cfg["openai_key"], "test-key")
        dlg.close()

    def test_subscription_only_selects_discovered_models_after_consent(self):
        with patch("konspekt.app.Recorder"), patch("konspekt.app.list_microphones", return_value=[]):
            dlg = SettingsDialog(dict(config.DEFAULTS))
        dlg.provider.setCurrentIndex(dlg.provider.findData("chatgpt"))
        dlg.on_chatgpt_connected({"session":{"sharing":False},"models":[]})
        self.assertIn("не разрешено", dlg.chatgpt_notice.text())
        dlg.on_chatgpt_connected({"session":{"sharing":True},"models":[{"slug":"account-luna","displayName":"Luna"}]})
        dlg.apply()
        self.assertEqual(dlg.cfg["llm_provider"], "chatgpt")
        self.assertEqual(dlg.cfg["chatgpt_model"], "account-luna")
        dlg.close()


if __name__ == "__main__":
    unittest.main()
