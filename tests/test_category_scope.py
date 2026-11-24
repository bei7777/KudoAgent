import pytest
from starlette.testclient import TestClient

from kudo_agent.application.config import Settings
from kudo_agent.integrations.web import create_web_app
from kudo_agent.research_agent.workflow import Workflow
from kudo_agent.research_core.collectors import Collectors
from kudo_agent.research_core.email_sites import EMAIL_PROVIDERS
from kudo_agent.research_core.http import HttpExecutor
from kudo_agent.research_core.models import Budget, Identifier, ResearchRequest
from kudo_agent.research_core.scope import resolve_sites


async def test_saved_multicategory_seed_keeps_the_selected_union(store, catalog):
    request = ResearchRequest(goal="查看所选目录的查询范围", site_categories=["technology", "media"],
                              identifiers=[Identifier(kind="username", value="sherlock-project"),
                                           Identifier(kind="email", value="support@github.com")],
                              budget=Budget(max_sites=6, max_depth=0))
    run_id = store.create(request, catalog.version)
    http = HttpExecutor(store, run_id, request.budget)
    try:
        workflow = Workflow(store, catalog, None, Collectors(catalog, store, run_id, http))
        await workflow.seed({"run_id": run_id})
    finally:
        await http.close()
    stored = ResearchRequest.model_validate(store.run(run_id)["request"])
    assert stored.site_categories == ["technology", "media"]
    scope = resolve_sites(catalog, stored)
    actions = [row["payload"] for row in store.actions(run_id)]
    assert {a["site"] for a in actions if a["capability"] == "username"} == {site.site_id for site in scope.usernames}
    assert len(scope.usernames) == 6
    assert {a["site"] for a in actions if a["capability"] == "email"} == set(scope.emails)
    assert {EMAIL_PROVIDERS[site].category for site in scope.emails} == set(stored.site_categories)
    assert all(site.category in stored.site_categories for site in scope.usernames)
    assert store.run(run_id)["request_count"] == 0


def test_prepare_intersections_and_empty_categories(tmp_path):
    with TestClient(create_web_app(Settings(data_dir=tmp_path)), base_url="http://127.0.0.1") as client:
        client.headers["X-Kudo-Token"] = client.get("/api/bootstrap").json()["token"]
        selection = {"clues": "sherlock-project\nsupport@github.com", "sites": ["GitHub"],
                     "email_sites": ["github", "spotify"], "site_categories": ["media"]}
        result = client.post("/api/prepare", json=selection)
        assert result.status_code == 200
        assert result.json()["scope"] == {"username_sites": 0, "email_sites": 1}
        assert [s["name"] for s in result.json()["username_sites"]] == ["GitHub"]
        assert result.json()["request"]["site_categories"] == ["media"]
        zero = client.post("/api/prepare", json=selection | {"clues": "sherlock-project"})
        assert zero.status_code == 400 and zero.json()["scope_field"] == "site_categories"
        for invalid in ([], ["missing"]):
            assert client.post("/api/prepare", json=selection | {"site_categories": invalid}).status_code == 422
        for kind in ("username", "email"):
            legacy = client.post("/api/prepare", json={"clues": selection["clues"]})
            assert legacy.json()["scope"][kind + "_sites"] > 1


def test_selected_category_can_have_no_email_targets(catalog):
    request = ResearchRequest(goal="检查所选目录", identifiers=[Identifier(kind="email", value="support@github.com")],
                              site_categories=["technology"], email_sites=[])
    with pytest.raises(ValueError, match="没有适用于"):
        resolve_sites(catalog, request)
