import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from bs4 import BeautifulSoup

from kudo_agent.application.config import Settings
from kudo_agent.application.service import Application
from kudo_agent.evidence.store import Store
from kudo_agent.research_core.email import EmailCollector
from kudo_agent.research_core.email_communities import (
    COMMUNITIES,
    discourse_settings,
    interpret_discourse,
    interpret_discuz,
    interpret_mybb,
    mybb_token,
)
from kudo_agent.research_core.email_sites import EMAIL_PROVIDERS, email_capabilities
from kudo_agent.research_core.http import HttpExecutor
from kudo_agent.research_core.models import EMAIL_SITES, ResearchRequest


def test_community_catalog_is_complete_and_has_distinct_targets():
    assert len(EMAIL_SITES) == len(EMAIL_PROVIDERS) == len(email_capabilities()) == 100
    assert set(EMAIL_SITES) == set(EMAIL_PROVIDERS)
    assert len(COMMUNITIES) == 73
    assert len({urlsplit(site.url).hostname for site in COMMUNITIES.values()}) == 73
    assert sum(site.region == "CN" for site in EMAIL_PROVIDERS.values()) == 40
    for site in COMMUNITIES.values():
        url = urlsplit(site.url)
        assert url.scheme == "https" and url.hostname and site.url.endswith("/")
        assert not url.query and not url.fragment
    request = ResearchRequest(goal="核查邮箱目录")
    assert request.email_sites == list(EMAIL_SITES)
    assert ResearchRequest.model_json_schema()["properties"]["email_sites"]["items"]["enum"] == list(EMAIL_SITES)


@pytest.fixture(scope="module")
def community_store(tmp_path_factory):
    configured = os.environ.get("KUDO_CAPTURED_EMAIL_COMMUNITIES")
    if not configured:
        pytest.skip("设置 KUDO_CAPTURED_EMAIL_COMMUNITIES，使用自有邮箱的真实社区采集目录")
    source = Path(configured).resolve(strict=True)
    root = tmp_path_factory.mktemp("communities")
    original = sqlite3.connect((source / "research.sqlite").as_uri() + "?mode=ro", uri=True)
    copied = sqlite3.connect(root / "research.sqlite")
    try:
        original.backup(copied)
    finally:
        original.close()
        copied.close()
    shutil.copytree(source / "runs", root / "runs")
    store = Store(root)
    try:
        yield store
    finally:
        store.close()


async def test_saved_email_evidence_links_to_archived_responses(community_store):
    row = community_store.db.execute(
        "SELECT run_id, id FROM evidence WHERE json_extract(payload,'$.kind')='email' "
        "AND json_array_length(payload,'$.artifact_ids')>=2 LIMIT 1"
    ).fetchone()
    assert row is not None
    run_id, evidence_id = row
    async with Application(Settings(data_dir=community_store.root)) as app:
        page = app.get_evidence(run_id, evidence_id=evidence_id)
        record = page["items"][0]
        assert record["attributes"]["source_role"] == "check_endpoint"
        artifacts = page["artifacts"]
        assert any(artifact["url"] == record["source_url"] for artifact in artifacts)
        assert record["source_url"] not in app.confirmation_sources(run_id)
        with pytest.raises(ValueError, match="确认关系"):
            await app.record_confirmation(run_id, record["source_url"], record["source_url"], "核对来源用途")
        markdown = app.get_report(run_id)
        assert f"检测请求地址 `{record['source_url']}`" in markdown
        assert "已保存的检测响应" in markdown
        for artifact in artifacts:
            metadata, body = app.get_artifact(run_id, artifact["artifact_id"])
            assert hashlib.sha256(body).hexdigest() == metadata.sha256
            assert f"]({metadata.body_path})" in markdown
        report = app.get_report(run_id, "json")
        assert record in report["evidence"]


@pytest.mark.parametrize("site", list(COMMUNITIES))
def test_real_community_responses_and_evidence(site, community_store):
    store = community_store
    latest = {}
    for row in store.db.execute("SELECT id FROM runs WHERE status='completed' ORDER BY created_at"):
        run = store.run(row["id"])
        if run["request"]["email_sites"] == [site]:
            latest[run["request"]["identifiers"][0]["value"]] = run
    assert len(latest) == 2, f"{site} 缺少两个不同自有邮箱的完成记录"
    states = set()
    for run in latest.values():
        evidence = store.evidence(run["id"])
        states.add(evidence[0].state if evidence else store.actions(run["id"])[0]["status"])
    published = next(provider["last_check"] for provider in email_capabilities() if provider["site"] == site)
    assert published["samples"] == len(latest)
    assert published["state"] == (next(iter(states)) if len(states) == 1 else "mixed")
    for email, run in latest.items():
        store.verify(run["id"])
        actions = store.actions(run["id"])
        assert len(actions) == 1
        artifacts = store.artifacts(run["id"])
        for artifact in artifacts:
            assert hashlib.sha256(store.body(run["id"], artifact)).hexdigest() == artifact.sha256
            assert urlsplit(artifact.url).hostname == urlsplit(COMMUNITIES[site].url).hostname
        if actions[0]["status"] in {"connection_failed", "blocked"}:
            assert actions[0]["error"]
            continue
        records = store.evidence(run["id"])
        assert len(records) == 1
        item = records[0]
        assert item.subject == email and item.attributes["provider"] == site
        assert set(item.artifact_ids) == {artifact.artifact_id for artifact in artifacts}
        if item.state in {"blocked", "unsupported", "rate_limited"}:
            assert item.summary and artifacts
            continue
        assert len(artifacts) == 2
        bootstrap, response = sorted(artifacts, key=lambda artifact: artifact.observed_at)
        http = HttpExecutor(store, run["id"], ResearchRequest.model_validate(run["request"]).budget)
        collector = EmailCollector(http, site, email, actions[0]["id"])
        collector.artifacts = [bootstrap, response]
        engine = COMMUNITIES[site].engine
        page = BeautifulSoup(http.text(bootstrap), "html.parser")
        if engine == "discuz":
            query = parse_qs(urlsplit(response.final_url).query)
            assert query["action"] == ["checkemail"] and query["email"] == [email]
            assert response.method == "GET"
            replay = interpret_discuz(collector, response)
            if item.state == "not_detected":
                assert item.attributes["field_message"] == "succeed"
        elif engine == "mybb":
            assert mybb_token(page)
            assert response.method == "GET"
            assert parse_qs(urlsplit(response.final_url).query)["action"] == ["email_availability"]
            replay = interpret_mybb(collector, response)
            if json.loads(http.text(response)) == "true":
                assert item.state == "unknown" and item.attributes["field_available"] is True
        else:
            assert response.method == "GET" and urlsplit(response.url).path.endswith("/u/check_email.json")
            replay = interpret_discourse(collector, response, discourse_settings(page))
            if item.state == "not_detected":
                assert item.attributes["hide_email_address_taken"] is False
            elif item.attributes.get("hide_email_address_taken") is not False:
                assert item.state != "not_detected"
        assert replay.state == item.state
        assert replay.attributes == item.attributes
