import os
import sqlite3
from pathlib import Path

import pytest

from kudo_agent.application.config import Settings
from kudo_agent.application.service import Application
from kudo_agent.research_core.collectors import Collectors
from kudo_agent.research_core.http import HttpExecutor
from kudo_agent.research_core.models import Action, Identifier, ResearchRequest


def captured_application(destination, variable, *, immutable=False):
    source = os.environ.get(variable)
    if not source:
        pytest.skip(f"设置 {variable}，使用已有真实采集记录验证证据浏览")
    path = Path(source).resolve(strict=True)
    destination.mkdir()
    uri = path.as_uri() + "?mode=ro" + ("&immutable=1" if immutable else "")
    original = sqlite3.connect(uri, uri=True)
    copied = sqlite3.connect(destination / "research.sqlite")
    try:
        original.backup(copied)
    finally:
        original.close()
        copied.close()
    return Application(Settings(data_dir=destination))


@pytest.fixture
def collected_app(tmp_path):
    app = captured_application(tmp_path / "collected", "KUDO_CAPTURED_COLLECTORS", immutable=True)
    yield app
    app.store.close()


@pytest.fixture
def researched_app(tmp_path):
    app = captured_application(tmp_path / "researched", "KUDO_CAPTURED_RESEARCH")
    yield app
    app.store.close()


async def test_metadata_browsing_uses_real_number_library(tmp_path):
    async with Application(Settings(data_dir=tmp_path)) as app:
        identifier = Identifier(kind="phone", value="+41446681800")
        request = ResearchRequest(goal="核查号码资料的分类和分页", identifiers=[identifier])
        run_id = app.store.create(request, app.catalog.version)
        action = Action(capability="phone", value=identifier.value)
        app.store.add_actions(run_id, [action])
        http = HttpExecutor(app.store, run_id, request.budget)
        try:
            items = await Collectors(app.catalog, app.store, run_id, http).collect(action)
            app.store.finish_action(run_id, action.key, items)
        finally:
            await http.close()
        response = app.get_evidence(run_id, view="metadata", query="41446681800")
        assert response["summary"] == {"all": 1, "pages": 0, "email": 0, "metadata": 1, "uncertain": 0}
        assert response["total"] == response["filtered_total"] == 1
        assert response["items"][0]["attributes"]["region"] == "CH"
        assert response["items"][0]["state"] == "metadata"
        assert app.get_evidence(run_id, view="pages")["items"] == []
        assert app.get_evidence(run_id, view="uncertain")["filtered_total"] == 0
        assert app.get_evidence(run_id, offset=1)["summary"] == response["summary"]
        assert app.store.run(run_id)["request_count"] == 0
        app.store.verify(run_id)
        with pytest.raises(ValueError, match="view"):
            app.get_evidence(run_id, view="invalid")
        with pytest.raises(ValueError, match="offset"):
            app.get_evidence(run_id, offset=-1)


def test_recorded_pages_share_one_page_without_losing_evidence(collected_app):
    app = collected_app
    row = app.store.db.execute(
        "SELECT run_id FROM evidence GROUP BY run_id HAVING count(*)=3"
    ).fetchone()
    assert row is not None, "采集记录需要包含用户名、对应主页和 IP 三项真实查询"
    run_id = row["run_id"]
    response = app.get_evidence(run_id, view="pages")
    assert response["total"] == 3
    assert response["filtered_total"] == 2
    assert response["summary"] == {"all": 3, "pages": 1, "email": 0, "metadata": 1, "uncertain": 0}
    assert {item["kind"] for item in response["items"]} == {"account", "profile"}
    assert {item["source_url"] for item in response["items"]} == {"https://github.com/sherlock-project"}
    account = next(item for item in response["items"] if item["kind"] == "account")
    assert len(account["detections"]) == 2
    assert {rule["rule_id"].split(":", 1)[0] for rule in account["detections"]} == {"maigret", "sherlock"}
    artifact_ids = {key for item in response["items"] for key in item["artifact_ids"]}
    assert {item["artifact_id"] for item in response["artifacts"]} == artifact_ids
    first = app.get_evidence(run_id, 0, 1, view="pages")
    second = app.get_evidence(run_id, 1, 1, view="pages")
    assert first["summary"] == second["summary"] == response["summary"]
    assert first["items"][0]["evidence_id"] != second["items"][0]["evidence_id"]
    assert app.get_evidence(run_id, query="GITHUB")["filtered_total"] == 2
    assert app.get_evidence(run_id, query="no-match-for-this-search")["filtered_total"] == 0
    located = app.get_evidence(run_id, view="email", query="no-match-for-this-search",
                               evidence_id=account["evidence_id"])
    assert located["filtered_total"] == 1
    assert located["items"] == [account]
    ordered = app.get_evidence(run_id)["items"]
    assert [item["kind"] for item in ordered[:2]] == [item["kind"] for item in response["items"]]
    assert ordered[-1]["kind"] == "ip"


def test_recorded_conflicts_keep_rules_and_global_counts(researched_app):
    app = researched_app
    row = app.store.db.execute(
        "SELECT run_id, id FROM evidence WHERE json_extract(payload, '$.attributes.conflict')=1 LIMIT 1"
    ).fetchone()
    assert row is not None, "采集记录需要包含真实规则冲突"
    run_id = row["run_id"]
    baseline = app.get_evidence(run_id, limit=1)
    response = app.get_evidence(run_id, view="uncertain", evidence_id=row["id"])
    item = response["items"][0]
    assert item["attributes"]["conflict"] is True
    assert item["state"] == "unknown"
    assert {rule["state"] for rule in item["detections"]} >= {"detected", "not_detected"}
    assert response["summary"] == baseline["summary"]
    assert response["total"] > response["filtered_total"] == 1
    site = item["attributes"]["site"]
    filtered = app.get_evidence(run_id, view="uncertain", query=site)
    assert row["id"] in {value["evidence_id"] for value in filtered["items"]}
    assert filtered["summary"] == baseline["summary"]
    assert baseline["items"][0]["state"] == "detected"
    expected = app.store.db.execute(
        "SELECT count(DISTINCT json_extract(payload, '$.source_url')) FROM evidence "
        "WHERE run_id=? AND json_extract(payload, '$.kind') IN ('account', 'profile') "
        "AND json_extract(payload, '$.state')='detected'", (run_id,),
    ).fetchone()[0]
    assert baseline["summary"]["pages"] == expected


def test_recorded_email_counts_include_unknown_and_unavailable(researched_app):
    app = researched_app
    row = app.store.db.execute(
        "SELECT run_id FROM evidence WHERE json_extract(payload, '$.kind')='email' "
        "GROUP BY run_id HAVING count(*)>2 LIMIT 1"
    ).fetchone()
    assert row is not None, "采集记录需要包含多个站点的真实邮箱检查"
    response = app.get_evidence(row["run_id"], view="email")
    assert response["summary"]["email"] == response["filtered_total"] == len(response["items"])
    assert response["summary"]["pages"] == 0
    assert any(item["state"] in {"unknown", "blocked"} for item in response["items"])
    assert response["summary"]["uncertain"] > 0
    provider = response["items"][0]["attributes"]["provider"]
    filtered = app.get_evidence(row["run_id"], view="email", query=provider)
    assert response["items"][0]["evidence_id"] in {item["evidence_id"] for item in filtered["items"]}
    assert filtered["summary"] == response["summary"]
