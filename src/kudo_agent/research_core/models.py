import hashlib
import ipaddress
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, get_args
from urllib.parse import urlsplit, urlunsplit

import phonenumbers
from email_validator import validate_email
from pydantic import BaseModel, ConfigDict, Field, model_validator


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


def digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def canonical_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("公开主页必须使用完整的 HTTP 或 HTTPS 地址")
    if parts.username or parts.password:
        raise ValueError("主页地址不能包含登录凭据")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RequestBudgetExceeded(RuntimeError):
    pass


class Identifier(Model):
    kind: Literal["username", "profile", "email", "phone", "ip"]
    value: str = Field(min_length=1, max_length=2048)

    @model_validator(mode="after")
    def normalize(self):
        self.value = self.value.strip()
        if self.kind == "profile":
            self.value = canonical_url(self.value)
        elif self.kind == "ip":
            address = ipaddress.ip_address(self.value)
            if not address.is_global:
                raise ValueError("IP 查询要求公共网络地址")
            self.value = str(address)
        elif self.kind == "email":
            self.value = validate_email(self.value, check_deliverability=False).normalized
        elif self.kind == "phone":
            if not self.value.startswith("+"):
                raise ValueError("电话号码必须包含 + 和国家代码")
            number = phonenumbers.parse(self.value, None)
            if not phonenumbers.is_possible_number(number):
                raise ValueError("电话号码长度或国家代码无效")
            self.value = phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
        elif not self.value or len(self.value) > 128 or any(
            c.isspace() or c in "/?#\\" for c in self.value
        ):
            raise ValueError("用户名包含无效字符或长度超过 128")
        return self


class Budget(Model):
    max_sites: int | None = Field(default=None, ge=1, le=100_000)
    concurrency: int = Field(default=10, ge=1, le=50)
    per_domain: int = Field(default=2, ge=1, le=10)
    request_timeout: float = Field(default=20, gt=0, le=300)
    run_timeout: float = Field(default=7200, gt=0, le=86400)
    max_rounds: int = Field(default=12, ge=1, le=100)
    max_requests: int = Field(default=50_000, ge=1, le=1_000_000)
    max_depth: int = Field(default=2, ge=0, le=5)
    max_response_bytes: int = Field(default=4_000_000, ge=1024, le=20_000_000)


EmailSite = Literal[
    "gitee", "tencent_cloud", "huawei_cloud", "jianguoyun", "zoho_cn", "cnblogs", "jd",
    "microsoft", "github", "twitter", "adobe", "spotify", "pinterest", "wordpress", "mozilla",
    "gravatar", "duolingo", "ebay", "soundcloud", "flickr", "replit", "imgur", "lastfm",
    "freelancer", "plurk", "komoot", "archive",
]
EMAIL_SITES = get_args(EmailSite)


class ResearchRequest(Model):
    goal: str = Field(min_length=1, max_length=8000)
    identifiers: list[Identifier] = Field(default_factory=list, max_length=20)
    sites: list[str] = Field(default_factory=list, max_length=100_000)
    email_sites: list[EmailSite] = Field(default_factory=lambda: list(EMAIL_SITES), max_length=len(EMAIL_SITES))
    budget: Budget = Field(default_factory=Budget)


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


class Action(Model):
    capability: Literal["username", "profile", "email", "phone", "ip", "confirmation"]
    value: str
    site: str = ""
    depth: int = Field(default=0, ge=0)
    parent_evidence: list[str] = Field(default_factory=list)

    @property
    def key(self) -> str:
        return digest({"capability": self.capability, "value": self.value, "site": self.site})


class PlannedAction(Model):
    action_id: str
    question: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    evidence_ids: list[str]


class ResearchDecision(Model):
    actions: list[PlannedAction] = Field(max_length=50)
    stop_reason: str

    @model_validator(mode="after")
    def check_stop(self):
        if bool(self.actions) == bool(self.stop_reason):
            raise ValueError("决策必须选择动作，或者填写停止原因")
        if len({a.action_id for a in self.actions}) != len(self.actions):
            raise ValueError("决策包含重复动作")
        return self


class ActionPlan(ResearchDecision):
    source: Literal["model", "catalog"] = "model"


class Detection(Model):
    rule_id: str
    state: Literal["detected", "not_detected", "unknown", "rate_limited", "blocked", "unsupported"]
    reason: str
    signals: list[str] = Field(default_factory=list)


class Relation(Model):
    source: str
    target: str
    kind: Literal["same_username", "links_to", "mentions_identifier", "user_confirmed"]
    evidence_ids: list[str]


class Evidence(Model):
    evidence_id: str
    action_id: str
    kind: Literal["account", "profile", "email", "phone", "ip", "confirmation"]
    subject: str
    source_url: str
    observed_at: str
    state: str
    summary: str
    artifact_ids: list[str]
    rule_versions: list[str] = Field(default_factory=list)
    detections: list[Detection] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    relations: list[Relation] = Field(default_factory=list)


class Claim(Model):
    text: str = Field(min_length=1, max_length=4000)
    evidence_ids: list[str] = Field(min_length=1)
    conflicting_evidence_ids: list[str] = Field(default_factory=list)


class ReportDraft(Model):
    claims: list[Claim]
    next_actions: list[str]


class RequestSpec(Model):
    method: Literal["GET", "HEAD", "POST"] = "GET"
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    form: dict[str, Any] | None = None
    multipart: dict[str, str] | None = None
    json_body: dict[str, Any] | None = None
    follow_redirects: bool = True
    identity_scope: str = "public"
    account_request: bool = False

    @model_validator(mode="after")
    def one_body_encoding(self):
        if sum(value is not None for value in (self.form, self.multipart, self.json_body)) > 1:
            raise ValueError("请求只能指定一种请求体编码")
        return self


class Artifact(Model):
    artifact_id: str
    request_key: str
    method: str
    url: str
    final_url: str
    status_code: int
    headers: dict[str, str]
    observed_at: str
    sha256: str
    body_path: str
    encoding: str
    redirects: list[dict[str, Any]] = Field(default_factory=list)
