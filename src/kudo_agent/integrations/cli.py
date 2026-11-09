import asyncio
import json
import logging
from pathlib import Path
from typing import Annotated

import typer
import uvicorn
from rich.console import Console

from kudo_agent.application.config import Settings
from kudo_agent.application.service import Application
from kudo_agent.integrations.web import create_web_app
from kudo_agent.research_agent.planner import Planner
from kudo_agent.research_core.models import Identifier, ResearchRequest

cli = typer.Typer(help="KudoAgent：公开足迹核验", pretty_exceptions_show_locals=False)
console = Console()


@cli.callback()
def setup(
    ctx: typer.Context,
    env_file: Annotated[Path | None, typer.Option(help="读取指定的本地 dotenv 配置")] = None,
    data_dir: Annotated[Path | None, typer.Option(help="任务与证据目录")] = None,
):
    ctx.obj = Settings.from_env(env_file, data_dir)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def output(value):
    console.print_json(json.dumps(value, ensure_ascii=False))


@cli.command()
def research(
    ctx: typer.Context,
    goal: Annotated[str, typer.Argument(help="研究目标与已知标识符")],
    request_file: Annotated[Path | None, typer.Option(help="完整 ResearchRequest JSON 文件")] = None,
    username: Annotated[list[str] | None, typer.Option()] = None,
    profile: Annotated[list[str] | None, typer.Option()] = None,
    email: Annotated[list[str] | None, typer.Option()] = None,
    phone: Annotated[list[str] | None, typer.Option()] = None,
    ip: Annotated[list[str] | None, typer.Option()] = None,
    site: Annotated[list[str] | None, typer.Option()] = None,
    email_site: Annotated[list[str] | None, typer.Option(help="可选的邮箱范围限制；默认全部站点")] = None,
):
    if request_file:
        request = ResearchRequest.model_validate_json(request_file.read_text())
    else:
        identifiers = [Identifier(kind=kind, value=value) for kind, values in
                       (("username", username), ("profile", profile), ("email", email), ("phone", phone), ("ip", ip))
                       for value in (values or [])]
        request = ResearchRequest(goal=goal, identifiers=identifiers, sites=site or [], email_sites=email_site or [])

    async def execute():
        async with Application(ctx.obj) as app:
            started = app.start_research(request)
            output(started)
            output(await app.wait(started["run_id"]))
            console.print(str(app.store.run_dir(started["run_id"]) / "report.md"))

    asyncio.run(execute())


def read_command(settings: Settings, operation):
    async def execute():
        async with Application(settings) as app:
            return operation(app)
    return asyncio.run(execute())


@cli.command()
def sites(ctx: typer.Context, query: str = "", offset: int = 0, limit: int = 50, region: str = ""):
    output(read_command(ctx.obj, lambda app: app.list_capabilities(offset, limit, query, region)))


@cli.command("email-sites")
def email_sites(ctx: typer.Context):
    output(read_command(ctx.obj, lambda app: app.list_capabilities(limit=1)["email"]))


@cli.command()
def status(ctx: typer.Context, run_id: str):
    output(read_command(ctx.obj, lambda app: app.get_status(run_id)))


@cli.command()
def evidence(ctx: typer.Context, run_id: str, offset: int = 0, limit: int = 50):
    output(read_command(ctx.obj, lambda app: app.get_evidence(run_id, offset, limit)))


@cli.command()
def report(ctx: typer.Context, run_id: str, format: str = "markdown", destination: Path | None = None):
    result = read_command(ctx.obj, lambda app: app.get_report(run_id, format))
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2)
    if destination:
        destination.write_text(text, encoding="utf-8")
    else:
        typer.echo(text)


@cli.command()
def cancel(ctx: typer.Context, run_id: str):
    output(read_command(ctx.obj, lambda app: app.cancel_research(run_id)))


@cli.command()
def resume(ctx: typer.Context, run_id: str, retry_interrupted_accounts: bool = False):
    async def execute():
        async with Application(ctx.obj) as app:
            output(app.resume_research(run_id, retry_interrupted_accounts))
            output(await app.wait(run_id))
    asyncio.run(execute())


@cli.command("compare")
def compare_runs(ctx: typer.Context, previous: str, current: str):
    output(read_command(ctx.obj, lambda app: app.compare_research(previous, current)))


@cli.command("confirm")
def confirm_relation(ctx: typer.Context, run_id: str, source: str, target: str, statement: str):
    async def execute():
        async with Application(ctx.obj) as app:
            output(await app.record_confirmation(run_id, source, target, statement))
    asyncio.run(execute())


@cli.command()
def doctor(ctx: typer.Context, online: bool = False):
    async def execute():
        async with Application(ctx.obj) as app:
            result = {"catalog": app.list_capabilities(limit=1), "data_dir": str(app.store.root),
                      "model_configured": bool(ctx.obj.model and ctx.obj.base_url and ctx.obj.api_key.get_secret_value())}
            if online:
                planner = Planner(ctx.obj)
                try:
                    result["model"] = await planner.doctor()
                finally:
                    await planner.close()
            output(result)
    asyncio.run(execute())


@cli.command()
def web(ctx: typer.Context, port: Annotated[int, typer.Option(min=1024, max=65535)] = 8765):
    """打开本机研究工作台。"""
    typer.echo(f"KudoAgent 工作台：http://127.0.0.1:{port}")
    uvicorn.run(create_web_app(ctx.obj), host="127.0.0.1", port=port, access_log=False)


@cli.command()
def mcp(ctx: typer.Context):
    from kudo_agent.integrations.mcp import create_server
    create_server(ctx.obj).run(transport="stdio")


def main():
    cli()
