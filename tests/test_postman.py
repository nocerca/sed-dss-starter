"""Check the portable client contract and external-transfer safeguards."""

import json
from pathlib import Path

from app.document_parser import parse_docx
from scripts.export_postman import build_collection, build_environment, export

ROOT = Path(__file__).resolve().parent.parent


def requests(collection):
    return [item for folder in collection["item"] for item in folder["item"]]


def script(item, kind):
    return "\n".join(line for event in item["event"] if event["listen"] == kind
                     for line in event["script"]["exec"])


def test_postman_matches_api_contract():
    schema = json.loads((ROOT / "docs/openapi.json").read_text(encoding="utf-8"))
    for item in requests(build_collection()):
        request = item["request"]
        path = request["url"].removeprefix("{{base_url}}").split("?", 1)[0]
        path = path.replace("{{report_id}}", "{report_id}")
        path = path.replace("CONTENT_VAGUE_DEADLINE", "{code}")
        operation = schema["paths"][path][request["method"].lower()]
        assert operation


def test_postman_requires_consent_and_contains_no_credentials():
    environment = {v["key"]: v for v in build_environment()["values"]}
    assert environment["app_api_key"]["value"] == ""
    assert environment["app_api_key"]["type"] == "secret"
    assert environment["external_consent"]["value"] == "false"
    assert environment["allow_delete"]["value"] == "false"
    collection = build_collection()
    assert collection["auth"]["type"] == "apikey"
    for item in requests(collection):
        request = item["request"]
        if request["method"] == "DELETE":
            assert 'get("allow_delete") !== "true"' in script(item, "prerequest")
            assert "skipRequest" in script(item, "prerequest")
        if "body" not in request:
            continue
        form = {field["key"]: field for field in request["body"]["formdata"]}
        if form["allow_external_transfer"]["value"] != "false":
            assert form["allow_external_transfer"]["value"] == "{{external_consent}}"
            assert 'get("external_consent") === "true"' in script(item, "prerequest")
            assert 'set("external_consent", "false")' in script(item, "prerequest")
            assert "skipRequest" not in script(item, "prerequest")
            assert "status(409)" in script(item, "test")
            assert "hybrid" in script(item, "test") and "completed" in script(item, "test")


def test_postman_export_does_not_read_secrets_or_overwrite_existing_file(tmp_path):
    (tmp_path / ".env").write_text("APP_API_KEY=PRIVATE_TEST_SENTINEL\n", encoding="utf-8")
    export(tmp_path)
    for path in (tmp_path / "postman").glob("*.json"):
        assert "PRIVATE_TEST_SENTINEL" not in path.read_text(encoding="utf-8")
    example = tmp_path / "examples/postman_synthetic.docx"
    assert "в кратчайшие сроки" in parse_docx(example.read_bytes()).text
    example.write_bytes(b"existing document must remain unchanged")
    export(tmp_path)
    assert example.read_bytes() == b"existing document must remain unchanged"


def test_committed_postman_files_are_current():
    for name, expected in [("SED-DSS.postman_collection.json", build_collection()),
                           ("SED-DSS.local.postman_environment.json", build_environment())]:
        assert json.loads((ROOT / "postman" / name).read_text(encoding="utf-8")) == expected
