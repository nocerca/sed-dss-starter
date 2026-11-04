import copy
import json
import socket
from io import BytesIO

import pytest
from docx import Document

from scripts.export_letter_policy import (
    ROOT,
    letters_inventory,
    load_policy,
    render_policy,
    source_binding,
)


def test_policy_is_sourced_scoped_and_only_declared_subset_is_activated():
    policy = load_policy(ROOT / "config" / "letter_policy.json")
    checks = {check["id"]: check for check in policy["checks"]}
    assert policy["runtime_enabled"] is True
    from app.letter_checks import IMPLEMENTED
    assert set(policy['implemented_checks']) == set(IMPLEMENTED)
    assert policy["baseline_examples_are_gold"] is False
    assert checks["ORG_DATE_FORMAT"]["expected"]["accepted_forms"] == [
        "numeric_dmy", "russian_verbal_dmy",
    ]
    assert checks["ORG_REFERENCE_DATA_FONT"]["expected"]["size_pt"] == 9
    assert checks["ORG_EXECUTOR_FONT"]["expected"]["size_pt"] == 12
    assert checks["ORG_BODY_FONT_SIZE"]["expected"] == {"min_pt": 10, "max_pt": 14}
    for code in ("REVIEW_EXCEL_PHRASE", "REVIEW_NUMERIC_DATE_ONLY", "REVIEW_EXECUTOR_WORD_FOOTER"):
        assert checks[code]["level"] == "unconfirmed"
    assert all(check["expected"]["verified"] is False for check in checks.values()
               if check["level"] == "advisory")


@pytest.mark.parametrize("mutation", ["duplicate", "missing_source", "unknown_source", "outside_chapter",
                                     "runtime_enabled", "gold_examples", "missing_applicability"])
def test_invalid_specification_fails_closed(tmp_path, mutation):
    policy = json.loads((ROOT / "config" / "letter_policy.json").read_text(encoding="utf-8"))
    if mutation == "duplicate":
        policy["checks"].append(copy.deepcopy(policy["checks"][0]))
    elif mutation == "missing_source":
        policy["checks"][0]["sources"] = []
    elif mutation == "unknown_source":
        policy["checks"][0]["sources"] = ["invented"]
    elif mutation == "outside_chapter":
        policy["references"]["III.2.1"]["paragraphs"] = [700]
    elif mutation == "runtime_enabled":
        policy["runtime_enabled"] = False
    elif mutation == "gold_examples":
        policy["baseline_examples_are_gold"] = True
    else:
        policy["checks"][0]["applies_when"] = ""
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy), encoding="utf-8")
    with pytest.raises(ValueError):
        load_policy(path)


def test_published_document_matches_catalog():
    policy = load_policy(ROOT / "config" / "letter_policy.json")
    assert (ROOT / "docs" / "LETTER_POLICY.md").read_text(encoding="utf-8") == render_policy(policy)


def test_local_binding_and_inventory_do_not_transmit_or_copy_content(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Local catalog tooling must not connect to a network")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    document = Document()
    document.add_paragraph("SYNTHETIC_PRIVATE_MARKER")
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "TABLE_PRIVATE_MARKER"
    document.add_paragraph("SECOND_PRIVATE_MARKER")
    stream = BytesIO()
    document.save(stream)
    source = tmp_path / "synthetic.docx"
    source.write_bytes(stream.getvalue())
    policy = {
        "source_alias": "synthetic_only",
        "references": {"test": {"paragraphs": [0, 1]}},
    }
    binding = source_binding(policy, source)
    anchors = binding["references"]["test"]
    assert [anchor["fragment_id"] for anchor in anchors] == ["body/p[0]", "body/p[2]"]
    assert len(binding["source_sha256"]) == 64
    inventory = letters_inventory(tmp_path)
    assert inventory["count"] == 1
    record = inventory["documents"][0]
    assert record["label"] == "unreviewed"
    assert record["allow_external_transfer"] is False
    assert record["split"] is None
    serialized = json.dumps([binding, inventory])
    assert "PRIVATE_MARKER" not in serialized
    assert source.read_bytes() == stream.getvalue()
    policy["references"]["test"]["paragraphs"] = [2]
    with pytest.raises(ValueError):
        source_binding(policy, source)


def test_private_material_is_excluded_from_repository_and_image():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "examples/**" in ignored
    exceptions = [line[1:] for line in ignored.splitlines() if line.startswith("!examples/")]
    assert set(exceptions) == {"examples/README.md", "examples/postman_synthetic.docx"}
    assert "examples" in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()


def test_inventory_rejects_missing_directory(tmp_path):
    with pytest.raises(ValueError):
        letters_inventory(tmp_path / "missing")
