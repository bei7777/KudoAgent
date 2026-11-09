import os
import sys
from pathlib import Path

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
