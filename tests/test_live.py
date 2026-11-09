import asyncio
import json
import os
from pathlib import Path

import pytest

from kudo_agent.application.config import Settings
from kudo_agent.application.service import Application
from kudo_agent.evidence.store import Store
from kudo_agent.research_agent.planner import Planner
from kudo_agent.research_agent.workflow import Workflow
from kudo_agent.research_core.collectors import Collectors
from kudo_agent.research_core.email_sites import EmailSample
from kudo_agent.research_core.http import HttpExecutor, QueryTargetBlocked
from kudo_agent.research_core.models import (
    EMAIL_SITES,
    Action,
    Budget,
    Identifier,
    RequestSpec,
    ResearchRequest,
)

pytestmark = pytest.mark.live


async def test_public_request_dedup_and_restart(tmp_path):
    store = Store(tmp_path / "requests")
    run_id = store.create(ResearchRequest(goal="公开项目 API 核查"), "http-only")
    spec = RequestSpec(url="https://api.github.com/repos/sherlock-project/sherlock")
    http = HttpExecutor(store, run_id, Budget())
    try:
        results = await asyncio.gather(http.fetch(spec), http.fetch(spec))
        assert results[0].artifact_id == results[1].artifact_id
        assert store.run(run_id)["request_count"] == 1
        assert results[0].status_code == 200
        assert json.loads(http.text(results[0]))["full_name"] == "sherlock-project/sherlock"
    finally:
        await http.close()
        store.close()
    reopened = Store(tmp_path / "requests")
    http = HttpExecutor(reopened, run_id, Budget())
    try:
        saved = await http.fetch(spec)
        assert saved.artifact_id == results[0].artifact_id
        assert reopened.run(run_id)["request_count"] == 1
        distinct = await http.fetch(spec.model_copy(update={"params": {"per_page": "1"}}))
        assert distinct.artifact_id != saved.artifact_id
        assert reopened.run(run_id)["request_count"] == 2
        reopened.verify(run_id)
    finally:
        await http.close()
        reopened.close()


async def test_public_collectors(store, catalog):
    run_id = store.create(ResearchRequest(
        goal="公开项目资料验证", identifiers=[Identifier(kind="username", value="sherlock-project")],
    ), catalog.version)
    http = HttpExecutor(store, run_id, Budget())
    collectors = Collectors(catalog, store, run_id, http)
    actions = [
        Action(capability="username", value="sherlock-project", site=catalog.select(["GitHub"], 1)[0].site_id),
        Action(capability="profile", value="https://github.com/sherlock-project"),
        Action(capability="ip", value="1.1.1.1"),
    ]
    store.add_actions(run_id, actions)
    try:
        for action in actions:
            items = await collectors.collect(action)
            store.finish_action(run_id, action.key, items)
            assert items[0].artifact_ids
            assert items[0].state == ("metadata" if action.capability == "ip" else "detected")
            if action.capability == "profile":
                assert any(relation.kind == "mentions_identifier" for relation in items[0].relations)
                assert "https://github.com/platform" not in items[0].attributes["links"]
                assert items[0].attributes["text_sha256"]
        store.verify(run_id)
    finally:
        await http.close()


async def test_real_model_doctor():
    settings = Settings.from_env()
    settings.require_model()
    planner = Planner(settings)
    try:
        assert (await planner.doctor())["ready"]
    finally:
        await planner.close()


async def test_authorized_email_samples(store, catalog):
    sample_file = Path(os.environ["KUDO_EMAIL_SAMPLES"])
    samples = [EmailSample.model_validate(value) for value in json.loads(sample_file.read_text(encoding="utf-8"))]
    assert samples
    assert {sample.site for sample in samples} <= set(EMAIL_SITES)
    for sample in samples:
        assert sample.expected_presence is not None, "注册状态填写 registered；公开头像或资料填写 observed"
    run_id = store.create(ResearchRequest(goal="自有邮箱注册状态验收"), catalog.version)
    http = HttpExecutor(store, run_id, Budget())
    collectors = Collectors(catalog, store, run_id, http)
    try:
        for sample in samples:
            action = Action(capability="email", value=sample.email, site=sample.site)
            store.add_actions(run_id, [action])
            results = await collectors.collect(action)
            store.finish_action(run_id, action.key, results)
            assert results[0].state == ("detected" if sample.expected_presence else "not_detected")
    finally:
        await http.close()


