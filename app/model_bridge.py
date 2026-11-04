"""Internal OpenClaw -> GigaChat OAuth adapter. No DB, documents or tools."""

import hmac
import os
from uuid import uuid4

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

app = FastAPI(title="Внутренний адаптер GigaChat", docs_url=None, redoc_url=None)


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str = Field(pattern="^(system|user|assistant)$")
    content: str = Field(max_length=60000)


class CompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    messages: list[Message] = Field(min_length=1, max_length=8)
    stream: bool = False
    temperature: float = Field(default=0.1, ge=0, le=2)
    max_tokens: int = Field(default=1600, ge=1, le=1600)


@app.get("/health/live")
def health_live() -> dict:
    return {"status": "ok"}


@app.post("/v1/chat/completions")
async def completions(request: CompletionRequest, authorization: str = Header("")) -> dict:
    expected = os.environ.get("MODEL_BRIDGE_API_KEY", "")
    if not expected or not hmac.compare_digest(authorization.encode(), f"Bearer {expected}".encode()):
        raise HTTPException(401, "Нет доступа к адаптеру модели")
    if request.stream or request.model != os.environ.get("GIGACHAT_MODEL", "GigaChat-2"):
        raise HTTPException(400, "Неподдерживаемая модель или потоковый режим")
    key = os.environ.get("GIGACHAT_AUTH_KEY", "")
    if not key:
        raise HTTPException(503, "GigaChat не настроен")
    try:
        async with httpx.AsyncClient(
            timeout=40, trust_env=False, follow_redirects=False,
            verify=os.environ.get("GIGACHAT_CA_BUNDLE") or True,
        ) as client:
            token_response = await client.post(
                "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
                headers={"Authorization": f"Basic {key}", "RqUID": str(uuid4())},
                data={"scope": os.environ.get("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")},
            )
            token_response.raise_for_status()
            token = token_response.json()["access_token"]
            response = await client.post(
                "https://api.giga.chat/v1/chat/completions",
                headers={"Authorization": f"Bearer {token}"},
                json=request.model_dump(),
            )
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, KeyError, ValueError, OSError) as exc:
        # No upstream bodies, credentials, letter contents or auth endpoints in errors.
        raise HTTPException(502, "Ошибка обращения адаптера к GigaChat") from exc
