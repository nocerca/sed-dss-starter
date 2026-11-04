"""Export the public schema without connecting to DB or exposing settings."""
import argparse
import json
import sys
from pathlib import Path

from app.main import app

target = Path(__file__).resolve().parent.parent / "docs" / "openapi.json"
parser = argparse.ArgumentParser()
parser.add_argument("--check", action="store_true")
args = parser.parse_args()
content = json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n"
if args.check:
    if not target.exists() or target.read_text(encoding="utf-8") != content:
        print("OpenAPI отличается от текущего кода")
        sys.exit(1)
    print("OpenAPI актуален")
else:
    target.write_text(content, encoding="utf-8")
    print("OpenAPI сохранён в docs/openapi.json")
