import json
from contextlib import asynccontextmanager
from functools import wraps
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

from kudo_agent.application.config import Settings
from kudo_agent.application.service import Application
from kudo_agent.research_core.models import BudgetIncrease, ResearchRequest
from kudo_agent.research_core.site_categories import SiteCategory


def explain_input_errors(function):
    @wraps(function)
    async def execute(*args, **kwargs):
        try:
            return await function(*args, **kwargs)
        except (ValueError, KeyError, FileNotFoundError) as error:
            raise ToolError(str(error)) from error
    return execute


def create_server(settings: Settings) -> MCPServer:
    services = {}

    @asynccontextmanager
    async def lifespan(server):
        async with Application(settings) as service:
            services["application"] = service
            yield service

    server = MCPServer(
        "KudoAgent", version="0.1.0", lifespan=lifespan,
        instructions="使用 get_status 查询后台任务。completed 表示执行完成，partial 表示预算用完，"
                     "两种状态均可用 get_report 读取结果。coverage 记录初始范围，can_resume 表示是否可以恢复。",
    )

    @server.tool(structured_output=True)
    @explain_input_errors
    async def list_capabilities(offset: int = 0, limit: int = 50, query: str = "", region: str = "",
                                categories: list[SiteCategory] | None = None) -> dict[str, Any]:
        """查询输入能力、站点目录和接口行为。"""
        return services["application"].list_capabilities(offset, limit, query, region, categories=categories)

    @server.tool(structured_output=True)
    @explain_input_errors
    async def start_research(request: ResearchRequest) -> dict[str, Any]:
        """创建研究任务并返回任务标识。"""
        return services["application"].start_research(request)

    @server.tool(structured_output=True)
    @explain_input_errors
    async def get_status(run_id: str) -> dict[str, Any]:
        """读取进度、预算与停止原因。"""
        return services["application"].get_status(run_id)

    @server.tool(structured_output=True)
    @explain_input_errors
    async def get_evidence(run_id: str, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        """分页读取证据及其关联来源。"""
        return services["application"].get_evidence(run_id, offset, limit)

    @server.tool()
    @explain_input_errors
    async def get_report(run_id: str, format: Literal["markdown", "json"] = "markdown", retry: bool = False) -> CallToolResult:
        """读取研究报告；retry=true 根据已有证据重新生成，随后可查询进度。"""
        if retry:
            result = services["application"].retry_report(run_id)
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(result))], structured_content=result)
        result = services["application"].get_report(run_id, format)
        if isinstance(result, str):
            return CallToolResult(content=[TextContent(type="text", text=result)])
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
                              structured_content=result)

    @server.tool(structured_output=True)
    @explain_input_errors
    async def cancel_research(run_id: str) -> dict[str, Any]:
        """取消指定任务及正在执行的请求。"""
        return services["application"].cancel_research(run_id)

    @server.tool(structured_output=True)
    @explain_input_errors
    async def resume_research(run_id: str, retry_interrupted_accounts: bool = False,
                              additional_seconds: float = 0, additional_requests: int = 0) -> dict[str, Any]:
        """恢复任务；未确认完成的账户请求需要明确指定重新执行。"""
        if additional_seconds or additional_requests:
            return services["application"].extend_budget(run_id, BudgetIncrease(
                additional_seconds=additional_seconds, additional_requests=additional_requests))
        return services["application"].resume_research(run_id, retry_interrupted_accounts)

    @server.tool(structured_output=True)
    @explain_input_errors
    async def compare_research(previous: str, current: str) -> dict[str, Any]:
        """比较两项研究的新增、变化与无法核查项目。"""
        return services["application"].compare_research(previous, current)

    @server.tool(structured_output=True)
    @explain_input_errors
    async def record_confirmation(run_id: str, source: str, target: str, statement: str) -> dict[str, Any]:
        """记录用户明确提供的关系确认声明；模型不得自行推断或代替用户确认。"""
        return await services["application"].record_confirmation(run_id, source, target, statement)

    return server


def main():
    create_server(Settings.from_env()).run(transport="stdio")
