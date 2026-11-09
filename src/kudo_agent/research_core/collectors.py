import hashlib
import json
import re
from importlib.metadata import version
from urllib.parse import unquote, urlencode, urljoin, urlsplit

import phonenumbers
from bs4 import BeautifulSoup
from phonenumbers import carrier, geocoder, timezone
from socid_extractor import extract

from kudo_agent.evidence.store import Store
from kudo_agent.research_core.catalog import Catalog
from kudo_agent.research_core.email import EmailCollector
from kudo_agent.research_core.http import HttpExecutor
from kudo_agent.research_core.models import (
    Action,
    Artifact,
    Detection,
    Evidence,
    Identifier,
    Relation,
    RequestSpec,
    ResearchRequest,
    canonical_url,
    digest,
    timestamp,
)
from kudo_agent.upstream import maigret, sherlock


def phone_metadata(value: str) -> dict:
    number = phonenumbers.parse(value, None)
    region = phonenumbers.region_code_for_number(number)
    formats = {
        "e164": phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164),
        "international": phonenumbers.format_number(
            number, phonenumbers.PhoneNumberFormat.INTERNATIONAL),
        "national": phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.NATIONAL),
    }
    formats["national_digits"] = phonenumbers.normalize_digits_only(formats["national"])
    order = ("national_digits", "national", "e164", "international") if region == "CN" else (
        "e164", "international", "national", "national_digits")
    number_formats = []
    for kind in order:
        if formats[kind] not in {item["value"] for item in number_formats}:
            number_formats.append({"kind": kind, "value": formats[kind]})
    engines = [("Google", "https://www.google.com/search", "q"),
               ("Bing", "https://www.bing.com/search", "q")]
    if region == "CN":
        engines.insert(0, ("Baidu", "https://www.baidu.com/s", "wd"))
    suggestions = []
    for engine, endpoint, parameter in engines:
        for item in number_formats:
            query = f'"{item["value"]}"'
            suggestions.append({
                "engine": engine, "category": "exact_number", "number_format": item["kind"],
                "query": query, "url": endpoint + "?" + urlencode({parameter: query}),
                "state": "not_executed",
            })
    return {
        **formats,
        "country_code": number.country_code,
        "country_name": geocoder.country_name_for_number(number, "zh"),
        "possible_number": phonenumbers.is_possible_number(number),
        "possibility_reason": phonenumbers.ValidationResult.to_string(
            phonenumbers.is_possible_number_with_reason(number)),
        "valid_range": phonenumbers.is_valid_number(number),
        "region": region,
        "prefix_description": geocoder.description_for_number(number, "zh"),
        "prefix_carrier": carrier.name_for_number(number, "en"),
        "mobile_number_portability": phonenumbers.is_mobile_number_portable_region(region),
        "time_zones": list(timezone.time_zones_for_number(number)),
        "number_type": phonenumbers.PhoneNumberType.to_string(phonenumbers.number_type(number)),
        "number_formats": number_formats,
        "search_suggestions": suggestions,
        "limitations": [
            "号码有效性表示长度和号段符合规则，当前启用及可接通状态尚未核实。",
            "地区来自号码分配规则；运营商资料表示号段原始运营商，携号转网可能改变当前运营商。",
            "搜索建议尚未执行，打开链接后才能查看对应搜索结果。",
        ],
    }


def phone_mentions(page_text: str, tel_links: list[dict], identifiers: list[Identifier],
                   source_url: str) -> list[dict]:
    regions = {}
    for identifier in identifiers:
        if identifier.kind != "phone":
            continue
        number = phonenumbers.parse(identifier.value, None)
        region = phonenumbers.region_code_for_number(number)
        regions.setdefault(region, {})[identifier.value] = number
    mentions = []
    for region, targets in regions.items():
        for match in phonenumbers.PhoneNumberMatcher(page_text, region):
            for normalized, number in targets.items():
                if phonenumbers.is_number_match(number, match.number) == phonenumbers.MatchType.EXACT_MATCH:
                    mentions.append({
                        "location": "body", "coordinate_space": "extracted_body",
                        "normalized": normalized, "raw": match.raw_string,
                        "start": match.start, "end": match.end,
                        "context": page_text[max(0, match.start - 48):match.end + 48],
                        "source_url": source_url, "assumed_region": region, "match": "exact",
                    })
    for link in tel_links:
        text = unquote(link["href"])
        parsed = urlsplit(text)
        # RFC 3966 的局部 phone-context 仅表示作用域，无法据此确认全球号码。
        if parsed.scheme != "tel" or not parsed.path.startswith("+"):
            continue
        if not phonenumbers.is_possible_number_string(text, None):
            continue
        uri_number = phonenumbers.parse(text, None)
        if not phonenumbers.is_valid_number(uri_number):
            continue
        for targets in regions.values():
            for normalized, number in targets.items():
                if phonenumbers.is_number_match(number, uri_number) == phonenumbers.MatchType.EXACT_MATCH:
                    mentions.append({
                        **link, "location": "tel", "coordinate_space": "decoded_href",
                        "normalized": normalized, "raw": text, "start": 0, "end": len(text),
                        "context": text, "source_url": source_url, "assumed_region": None, "match": "exact",
                    })
    return mentions


