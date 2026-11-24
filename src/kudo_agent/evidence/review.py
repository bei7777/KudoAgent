from collections import defaultdict
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from kudo_agent.evidence.report import local_phone_report
from kudo_agent.research_core.email_sites import EMAIL_PROVIDERS
from kudo_agent.research_core.models import Claim, Evidence

UNCERTAIN = {"unknown", "blocked", "rate_limited", "unsupported"}
KIND_LABELS = {"account": "用户名", "profile": "页面", "email": "邮箱", "phone": "号码", "ip": "IP"}
STATE_LABELS = {"unknown": "结果未能判定", "blocked": "访问受到限制", "rate_limited": "请求受到限流",
                "unsupported": "当前规则无法检查"}


def page_key(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), parts.query, ""))


def review_key(item: Evidence) -> tuple[str, ...]:
    data = item.attributes
    if item.kind == "account":
        if data.get("site") and (host := urlsplit(item.source_url).hostname):
            return (item.kind, item.subject, host.removeprefix("www."), data["site"].casefold())
        return (item.kind, item.subject, page_key(item.source_url))
    if item.kind == "profile":
        return (item.kind, page_key(item.subject))
    if item.kind == "email":
        return (item.kind, item.subject, data.get("provider") or item.source_url, data.get("interaction", ""))
    if item.kind == "confirmation":
        return (item.kind, item.evidence_id)
    return (item.kind, item.subject)


def email_observation(item: Evidence) -> str:
    data = item.attributes
    provider = EMAIL_PROVIDERS.get(data.get("provider"))
    name = provider.name if provider else urlsplit(item.source_url).netloc or "邮箱服务"
    prefix = f"{name} 对邮箱 {item.subject} 的检查"
    interaction = data.get("interaction")
    if item.state in UNCERTAIN:
        if data.get("field_available") is True:
            return f"{prefix}显示字段允许继续填写，账户注册状态未核实。"
        return f"{prefix}{STATE_LABELS[item.state]}，账户状态未核实。"
    if interaction == "public_avatar":
        observed = "返回了公开头像" if item.state == "detected" else "未返回公开头像"
        return f"{prefix}{observed}，账户注册状态未核实。"
    if interaction == "public_profile":
        count = data.get("returned_records")
        observed = f"返回 {count} 条公开资料记录" if type(count) is int else "已完成公开资料查询"
        return f"{prefix}{observed}，账户注册状态未核实。"
    if interaction == "registration_check":
        if data.get("field_available") is True:
            return f"{prefix}显示字段允许继续填写，账户注册状态未核实。"
        observed = "报告字段已被使用" if item.state == "detected" else "未报告字段已被使用"
        return f"{prefix}{observed}。这条记录只描述注册表单的字段检查。"
    # 适配器的摘要区分具体的登录选项、账户类型和可用性，保存其原始陈述。
    return f"{item.subject} 的接口观察记录为「{item.summary[:750]}」。"


def observed_text(item: Evidence) -> str:
    data = item.attributes
    if item.kind == "email":
        return email_observation(item)
    if item.kind == "confirmation":
        return f"用户确认：{item.summary}"
    if item.kind == "account":
        name = data.get("site") or urlsplit(item.source_url).netloc
        prefix = f"{name} 上用户名 {item.subject} 的页面"
        if item.state == "detected":
            return f"{prefix}符合站点规则，是同名候选页面，账户归属尚未核实。"
        if item.state == "not_detected":
            return f"{prefix}未匹配站点规则。"
        return f"{prefix}{STATE_LABELS.get(item.state, '未取得明确结果')}。"
    if item.kind == "profile" and item.state == "detected":
        title = str(data.get("title") or "")[:350]
        description = str(data.get("description") or "")[:550]
        text = f"已读取公开页面 {item.source_url[:720]}。"
        if title:
            text += f"页面标题为「{title}」。"
        if description:
            text += f"页面简介写道「{description}」。"
        return text
    if item.kind == "phone" and item.state == "metadata":
        claims = local_phone_report([item]).claims
        return " ".join(claim.text for claim in claims)
    if item.kind == "ip" and item.state == "metadata":
        place = "、".join(str(data[k]) for k in ("country", "region", "city") if data.get(k))
        text = f"{item.subject} 的 IP 服务返回了网络资料。"
        return text + (f"服务估算的网络地区为{place}。" if place else "")
    return f"{KIND_LABELS[item.kind]} {item.subject[:720]} 的观察记录为「{item.summary[:750]}」。"


@dataclass
class EvidenceReview:
    observations: dict[str, Claim]
    required: list[Claim]
    next_actions: list[str]

    def validate_selection(self, identifiers: list[str], offered: list[str]) -> None:
        if len(identifiers) != len(set(identifiers)) or not set(identifiers).issubset(offered):
            raise ValueError("报告选择包含重复或未提供的观测编号")


def review_evidence(items: list[Evidence]) -> EvidenceReview:
    groups: dict[tuple[str, ...], list[Evidence]] = defaultdict(list)
    unresolved: dict[tuple[str, str], list[str]] = defaultdict(list)
    for item in items:
        groups[review_key(item)].append(item)
        if item.state in UNCERTAIN and not item.attributes.get("conflict"):
            unresolved[(item.kind, item.subject)].append(item.evidence_id)

    observations = {}
    required = []
    next_actions = []
    for group in groups.values():
        positive, negative = [], []
        for item in group:
            states = {detection.state for detection in item.detections} | {item.state}
            if "detected" in states:
                positive.append(item.evidence_id)
            if "not_detected" in states:
                negative.append(item.evidence_id)
        if (positive and negative) or any(item.attributes.get("conflict") for item in group):
            first = group[0]
            location = first.attributes.get("site") or first.attributes.get("provider") or first.source_url
            all_ids = [item.evidence_id for item in group]
            different_pages = first.kind == "account" and len({page_key(item.source_url) for item in group}) > 1
            observation = ("的多个页面入口返回了不同的检测结果，账户状态尚未核实。" if different_pages else
                           "的同一项核查中出现相互冲突的结果，尚无法作出判定。")
            required.append(Claim(
                text=f"{first.subject[:720]} 在 {str(location)[:720]} {observation}",
                evidence_ids=positive or all_ids, conflicting_evidence_ids=negative or all_ids,
            ))
            if not next_actions:
                next_actions.append("核对冲突证据中的规则判断、取得时间和原始响应。")
            continue
        for item in group:
            claim = Claim(text=observed_text(item), evidence_ids=[item.evidence_id])
            if item.kind == "confirmation":
                required.append(claim)
            else:
                observations[item.evidence_id] = claim

    for (kind, subject), ids in unresolved.items():
        required.append(Claim(text=f"{KIND_LABELS[kind]} {subject[:720]} 有 {len(ids)} 条记录未能判定。"
                              "完整记录见待核查资料。", evidence_ids=ids[:3]))
    if unresolved:
        next_actions.append("在待核查资料中查看访问限制和无法判定的具体原因。")
    return EvidenceReview(observations, required, next_actions)
