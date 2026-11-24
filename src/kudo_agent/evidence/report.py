import json
from pathlib import Path

from kudo_agent.evidence.store import Store
from kudo_agent.research_core.models import Claim, Evidence, ReportDraft


def evidence_context(items: list[Evidence]) -> list[dict]:
    return [{
        "evidence_id": item.evidence_id, "kind": item.kind, "subject": item.subject,
        "state": item.state, "summary": item.summary, "source_url": item.source_url,
        "artifact_ids": item.artifact_ids, "detections": [d.model_dump() for d in item.detections],
        "attributes": {key: value for key, value in item.attributes.items()
                       if key not in {"text_excerpt", "links", "extracted"}},
        "excerpt": item.attributes.get("text_excerpt", "")[:1200],
        "relations": [r.model_dump() for r in item.relations[:30]],
    } for item in items]


def validate_draft(draft: ReportDraft, items: list[Evidence]):
    known = {item.evidence_id for item in items}
    for claim in draft.claims:
        if (set(claim.evidence_ids) | set(claim.conflicting_evidence_ids)) - known:
            raise ValueError("报告包含不存在的证据引用")


def local_phone_report(items: list[Evidence]) -> ReportDraft:
    claims = []
    for item in items:
        if item.kind == "confirmation":
            claims.append(Claim(text=f"用户确认：{item.summary}", evidence_ids=[item.evidence_id]))
            continue
        if item.kind != "phone":
            raise ValueError("本机号码报告只接受号码资料和用户确认")
        data = item.attributes
        fields = [
            ("国家或地区", data.get("country_name") or data.get("region")),
            ("号段地区", data.get("prefix_description")),
            ("号段原始运营商", data.get("prefix_carrier")),
            ("号码类型", data.get("number_type")),
        ]
        facts = [f"{label}为 {value}" for label, value in fields if value]
        if "valid_range" in data:
            facts.append("符合分配号段规则" if data["valid_range"] else "未通过分配号段校验")
        summary = "，".join(facts) if facts else item.summary
        claims.append(Claim(text=f"号码 {item.subject} 的本机记录显示，{summary}。", evidence_ids=[item.evidence_id]))
    phone_ids = [item.evidence_id for item in items if item.kind == "phone"]
    if phone_ids:
        claims.append(Claim(
            text="号段资料反映号码分配时的记录。号码当前的运营商、启用状态、持有人和实时位置均未核实。搜索建议尚未执行。",
            evidence_ids=phone_ids,
        ))
    return ReportDraft(claims=claims, next_actions=[], evidence_review_version=1)


