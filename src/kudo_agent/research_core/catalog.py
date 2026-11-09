import json
import re
from dataclasses import dataclass, field
from importlib.resources import files
from urllib.parse import quote, urlsplit

from kudo_agent import __version__
from kudo_agent.research_core.models import RequestSpec, digest
from kudo_agent.upstream import maigret, sherlock

DOMESTIC_PRIORITY = {name: position for position, name in enumerate((
    "Gitee", "CSDN", "Zhihu", "Douban", "Baidu", "v2ex.com", "cnblogs.com", "Juejin",
    "Yuque", "GitCode", "Bilibili", "Weibo",
))}
INPUT_HINTS = {
    "Juejin": "填写掘金数字用户 ID，或完整公开主页链接。",
    "NetEase Music": "填写网易云音乐数字用户 ID，或完整公开主页链接。",
    "Bilibili": "该规则需要哔哩哔哩数字 UID；请提供包含 UID 的完整公开主页链接。",
    "QQ": "该规则需要 QQ 号码；请提供完整公开空间链接。",
    "Weibo": "用户名查询需要微博访客会话激活；当前可提供完整公开主页链接进行资料分析。",
    "Zhihu": "填写知乎个人主页地址中的标识符，或完整公开主页链接。",
    "Baidu": "填写百度用户名，或完整公开主页链接。",
}


@dataclass
class Rule:
    origin: str
    name: str
    data: dict
    unsupported: str = ""

    @property
    def rule_id(self):
        version = maigret.COMMIT if self.origin == "maigret" else sherlock.COMMIT
        return f"{self.origin}:{self.name}@{version}"

    @property
    def profile_template(self):
        return self.data.get("url", "").replace("{}", "{username}")

    def profile_url(self, username: str):
        return self.profile_template.replace("{username}", quote(username, safe=""))

    def valid_username(self, username: str):
        expression = self.data.get("regexCheck")
        return not expression or re.search(expression, username) is not None

    def request(self, username: str) -> RequestSpec:
        value = quote(username, safe="")
        template = self.data.get("urlProbe") or self.profile_template
        url = sherlock.interpolate_string(template, value).replace("{username}", value)
        method = self.data.get("request_method", "GET").upper() or "GET"
        check = self.data.get("checkType", self.data.get("errorType"))
        checks = [check] if isinstance(check, str) else check
        return RequestSpec(
            method=method,
            url=url,
            headers=self.data.get("headers", {}),
            params={key: str(value).replace("{username}", username).replace("{}", username)
                    for key, value in self.data.get("getParams", {}).items()},
            follow_redirects="response_url" not in checks,
        )


@dataclass
class Site:
    site_id: str
    name: str
    template: str
    rank: int
    rules: list[Rule] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)

    @property
    def region(self):
        return "CN" if any("cn" in rule.data.get("tags", []) for rule in self.rules) else ""

    @property
    def sort_key(self) -> tuple[bool, int, int, str]:
        return (self.region != "CN", DOMESTIC_PRIORITY.get(self.name, len(DOMESTIC_PRIORITY)),
                self.rank, self.site_id)

    @property
    def input_hint(self) -> str:
        return INPUT_HINTS.get(self.name, "填写该站点用户名，或完整公开主页链接。")

    def matches(self, query: str):
        terms = query.casefold().strip().split()
        text = " ".join([self.site_id, self.name, self.template, *self.aliases,
                         *(rule.name for rule in self.rules),
                         "中国 国内 中文 cn" if self.region == "CN" else ""]).casefold()
        return all(term in text for term in terms)

    @property
    def supported(self):
        return any(not rule.unsupported for rule in self.rules)

    def public(self):
        return {
            "site_id": self.site_id, "name": self.name, "profile_template": self.template,
            "supported": self.supported,
            "aliases": self.aliases, "region": self.region,
            "input_types": sorted({rule.data.get("type", "username") for rule in self.rules}),
            "input_hint": self.input_hint,
            "rules": [{"rule_id": r.rule_id, "unsupported": r.unsupported} for r in self.rules],
        }


