import asyncio
import os
import sys
from pathlib import Path

import phonenumbers
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from kudo_agent.research_core.models import EMAIL_SITES


async def test_real_stdio_server(tmp_path):
    binary = Path(sys.executable).parent / ("kudo-mcp.exe" if os.name == "nt" else "kudo-mcp")
    environment = {**os.environ, "KUDO_DATA_DIR": str(tmp_path / "mcp-data"),
                   "KUDO_BASE_URL": "", "KUDO_MODEL": "", "KUDO_API_KEY": "",
                   "KUDO_API_KEY_ENV": "KUDO_API_KEY"}
    async with stdio_client(StdioServerParameters(command=str(binary), env=environment)) as streams:
        async with ClientSession(*streams, read_timeout_seconds=30) as client:
            await client.initialize()
            tools = await client.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "list_capabilities", "start_research", "get_status", "get_evidence", "get_report",
                "cancel_research", "resume_research", "compare_research", "record_confirmation",
            }
            capabilities = await client.call_tool("list_capabilities", {"query": "GitHub", "limit": 5})
            assert not capabilities.is_error
            assert "GitHub" in str(capabilities.content)
            assert capabilities.structured_content["inputs"] == ["username", "profile", "email", "phone", "ip"]
            email = capabilities.structured_content["email"]
            assert set(email["sites"]) == set(EMAIL_SITES)
            providers = {provider["site"]: provider for provider in email["providers"]}
            assert providers.keys() == set(EMAIL_SITES)
            assert {"gitee", "tencent_cloud", "cnblogs", "jd"} <= {
                key for key, value in providers.items() if value["region"] == "CN"
            }
            assert providers["gravatar"]["interaction"] == "public_avatar"
            assert providers["duolingo"]["interaction"] == "public_profile"
            assert all(provider["last_check"]["checked_on"] for provider in providers.values())
            missing_configuration = await client.call_tool("start_research", {"request": {"goal": "检查配置"}})
            assert missing_configuration.is_error
            assert "KUDO" in str(missing_configuration.content)

            sample = phonenumbers.format_number(
                phonenumbers.example_number("CH"), phonenumbers.PhoneNumberFormat.E164
            )
            started = await client.call_tool("start_research", {"request": {
                "goal": "查询号码的号段资料", "identifiers": [{"kind": "phone", "value": sample}],
            }})
            assert not started.is_error
            run_id = started.structured_content["run_id"]
            async with asyncio.timeout(20):
                while True:
                    result = await client.call_tool("get_status", {"run_id": run_id})
                    assert not result.is_error
                    if result.structured_content["status"] not in {"queued", "running"}:
                        break
                    await asyncio.sleep(0.05)
            assert result.structured_content["status"] == "completed"
            assert result.structured_content["model_usage"]["attempts"] == 0
            assert result.structured_content["request_count"] == 0
            report = await client.call_tool("get_report", {"run_id": run_id, "format": "json"})
            assert not report.is_error
            evidence = report.structured_content["evidence"]
            assert len(evidence) == 1 and evidence[0]["subject"] == sample
            claims = report.structured_content["report"]["claims"]
            assert any(evidence[0]["evidence_id"] in claim["evidence_ids"] for claim in claims)
            limited = await client.call_tool("start_research", {"request": {
                "goal": "整理号码资料", "identifiers": [{"kind": "phone", "value": sample}],
                "budget": {"run_timeout": 0.000001},
            }})
            partial_id = limited.structured_content["run_id"]
            async with asyncio.timeout(20):
                while True:
                    state = (await client.call_tool("get_status", {"run_id": partial_id})).structured_content
                    if state["status"] == "partial":
                        break
                    await asyncio.sleep(0.05)
            assert state["can_extend_budget"]
            resumed = await client.call_tool("resume_research", {"run_id": partial_id, "additional_seconds": 5})
            assert not resumed.is_error and resumed.structured_content["run_id"] == partial_id
            async with asyncio.timeout(20):
                while True:
                    state = (await client.call_tool("get_status", {"run_id": partial_id})).structured_content
                    if state["status"] == "completed":
                        break
                    await asyncio.sleep(0.05)
            assert state["report_status"] == "ready" and state["request_count"] == 0
