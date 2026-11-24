import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from kudo_agent.application.config import Settings
from kudo_agent.integrations.web import create_web_app
from kudo_agent.research_agent.workflow import Workflow
from kudo_agent.research_core.collectors import Collectors
from kudo_agent.research_core.http import HttpExecutor
from kudo_agent.research_core.models import (
    Action,
    Artifact,
    Evidence,
    Identifier,
    ResearchRequest,
)

PROJECT = "https://github.com/sherlock-project"


def test_profile_depth_for_new_requests_and_saved_budgets():
    profile = Identifier(kind="profile", value=PROJECT)
    goal = "核对开源项目主页"
    direct = ResearchRequest(goal=goal, identifiers=[profile], budget={"max_requests": 3})
    assert direct.budget.max_depth == 0 and direct.follow_profile_links is False
    saved = {"goal": goal, "identifiers": [profile.model_dump()], "budget": {"max_depth": 3}}
    restored = ResearchRequest.model_validate(saved)
    assert restored.budget.max_depth == 3 and restored.follow_profile_links is None
    mixed = ResearchRequest(goal=goal, identifiers=[profile, Identifier(kind="username", value="sherlock-project")])
    assert mixed.budget.max_depth == 2 and mixed.follow_profile_links is None
    natural = ResearchRequest(goal=goal + " " + PROJECT)
    extracted = ResearchRequest.model_validate(natural.model_dump() | {"identifiers": [profile]})
    assert extracted.budget.max_depth == 0 and extracted.follow_profile_links is False


def test_web_profile_prepare_respects_opt_in(tmp_path):
    with TestClient(create_web_app(Settings(data_dir=tmp_path)), base_url="http://127.0.0.1") as client:
        client.headers["X-Kudo-Token"] = client.get("/api/bootstrap").json()["token"]
        initial = client.post("/api/prepare", json={"clues": PROJECT}).json()
        assert initial["request"]["budget"]["max_depth"] == 0
        assert initial["scope"] == {"username_sites": 0, "email_sites": 0}
        empty = client.post("/api/prepare", json={"clues": PROJECT, "follow_profile_links": True}).json()
        assert empty["scope"]["username_sites"] == 0
        rejected = client.post("/api/research", json=empty["request"])
        assert rejected.status_code == 400 and "选择站点类别" in rejected.json()["error"]
        chosen = {"clues": PROJECT, "sites": ["GitHub"], "follow_profile_links": True, "budget": {"max_depth": 1}}
        expanded = client.post("/api/prepare", json=chosen).json()
        assert expanded["request"]["budget"]["max_depth"] == expanded["scope"]["username_sites"] == 1
        direct = client.post("/api/prepare", json=chosen | {"follow_profile_links": False}).json()
        assert direct["request"]["budget"]["max_depth"] == 0
        retained = client.post("/api/prepare", json={"clues": PROJECT, "budget": {"max_depth": 3}}).json()
        assert retained["request"]["budget"]["max_depth"] == 3
        assert client.get("/api/research").json()["total"] == 0


@pytest.mark.parametrize("follow", [False, True])
async def test_profile_discovery_replays_public_project_page(store, catalog, follow):
    source = os.environ.get("KUDO_CAPTURED_COLLECTORS")
    if not source:
        pytest.skip("设置 KUDO_CAPTURED_COLLECTORS 后读取开源项目主页的存档")
    archive = Path(source).resolve(strict=True)
    action = Action(capability="profile", value=PROJECT)
    with closing(sqlite3.connect(archive.as_uri() + "?mode=ro&immutable=1", uri=True)) as original:
        original_id, payload = original.execute(
            "SELECT run_id,payload FROM evidence WHERE action_id=? LIMIT 1", (action.key,),
        ).fetchone()
        evidence = Evidence.model_validate_json(payload)
        artifacts = [Artifact.model_validate_json(original.execute(
            "SELECT payload FROM artifacts WHERE run_id=? AND id=?", (original_id, key),
        ).fetchone()[0]) for key in evidence.artifact_ids]
    request = ResearchRequest(goal="核对开源项目网页", identifiers=[Identifier(kind="profile", value=PROJECT)],
                              follow_profile_links=follow, sites=["GitHub"])
    run_id = store.create(request, catalog.version)
    for artifact in artifacts:
        store.save_artifact(run_id, artifact, (archive.parent / "runs" / original_id / artifact.body_path).read_bytes())
    http = HttpExecutor(store, run_id, request.budget)
    try:
        collectors = Collectors(catalog, store, run_id, http)
        flow = Workflow(store, catalog, None, collectors)
        await flow.seed({"run_id": run_id})
        store.finish_action(run_id, action.key, [evidence])
        flow.discover(run_id)
        pending = [row["payload"] for row in store.actions(run_id, "pending")]
        assert bool(pending) is follow
        for item in pending:
            assert item["capability"] == "profile" and item["depth"] == 1
            assert item["value"] in evidence.attributes["links"]
            assert item["parent_evidence"] == [evidence.evidence_id]
        assert not http.clients and store.run(run_id)["request_count"] == 0
        store.verify(run_id)
    finally:
        await http.close()
