import asyncio
import json
import os
import sys
from importlib.metadata import distribution
from importlib.resources import files
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from starlette.testclient import TestClient

import kudo_agent
from kudo_agent.application.config import Settings
from kudo_agent.application.service import Application
from kudo_agent.integrations.web import create_web_app
from kudo_agent.research_core.models import EMAIL_SITES


async def main():
    data_dir = Path(sys.argv[1]).resolve()
    package_path = Path(kudo_agent.__file__).resolve()
    assert "site-packages" in package_path.parts, package_path
    resource = files("kudo_agent.upstream")
    provenance = json.loads(resource.joinpath("PROVENANCE.json").read_text())
    assert len(provenance["sources"]) == 2
    for name in ("maigret", "sherlock"):
        assert json.loads(resource.joinpath(f"data/{name}.json").read_text())
        assert "MIT" in resource.joinpath(f"licenses/{name}.txt").read_text()
    metadata = distribution("kudo-agent")
    assert any(str(path).endswith("THIRD_PARTY_NOTICES.md") for path in metadata.files)
    with TestClient(create_web_app(Settings(data_dir=data_dir / "web")), base_url="http://127.0.0.1") as client:
        assert client.get("/").status_code == 200
        for asset in ("app.js", "app.css", "icon.svg"):
            assert client.get(f"/static/{asset}").status_code == 200
        assert len(client.get("/api/bootstrap").json()["capabilities"]["email"]["sites"]) == len(EMAIL_SITES)
        assert client.get("/api/bootstrap").json()["budget"]["max_sites"] is None
        client.headers["X-Kudo-Token"] = client.get("/api/bootstrap").json()["token"]
        prepared = client.post("/api/prepare", json={"clues": "https://github.com/sherlock-project"})
        assert prepared.status_code == 200
        assert prepared.json()["identifiers"][0]["kind"] == "profile"
        assert prepared.json()["request"]["sites"] == []
        assert prepared.json()["request"]["goal"]
        domestic = client.get("/api/capabilities", params={"query": "知乎", "region": "CN"}).json()
        assert any(site["name"] == "Zhihu" and "知乎" in site["aliases"] for site in domestic["sites"])
    async with Application(Settings(data_dir=data_dir / "application")) as app:
        capabilities = app.list_capabilities(query="GitHub")
        assert any(site["name"] == "GitHub" for site in capabilities["sites"])
        assert set(capabilities["email"]["sites"]) == set(EMAIL_SITES)
        assert {item["site"] for item in capabilities["email"]["providers"]} == set(EMAIL_SITES)
        assert all(item["last_check"]["checked_on"] for item in capabilities["email"]["providers"])
    binary = Path(sys.executable).parent / ("kudo-mcp.exe" if os.name == "nt" else "kudo-mcp")
    environment = {**os.environ, "KUDO_DATA_DIR": str(data_dir / "mcp")}
    async with stdio_client(StdioServerParameters(command=str(binary), env=environment)) as streams:
        async with ClientSession(*streams, read_timeout_seconds=30) as client:
            await client.initialize()
            tools = await client.list_tools()
            assert len(tools.tools) == 9
            result = await client.call_tool("list_capabilities", {"query": "GitHub"})
            assert not result.is_error
            assert result.structured_content["inputs"] == ["username", "profile", "email", "phone", "ip"]
            assert set(result.structured_content["email"]["sites"]) == set(EMAIL_SITES)
    print(json.dumps({"installed_package": str(package_path), "resources_verified": True,
                      "mcp_tools": len(tools.tools), "web_verified": True}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
