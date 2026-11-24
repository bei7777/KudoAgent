import pytest
from starlette.testclient import TestClient

from kudo_agent.application.config import Settings
from kudo_agent.integrations.web import create_web_app
from kudo_agent.research_agent.workflow import Workflow
from kudo_agent.research_core.collectors import Collectors
from kudo_agent.research_core.goal_scope import bind_goal_site
from kudo_agent.research_core.http import HttpExecutor
from kudo_agent.research_core.models import Budget, Identifier, ResearchRequest
from kudo_agent.research_core.scope import resolve_sites


def test_explicit_platform_limits_preview_and_execution(catalog, tmp_path):
    goal = "只核查这个项目的 GitHub 主页"
    request = ResearchRequest(goal=goal, identifiers=[Identifier(kind="username", value="sherlock-project")],
                              budget=Budget(max_sites=1))
    bound = bind_goal_site(catalog, request)
    assert bound.goal_site == "GitHub"
    assert [site.name for site in resolve_sites(catalog, bound).usernames] == ["GitHub"]
    with TestClient(create_web_app(Settings(data_dir=tmp_path)), base_url="http://127.0.0.1") as client:
        client.headers["X-Kudo-Token"] = client.get("/api/bootstrap").json()["token"]
        payload = {"goal": goal, "clues": "sherlock-project", "sites": ["Gitee", "GitHub"]}
        preview = client.post("/api/prepare", json=payload).json()
        assert preview["goal_site"] == "GitHub" and preview["scope"]["username_sites"] == 1
        assert [item["name"] for item in preview["username_sites"]] == ["Gitee", "GitHub"]
        assert preview["request"]["sites"] == payload["sites"]
        assert client.post("/api/prepare", json=payload | {"site_categories": ["media"]}).status_code == 400
        assert client.post("/api/prepare", json=payload | {"goal": ""}).json()["scope"]["username_sites"] == 2
        assert client.get("/api/research").json()["total"] == 0


def test_unknown_or_conflicting_explicit_platform(catalog):
    identifier = Identifier(kind="username", value="sherlock-project")
    with pytest.raises(ValueError, match="找不到"):
        bind_goal_site(catalog, ResearchRequest(goal="只查 GitHuub", identifiers=[identifier]))
    request = ResearchRequest(goal="只查 GitHub", identifiers=[identifier], sites=["Gitee"])
    with pytest.raises(ValueError, match="GitHub"):
        resolve_sites(catalog, bind_goal_site(catalog, request))


@pytest.mark.parametrize("goal", [
    "只查 GitHub 和 Gitee", "查询 GitHub、Gitee 这两个平台", "只核查这个项目的 GitHub 与 Gitee 主页",
    "只查 GitHub, Gitee，附上来源", "只查 GitHub and Gitee", "只查 GitHub；只查 Gitee",
])
def test_named_platform_list(catalog, goal):
    request = ResearchRequest(goal=goal, site_categories=["technology"],
                              identifiers=[Identifier(kind="username", value="sherlock-project")])
    bound = bind_goal_site(catalog, request)
    assert bound.goal_sites == ["GitHub", "Gitee"]
    assert [site.name for site in resolve_sites(catalog, bound).usernames] == ["GitHub", "Gitee"]


def test_platform_preview_uses_filtered_sites_and_rejects_unknown_names(tmp_path):
    with TestClient(create_web_app(Settings(data_dir=tmp_path)), base_url="http://127.0.0.1") as client:
        client.headers["X-Kudo-Token"] = client.get("/api/bootstrap").json()["token"]
        payload = {"clues": "sherlock-project\nsupport@github.com", "goal": "只查 GitHub 和 Gitee",
                   "site_categories": ["technology"], "email_sites": ["gitee"]}
        prepared = client.post("/api/prepare", json=payload).json()
        assert prepared["goal_sites"] == ["GitHub", "Gitee"]
        assert prepared["goal_scope"] == {"username": ["GitHub", "Gitee"], "email": ["Gitee"]}
        assert prepared["scope"] == {"username_sites": 2, "email_sites": 1}
        for change in ({"goal": "只查 GitHub 和目录中不存在的平台"}, {"site_categories": ["media"]},
                       {"goal": "只查 GitHuub、Gittee"}, {"budget": {"max_sites": 1}}):
            assert client.post("/api/prepare", json=payload | change).status_code == 400
        general = client.post("/api/prepare", json=payload | {"goal": "只查公开资料和页面标题"}).json()
        assert general["goal_sites"] == [] and general["scope"]["username_sites"] > 2
        assert client.get("/api/research").json()["total"] == 0


def test_direct_page_can_belong_to_a_named_platform_without_username_lookup(catalog):
    request = ResearchRequest(goal="只查 GitHub 和 Bilibili",
                              identifiers=[Identifier(kind="profile", value="https://space.bilibili.com/")])
    assert resolve_sites(catalog, bind_goal_site(catalog, request)).counts() == {"username_sites": 0, "email_sites": 0}
    request.goal = "只查 GitHub 和 Gitee"
    with pytest.raises(ValueError, match="所填主页地址"):
        resolve_sites(catalog, bind_goal_site(catalog, request))


def test_structured_request_keeps_named_restrictions(catalog):
    request = ResearchRequest(goal="查看项目的公开主页", goal_sites=["GitHub", "Gitee"],
                              identifiers=[Identifier(kind="username", value="sherlock-project")])
    assert len(resolve_sites(catalog, bind_goal_site(catalog, request)).usernames) == 2
    with pytest.raises(ValueError, match="名单"):
        bind_goal_site(catalog, request.model_copy(update={"goal": "只查 GitHub"}))
    with pytest.raises(ValueError, match="GitHuub"):
        bind_goal_site(catalog, request.model_copy(update={"goal_sites": ["GitHuub"]}))


async def test_saved_platform_list_and_legacy_restriction(catalog, store):
    request = ResearchRequest(goal="只查 GitHub 和 Gitee", site_categories=["technology"],
                              identifiers=[Identifier(kind="username", value="sherlock-project")])
    bound = bind_goal_site(catalog, request)
    run_id = store.create(bound, catalog.version)
    http = HttpExecutor(store, run_id, bound.budget)
    try:
        await Workflow(store, catalog, None, Collectors(catalog, store, run_id, http)).seed({"run_id": run_id})
    finally:
        await http.close()
    assert store.run(run_id)["request_count"] == 0
    assert store.run(run_id)["request"]["goal_sites"] == ["GitHub", "Gitee"]
    assert {catalog.sites[row["payload"]["site"]].name for row in store.actions(run_id)} == {"GitHub", "Gitee"}

    legacy = request.model_dump(exclude={"goal_sites"}) | {"goal_site": "GitHub", "goal": "只查 GitHub"}
    restored = ResearchRequest.model_validate(legacy)
    assert [site.name for site in resolve_sites(catalog, restored).usernames] == ["GitHub"]
