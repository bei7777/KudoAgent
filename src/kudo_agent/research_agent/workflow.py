import asyncio
import logging
from typing import NotRequired, TypedDict
from urllib.parse import urlsplit

from langgraph.graph import END, START, StateGraph

from kudo_agent.evidence.report import evidence_context
from kudo_agent.evidence.store import Store
from kudo_agent.research_agent.planner import Planner
from kudo_agent.research_agent.reporting import generate_report
from kudo_agent.research_core.catalog import Catalog
from kudo_agent.research_core.collectors import Collectors
from kudo_agent.research_core.email_sites import EMAIL_PROVIDERS
from kudo_agent.research_core.http import QueryConnectionError, QueryTargetBlocked
from kudo_agent.research_core.models import (
    Action,
    ActionPlan,
    BudgetStop,
    Identifier,
    PlannedAction,
    RequestBudgetExceeded,
    ResearchRequest,
    RunStatus,
)
from kudo_agent.research_core.scope import resolve_sites
from kudo_agent.upstream.maigret import make_profile_url_regexp

logger = logging.getLogger(__name__)


class State(TypedDict):
    run_id: str
    round: int
    selected: list[str]
    stop_reason: str
    budget_exhausted: NotRequired[bool]


class Workflow:
    def __init__(self, store: Store, catalog: Catalog, planner: Planner | None, collectors: Collectors):
        self.store, self.catalog, self._planner, self.collectors = store, catalog, planner, collectors

    @property
    def planner(self) -> Planner:
        if self._planner is None:
            raise RuntimeError("本机号码研究无法发起模型调用")
        return self._planner

    def request(self, run_id: str) -> ResearchRequest:
        return ResearchRequest.model_validate(self.store.run(run_id)["request"])

    def initial_priority(self, action: Action, explicit_sites: bool) -> int:
        if action.capability == "username":
            if explicit_sites:
                return 1
            return 1 if self.catalog.sites[action.site].region == "CN" else 2
        if action.capability == "email":
            return 1 if EMAIL_PROVIDERS[action.site].region == "CN" else 2
        return 0

    async def seed(self, state: State):
        run_id = state["run_id"]
        request = self.request(run_id)
        if not request.identifiers:
            identifiers = await self.planner.extract_input(request.goal)
            request = ResearchRequest.model_validate(request.model_dump() | {"identifiers": identifiers})
            self.store.update(run_id, request=request.model_dump_json())
        scope = resolve_sites(self.catalog, request)
        actions = []
        for identifier in request.identifiers:
            if identifier.kind == "username":
                actions += [Action(capability="username", value=identifier.value, site=s.site_id) for s in scope.usernames]
            elif identifier.kind == "email":
                actions += [Action(capability="email", value=identifier.value, site=s) for s in scope.emails]
            else:
                actions.append(Action(capability=identifier.kind, value=identifier.value))
        self.store.add_actions(run_id, sorted(
            actions, key=lambda action: self.initial_priority(action, bool(request.sites))
        ))
        return {"round": state.get("round", 0), "selected": [], "stop_reason": "", "budget_exhausted": False}

    async def plan(self, state: State):
        run_id, number = state["run_id"], state["round"]
        request = self.request(run_id)
        pending = self.store.actions(run_id, "pending")
        if not pending:
            reason = "已完成本机号码资料核查" if self._planner is None else "没有可执行的新线索"
            return {"selected": [], "stop_reason": reason}
        saved = self.store.decision(run_id, number)
        attempts = self.store.model_usage(run_id)["planning_attempts"]
        if self.store.run(run_id)["request_count"] >= request.budget.max_requests:
            return {"selected": [], "stop_reason": BudgetStop.REQUESTS.value, "budget_exhausted": True}
        # 全部初始查询按持久化顺序执行，关联扩展再由模型安排。
        initial = [row for row in pending if row["payload"]["depth"] == 0][:50]
        if not saved and initial:
            plan = ActionPlan(source="catalog", stop_reason="", actions=[PlannedAction(
                action_id=row["id"], question=f"核查 {row['payload']['value']} 的公开记录",
                reason="读取本机号码分配规则" if row["payload"]["capability"] == "phone" else
                       "执行初始范围内的查询，保存站点响应与检测结果",
                evidence_ids=[],
            ) for row in initial])
            self.store.save_decision(run_id, number, plan.model_dump())
            return {"selected": [action.action_id for action in plan.actions], "stop_reason": ""}
        if not saved and attempts >= request.budget.max_rounds:
            return {"selected": [], "stop_reason": BudgetStop.PLANNING.value, "budget_exhausted": True}
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
                if self._planner is None and action.capability != "phone":
                    raise ValueError("本机号码研究只能执行号码资料查询")
                try:
                    results = await self.collectors.collect(action)
                except RequestBudgetExceeded:
                    raise
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
            return {"round": state["round"] + 1, "selected": [], "stop_reason": BudgetStop.REQUESTS.value,
                    "budget_exhausted": True}
        self.discover(run_id, set(state["selected"]))
        return {"round": state["round"] + 1, "selected": []}

    def discover(self, run_id: str, action_ids: set[str] | None = None):
        request = self.request(run_id)
        if not request.budget.max_depth:
            return
        sites = resolve_sites(self.catalog, request).usernames
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
                            actions.append(Action(capability="profile", value=link, depth=parent.depth + 1,
                                                  parent_evidence=[item.evidence_id]))
                            if request.follow_profile_links:
                                break
                            identifier = Identifier(kind="username", value=match.group("_maigret_username"))
                            actions.extend(Action(capability="username", value=identifier.value, site=s.site_id,
                                                  depth=parent.depth + 1, parent_evidence=[item.evidence_id]) for s in sites)
        self.store.add_actions(run_id, actions)

    async def report(self, state: State):
        run_id = state["run_id"]
        reason = state["stop_reason"]
        exhausted = (state["budget_exhausted"] if "budget_exhausted" in state
                     else self.store.legacy_budget_stop(run_id, reason))
        self.store.begin_report(run_id, RunStatus.PARTIAL if exhausted else RunStatus.COMPLETED, reason)
        await generate_report(self.store, run_id, self._planner)
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
