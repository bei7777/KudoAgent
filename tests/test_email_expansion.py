import json
import os
import shutil
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from bs4 import BeautifulSoup

from kudo_agent.evidence.store import Store
from kudo_agent.research_core.email import EmailCollector
from kudo_agent.research_core.email_products import microsoft_result
from kudo_agent.research_core.http import HttpExecutor
from kudo_agent.research_core.models import ResearchRequest
from kudo_agent.upstream.maigret import detect_error_page

PROVIDERS = (
    "huawei_cloud", "jianguoyun", "zoho_cn", "twitter", "soundcloud", "flickr",
    "plurk", "komoot", "microsoft", "ebay", "adobe", "archive",
)


@pytest.fixture(params=PROVIDERS)
def expansion_records(request, tmp_path):
    configured = os.environ.get("KUDO_CAPTURED_EMAIL_EXPANSION")
    if not configured:
        pytest.skip("设置 KUDO_CAPTURED_EMAIL_EXPANSION，使用真实邮箱采集目录，多个目录以 os.pathsep 分隔")
    databases = sorted({path for root in configured.split(os.pathsep)
                        for path in Path(root).resolve(strict=True).rglob("research.sqlite")})
    stores = []
    try:
        for index, database in enumerate(databases):
            original = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
            try:
                sites = {json.loads(row[0]).get("site") for row in original.execute("SELECT payload FROM actions")}
                if request.param not in sites:
                    continue
                destination = tmp_path / str(index)
                destination.mkdir()
                copied = sqlite3.connect(destination / "research.sqlite")
                try:
                    original.backup(copied)
                finally:
                    copied.close()
                shutil.copytree(database.parent / "runs", destination / "runs")
                stores.append(Store(destination))
            finally:
                original.close()
        assert stores, f"缺少 {request.param} 的真实采集记录"
        yield request.param, stores
    finally:
        for store in stores:
            store.close()


def _assert_presence(provider, item, response, payload):
    assert response.status_code == 200
    detected = item.state == "detected"
    if provider == "microsoft":
        assert payload.get("ThrottleStatus") in {None, 0}
        assert payload["IfExistsResult"] in ({0, 5, 6, 8} if detected else {1})
        assert item.attributes["result_code"] == payload["IfExistsResult"]
    elif provider == "adobe":
        assert isinstance(payload, list)
        if detected:
            assert any(account["type"] in {"individual", "enterprise"}
                       and account["authenticationMethods"] for account in payload)
        else:
            assert payload == []
    elif provider == "ebay":
        if detected:
            assert payload["userType"] in {"regular", "2fa", "ppa", "ato", "eu_seller"}
            assert not payload.get("errorMsg")
        else:
            assert payload["signinErrorUpdate"] is True
            assert payload["regUrlErrorMsg"] and payload["errorMsg"]
    elif provider == "huawei_cloud":
        assert payload["identityUsed"] == ("true" if detected else "false")
        if not detected:
            assert payload["canUse"] == "true"
    elif provider == "jianguoyun":
        assert payload["isNotReg"] is (not detected)
    elif provider == "zoho_cn":
        assert not detected
        assert payload["status_code"] == 201 and payload["resource_name"] == "signupvalidate"
        assert not payload.get("errors")
    elif provider == "twitter":
        assert payload["taken"] is detected
        if not detected:
            assert payload["valid"] is True
    elif provider == "soundcloud":
        assert payload["type"] == "email"
        assert payload["status"] == ("in_use" if detected else "available")
    elif provider == "plurk":
        assert payload["status"] == ("exist" if detected else "unused")
    elif provider == "komoot":
        assert payload["type"] == ("login" if detected else "register")
        assert not payload.get("error")
    elif provider == "archive":
        assert not detected and payload["success"] is True
    else:
        pytest.fail(f"{provider} 当前响应只提供登录流程资料，不能确认邮箱注册状态")


@pytest.mark.asyncio
@pytest.mark.parametrize("expansion_records", ["microsoft"], indirect=True)
async def test_microsoft_interprets_archived_responses_with_real_collector(expansion_records):
    _, stores = expansion_records
    subjects, states = set(), set()
    for store in stores:
        rows = store.db.execute("SELECT id FROM runs WHERE status='completed' ORDER BY created_at,id").fetchall()
        for row in rows:
            run_id = row["id"]
            store.verify(run_id)
            action = store.actions(run_id)[0]
            assert action["payload"]["site"] == "microsoft"
            run = store.run(run_id)
            request = ResearchRequest.model_validate(run["request"])
            http = HttpExecutor(store, run_id, request.budget)
            try:
                collector = EmailCollector(http, "microsoft", action["payload"]["value"], action["id"])
                collector.artifacts.extend(store.artifacts(run_id))
                responses = [artifact for artifact in collector.artifacts
                             if urlsplit(artifact.url).path == "/GetCredentialType.srf"]
                assert len(responses) == 1
                response = responses[0]
                payload = json.loads(store.body(run_id, response))
                observation = microsoft_result(collector, response)
                original = store.evidence(run_id)[0]
                assert observation.state == original.state
                assert observation.source == response.url
                assert observation.attributes["result_code"] == payload["IfExistsResult"]
                assert observation.attributes["throttle_status"] == payload.get("ThrottleStatus", 0)
                assert observation.attributes["lookup_scope"] == "microsoft_identity"
                assert {artifact.artifact_id for artifact in observation.artifacts} == set(original.artifact_ids)
                assert not http.clients
                assert store.run(run_id)["request_count"] == run["request_count"]
                subjects.add(action["payload"]["value"])
                states.add(observation.state)
            finally:
                await http.close()
    assert len(subjects) >= 2
    assert states == {"detected", "not_detected"}


