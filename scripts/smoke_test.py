"""Smoke test using only a generated synthetic DOCX and Python's standard library."""

import argparse
import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile


def synthetic_docx() -> bytes:
    stream = io.BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        archive.writestr("word/document.xml", '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Просим в кратчайшие сроки направить перечень учебных материалов.</w:t></w:r></w:p><w:sectPr/></w:body></w:document>')
    return stream.getvalue()


def local_api_key(root: Path) -> str:
    for line in (root / ".env").read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("APP_API_KEY="):
            return line.split("=", 1)[1].strip().strip("\"'")
    raise ValueError("APP_API_KEY не задан в .env")


def run(base_url: str, api_key: str, use_llm: bool, use_language_checks: bool = False) -> dict:
    def request(path: str, body: bytes | None = None, content_type: str | None = None):
        headers = {"X-API-Key": api_key}
        if content_type:
            headers["Content-Type"] = content_type
        with urllib.request.urlopen(urllib.request.Request(
            base_url.rstrip("/") + path, data=body, headers=headers
        ), timeout=180) as response:
            return json.load(response)

    assert request("/health/live")["status"] == "ok", "API не отвечает"
    assert request("/health/ready")["status"] == "ready", "БД недоступна"
    capabilities = request("/api/v1/capabilities")
    assert capabilities.get("required_llm_gateway") == "openclaw", "Запущена старая версия API: пересоберите контейнер"
    boundary = uuid4().hex
    parts = []
    if use_llm:
        for name in ("use_llm", "allow_external_transfer"):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\ntrue\r\n'.encode())
    if use_language_checks:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="use_language_checks"\r\n\r\ntrue\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="synthetic.docx"\r\nContent-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document\r\n\r\n'.encode())
    parts.append(synthetic_docx())
    parts.append(f'\r\n--{boundary}--\r\n'.encode())
    report = request("/api/v1/analyze", b"".join(parts), f"multipart/form-data; boundary={boundary}")
    assert any(f["code"] == "CONTENT_VAGUE_DEADLINE" for f in report["findings"]), "Правило не сработало"
    assert request(f"/api/v1/reports/{report['id']}")["id"] == report["id"], "Отчёт не сохранён"
    if use_llm:
        assert report["mode"] == "hybrid" and report["status"] == "completed", "LLM-проверка не завершена; проверьте настройки и доступность модели"
    if use_language_checks:
        assert report["metrics"]["language_check"]["status"] == "completed", "Локальная языковая проверка не завершена"
    print("OK: API, БД, анализ синтетического DOCX, сохранение отчёта" + (", маршрут через OpenClaw" if use_llm else " (без LLM)"))
    if use_language_checks:
        print("OK: локальная орфография/грамматика LanguageTool")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Проверка только на встроенном синтетическом письме")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--llm", action="store_true", help="Явно разрешить передачу встроенного синтетического письма через OpenClaw во внешнюю модель")
    parser.add_argument("--language", action="store_true", help="Проверить локальный LanguageTool без внешней передачи")
    args = parser.parse_args()
    try:
        run(args.base_url, local_api_key(Path(__file__).resolve().parent.parent), args.llm, args.language)
    except (OSError, urllib.error.URLError, ValueError, AssertionError, KeyError) as exc:
        # Do not print server response bodies or secrets.
        print("Проверка не пройдена:", str(exc) if isinstance(exc, AssertionError) else type(exc).__name__)
        sys.exit(1)
