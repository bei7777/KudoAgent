import json
import secrets
from contextlib import asynccontextmanager
from importlib.resources import files
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, ValidationError
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from kudo_agent import __version__
from kudo_agent.application.config import Settings
from kudo_agent.application.intake import ResearchIntake
from kudo_agent.application.service import Application
from kudo_agent.research_agent.planner import Planner
from kudo_agent.research_core.domestic import DOMESTIC_PLATFORMS
from kudo_agent.research_core.models import Budget, Model, ResearchRequest


class PageQuery(Model):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)
    query: str = Field(default="", max_length=200)


class CatalogQuery(PageQuery):
    region: Literal["", "CN"] = ""


class EvidenceQuery(PageQuery):
    view: Literal["all", "pages", "email", "metadata", "uncertain"] = "all"
    evidence_id: str = Field(default="", pattern=r"^(?:[a-f0-9]{64})?$")


class ResumeRequest(Model):
    retry_interrupted_accounts: bool = False


class ConfirmationRequest(Model):
    source: str
    target: str
    statement: str = Field(min_length=1, max_length=4000)


class ReportQuery(Model):
    format: Literal["markdown", "json"] = "json"
    download: bool = False


class LocalAccess(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        origin = request.headers.get("origin")
        if request.headers.get("sec-fetch-site") == "cross-site" or (
            origin and origin != str(request.base_url).rstrip("/")
        ):
            return JSONResponse({"error": "仅接受本机应用页面的请求"}, status_code=403)
        if request.method not in {"GET", "HEAD"}:
            if not secrets.compare_digest(request.headers.get("x-kudo-token", ""), request.app.state.token):
                return JSONResponse({"error": "页面会话已经失效，请刷新页面"}, status_code=403)
            if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
                return JSONResponse({"error": "请求必须使用 JSON"}, status_code=415)
        response = await call_next(request)
        response.headers.update({
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
                                       "img-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
                                       "base-uri 'none'; form-action 'self'",
        })
        return response


def service(request: Request) -> Application:
    return request.app.state.application


async def read_body(request: Request, schema):
    body = await request.body()
    if len(body) > 128_000:
        raise HTTPException(413, "请求内容超过允许大小")
    return schema.model_validate_json(body)


def run_id(request: Request) -> str:
    return str(request.path_params["run_id"])


async def index(request):
    return FileResponse(files("kudo_agent.integrations").joinpath("web_static/index.html"), media_type="text/html")


async def bootstrap(request):
    app = service(request)
    settings = app.settings
    return JSONResponse({
        "token": request.app.state.token, "version": __version__,
        "model": settings.model, "base_url": settings.base_url,
        "model_configured": bool(settings.model and settings.base_url and settings.api_key.get_secret_value()),
        "api_key_env": settings.api_key_env, "data_dir": str(settings.data_dir),
        "budget": Budget().model_dump(), "capabilities": app.list_capabilities(limit=50),
        "domestic_platforms": DOMESTIC_PLATFORMS,
    })


async def capabilities(request):
    query = CatalogQuery.model_validate(dict(request.query_params))
    return JSONResponse(service(request).list_capabilities(**query.model_dump()))


async def prepare(request):
    payload = await read_body(request, ResearchIntake)
    return JSONResponse(payload.prepare())


async def researches(request):
    if request.method == "POST":
        payload = await read_body(request, ResearchRequest)
        return JSONResponse(service(request).start_research(payload), status_code=202)
    query = PageQuery.model_validate(dict(request.query_params))
    return JSONResponse(service(request).list_research(**query.model_dump()))


async def research(request):
    return JSONResponse(service(request).get_research(run_id(request)))


async def evidence(request):
    query = EvidenceQuery.model_validate(dict(request.query_params))
    return JSONResponse(service(request).get_evidence(run_id(request), query.offset, query.limit,
                       view=query.view, query=query.query, evidence_id=query.evidence_id))


async def report(request):
    query = ReportQuery.model_validate(dict(request.query_params))
    value = service(request).get_report(run_id(request), query.format)
    if query.download:
        extension = "md" if query.format == "markdown" else "json"
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
        return PlainTextResponse(text, headers={"Content-Disposition": f'attachment; filename="kudo-{run_id(request)}.{extension}"'})
    return JSONResponse({"markdown": value} if query.format == "markdown" else value)


async def artifact(request):
    metadata, body = service(request).get_artifact(run_id(request), request.path_params["artifact_id"])
    if request.query_params.get("download") == "true":
        return Response(body, media_type="application/octet-stream", headers={
            "Content-Disposition": f'attachment; filename="{metadata.artifact_id}.bin"',
        })
    content_type = metadata.headers.get("content-type", "")
    binary = content_type.startswith(("image/", "audio/", "video/"))
    return JSONResponse({"artifact": metadata.model_dump(), "binary": binary, "bytes": len(body),
                         "truncated": len(body) > 100_000,
                         "text": "" if binary else body[:100_000].decode(metadata.encoding, errors="replace")})


async def cancel(request):
    return JSONResponse(service(request).cancel_research(run_id(request)))


async def resume(request):
    payload = await read_body(request, ResumeRequest)
    return JSONResponse(service(request).resume_research(run_id(request), payload.retry_interrupted_accounts), status_code=202)


async def confirmation(request):
    payload = await read_body(request, ConfirmationRequest)
    return JSONResponse(await service(request).record_confirmation(run_id(request), **payload.model_dump()))


async def comparison(request):
    previous, current = str(request.path_params["previous"]), str(request.path_params["current"])
    return JSONResponse(service(request).compare_research(previous, current))


async def doctor(request):
    planner = Planner(service(request).settings)
    try:
        return JSONResponse(await planner.doctor())
    finally:
        await planner.close()


async def input_error(request, exception):
    if isinstance(exception, ValidationError):
        return JSONResponse({"error": "输入内容不符合要求", "details": exception.errors(include_url=False, include_context=False, include_input=False)},
                            status_code=422)
    if isinstance(exception, HTTPException):
        return JSONResponse({"error": str(exception.detail)}, status_code=exception.status_code)
    return JSONResponse({"error": str(exception)}, status_code=404 if isinstance(exception, KeyError) else 400)


def create_web_app(settings: Settings) -> Starlette:
    if settings.base_url and urlsplit(settings.base_url).username:
        raise ValueError("模型地址不能包含登录凭据")

    @asynccontextmanager
    async def lifespan(app):
        async with Application(settings) as application:
            app.state.application = application
            app.state.token = secrets.token_urlsafe(32)
            yield

    return Starlette(lifespan=lifespan, routes=[
        Route("/", index), Route("/api/bootstrap", bootstrap), Route("/api/capabilities", capabilities),
        Route("/api/prepare", prepare, methods=["POST"]),
        Route("/api/research", researches, methods=["GET", "POST"]),
        Route("/api/research/{run_id:uuid}", research),
        Route("/api/research/{run_id:uuid}/evidence", evidence),
        Route("/api/research/{run_id:uuid}/report", report),
        Route("/api/research/{run_id:uuid}/artifacts/{artifact_id}", artifact),
        Route("/api/research/{run_id:uuid}/cancel", cancel, methods=["POST"]),
        Route("/api/research/{run_id:uuid}/resume", resume, methods=["POST"]),
        Route("/api/research/{run_id:uuid}/confirm", confirmation, methods=["POST"]),
        Route("/api/compare/{previous:uuid}/{current:uuid}", comparison),
        Route("/api/doctor", doctor, methods=["POST"]),
        Mount("/static", app=StaticFiles(directory=files("kudo_agent.integrations").joinpath("web_static"))),
    ], middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"]), Middleware(LocalAccess)],
        exception_handlers={ValidationError: input_error, ValueError: input_error, KeyError: input_error, HTTPException: input_error})