def test_email_expansion_preserves_real_sources_and_states(expansion_records):
    provider, stores = expansion_records
    subjects, checked = set(), 0
    for store in stores:
        for row in store.db.execute("SELECT id,status,error FROM runs ORDER BY created_at,id").fetchall():
            run_id = row["id"]
            actions = [action for action in store.actions(run_id) if action["payload"].get("site") == provider]
            if not actions:
                continue
            assert len(actions) == 1
            action = actions[0]
            subjects.add(action["payload"]["value"])
            store.verify(run_id)
            evidence = store.evidence(run_id)
            if row["status"] == "failed":
                assert row["error"] and action["status"] == "failed" and action["error"]
                assert not evidence
                continue
            assert row["status"] == "completed"
            assert action["status"] == "completed"
            assert len(evidence) == 1
            item = evidence[0]
            assert item.kind == "email" and item.attributes["provider"] == provider
            artifacts = store.artifacts(run_id)
            assert artifacts and store.run(run_id)["request_count"] >= len(artifacts)
            assert set(item.artifact_ids) == {artifact.artifact_id for artifact in artifacts}
            response = artifacts[-1]
            text = store.body(run_id, response).decode(response.encoding)
            if item.state in {"detected", "not_detected"}:
                _assert_presence(provider, item, response, json.loads(text))
            elif item.state == "metadata":
                payload = json.loads(text)
                assert provider == "flickr" and payload["stat"] == "ok"
                assert item.attributes["registration_status"] == "unknown"
                assert str(item.attributes["migration_state"]) == str(payload["state_code"])
            elif item.state == "blocked":
                restriction = detect_error_page(text, response.status_code)
                if restriction != "blocked":
                    if provider == "ebay":
                        page = BeautifulSoup(text, "html.parser")
                        assert ((page.title and page.title.get_text(strip=True) == "Security Measure | eBay")
                                or urlsplit(response.headers.get("location", "")).path.startswith("/splashui/"))
                    elif provider == "huawei_cloud":
                        assert json.loads(text)["error_code"] in {
                            "CBC.7196", "CBC.7197", "CBC.7198", "CBC.7199", "CBC.7239", "CBC.7012", "CBC.7250",
                        }
                    elif provider == "jianguoyun":
                        assert json.loads(text)["needRealCaptcha"] is True
                    else:
                        assert provider == "plurk" and json.loads(text)["status"] == "blocked"
            elif item.state == "unknown":
                if provider == "flickr":
                    payload = json.loads(text)
                    assert payload["stat"] == "fail"
                    assert payload["code"] in {1, 11, 25}
                    assert item.attributes["result_code"] == payload["code"]
                    if "registration_status" in item.attributes:
                        assert item.attributes["registration_status"] == "unknown"
                    if payload["code"] == 11:
                        assert item.attributes.get("next_step") is None
                        assert payload["message"] == "application error - cognito error"
                    else:
                        assert item.attributes["registration_status"] == "unknown"
                        assert item.attributes["next_step"] == "signup"
                elif provider == "microsoft":
                    payload = json.loads(text)
                    assert payload["IfExistsResult"] in {-1, 4}
                    assert item.attributes["result_code"] == payload["IfExistsResult"]
                else:
                    assert detect_error_page(text, response.status_code) == "unknown"
            elif item.state == "rate_limited":
                if response.status_code != 429:
                    assert provider == "microsoft"
                    payload = json.loads(text)
                    assert payload["IfExistsResult"] == 2 or payload.get("ThrottleStatus") in {1, 2}
                    if "ThrottleStatus" in payload:
                        assert item.attributes["throttle_status"] == payload["ThrottleStatus"]
            else:
                assert item.state == "unsupported" and response.status_code in {404, 410}
            checked += 1
    assert len(subjects) >= 2, f"{provider} 需要两个不同自有邮箱的真实记录"
    assert checked >= 2, f"{provider} 需要两个完整归档的接口观察结果"