def render(store: Store, run_id: str, draft: ReportDraft | None = None) -> str:
    store.verify(run_id)
    run, items = store.run(run_id), store.evidence(run_id)
    if draft is None and run["report"]:
        draft = ReportDraft.model_validate(run["report"])
    if draft:
        validate_draft(draft, items)
    model_usage = store.model_usage(run_id)
    coverage = store.coverage(run_id)
    progress = store.report_progress(run_id)
    lines = ["# 公开足迹核验报告", "", run["request"]["goal"], "",
             f"研究标识：`{run_id}`", f"状态：`{run['status']}`", f"网络请求：{run['request_count']}",
             f"模型调用：{model_usage['attempts']}；已返回的 token 用量：{model_usage['total_tokens']}",
             f"初始查询范围：{coverage['planned']} 项；已返回结果：{coverage['checked']} 项；"
             f"连接失败：{coverage['failed']} 项；查询受限：{coverage['blocked']} 项；待执行：{coverage['remaining']} 项",
             f"停止原因：{run['stop_reason'] or run['error'] or '研究仍在进行'}"]
    if progress["status"] == "failed":
        lines.append(f"报告生成失败：{progress['error']}")
    lines.extend(["", "## 研究结论", ""])
    if draft:
        if draft.evidence_review_version is None:
            lines.extend(["此报告采用旧版结论格式。可从任务详情重新生成报告并核对已有证据。", ""])
        for claim in draft.claims:
            citations = " ".join(f"[证据 {eid[:10]}](#e-{eid})" for eid in claim.evidence_ids)
            lines.append(f"- {claim.text} {citations}")
            if claim.conflicting_evidence_ids:
                lines.append("  冲突来源：" + " ".join(
                    f"[证据 {eid[:10]}](#e-{eid})" for eid in claim.conflicting_evidence_ids))
    else:
        lines.append("报告尚未生成，已采集资料见证据记录。")
    lines += ["", "## 证据记录", ""]
    artifacts = {artifact.artifact_id: artifact for artifact in store.artifacts(run_id)}
    for item in items:
        source = f"检测请求地址 `{item.source_url}`" if item.kind == "email" else f"来源：{item.source_url}"
        lines += [f'<a id="e-{item.evidence_id}"></a>', f"### {item.summary}", "",
                  f"标识符：`{item.subject}`；状态：`{item.state}`；获取时间：{item.observed_at}",
                  source, ""]
        for finding in item.detections:
            lines.append(f"- `{finding.rule_id}`：{finding.state}；{finding.reason}")
        if item.relations:
            lines += ["", "关联记录：", ""]
            for relation in item.relations:
                lines.append(f"- `{relation.kind}`：{relation.source} → {relation.target}")
        lines += ["", "已保存的检测响应" if item.kind == "email" else "原始资料：", ""]
        for identifier in item.artifact_ids:
            artifact = artifacts[identifier]
            lines.append(f"- [{identifier[:10]}]({artifact.body_path})：{artifact.method}；"
                         f"状态 {artifact.status_code}；SHA-256 `{artifact.sha256}`")
        lines.append("")
    lines += ["## 查询理由", ""]
    for decision in store.decisions(run_id):
        for action in decision["actions"]:
            citations = " ".join(f"[证据 {eid[:10]}](#e-{eid})" for eid in action["evidence_ids"])
            lines.append(f"- 第 {decision['round'] + 1} 轮，`{action['action_id'][:10]}`："
                         f"{action['question']}；{action['reason']} {citations}")
    lines += ["## 查询覆盖", ""]
    counts = {}
    for action in store.actions(run_id):
        counts[action["status"]] = counts.get(action["status"], 0) + 1
    for status, count in sorted(counts.items()):
        lines.append(f"- {status}：{count}")
    failures = store.failed_queries(run_id)
    if failures:
        lines += ["", "## 连接失败与查询限制", "", "以下查询没有取得完整响应，账户状态保持未知。", ""]
        for failure in failures:
            action = failure["payload"]
            lines.append(f"- `{action['site'] or action['capability']}`：{failure['error']}")
    if draft and draft.next_actions:
        lines += ["", "## 后续行动", "", *[f"- {item}" for item in draft.next_actions]]
    return "\n".join(lines) + "\n"


def export_report(store: Store, run_id: str) -> Path:
    destination = store.run_dir(run_id) / "report.md"
    temporary = destination.with_suffix(".partial")
    temporary.write_text(render(store, run_id), encoding="utf-8")
    temporary.replace(destination)
    return destination


def compare(store: Store, previous: str, current: str) -> dict:
    store.verify(previous)
    store.verify(current)
    before = {item.action_id: item for item in store.evidence(previous)}
    after = {item.action_id: item for item in store.evidence(current)}
    failures = {item["id"]: item["error"] for item in store.failed_queries(current)}
    categories = {key: [] for key in ("added", "disappeared", "changed", "unverified", "not_rechecked", "unchanged")}
    for action_id in sorted(before.keys() | after.keys()):
        old, new = before.get(action_id), after.get(action_id)
        if action_id in failures:
            category = "unverified"
        elif new is None:
            category = "not_rechecked"
        elif new.state not in {"detected", "not_detected", "metadata"}:
            category = "unverified"
        elif old is None:
            category = "added"
        elif old.state == "detected" and new.state == "not_detected":
            category = "disappeared"
        elif new.state == "detected" and old.state == "not_detected":
            category = "added"
        elif old.state != new.state or comparable_attributes(old) != comparable_attributes(new):
            category = "changed"
        else:
            category = "unchanged"
        categories[category].append({"action_id": action_id, "previous": old.public_record() if old else None,
                                     "current": new.public_record() if new else None,
                                     "failure": failures.get(action_id, "")})
    return {"previous": previous, "current": current, "changes": categories}


def comparable_attributes(item: Evidence) -> str:
    attributes = dict(item.attributes)
    if item.kind == "ip" and isinstance(attributes.get("timezone"), dict):
        # 服务端时钟随每次请求变化，原始值保留在资料中。
        attributes["timezone"] = {key: value for key, value in attributes["timezone"].items()
                                  if key not in {"current_time", "timestamp"}}
    return json.dumps(attributes, sort_keys=True)
