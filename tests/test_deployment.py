"""Check that backend credentials cannot accidentally reach the public API service."""

from pathlib import Path

import yaml


def test_model_credentials_are_scoped_to_gateway_or_bridge():
    config = yaml.safe_load((Path(__file__).resolve().parent.parent / "compose.yaml").read_text(encoding="utf-8"))
    services = config["services"]
    api = services["api"]["environment"]
    gateway = services["openclaw"]["environment"]
    bridge = services["model-bridge"]["environment"]
    for secret in ("ZAI_API_KEY", "YANDEX_API_KEY", "GIGACHAT_AUTH_KEY"):
        assert secret not in api
    assert "ZAI_API_KEY" in gateway and "YANDEX_API_KEY" in gateway
    assert "GIGACHAT_AUTH_KEY" in bridge and "GIGACHAT_AUTH_KEY" not in gateway
    assert "ZAI_API_KEY" not in bridge
    assert not services["openclaw"].get("ports")
    assert services["openclaw"]["read_only"] is True
    assert any("/home/node/.cache" in entry for entry in services["openclaw"]["tmpfs"])
    assert all(".env" not in entry for entry in services["openclaw"]["volumes"])


def test_local_language_service_has_no_egress_port_or_model_keys():
    config = yaml.safe_load((Path(__file__).resolve().parent.parent / "compose.yaml").read_text(encoding="utf-8"))
    language = config["services"]["languagetool"]
    assert language["networks"] == ["language"]
    assert config["networks"]["language"]["internal"] is True
    assert not language.get("ports") and not language.get("environment")
    assert language["read_only"] is True and language["profiles"] == ["language"]
    ignored = (Path(__file__).resolve().parent.parent / ".dockerignore").read_text().splitlines()
    assert "examples" in ignored
