import json
from importlib.metadata import metadata
from urllib.parse import parse_qs, unquote, urlsplit

import phonenumbers
import pytest

from kudo_agent.research_core.collectors import Collectors, phone_mentions, phone_metadata
from kudo_agent.research_core.http import HttpExecutor
from kudo_agent.research_core.models import Action, Identifier, ResearchRequest


@pytest.mark.parametrize("region,number_type,engines", [
    ("CN", phonenumbers.PhoneNumberType.MOBILE, {"Baidu", "Google", "Bing"}),
    ("CH", phonenumbers.PhoneNumberType.FIXED_LINE, {"Google", "Bing"}),
])
def test_phone_formats_and_search_suggestions_from_library_examples(region, number_type, engines):
    number = phonenumbers.example_number_for_type(region, number_type)
    value = phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
    data = phone_metadata(value)
    assert data["region"] == region
    assert data["possible_number"] and data["valid_range"]
    assert data["possibility_reason"] == "IS_POSSIBLE"
    assert data["country_code"] == number.country_code
    assert data["country_name"]
    assert isinstance(data["mobile_number_portability"], bool)
    assert data["number_type"] == phonenumbers.PhoneNumberType.to_string(number_type)
    assert {item["engine"] for item in data["search_suggestions"]} == engines
    assert len({item["url"] for item in data["search_suggestions"]}) == len(data["search_suggestions"])
    if region == "CN":
        assert data["number_formats"][0] == {"kind": "national_digits", "value": data["national_digits"]}
        assert data["search_suggestions"][0]["engine"] == "Baidu"
    for suggestion in data["search_suggestions"]:
        assert suggestion["state"] == "not_executed"
        assert suggestion["category"] == "exact_number"
        url = urlsplit(suggestion["url"])
        parameter = "wd" if suggestion["engine"] == "Baidu" else "q"
        assert parse_qs(url.query)[parameter] == [suggestion["query"]]
        assert suggestion["query"] == f'"{data[suggestion["number_format"]]}"'


async def test_phone_suggestions_are_local_evidence_and_preserve_request_budget(store, catalog):
    number = phonenumbers.example_number_for_type("CN", phonenumbers.PhoneNumberType.MOBILE)
    identifier = Identifier(kind="phone", value=phonenumbers.format_number(
        number, phonenumbers.PhoneNumberFormat.E164))
    request = ResearchRequest(goal="核查号码规则与公开搜索建议", identifiers=[identifier])
    run_id = store.create(request, catalog.version)
    action = Action(capability="phone", value=identifier.value)
    store.add_actions(run_id, [action])
    http = HttpExecutor(store, run_id, request.budget)
    try:
        evidence = (await Collectors(catalog, store, run_id, http).collect(action))[0]
    finally:
        await http.close()
    store.finish_action(run_id, action.key, [evidence])
    artifact = store.artifact(run_id, evidence.artifact_ids[0])
    saved = json.loads((store.run_dir(run_id) / artifact.body_path).read_text(encoding="utf-8"))
    assert saved == evidence.attributes
    assert artifact.method == "LOCAL"
    assert evidence.state == "metadata"
    assert evidence.relations == []
    assert store.run(run_id)["request_count"] == 0
    assert len(store.actions(run_id)) == 1
    store.verify(run_id)


def test_phone_mentions_use_official_matcher_examples_and_keep_text_positions():
    text = "Call me at 510-748-8230 if it's before 9:30, or on 703-4800500 after 10am."
    assert text in metadata("phonenumbers").get_payload()
    identifiers = [Identifier(kind="phone", value="+15107488230"),
                   Identifier(kind="phone", value="+17034800500"),
                   Identifier(kind="phone", value="+442083661177")]
    source = "https://github.com/daviddrysdale/python-phonenumbers"
    mentions = phone_mentions(text, [], identifiers, source)
    assert {item["normalized"] for item in mentions} == {"+15107488230", "+17034800500"}
    assert len(mentions) == 2
    for item in mentions:
        assert text[item["start"]:item["end"]] == item["raw"]
        assert item["source_url"] == source
        assert item["location"] == "body"
        assert item["coordinate_space"] == "extracted_body"
        assert item["assumed_region"] == "US"
        assert item["match"] == "exact"


def test_phone_mentions_accept_domestic_chinese_formats_from_library_examples():
    number = phonenumbers.example_number_for_type("CN", phonenumbers.PhoneNumberType.MOBILE)
    identifier = Identifier(kind="phone", value=phonenumbers.format_number(
        number, phonenumbers.PhoneNumberFormat.E164))
    national = phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.NATIONAL)
    mentions = phone_mentions(national, [], [identifier], "phonenumbers://example/CN/MOBILE")
    assert len(mentions) == 1
    assert mentions[0]["raw"] == national
    assert mentions[0]["normalized"] == identifier.value
    assert mentions[0]["assumed_region"] == "CN"


