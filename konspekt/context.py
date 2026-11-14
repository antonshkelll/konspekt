"""Bounded local file extraction. Documents are sent only with an AI request."""
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree

MAX_BYTES = 5 * 1024 * 1024
MAX_CHARS = 16000


def compose_context(meeting: str, attachments: dict[str, str], compact=False) -> str:
    if not compact:
        return meeting[:4000] + "\n\n" + "\n\n".join(f"Файл: {name}\n{text}" for name, text in attachments.items())
    parts = [meeting[:800]]
    budget = max(0, 3400 - len(parts[0]))
    share = budget // max(1, len(attachments))
    for name, text in attachments.items():
        header = f"Файл: {name[:100]}\n"
        parts.append(header + text[:max(0, share-len(header)-30)] + "\n[фрагмент файла]")
    return "\n\n".join(parts)[:3500]


def read_context(path: str) -> str:
    p = Path(path)
    if p.stat().st_size > MAX_BYTES:
        raise ValueError("Файл больше 5 МБ. Выбери сокращённую версию.")
    suffix = p.suffix.lower()
    if suffix in (".txt", ".md", ".csv"):
        text = p.read_text(encoding="utf-8-sig")
    elif suffix == ".docx":
        with ZipFile(p) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > MAX_BYTES:
                raise ValueError("Содержимое DOCX больше 5 МБ.")
            root = ElementTree.fromstring(archive.read(info))
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        text = "\n".join("".join(t.itertext()) for t in root.findall(".//w:p", ns))
    elif suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(p)
        parts = []
        for page in reader.pages[:50]:
            parts.append(page.extract_text() or "")
            if sum(map(len, parts)) > MAX_CHARS:
                break
        text = "\n".join(parts)
    else:
        raise ValueError("Поддерживаются TXT, MD, CSV, DOCX и текстовые PDF.")
    text = text.strip()
    if not text:
        raise ValueError("В файле нет текста. Для сканов нужен OCR.")
    if len(text) > MAX_CHARS:
        raise ValueError("В файле больше 16 000 символов. Прикрепи нужный фрагмент.")
    return text
