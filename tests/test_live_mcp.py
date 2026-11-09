import asyncio
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from kudo_agent.application.config import Settings

pytestmark = pytest.mark.live


async def test_real_mcp_research_cancel_resume(tmp_path):
    settings = Settings.from_env()
    settings.require_model()
    binary = Path(sys.executable).parent / ("kudo-mcp.exe" if os.name == "nt" else "kudo-mcp")
    environment = {**os.environ, "KUDO_DATA_DIR": str(tmp_path / "live-mcp")}
    request = {
        "goal": "检查 Sherlock 项目在 GitHub 的账户和公开主页，引用来源解释发现。",
        "identifiers": [{"kind": "username", "value": "sherlock-project"},
                        {"kind": "profile", "value": "https://github.com/sherlock-project"}],
        "sites": ["GitHub"],
    }
    async with stdio_client(StdioServerParameters(command=str(binary), env=environment)) as streams:
        async with ClientSession(*streams, read_timeout_seconds=120) as client:
            await client.initialize()
            created = await client.call_tool("start_research", {"request": request})
            assert not created.is_error
            run_id = created.structured_content["run_id"]
            async with asyncio.timeout(300):
                while True:
                    result = await client.call_tool("get_status", {"run_id": run_id})
                    progress = result.structured_content
                    assert progress["status"] not in {"failed", "interrupted", "completed"}, progress
                    if progress["evidence_count"]:
                        break
                    await asyncio.sleep(0.05)
            prior = await client.call_tool("get_evidence", {"run_id": run_id})
            before = {item["evidence_id"] for item in prior.structured_content["items"]}
            cancelled = await client.call_tool("cancel_research", {"run_id": run_id})
            assert not cancelled.is_error
            async with asyncio.timeout(30):
                while True:
                    result = await client.call_tool("get_status", {"run_id": run_id})
                    if result.structured_content["status"] == "cancelled":
                        break
                    await asyncio.sleep(0.05)
    async with stdio_client(StdioServerParameters(command=str(binary), env=environment)) as streams:
        async with ClientSession(*streams, read_timeout_seconds=120) as client:
            await client.initialize()
            resumed = await client.call_tool("resume_research", {"run_id": run_id})
            assert not resumed.is_error
            async with asyncio.timeout(600):
                while True:
                    result = await client.call_tool("get_status", {"run_id": run_id})
                    progress = result.structured_content
                    assert progress["status"] not in {"failed", "interrupted", "cancelled"}, progress
                    if progress["status"] == "completed":
                        break
                    await asyncio.sleep(0.1)
            evidence = await client.call_tool("get_evidence", {"run_id": run_id})
            assert before <= {item["evidence_id"] for item in evidence.structured_content["items"]}
            report = await client.call_tool("get_report", {"run_id": run_id, "format": "json"})
            assert not report.is_error
            assert report.structured_content["report"]["claims"]
