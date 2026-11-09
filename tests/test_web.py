from starlette.testclient import TestClient

from kudo_agent.application.config import Settings
from kudo_agent.integrations.web import create_web_app
from kudo_agent.research_core.models import EMAIL_SITES


def test_web_assets_and_real_capabilities(tmp_path):
    with TestClient(create_web_app(Settings(data_dir=tmp_path)), base_url="http://127.0.0.1") as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "KudoAgent" in page.text
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        for asset in ("app.js", "app.css", "icon.svg"):
            assert client.get(f"/static/{asset}").status_code == 200
        data = client.get("/api/bootstrap").json()
        assert data["model_configured"] is False
        assert "api_key" not in data
        providers = data["capabilities"]["email"]["providers"]
        assert len(providers) == len(EMAIL_SITES) == 15
        assert {provider["site"] for provider in providers} == set(EMAIL_SITES)
        assert {"Gitee", "腾讯云", "博客园", "京东"} <= {
            provider["name"] for provider in providers if provider["region"] == "CN"
        }
        platforms = {platform["name"]: platform for platform in data["domestic_platforms"]}
        assert len(platforms) == 10
        assert platforms["抖音"]["username_state"] == "profile_only"
        assert platforms["小红书"]["username_state"] == "profile_only"
        assert platforms["微博"]["username_state"] == "restricted"
        assert platforms["Gitee"]["username_state"] == "available"
        assert client.get("/api/research").json()["items"] == []
        sites = client.get("/api/capabilities?query=GitHub").json()["sites"]
        assert any(site["name"] == "GitHub" for site in sites)


def test_web_full_catalog_chinese_search_and_paging(tmp_path):
    with TestClient(create_web_app(Settings(data_dir=tmp_path)), base_url="http://127.0.0.1") as client:
        first = client.get("/api/capabilities?limit=30").json()
        second = client.get("/api/capabilities?offset=30&limit=30").json()
        assert first["total_sites"] == second["total_sites"] == 9011
        assert first["supported_sites"] == 8303
        assert len(first["sites"]) == len(second["sites"]) == 30
        assert all(site["region"] == "CN" for site in first["sites"] + second["sites"])
        assert [site["name"] for site in first["sites"][:8]] == [
            "Gitee", "CSDN", "Zhihu", "Douban", "Baidu", "v2ex.com", "cnblogs.com", "Juejin",
        ]
        assert not {site["site_id"] for site in first["sites"]} & {site["site_id"] for site in second["sites"]}
        domestic = client.get("/api/capabilities?region=CN&limit=100").json()
        assert domestic["total_sites"] == first["cn_sites"] > 30
        assert all(site["region"] == "CN" for site in domestic["sites"])
        zhihu = client.get("/api/capabilities", params={"query": "知乎", "region": "CN"}).json()
        assert zhihu["sites"][0]["name"] == "Zhihu"
        assert "知乎" in zhihu["sites"][0]["aliases"]
        bilibili = client.get("/api/capabilities", params={"query": "哔哩哔哩"}).json()
        assert bilibili["sites"][0]["supported"] is False
        assert "UID" in bilibili["sites"][0]["rules"][0]["unsupported"]
        assert "UID" in bilibili["sites"][0]["input_hint"]
        juejin = client.get("/api/capabilities", params={"query": "掘金"}).json()
        assert "数字用户 ID" in juejin["sites"][0]["input_hint"]
        weibo = client.get("/api/capabilities", params={"query": "微博"}).json()
        assert "会话激活" in weibo["sites"][0]["input_hint"]
        assert client.get("/api/capabilities?region=invalid").status_code == 422
        assert client.get("/api/bootstrap").json()["budget"]["max_sites"] is None


def test_web_origin_and_session_protection(tmp_path):
    with TestClient(create_web_app(Settings(data_dir=tmp_path)), base_url="http://127.0.0.1") as client:
        assert client.get("/api/bootstrap", headers={"Host": "untrusted.example"}).status_code == 400
        assert client.get("/api/bootstrap", headers={"Origin": "https://untrusted.example"}).status_code == 403
        assert client.get("/api/bootstrap", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
        assert client.post("/api/research", json={"goal": "公开资料研究"}).status_code == 403
        token = client.get("/api/bootstrap").json()["token"]
        client.headers["X-Kudo-Token"] = token
        assert client.post("/api/research", content="{}").status_code == 415
        missing = client.post("/api/research", json={"goal": "公开资料研究"})
        assert missing.status_code == 400
        assert "缺少模型配置" in missing.json()["error"]
        assert client.get("/api/research").json()["total"] == 0


def test_web_rejects_invalid_inputs_and_paths(tmp_path):
    with TestClient(create_web_app(Settings(data_dir=tmp_path)), base_url="http://127.0.0.1") as client:
        token = client.get("/api/bootstrap").json()["token"]
        client.headers["X-Kudo-Token"] = token
        invalid = client.post("/api/research", json={"goal": "校验输入", "identifiers": [{"kind": "profile", "value": "file:///etc/passwd"}]})
        assert invalid.status_code == 422
        assert "file:///etc/passwd" not in invalid.text
        assert client.get("/api/research?limit=101").status_code == 422
        assert client.get("/api/research/not-a-run").status_code == 404
        assert client.get("/api/research/00000000-0000-0000-0000-000000000000").status_code == 404
        assert client.get("/static/../application/config.py").status_code == 404


def test_web_session_expires_on_restart(tmp_path):
    app = create_web_app(Settings(data_dir=tmp_path))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        old = client.get("/api/bootstrap").json()["token"]
    with TestClient(app, base_url="http://127.0.0.1") as client:
        new = client.get("/api/bootstrap").json()["token"]
        assert old != new
        assert client.post("/api/research", json={"goal": "公开资料研究"}, headers={"X-Kudo-Token": old}).status_code == 403
