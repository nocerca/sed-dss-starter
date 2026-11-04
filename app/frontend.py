"""Same-origin web client; the integration API remains independently usable."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

STATIC_DIRECTORY = Path(__file__).resolve().parent / "static"
router = APIRouter()
FRONTEND_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'none'"
)


class FrontendFiles(StaticFiles):
    """Windows MIME registry can incorrectly identify ES modules as plain text."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if path.endswith('.mjs'):
            response.headers['Content-Type'] = 'text/javascript; charset=utf-8'
        return response


@router.get("/", include_in_schema=False)
def web_client() -> FileResponse:
    return FileResponse(STATIC_DIRECTORY / "index.html", media_type="text/html")
