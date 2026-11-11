import json
from importlib.resources import files

import pytest
from pydantic import ValidationError

from kudo_agent.evidence.report import compare, render, validate_draft
from kudo_agent.research_agent.planner import DeepSeekToolSchema, ResearchDecision
from kudo_agent.research_core.collectors import Collectors
from kudo_agent.research_core.http import HttpExecutor, validate_public_url
from kudo_agent.research_core.models import (
    EMAIL_SITES,
    Action,
    ActionPlan,
    Budget,
    Claim,
    Identifier,
    ReportDraft,
    RequestSpec,
    ResearchRequest,
    digest,
)
from kudo_agent.upstream.maigret import load_sites
from kudo_agent.upstream.sherlock import interpolate_string


def test_catalog_reuses_real_snapshots(catalog):
    provenance = json.loads(files("kudo_agent.upstream").joinpath("PROVENANCE.json").read_text(encoding="utf-8"))
    assert len(provenance["sources"]) == 2
    site = catalog.select(["GitHub"], 50)[0]
    assert {rule.origin for rule in site.rules} == {"maigret", "sherlock"}
    assert site.supported
    assert all(rule.valid_username("sherlock-project") for rule in site.rules)
    assert all(not rule.valid_username("contains spaces") for rule in site.rules)
    assert len(catalog.select([], 50)) == 50
    assert any(not site.supported for site in catalog.sites.values())


def test_default_scope_contains_all_adapted_sites(catalog):
    request = ResearchRequest(goal="核查全部适配规则")
    assert request.budget.max_sites is None
    selected = catalog.select(request.sites, request.budget.max_sites)
    assert len(selected) == 8303
    assert len({site.site_id for site in selected}) == len(selected)
    assert {"Zhihu", "Douban", "Gitee", "CSDN"} <= {site.name for site in selected}
    assert {"cnblogs", "jd", "github", "gitee", "tencent_cloud"} <= set(request.email_sites)
    assert request.email_sites == list(EMAIL_SITES)
    assert len(request.email_sites) == 100
    assert {"huawei_cloud", "jianguoyun", "zoho_cn", "microsoft", "adobe", "twitter",
            "ebay", "soundcloud", "flickr", "plurk", "komoot", "archive"} <= set(request.email_sites)
    assert len(catalog.select([], 50)) == 50


def test_default_scope_queries_domestic_sites_before_other_regions(catalog):
    selected = catalog.select([])
    domestic = [site for site in catalog.sites.values() if site.region == "CN" and site.supported]
    assert len(selected) == 8303
    assert len(domestic) == 131
    assert {site.site_id for site in selected[:len(domestic)]} == {site.site_id for site in domestic}
    assert all(site.region != "CN" for site in selected[len(domestic):])
    assert [site.name for site in selected[:8]] == [
        "Gitee", "CSDN", "Zhihu", "Douban", "Baidu", "v2ex.com", "cnblogs.com", "Juejin",
    ]
    assert catalog.select([], 50) == selected[:50]
    assert all(site.region == "CN" for site in catalog.select([], 50))
    international = selected[len(domestic):]
    assert international == sorted(international, key=lambda site: (site.rank, site.site_id))
    explicit = catalog.select(["GitHub", "Gitee", "Douban"])
    assert [site.name for site in explicit] == ["GitHub", "Gitee", "Douban"]


def test_real_site_input_requirements_remain_visible(catalog):
    for name in ("Juejin", "NetEase Music"):
        site = catalog.select([name])[0]
        assert "数字用户 ID" in site.public()["input_hint"]
        assert all(not rule.valid_username("公开昵称") for rule in site.rules)
    bilibili = catalog.select(["Bilibili"])[0]
    assert not bilibili.supported
    assert "UID" in bilibili.public()["input_hint"]
    weibo = catalog.select(["Weibo"])[0]
    assert not weibo.supported
    assert "会话激活" in weibo.public()["input_hint"]
    tiktok = catalog.select(["TikTok"])[0]
    assert tiktok.region == ""
    assert not tiktok.matches("抖音")


@pytest.mark.parametrize("query,name", [
    ("知乎", "Zhihu"), ("豆瓣", "Douban"), ("博客园", "cnblogs.com"),
    ("B站", "Bilibili"), ("微博", "Weibo"), ("gitee.com", "Gitee"),
])
def test_chinese_names_and_domains_match_real_catalog(catalog, query, name):
    matches = [site for site in catalog.sites.values() if site.matches(query)]
    assert any(site.name == name for site in matches)
    assert any(site.region == "CN" for site in matches)


def test_request_body_encoding_and_email_scope():
    with pytest.raises(ValidationError, match="一种请求体编码"):
        RequestSpec(url="https://account.cnblogs.com/account/checkEmail", form={}, multipart={})
    with pytest.raises(ValidationError, match="一种请求体编码"):
        RequestSpec(url="https://account.cnblogs.com/account/checkEmail", multipart={}, json_body={})
    with pytest.raises(ValidationError):
        ResearchRequest(goal="验证邮箱站点范围", email_sites=["unregistered-provider"])


def test_engine_rules_are_inherited():
    database = json.loads(files("kudo_agent.upstream").joinpath("data/maigret.json").read_text(encoding="utf-8"))
    sites = load_sites(database)
    site = next(site for site in sites if site.data.get("engine") == "XenForo")
    engine = database["engines"]["XenForo"]["site"]
    assert set(engine["absenceStrs"]) <= set(site.data["absenceStrs"])
    assert "{urlMain}" not in site.data["url"]