def test_tel_uri_mentions_from_rfc3966_keep_href_and_positions():
    # RFC 3966 第六节提供的国际号码示例。
    href = "tel:+1-201-555-0123"
    identifier = Identifier(kind="phone", value="+12015550123")
    source = "https://www.rfc-editor.org/rfc/rfc3966"
    mentions = phone_mentions("", [{"href": href, "link_index": 0}], [identifier], source)
    assert len(mentions) == 1
    mention = mentions[0]
    assert mention["location"] == "tel"
    assert mention["href"] == href
    assert mention["coordinate_space"] == "decoded_href"
    assert unquote(href)[mention["start"]:mention["end"]] == mention["raw"]
    assert mention["normalized"] == identifier.value
    assert mention["link_index"] == 0


@pytest.mark.parametrize("href", [
    "tel:7042;phone-context=example.com",
    "tel:863-1234;phone-context=+1-914-555",
])
def test_rfc3966_local_context_does_not_establish_global_number_relation(href):
    # RFC 3966 第六节的局部号码示例；作用域无法转换为全球号码。
    identifier = Identifier(kind="phone", value="+12015550123")
    assert phone_mentions("", [{"href": href}], [identifier],
                          "https://www.rfc-editor.org/rfc/rfc3966") == []


def test_incomplete_tel_uri_does_not_interrupt_other_rfc3966_mentions():
    href = "tel:+1-201-555-0123"
    identifier = Identifier(kind="phone", value="+12015550123")
    links = [{"href": "tel:+"}, {"href": href, "link_index": 1}]
    mentions = phone_mentions("", links, [identifier], "https://www.rfc-editor.org/rfc/rfc3966")
    assert len(mentions) == 1
    assert mentions[0]["href"] == href
    assert mentions[0]["raw"] == href
    assert mentions[0]["start"] == 0 and mentions[0]["end"] == len(href)
    assert mentions[0]["link_index"] == 1
    assert mentions[0]["assumed_region"] is None


def test_email_and_username_inputs_do_not_create_phone_mentions():
    text = metadata("phonenumbers").get_payload()
    identifiers = [Identifier(kind="username", value="phonenumbers"),
                   Identifier(kind="email", value="reference@example.com")]
    assert phone_mentions(text, [], identifiers, "https://pypi.org/project/phonenumbers/") == []


async def test_numeric_site_input_limit_is_saved_without_account_absence_claim(store, catalog):
    site = next(site for site in catalog.sites.values() if site.name == "Juejin")
    identifier = Identifier(kind="username", value="beishaoqi")
    request = ResearchRequest(goal="核查站点输入格式要求", identifiers=[identifier], sites=[site.site_id])
    run_id = store.create(request, catalog.version)
    action = Action(capability="username", value=identifier.value, site=site.site_id)
    http = HttpExecutor(store, run_id, request.budget)
    try:
        evidence = (await Collectors(catalog, store, run_id, http).collect(action))[0]
    finally:
        await http.close()
    assert evidence.state == "unsupported"
    assert all(detection.state == "unsupported" for detection in evidence.detections)
    assert any("完整主页链接" in detection.reason for detection in evidence.detections)
    assert evidence.relations == []
    assert store.run(run_id)["request_count"] == 0


@pytest.mark.live
async def test_profile_phone_mentions_from_actual_library_documentation(store, catalog):
    url = "https://raw.githubusercontent.com/daviddrysdale/python-phonenumbers/dev/README.md"
    identifier = Identifier(kind="phone", value="+442083661177")
    request = ResearchRequest(goal="核查号码库官方文档中的格式示例", identifiers=[identifier])
    run_id = store.create(request, catalog.version)
    action = Action(capability="profile", value=url)
    store.add_actions(run_id, [action])
    http = HttpExecutor(store, run_id, request.budget)
    try:
        evidence = (await Collectors(catalog, store, run_id, http).collect(action))[0]
    finally:
        await http.close()
    store.finish_action(run_id, action.key, [evidence])
    assert evidence.state == "detected"
    assert evidence.attributes["phone_mentions"]
    assert {item["normalized"] for item in evidence.attributes["phone_mentions"]} == {identifier.value}
    relations = [relation for relation in evidence.relations if relation.kind == "mentions_identifier"]
    assert len(relations) == 1
    assert relations[0].target == f"phone:{identifier.value}"
    assert relations[0].evidence_ids == [evidence.evidence_id]
    assert not any(relation.kind == "same_username" for relation in evidence.relations)
    assert store.run(run_id)["request_count"] == 1
    store.verify(run_id)
