import hashlib
import json
import re
from importlib.metadata import version
from urllib.parse import urlencode, urljoin, urlsplit

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
                detections.append(Detection(rule_id=rule.rule_id, state="not_detected", reason="用户名不符合站点格式"))
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
        description = soup.find("meta", attrs={"name": "description"})
        metadata = {"title": title, "description": description.get("content", "") if description else "",
                    "language": soup.html.get("lang", "") if soup.html else ""}
        canonical = soup.find("link", rel="canonical")
        declared_url = urljoin(artifact.final_url, canonical.get("href", "")) if canonical else ""
        content = soup.find("main") or soup.find(attrs={"role": "main"}) or soup.body or soup
        for tag in content.select("script,style,noscript,nav,header,footer,[role=navigation],[role=banner],[role=contentinfo]"):
            tag.decompose()
        links = set()
        for anchor in content.find_all("a", href=True):
            url = urljoin(artifact.final_url, anchor["href"])
            parsed = urlsplit(url)
            if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password:
                links.add(canonical_url(url))
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
        for identifier in request.identifiers:
            pattern = re.compile(r"(?<!\w)" + re.escape(identifier.value) + r"(?!\w)", re.IGNORECASE)
            if pattern.search(page_text):
                item.relations.append(Relation(
                    source=artifact.final_url, target=f"{identifier.kind}:{identifier.value}",
                    kind="mentions_identifier", evidence_ids=[item.evidence_id],
                ))
        return item

    async def phone(self, action: Action) -> Evidence:
        number = phonenumbers.parse(action.value, None)
        data = {
            "e164": phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164),
            "international": phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.INTERNATIONAL),
            "valid_range": phonenumbers.is_valid_number(number),
            "region": phonenumbers.region_code_for_number(number),
            "prefix_description": geocoder.description_for_number(number, "zh"),
            "prefix_carrier": carrier.name_for_number(number, "en"),
            "time_zones": list(timezone.time_zones_for_number(number)),
            "number_type": phonenumbers.PhoneNumberType.to_string(phonenumbers.number_type(number)),
            "search_suggestions": [
                {"query": f'"{action.value}"', "url": "https://www.google.com/search?" + urlencode({"q": f'"{action.value}"'})},
                {"query": f'"{action.value}"', "url": "https://www.bing.com/search?" + urlencode({"q": f'"{action.value}"'})},
            ],
        }
        source = f"phonenumbers://{version('phonenumbers')}"
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