class Collectors:
    def __init__(self, catalog: Catalog, store: Store, run_id: str, http: HttpExecutor):
        self.catalog, self.store, self.run_id, self.http = catalog, store, run_id, http

    async def collect(self, action: Action) -> list[Evidence]:
        handlers = {"username": self.username, "profile": self.profile, "email": self.email,
                    "phone": self.phone, "ip": self.ip}
        if action.capability not in handlers:
            raise ValueError("该动作不属于网络采集能力")
        normalized = Identifier(kind=action.capability, value=action.value)
        if normalized.value != action.value:
            raise ValueError("采集动作必须使用已经标准化的输入")
        return [await handlers[action.capability](action)]

    def item(self, action: Action, kind: str, state: str, summary: str,
             source: str, artifacts: list[Artifact], **values) -> Evidence:
        return Evidence(
            evidence_id=digest([action.key, kind]), action_id=action.key, kind=kind,
            subject=action.value, source_url=source, state=state, summary=summary,
            observed_at=max((a.observed_at for a in artifacts), default=timestamp()),
            artifact_ids=list(dict.fromkeys(a.artifact_id for a in artifacts)), **values,
        )

    def local_artifact(self, action: Action, source: str, data: dict) -> Artifact:
        key = digest([action.key, source])
        existing = self.store.artifact(self.run_id, key)
        if existing:
            return existing
        body = json.dumps(data, ensure_ascii=False, sort_keys=True).encode()
        artifact = Artifact(
            artifact_id=key, request_key=key, method="LOCAL", url=source, final_url=source,
            status_code=0, headers={"content-type": "application/json"}, observed_at=timestamp(),
            sha256=hashlib.sha256(body).hexdigest(), body_path=f"artifacts/{key}.json", encoding="utf-8",
        )
        self.store.save_artifact(self.run_id, artifact, body)
        return artifact

    async def username(self, action: Action) -> Evidence:
        site = self.catalog.sites[action.site]
        detections, artifacts = [], []
        source = site.rules[0].profile_url(action.value)
        for rule in site.rules:
            if rule.unsupported:
                detections.append(Detection(rule_id=rule.rule_id, state="unsupported", reason=rule.unsupported))
                continue
            if not rule.valid_username(action.value):
                detections.append(Detection(
                    rule_id=rule.rule_id, state="unsupported",
                    reason="输入不符合该站点查询格式，请提供对应标识符或完整主页链接",
                ))
                continue
            spec = rule.request(action.value)
            artifact = await self.http.fetch(spec)
            artifacts.append(artifact)
            text = self.http.text(artifact)
            expected = (301, 302, 303, 307, 308) if not spec.follow_redirects else ()
            problem = maigret.detect_error_page(text, artifact.status_code, rule.data.get("errors"), expected)
            if problem:
                detections.append(Detection(rule_id=rule.rule_id, state=problem,
                                            reason=f"HTTP {artifact.status_code} 或错误页面标记"))
            elif rule.origin == "maigret":
                detections.append(maigret.process_site_result(
                    rule.rule_id, rule.data, action.value, text, artifact.status_code))
            else:
                detections.append(sherlock.evaluate(rule.rule_id, rule.data, text, artifact.status_code))
        decisions = {d.state for d in detections if d.state in {"detected", "not_detected"}}
        state = next(iter(decisions)) if len(decisions) == 1 else "unknown"
        if not decisions:
            states = {d.state for d in detections}
            state = next(iter(states)) if len(states) == 1 else "unknown"
        conflict = len(decisions) > 1
        item = self.item(
            action, "account", state, f"{site.name}：{'检测规则存在冲突' if conflict else state}",
            source, artifacts, detections=detections, rule_versions=[r.rule_id for r in site.rules],
            attributes={"site_id": site.site_id, "site": site.name, "conflict": conflict},
        )
        if state == "detected":
            item.relations.append(Relation(source=f"username:{action.value}", target=source,
                                           kind="same_username", evidence_ids=[item.evidence_id]))
        return item

    async def profile(self, action: Action) -> Evidence:
        artifact = await self.http.fetch(RequestSpec(url=action.value))
        text = self.http.text(artifact)
        problem = maigret.detect_error_page(text, artifact.status_code)
        if problem or not 200 <= artifact.status_code < 300:
            return self.item(action, "profile", problem or "not_detected", f"HTTP {artifact.status_code}",
                             action.value, [artifact])
        soup = BeautifulSoup(text, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        if urlsplit(artifact.final_url).hostname == "passport.weibo.com" or title == "Sina Visitor System":
            return self.item(action, "profile", "blocked", "微博主页要求访客会话激活，尚未取得主页内容",
                             action.value, [artifact], attributes={"title": title,
                                                                  "access_url": artifact.final_url})
        description = soup.find("meta", attrs={"name": "description"})
        metadata = {"title": title, "description": description.get("content", "") if description else "",
                    "language": soup.html.get("lang", "") if soup.html else ""}
        canonical = soup.find("link", rel="canonical")
        declared_url = urljoin(artifact.final_url, canonical.get("href", "")) if canonical else ""
        content = soup.find("main") or soup.find(attrs={"role": "main"}) or soup.body or soup
        for tag in content.select("script,style,noscript,nav,header,footer,[role=navigation],[role=banner],[role=contentinfo]"):
            tag.decompose()
        links, tel_links = set(), []
        for index, anchor in enumerate(content.find_all("a", href=True)):
            url = urljoin(artifact.final_url, anchor["href"])
            parsed = urlsplit(url)
            if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password:
                links.add(canonical_url(url))
            elif parsed.scheme == "tel":
                tel_links.append({"href": anchor["href"], "link_index": index,
                                  "source_line": anchor.sourceline, "source_column": anchor.sourcepos})
        page_text = content.get_text(" ", strip=True)
        candidates = []
        for site in self.catalog.sites.values():
            if urlsplit(site.template).netloc.lower() == urlsplit(artifact.final_url).netloc.lower() and site.template.count("{username}") == 1:
                match = maigret.make_profile_url_regexp(site.template).fullmatch(artifact.final_url.rstrip("/"))
                if match:
                    candidates.append(match.group("_maigret_username"))
        signals = {
            "canonical_url": declared_url,
            "canonical_matches": bool(declared_url) and declared_url.rstrip("/") == artifact.final_url.rstrip("/"),
            "username_in_title": any(name.casefold() in title.casefold() for name in candidates),
            "username_in_description": any(name.casefold() in metadata["description"].casefold() for name in candidates),
            "username_in_text": any(name.casefold() in page_text.casefold() for name in candidates),
        }
        attributes = {**metadata, "text_signals": signals, "text_excerpt": page_text[:8000],
                      "text_sha256": hashlib.sha256(page_text.encode()).hexdigest(),
                      "links": sorted(links)[:1000], "extracted": extract(text)}
        item = self.item(action, "profile", "detected", title or "公开页面已取得", artifact.final_url,
                         [artifact], attributes=attributes, rule_versions=[f"socid-extractor@{version('socid-extractor')}"])
        item.relations = [Relation(source=artifact.final_url, target=url, kind="links_to",
                                   evidence_ids=[item.evidence_id]) for url in attributes["links"]]
        request = ResearchRequest.model_validate(self.store.run(self.run_id)["request"])
        mentions = phone_mentions(page_text, tel_links, request.identifiers, artifact.final_url)
        item.attributes["phone_mentions"] = mentions
        if any(identifier.kind == "phone" for identifier in request.identifiers):
            item.rule_versions.append(f"phonenumbers@{version('phonenumbers')}")
        for identifier in request.identifiers:
            if identifier.kind == "phone":
                mentioned = any(item["normalized"] == identifier.value for item in mentions)
            else:
                pattern = re.compile(r"(?<!\w)" + re.escape(identifier.value) + r"(?!\w)", re.IGNORECASE)
                mentioned = bool(pattern.search(page_text))
            if mentioned:
                item.relations.append(Relation(
                    source=artifact.final_url, target=f"{identifier.kind}:{identifier.value}",
                    kind="mentions_identifier", evidence_ids=[item.evidence_id],
                ))
        return item

    async def phone(self, action: Action) -> Evidence:
        data = phone_metadata(action.value)
        source = f"phonenumbers://{version('phonenumbers')}/phone-metadata/2"
        artifact = self.local_artifact(action, source, data)
        return self.item(action, "phone", "metadata", "号码规则与号段资料；搜索链接为待执行建议",
                         source, [artifact], attributes=data, rule_versions=[source])

    async def ip(self, action: Action) -> Evidence:
        artifact = await self.http.fetch(RequestSpec(url=f"https://ipwho.is/{action.value}"))
        text = self.http.text(artifact)
        problem = maigret.detect_error_page(text, artifact.status_code)
        if problem:
            return self.item(action, "ip", problem, f"IP 服务返回 HTTP {artifact.status_code}", artifact.url, [artifact])
        data = json.loads(text)
        if not isinstance(data.get("success"), bool):
            raise ValueError("IP 服务响应缺少 success 布尔字段")
        state = "metadata" if data["success"] else "unknown"
        return self.item(action, "ip", state, "IP 网络资料与估算地理位置", artifact.url, [artifact], attributes=data)

    async def email(self, action: Action) -> Evidence:
        result = await EmailCollector(self.http, action.site, action.value, action.key).collect()
        return self.item(action, "email", result.state, result.summary, result.source, result.artifacts,
                         attributes=result.attributes, rule_versions=[f"email:{action.site}@1"])
