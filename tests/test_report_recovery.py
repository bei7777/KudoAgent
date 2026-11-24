import time

import phonenumbers
import pytest
from filelock import FileLock
from starlette.testclient import TestClient

from kudo_agent.application.config import Settings
from kudo_agent.application.service import Application
from kudo_agent.integrations.web import create_web_app
from kudo_agent.research_core.collectors import Collectors
from kudo_agent.research_core.http import HttpExecutor
from kudo_agent.research_core.models import (
    Action,
    Budget,
    BudgetIncrease,
    Identifier,
    ResearchRequest,
    RunStatus,
)


def phone_request(**budget):
    example = phonenumbers.example_number("CH")
    number = phonenumbers.format_number(example, phonenumbers.PhoneNumberFormat.E164)
    return ResearchRequest(goal="整理电话号码分配资料", identifiers=[Identifier(kind="phone", value=number)],
                           budget=Budget(**budget))


async def test_extend_local_budget_in_same_run(tmp_path):
    settings = Settings(data_dir=tmp_path)
    async with Application(settings) as app, Application(settings) as observer:
        run_id = app.start_research(phone_request(run_timeout=0.000001))["run_id"]
        assert observer.get_status(run_id)["status"] == "queued"
        stopped = await app.wait(run_id)
        assert stopped["status"] == "partial" and stopped["can_extend_budget"]
        with pytest.raises(ValueError, match="追加已耗尽的运行时间"):
            app.extend_budget(run_id, BudgetIncrease(additional_requests=1))
        assert app.extend_budget(run_id, BudgetIncrease(additional_seconds=4))["run_id"] == run_id
        assert observer.get_status(run_id)["status"] == "queued"
        with pytest.raises(ValueError):
            app.extend_budget(run_id, BudgetIncrease(additional_seconds=4))
        resumed = await app.wait(run_id)
        assert resumed["status"] == "completed" and resumed["report_status"] == "ready"
        assert resumed["coverage"]["checked"] == resumed["evidence_count"] == 1
        assert resumed["request_count"] == resumed["model_usage"]["attempts"] == 0
        assert app.list_research()["total"] == 1


async def test_extension_only_finishes_remaining_explicit_numbers(tmp_path):
    request = phone_request(run_timeout=0.000001)
    second = phonenumbers.example_number("GB")
    request.identifiers.append(Identifier(kind="phone", value=phonenumbers.format_number(second, phonenumbers.PhoneNumberFormat.E164)))
    async with Application(Settings(data_dir=tmp_path)) as app:
        run_id = app.store.create(request, app.catalog.version)
        http = HttpExecutor(app.store, run_id, request.budget)
        try:
            actions = [Action(capability="phone", value=item.value) for item in request.identifiers]
            app.store.add_actions(run_id, actions)
            first = await Collectors(app.catalog, app.store, run_id, http).collect(actions[0])
            app.store.finish_action(run_id, actions[0].key, first)
        finally:
            await http.close()
        app._schedule(run_id, resume=False, requires_model=False)
        stopped = await app.wait(run_id)
        assert stopped["coverage"]["checked"] == 1 and stopped["coverage"]["remaining"] == 1
    async with Application(Settings(data_dir=tmp_path)) as app:
        app.extend_budget(run_id, BudgetIncrease(additional_seconds=5))
        completed = await app.wait(run_id)
        assert completed["coverage"]["checked"] == completed["evidence_count"] == 2
        assert completed["request_count"] == 0
        assert first[0] in app.store.evidence(run_id)
        assert all(step["action_id"] == actions[1].key for decision in app.store.decisions(run_id) for step in decision["actions"])


async def test_rebuild_interrupted_phone_report_without_repeating_collection(tmp_path):
    async with Application(Settings(data_dir=tmp_path)) as app:
        request = phone_request()
        run_id = app.store.create(request, app.catalog.version)
        http = HttpExecutor(app.store, run_id, request.budget)
        try:
            action = Action(capability="phone", value=request.identifiers[0].value)
            app.store.add_actions(run_id, [action])
            app.store.finish_action(run_id, action.key, await Collectors(app.catalog, app.store, run_id, http).collect(action))
        finally:
            await http.close()
        app.store.begin_report(run_id, RunStatus.COMPLETED, "已完成本机号码资料核查")
    async with Application(Settings(data_dir=tmp_path)) as app:
        stopped = app.get_status(run_id)
        assert stopped["status"] == "completed" and stopped["report_status"] == "failed"
        assert stopped["can_retry_report"] and stopped["report_error"]
        with FileLock(app.store.run_dir(run_id) / "run.lock"):
            with pytest.raises(ValueError, match="正在执行"):
                app.retry_report(run_id)
        before = app.store.evidence(run_id)
        app.retry_report(run_id)
        assert app.get_status(run_id)["phase"] == "report"
        with pytest.raises(ValueError):
            app.retry_report(run_id)
        await app.report_tasks[run_id]
        finished = app.get_status(run_id)
        assert finished["report_status"] == "ready" and not finished["report_error"] and not finished["can_retry_report"]
        assert finished["stop_reason"] == stopped["stop_reason"]
        assert finished["coverage"] == stopped["coverage"]
        assert app.store.evidence(run_id) == before
        assert finished["request_count"] == finished["model_usage"]["attempts"] == 0
        assert app.get_report(run_id, "json")["report"]["claims"]


async def test_report_file_error_preserves_completed_collection_and_web_can_retry(tmp_path):
    settings = Settings(data_dir=tmp_path)
    async with Application(settings) as app:
        run_id = app.start_research(phone_request())["run_id"]
        destination = app.store.run_dir(run_id) / "report.partial"
        destination.mkdir()
        finished = await app.wait(run_id)
        assert finished["status"] == "completed" and finished["report_status"] == "failed"
        assert finished["report_error"].startswith(("IsADirectoryError:", "PermissionError:"))
        assert destination.name in finished["report_error"] and finished["can_retry_report"]
        assert finished["coverage"]["checked"] == 1
        destination.rmdir()
    with TestClient(create_web_app(settings), base_url="http://127.0.0.1") as client:
        client.headers["X-Kudo-Token"] = client.get("/api/bootstrap").json()["token"]
        result = client.post(f"/api/research/{run_id}/report/retry", json={})
        assert result.status_code == 202
        limit = time.monotonic() + 10
        while time.monotonic() < limit:
            progress = client.get(f"/api/research/{run_id}/progress").json()
            if progress["report_status"] != "generating":
                break
            time.sleep(0.01)
        assert progress["report_status"] == "ready" and not progress["report_error"]
        assert progress["evidence_count"] == 1 and progress["request_count"] == 0
        assert (destination.parent / "report.md").is_file()