def test_real_rule_interpolation(catalog):
    site = catalog.select(["GitHub"], 50)[0]
    rule = next(rule for rule in site.rules if rule.origin == "sherlock")
    assert "sherlock-project" in interpolate_string(rule.data, "sherlock-project")["url"]
    assert rule.request("sherlock-project").url.startswith("https://")


@pytest.mark.parametrize("kind,value", [
    ("profile", "file:///etc/passwd"), ("profile", "https://name:password@github.com/"),
    ("ip", "127.0.0.1"), ("ip", "::1"), ("phone", "12345"),
    ("username", "../../private"), ("email", "invalid-address"),
])
def test_invalid_identifiers_fail(kind, value):
    with pytest.raises(ValueError):
        Identifier(kind=kind, value=value)


def test_invalid_plan_fails():
    with pytest.raises(ValidationError):
        ActionPlan(actions=[], stop_reason="")
    with pytest.raises(ValidationError):
        Budget(max_sites=0)


def test_deepseek_schema_retains_local_constraints():
    remote = ReportDraft.model_json_schema(schema_generator=DeepSeekToolSchema)
    local = ReportDraft.model_json_schema()
    claim = remote["$defs"]["Claim"]
    assert set(claim["required"]) == set(claim["properties"])
    assert claim["additionalProperties"] is False
    assert "minLength" not in claim["properties"]["text"]
    assert "minItems" not in claim["properties"]["evidence_ids"]
    assert local["$defs"]["Claim"]["properties"]["text"]["minLength"] == 1
    with pytest.raises(ValidationError):
        Claim(text="", evidence_ids=[])


def test_decision_requires_one_branch():
    assert ResearchDecision.model_validate({"actions": [], "stop_reason": "没有新线索"}).stop_reason
    with pytest.raises(ValidationError):
        ResearchDecision.model_validate({"actions": [], "stop_reason": ""})
    with pytest.raises(ValidationError):
        ResearchDecision.model_validate({"actions": []})
    action = {"action_id": Action(capability="profile", value="https://github.com/sherlock-project").key,
              "question": "核对项目主页", "reason": "读取公开介绍", "evidence_ids": []}
    assert ResearchDecision.model_validate({"actions": [action], "stop_reason": ""}).actions
    with pytest.raises(ValidationError):
        ResearchDecision.model_validate({"actions": [action], "stop_reason": "结束研究"})


async def test_private_network_rejected():
    with pytest.raises(ValueError, match="非公共网络"):
        await validate_public_url("http://127.0.0.1/")
    with pytest.raises(ValueError, match="端口"):
        await validate_public_url("https://github.com:8080/")


async def collect_number(store, catalog):
    identifier = Identifier(kind="phone", value="+41446681800")
    request = ResearchRequest(goal="核查号码规则资料", identifiers=[identifier])
    run_id = store.create(request, catalog.version)
    action = Action(capability="phone", value=identifier.value)
    store.add_actions(run_id, [action])
    http = HttpExecutor(store, run_id, request.budget)
    try:
        items = await Collectors(catalog, store, run_id, http).collect(action)
        store.finish_action(run_id, action.key, items)
    finally:
        await http.close()
    return run_id, action, items


async def test_phone_persistence_provenance_and_report(store, catalog):
    run_id, action, items = await collect_number(store, catalog)
    assert items[0].attributes["e164"] == "+41446681800"
    assert items[0].attributes["region"] == "CH"
    assert store.run(run_id)["request_count"] == 0
    store.verify(run_id)
    assert store.actions(run_id)[0]["status"] == "completed"
    store.add_actions(run_id, [action])
    assert len(store.actions(run_id)) == 1
    assert "phonenumbers://" in render(store, run_id)
    draft = ReportDraft(claims=[Claim(text="号码地区为 CH", evidence_ids=[items[0].evidence_id])], next_actions=[])
    validate_draft(draft, items)
    with pytest.raises(ValueError, match="不存在"):
        validate_draft(ReportDraft(claims=[Claim(text="不存在的引用", evidence_ids=[digest("missing")])], next_actions=[]), items)


async def test_real_metadata_comparison(store, catalog):
    first, _, _ = await collect_number(store, catalog)
    second, _, _ = await collect_number(store, catalog)
    result = compare(store, first, second)
    assert len(result["changes"]["unchanged"]) == 1
    assert not result["changes"]["disappeared"]


async def test_checksum_detects_changed_artifact(store, catalog):
    run_id, _, items = await collect_number(store, catalog)
    artifact = store.artifact(run_id, items[0].artifact_ids[0])
    path = store.run_dir(run_id) / artifact.body_path
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="校验失败"):
        store.verify(run_id)


def test_request_budget_and_pending_account_intent(store, catalog):
    run_id = store.create(ResearchRequest(goal="请求预算验证"), catalog.version)
    store.reserve_request(run_id, "account-check", True, 1)
    assert store.uncertain_requests(run_id) == ["account-check"]
    with pytest.raises(RuntimeError, match="预算"):
        store.reserve_request(run_id, "second", False, 1)
    assert store.run(run_id)["request_count"] == 1
    store.acknowledge_uncertain_requests(run_id)
    assert not store.uncertain_requests(run_id)
    store.reserve_request(run_id, "account-check", True, 2)
    assert store.uncertain_requests(run_id) == ["account-check"]
