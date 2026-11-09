import asyncio
import logging
from typing import TypedDict
from urllib.parse import urlsplit

from langgraph.graph import END, START, StateGraph

from kudo_agent.evidence.report import evidence_context, validate_draft
from kudo_agent.evidence.store import Store
from kudo_agent.research_agent.planner import Planner
from kudo_agent.research_core.catalog import Catalog
from kudo_agent.research_core.collectors import Collectors
from kudo_agent.research_core.http import QueryConnectionError, QueryTargetBlocked
from kudo_agent.research_core.models import (
    Action,
    ActionPlan,
    Identifier,
    PlannedAction,
    ReportDraft,
    RequestBudgetExceeded,
    ResearchRequest,
)
from kudo_agent.upstream.maigret import make_profile_url_regexp

logger = logging.getLogger(__name__)


class State(TypedDict):
    run_id: str
    round: int
    selected: list[str]
    stop_reason: str


class Workflow:
    def __init__(self, store: Store, catalog: Catalog, planner: Planner, collectors: Collectors):
        self.store, self.catalog, self.planner, self.collectors = store, catalog, planner, collectors

    def request(self, run_id: str) -> ResearchRequest:
        return ResearchRequest.model_validate(self.store.run(run_id)["request"])

    async def seed(self, state: State):
        run_id = state["run_id"]
        request = self.request(run_id)
        if not request.identifiers:
            request.identifiers = await self.planner.extract_input(request.goal)
            self.store.update(run_id, request=request.model_dump_json())
        sites = self.catalog.select(request.sites, request.budget.max_sites)
        actions = []
        for identifier in request.identifiers:
            if identifier.kind == "username":
                actions += [Action(capability="username", value=identifier.value, site=s.site_id) for s in sites]
            elif identifier.kind == "email":
                if not request.email_sites:
                    raise ValueError("邮箱查询必须明确指定 email_sites")
                actions += [Action(capability="email", value=identifier.value, site=s) for s in request.email_sites]
            else:
                actions.append(Action(capability=identifier.kind, value=identifier.value))
        self.store.add_actions(run_id, actions)
        return {"round": 0, "selected": [], "stop_reason": ""}

    async def plan(self, state: State):
        run_id, number = state["run_id"], state["round"]
        request = self.request(run_id)
        pending = self.store.actions(run_id, "pending")
        if not pending:
            return {"selected": [], "stop_reason": "没有可执行的新线索"}
        saved = self.store.decision(run_id, number)
        attempts = self.store.model_usage(run_id)["planning_attempts"]
        if self.store.run(run_id)["request_count"] >= request.budget.max_requests:
            return {"selected": [], "stop_reason": "网络请求数量已达到预算"}
        # 全部初始查询按持久化顺序执行，关联扩展再由模型安排。
        initial = [row for row in pending if row["payload"]["depth"] == 0][:50]
        if not saved and initial:
            plan = ActionPlan(source="catalog", stop_reason="", actions=[PlannedAction(
                action_id=row["id"], question=f"核查 {row['payload']['value']} 的公开记录",
                reason="执行默认查询范围中的初始查询，保留该站点的实际响应与检测结果",
                evidence_ids=[],
            ) for row in initial])
            self.store.save_decision(run_id, number, plan.model_dump())
            return {"selected": [action.action_id for action in plan.actions], "stop_reason": ""}
        if not saved and attempts >= request.budget.max_rounds:
            return {"selected": [], "stop_reason": "模型决策轮数已达到预算"}
        items = self.store.evidence(run_id)
        # 候选分组进入模型上下文，完整队列保存在任务数据库。
        offered = pending[:100]
        plan = ActionPlan.model_validate(saved) if saved else await self.planner.plan({
            "goal": request.goal, "round": number, "budget": request.budget.model_dump(),
            "requests_used": self.store.run(run_id)["request_count"],
            "candidate_count": len(pending),
            "candidates": [{"action_id": r["id"], **r["payload"]} for r in offered],
            "evidence": evidence_context(items)[-100:],
        })
        eligible = {r["id"] for r in (self.store.actions(run_id) if saved else offered)}
        known = {item.evidence_id for item in items}
        for chosen in plan.actions:
            if chosen.action_id not in eligible or set(chosen.evidence_ids) - known:
                raise ValueError("模型选择了范围之外的动作或证据")
        self.store.save_decision(run_id, number, plan.model_dump())
        return {"selected": [a.action_id for a in plan.actions], "stop_reason": plan.stop_reason}

    async def collect(self, state: State):
        run_id = state["run_id"]
        actions = {a["id"]: a for a in self.store.actions(run_id)}
        limit = asyncio.Semaphore(self.request(run_id).budget.concurrency)

        async def execute(action_id: str):
            record = actions[action_id]
            if record["status"] in {"completed", "unreachable", "blocked"}:
                return
            async with limit:
                self.store.action_status(run_id, action_id, "running")
                action = Action.model_validate(record["payload"])
                try:
                    results = await self.collectors.collect(action)
                except (QueryConnectionError, QueryTargetBlocked) as error:
                    status = "blocked" if isinstance(error, QueryTargetBlocked) else "unreachable"
                    self.store.action_status(run_id, action_id, status, str(error))
                    logger.warning("查询未取得结果 %s %s [%s]：%s", run_id, action.site or action.capability, status, error)
                    return
                except Exception as error:
                    error.add_note(f"查询站点：{action.site or action.capability}；输入：{action.value}")
                    self.store.action_status(run_id, action_id, "failed", f"{type(error).__name__}: {error}")
                    raise
                self.store.finish_action(run_id, action_id, results)

        exhausted = False
        try:
            async with asyncio.TaskGroup() as group:
                for action_id in state["selected"]:
                    group.create_task(execute(action_id))
        except* RequestBudgetExceeded:
            exhausted = True
        if exhausted:
            for record in self.store.actions(run_id, "running"):
                self.store.action_status(run_id, record["id"], "interrupted", "网络请求预算已耗尽")
            return {"round": state["round"] + 1, "selected": [], "stop_reason": "网络请求预算已耗尽"}
        self.discover(run_id, set(state["selected"]))
        return {"round": state["round"] + 1, "selected": []}

    def discover(self, run_id: str, action_ids: set[str] | None = None):
        request = self.request(run_id)
        sites = self.catalog.select(request.sites, request.budget.max_sites)
        records = {a["id"]: Action.model_validate(a["payload"]) for a in self.store.actions(run_id)}
        actions = []
        for item in self.store.evidence(run_id):
            if action_ids is not None and item.action_id not in action_ids:
                continue
            parent = records[item.action_id]
            if parent.depth >= request.budget.max_depth or item.state != "detected":
                continue
            if item.kind == "account":
                actions.append(Action(capability="profile", value=item.source_url, depth=parent.depth + 1,
                                      parent_evidence=[item.evidence_id]))
            elif item.kind == "profile":
                for link in item.attributes.get("links", []):
                    for site in sites:
                        if site.template.count("{username}") != 1:
                            continue
                        if urlsplit(link).netloc.lower() != urlsplit(site.template).netloc.lower():
                            continue
                        match = make_profile_url_regexp(site.template).fullmatch(link.rstrip("/"))
                        if match:
                            identifier = Identifier(kind="username", value=match.group("_maigret_username"))
                            actions.append(Action(capability="profile", value=link, depth=parent.depth + 1,
                                                  parent_evidence=[item.evidence_id]))
                            actions.extend(Action(capability="username", value=identifier.value, site=s.site_id,
                                                  depth=parent.depth + 1, parent_evidence=[item.evidence_id]) for s in sites)
        self.store.add_actions(run_id, actions)

    async def report(self, state: State):
        run_id = state["run_id"]
        self.store.verify(run_id)
        items = self.store.evidence(run_id)
        draft = ReportDraft(claims=[], next_actions=[])
        for offset in range(0, len(items), 50):
            part = await self.planner.report({
                "goal": self.request(run_id).goal, "stop_reason": state["stop_reason"],
                "evidence": evidence_context(items[offset:offset + 50]),
            })
            validate_draft(part, items[offset:offset + 50])
            draft.claims.extend(part.claims)
            draft.next_actions.extend(part.next_actions)
        draft.next_actions = list(dict.fromkeys(draft.next_actions))
        validate_draft(draft, items)
        self.store.update(run_id, report=draft.model_dump_json(), stop_reason=state["stop_reason"])
        return {}

    def compile(self, checkpointer):
        graph = StateGraph(State)
        graph.add_node("seed", self.seed)
        graph.add_node("plan", self.plan)
        graph.add_node("collect", self.collect)
        graph.add_node("report", self.report)
        graph.add_edge(START, "seed")
        graph.add_edge("seed", "plan")
        graph.add_conditional_edges("plan", lambda state: "report" if state["stop_reason"] else "collect")
        graph.add_conditional_edges("collect", lambda state: "report" if state["stop_reason"] else "plan")
        graph.add_edge("report", END)
        return graph.compile(checkpointer=checkpointer)
