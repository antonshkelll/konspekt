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
from konspekt import config, llm
from konspekt.context import read_context, compose_context
from konspekt.app import MainWindow
from konspekt.groq_stt import GroqSource


class AudioTests(unittest.TestCase):
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
        cfg = dict(config.DEFAULTS, deepseek_key="test-key")
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


if __name__ == "__main__":
    unittest.main()