async def test_real_agent(tmp_path):
    settings = Settings.from_env(data_dir=tmp_path / "agent")
    async with Application(settings) as app:
        request = ResearchRequest.model_validate({
            "goal": "检查 sherlock-project 在 GitHub 的公开主页，引用证据解释结果。",
            "identifiers": [{"kind": "username", "value": "sherlock-project"}], "sites": ["GitHub"],
        })
        run_id = app.start_research(request)["run_id"]
        assert (await app.wait(run_id))["status"] == "completed"
        assert app.get_report(run_id, "json")["report"]["claims"]
        assert app.get_evidence(run_id)["total"] > 0


async def test_full_default_scope_is_persisted(tmp_path):
    settings = Settings.from_env(data_dir=tmp_path / "scope")
    async with Application(settings) as app:
        request = ResearchRequest(goal="核查公开项目的完整用户名范围", identifiers=[
            Identifier(kind="username", value="sherlock-project"),
        ])
        run_id = app.store.create(request, app.catalog.version)
        planner = Planner(settings, app.store, run_id)
        http = HttpExecutor(app.store, run_id, request.budget)
        workflow = Workflow(app.store, app.catalog, planner, Collectors(app.catalog, app.store, run_id, http))
        try:
            state = {"run_id": run_id, **await workflow.seed({"run_id": run_id})}
            actions = app.store.actions(run_id)
            assert len(actions) == 8303
            assert {item["payload"]["site"] for item in actions} == {
                site.site_id for site in app.catalog.select([])
            }
            selected = await workflow.plan(state)
            assert len(selected["selected"]) == 50
            assert app.store.decision(run_id, 0)["source"] == "catalog"
            assert app.get_status(run_id)["coverage"]["remaining"] == 8303
            assert app.store.model_usage(run_id)["attempts"] == 0
        finally:
            await http.close()
            await planner.close()
    reopened = Store(settings.data_dir)
    try:
        assert len(reopened.actions(run_id)) == 8303
        assert reopened.decision(run_id, 0)["actions"][0]["action_id"] == selected["selected"][0]
    finally:
        reopened.close()


async def test_real_target_rejection_is_isolated(tmp_path):
    settings = Settings.from_env(data_dir=tmp_path / "target-rejection")
    async with Application(settings) as app:
        request = ResearchRequest(goal="验证地址检查和独立查询执行", identifiers=[
            Identifier(kind="profile", value="http://127.0.0.1/"),
            Identifier(kind="phone", value="+41446681800"),
        ])
        run_id = app.store.create(request, app.catalog.version)
        planner = Planner(settings, app.store, run_id)
        http = HttpExecutor(app.store, run_id, request.budget)
        workflow = Workflow(app.store, app.catalog, planner, Collectors(app.catalog, app.store, run_id, http))
        try:
            state = {"run_id": run_id, **await workflow.seed({"run_id": run_id})}
            state.update(await workflow.plan(state))
            await workflow.collect(state)
            assert app.store.coverage(run_id) == {
                "planned": 2, "checked": 1, "failed": 0, "blocked": 1, "remaining": 0, "complete": True,
            }
            assert app.store.run(run_id)["request_count"] == 0
            assert app.store.evidence(run_id)[0].attributes["region"] == "CH"
            failure = app.store.failed_queries(run_id)[0]
            assert "http://127.0.0.1/" in failure["error"]
            assert "非公共网络" in failure["error"]
            app.store.reset_incomplete(run_id)
            assert app.store.failed_queries(run_id) == [failure]
            assert "连接失败与查询限制" in app.get_report(run_id)
            app.store.verify(run_id)
        finally:
            await http.close()
            await planner.close()


async def test_public_cookie_rejection_preserves_client(store, catalog):
    request = ResearchRequest(goal="验证实际站点的 Cookie 字符限制")
    run_id = store.create(request, catalog.version)
    http = HttpExecutor(store, run_id, request.budget)
    site = catalog.select(["psychologies-ru-d3422ece"], 1)[0]
    try:
        with pytest.raises(QueryTargetBlocked, match="Cookie"):
            await http.fetch(site.rules[0].request("sherlock-project"))
        artifact = await http.fetch(RequestSpec(url="https://gitee.com/oschina"))
        assert artifact.status_code == 200
        store.verify(run_id)
    finally:
        await http.close()