def unsupported_reason(data: dict, origin: str) -> str:
    if data.get("disabled"):
        return "上游禁用的站点"
    if data.get("type", "username") != "username":
        identifier = {"bilibili_id": "哔哩哔哩数字 UID", "qq_id": "QQ 号码"}.get(data["type"], data["type"])
        return f"该规则需要 {identifier}，可提供完整公开主页地址进行资料查询"
    if data.get("similarSearch"):
        return "规则使用模糊检索"
    if data.get("activation"):
        return "规则需要专门的会话激活"
    if any(key.lower() in {"authorization", "cookie", "x-api-key"} for key in data.get("headers", {})):
        return "规则需要站点身份凭据"
    if data.get("protocol", "") not in {"", "http", "https"}:
        return "规则需要独立协议环境"
    if (data.get("request_method") or "GET").upper() not in {"GET", "HEAD"}:
        return "公开用户名查询仅执行 GET 或 HEAD"
    if data.get("request_payload") or data.get("requestPayload"):
        return "规则需要额外请求载荷"
    template = data.get("urlProbe") or data.get("url", "")
    if not template.startswith(("http://", "https://")):
        return "规则缺少公共 HTTP 地址"
    remaining = template.replace("{username}", "").replace("{}", "")
    if "{" in remaining or "}" in remaining:
        return "规则包含未支持的模板参数"
    check = data.get("checkType", "status_code") if origin == "maigret" else data.get("errorType")
    checks = [check] if isinstance(check, str) else check
    if not checks or any(c not in {"message", "status_code", "response_url"} for c in checks):
        return "规则包含未支持的检测方式"
    return ""


class Catalog:
    def __init__(self):
        resources = files("kudo_agent.upstream").joinpath("data")
        database = json.loads(resources.joinpath("maigret.json").read_text(encoding="utf-8"))
        rules = [Rule("maigret", s.name, s.data) for s in maigret.load_sites(database)]
        sherlock_data = json.loads(resources.joinpath("sherlock.json").read_text(encoding="utf-8"))
        rules += [Rule("sherlock", name, data) for name, data in sherlock_data.items() if name != "$schema"]
        groups = {}
        for rule in rules:
            rule.unsupported = unsupported_reason(rule.data, rule.origin)
            template = rule.profile_template
            parts = urlsplit(template)
            key = (parts.netloc.lower().removeprefix("www."), parts.path.rstrip("/"), parts.query,
                   rule.data.get("type", "username"))
            rank = rule.data.get("alexaRank") or 2**63 - 1
            if key not in groups:
                slug = re.sub(r"[^a-z0-9]+", "-", rule.name.lower()).strip("-") or "site"
                groups[key] = Site(f"{slug}-{digest(key)[:8]}", rule.name, template, rank)
            site = groups[key]
            site.rank = min(site.rank, rank)
            site.rules.append(rule)
        self.sites = {site.site_id: site for site in groups.values()}
        aliases = json.loads(files("kudo_agent.research_core").joinpath("data/site-aliases.json").read_text(encoding="utf-8"))
        for site in self.sites.values():
            site.aliases = sorted({alias for rule in site.rules for alias in aliases.get(rule.name, [])})
        self.version = digest([__version__, maigret.COMMIT, sherlock.COMMIT, database, sherlock_data])

    def select(self, names: list[str], maximum: int | None = None) -> list[Site]:
        if not names:
            return sorted(
                (s for s in self.sites.values() if s.supported), key=lambda s: s.sort_key
            )[:maximum]
        selected = {}
        for name in names:
            matches = [
                site for site in self.sites.values()
                if site.site_id == name or any(r.name.casefold() == name.casefold() for r in site.rules)
            ]
            if len(matches) != 1:
                raise ValueError(f"站点名称无法唯一识别：{name}；请使用 sites 列出的 site_id")
            selected[matches[0].site_id] = matches[0]
        if maximum is not None and len(selected) > maximum:
            raise ValueError("指定站点数量超过 max_sites")
        return list(selected.values())
