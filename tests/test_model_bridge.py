import httpx
import pytest
from fastapi.testclient import TestClient

from app.model_bridge import app


def request_body(**overrides):
    return {"model": "GigaChat-2", "messages": [{"role": "user", "content": "Синтетический текст"}], **overrides}


def test_bridge_requires_own_key(monkeypatch):
    monkeypatch.setenv("MODEL_BRIDGE_API_KEY", "internal-test")
    with TestClient(app) as client:
        assert client.post("/v1/chat/completions", json=request_body()).status_code == 401


@pytest.mark.parametrize("override", [{"tools": []}, {"stream": True}, {"model": "other"}, {"max_tokens": 2000}])
def test_bridge_rejects_unexpected_calls(monkeypatch, override):
    monkeypatch.setenv("MODEL_BRIDGE_API_KEY", "internal-test")
    with TestClient(app) as client:
        assert client.post("/v1/chat/completions", headers={"Authorization": "Bearer internal-test"},
                           json=request_body(**override)).status_code in {400, 422}


def test_bridge_oauth_then_completion(monkeypatch):
    monkeypatch.setenv("MODEL_BRIDGE_API_KEY", "internal-test")
    monkeypatch.setenv("GIGACHAT_AUTH_KEY", "synthetic-auth-key")
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/oauth"):
            return httpx.Response(200, json={"access_token": "synthetic-access-token"})
        assert request.headers["Authorization"] == "Bearer synthetic-access-token"
        return httpx.Response(200, json={"choices": [{"message": {"content": "Ответ"}}]})
    client_class = httpx.AsyncClient
    monkeypatch.setattr("app.model_bridge.httpx.AsyncClient", lambda **kwargs: client_class(
        **kwargs, transport=httpx.MockTransport(handler)
    ))
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", headers={"Authorization": "Bearer internal-test"}, json=request_body())
        assert response.status_code == 200
    assert len(calls) == 2
