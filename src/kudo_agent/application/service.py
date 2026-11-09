import asyncio
import json
import logging
import time

from filelock import FileLock, Timeout
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from kudo_agent.application.config import Settings
from kudo_agent.evidence.report import compare, export_report, render
from kudo_agent.evidence.store import Store
from kudo_agent.research_agent.planner import Planner
from kudo_agent.research_agent.workflow import Workflow
from kudo_agent.research_core.catalog import Catalog
from kudo_agent.research_core.collectors import Collectors
from kudo_agent.research_core.email_sites import email_capabilities
from kudo_agent.research_core.http import HttpExecutor
from kudo_agent.research_core.models import (
    EMAIL_SITES,
    Action,
    Claim,
    Evidence,
    Relation,
    ReportDraft,
    ResearchRequest,
    RunStatus,
    canonical_url,
    digest,
)

logger = logging.getLogger(__name__)


class Application:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.catalog = Catalog()
        self.store = Store(settings.data_dir)
        self.tasks: dict[str, asyncio.Task] = {}
        self.owned: set[str] = set()
        self.shutting_down = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.shutting_down = True
        for task in self.tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.store.close()

    def list_capabilities(self, offset: int = 0, limit: int = 50, query: str = "", region: str = "") -> dict:
        if offset < 0 or not 1 <= limit <= 500:
            raise ValueError("offset 必须非负，limit 必须介于 1 和 500")
        if region not in {"", "CN"}:
            raise ValueError("region 只能为空或 CN")
        sites = sorted(self.catalog.sites.values(), key=lambda s: (s.rank, s.name))
        catalog_total = len(sites)
        domestic = [site for site in sites if site.region == "CN"]
        if region:
            sites = [site for site in sites if site.region == region]
        if query:
            sites = [site for site in sites if site.matches(query)]
        return {
            "inputs": ["username", "profile", "email", "phone", "ip"],
            "catalog_version": self.catalog.version, "total_sites": len(sites),
            "supported_sites": sum(site.supported for site in sites),
            "catalog_total": catalog_total, "cn_sites": len(domestic),
            "cn_supported_sites": sum(site.supported for site in domestic),
            "sites": [site.public() for site in sites[offset:offset + limit]],
            "email": {"sites": list(EMAIL_SITES), "providers": email_capabilities(),
                      "validation": "需要使用自有邮箱核对当前接口结果"},
            "phone": "本地号码资料和搜索建议", "ip": "ipwho.is HTTPS 资料查询",
            "model": {"protocol": "OpenAI compatible Chat Completions tools",
                      "required_settings": ["KUDO_BASE_URL", "KUDO_MODEL", self.settings.api_key_env]},
            "behavior": {"start": "立即返回 run_id，在当前服务进程内执行",
                         "resume": "复用已完成资料；未确认完成的账户请求需要明确允许重新执行",
                         "scope": "默认查询全部已适配用户名规则和全部邮箱查询站点；按输入类型执行"},
        }

    def start_research(self, request: ResearchRequest) -> dict:
        self.settings.require_model()
        self.catalog.select(request.sites, request.budget.max_sites)
        if not request.email_sites:
            request = request.model_copy(update={"email_sites": list(EMAIL_SITES)})
        run_id = self.store.create(request, self.catalog.version)
        self._schedule(run_id, resume=False)
        return {"run_id": run_id, "status": "queued"}

    def list_research(self, offset: int = 0, limit: int = 50, query: str = "") -> dict:
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("offset 必须非负，limit 必须介于 1 和 100")
        condition = "instr(lower(json_extract(request, '$.goal')), lower(?)) > 0"
        total = self.store.db.execute(f"SELECT count(*) FROM runs WHERE {condition}", (query,)).fetchone()[0]
        rows = self.store.db.execute(
            f"SELECT id FROM runs WHERE {condition} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (query, limit, offset),
        ).fetchall()
        items = [{**self.get_status(row["id"]), "request": self.store.run(row["id"])["request"]} for row in rows]
        return {"items": items, "total": total, "offset": offset, "limit": limit}

    def get_research(self, run_id: str) -> dict:
        request = ResearchRequest.model_validate(self.store.run(run_id)["request"])
        return {**self.get_status(run_id), "request": request.model_dump(),
                "scope": {"username_sites": len(self.catalog.select(request.sites, request.budget.max_sites)),
                          "email_sites": len(request.email_sites)},
                "timeline": self.store.decisions(run_id),
                "query_failures": [{"site": self.catalog.sites[item["payload"]["site"]].name
                                    if item["payload"]["site"] in self.catalog.sites else item["payload"]["capability"],
                                    "error": item["error"], "state": item["status"]}
                                   for item in self.store.failed_queries(run_id)[-20:]],
                "uncertain_accounts": len(self.store.uncertain_requests(run_id))}

    def get_artifact(self, run_id: str, artifact_id: str):
        self.store.run(run_id)
        artifact = next((item for item in self.store.artifacts(run_id) if item.artifact_id == artifact_id), None)
        if artifact is None:
            raise KeyError("原始资料不存在")
        return artifact, self.store.body(run_id, artifact)

    def _schedule(self, run_id: str, resume: bool):
        if run_id in self.tasks and not self.tasks[run_id].done():
            raise ValueError("任务已经运行")
        task = asyncio.create_task(self._run(run_id, resume), name=f"research-{run_id}")
        self.tasks[run_id] = task
        task.add_done_callback(lambda completed: self._settled(run_id, completed))

    def _settled(self, run_id: str, task: asyncio.Task):
        if run_id not in self.owned:
            if task.cancelled() and self.store.run(run_id)["cancel_requested"]:
                self.store.update(run_id, status=RunStatus.CANCELLED, stop_reason="用户取消")
            elif not task.cancelled():
                task.exception()
            return
        if task.cancelled():
            status = RunStatus.CANCELLED if self.store.run(run_id)["cancel_requested"] else RunStatus.INTERRUPTED
            self.store.update(run_id, status=status, stop_reason="用户取消" if status == RunStatus.CANCELLED else "执行被中断")
        elif task.exception() is not None:
            error = self._error_text(task.exception())
            self.store.update(run_id, status=RunStatus.FAILED, error=error, stop_reason="执行错误")
            for action in self.store.actions(run_id, "running"):
                self.store.action_status(run_id, action["id"], "interrupted", "研究终止，当前查询未完成")
            logger.error("研究 %s 失败：%s", run_id, error)
        self.owned.discard(run_id)

    def _error_text(self, error: BaseException) -> str:
        if isinstance(error, BaseExceptionGroup):
            message = "; ".join(self._error_text(e) for e in error.exceptions)
        else:
            message = f"{type(error).__name__}: {error}"
            if getattr(error, "__notes__", None):
                message += "；" + "；".join(error.__notes__)
        secret = self.settings.api_key.get_secret_value()
        return message.replace(secret, "[redacted]") if secret else message

    async def _run(self, run_id: str, resume: bool):
        with FileLock(self.store.run_dir(run_id) / "run.lock", timeout=0):
            self.owned.add(run_id)
            self.store.update(run_id, status=RunStatus.RUNNING, error="", stop_reason="", cancel_requested=0)
            request = ResearchRequest.model_validate(self.store.run(run_id)["request"])
            elapsed = self.store.run(run_id)["elapsed"]
            remaining = request.budget.run_timeout - elapsed
            if remaining <= 0:
                raise TimeoutError("研究运行期限已经耗尽")
            started = time.monotonic()
            planner = Planner(self.settings, self.store, run_id)
            http = HttpExecutor(self.store, run_id, request.budget)
            collectors = Collectors(self.catalog, self.store, run_id, http)
            workflow = Workflow(self.store, self.catalog, planner, collectors)
            owner = asyncio.current_task()

            async def watch_cancel():
                while True:
                    await asyncio.sleep(0.25)
                    self.store.update(run_id, elapsed=elapsed + time.monotonic() - started)
                    if self.store.run(run_id)["cancel_requested"]:
                        owner.cancel()
                        return

            try:
                async with asyncio.timeout(remaining):
                    async with asyncio.TaskGroup() as group:
                        watcher = group.create_task(watch_cancel())
                        try:
                            async with AsyncSqliteSaver.from_conn_string(str(self.store.root / "checkpoints.sqlite")) as saver:
                                graph = workflow.compile(saver)
                                scope_size = max(len(self.catalog.select(request.sites, request.budget.max_sites)),
                                                 len(request.email_sites), 1)
                                initial_batches = (scope_size * 20 + 49) // 50
                                config = {"configurable": {"thread_id": run_id},
                                          "recursion_limit": (initial_batches + request.budget.max_rounds) * 3 + 10}
                                snapshot = await graph.aget_state(config) if resume else None
                                if resume and snapshot and snapshot.values:
                                    if snapshot.next:
                                        await graph.ainvoke(None, config, durability="sync")
                                else:
                                    await graph.ainvoke({"run_id": run_id, "round": 0, "selected": [], "stop_reason": ""}, config,
                                                        durability="sync")
                        finally:
                            watcher.cancel()
                self.store.update(run_id, status=RunStatus.COMPLETED)
                export_report(self.store, run_id)
            except asyncio.CancelledError:
                cancelled = self.store.run(run_id)["cancel_requested"]
                self.store.update(run_id, status=RunStatus.CANCELLED if cancelled else RunStatus.INTERRUPTED,
                                  stop_reason="用户取消" if cancelled else "执行被中断")
                raise
            except Exception as error:
                self.store.update(run_id, status=RunStatus.FAILED, error=self._error_text(error), stop_reason="执行错误")
                raise
            finally:
                await http.close()
                await planner.close()
                self.store.update(run_id, elapsed=elapsed + time.monotonic() - started)

    async def wait(self, run_id: str):
        await self.tasks[run_id]
        return self.get_status(run_id)

    def _locked(self, run_id: str) -> bool:
        lock = FileLock(self.store.run_dir(run_id) / "run.lock", timeout=0)
        try:
            lock.acquire()
        except Timeout:
            return True
        lock.release()
        return False

    def get_status(self, run_id: str) -> dict:
        run = self.store.run(run_id)
        local = self.tasks.get(run_id)
        if run["status"] in {RunStatus.RUNNING, RunStatus.QUEUED} and not (local and not local.done()) and not self._locked(run_id):
            self.store.update(run_id, status=RunStatus.INTERRUPTED, stop_reason="执行进程已经退出")
            run = self.store.run(run_id)
        return {key: run[key] for key in ("id", "status", "created_at", "updated_at", "stop_reason", "error", "request_count", "elapsed")} | {
            "actions": self.store.action_counts(run_id),
            "coverage": self.store.coverage(run_id),
            "evidence_count": self.store.db.execute("SELECT count(*) FROM evidence WHERE run_id=?", (run_id,)).fetchone()[0],
            "model_usage": self.store.model_usage(run_id),
        }

    def get_evidence(
        self, run_id: str, offset: int = 0, limit: int = 50, *,
        view: str = "all", query: str = "", evidence_id: str = "",
    ) -> dict:
        self.store.run(run_id)
        if offset < 0 or not 1 <= limit <= 500:
            raise ValueError("offset 必须非负，limit 必须介于 1 和 500")
        if view not in {"all", "pages", "email", "metadata", "uncertain"}:
            raise ValueError("view 必须是 all、pages、email、metadata 或 uncertain")
        items = self.store.evidence(run_id)

        def groups(item: Evidence) -> set[str]:
            result = {"all"}
            if item.kind in {"account", "profile"} and item.state == "detected":
                result.add("pages")
            if item.kind == "email":
                result.add("email")
            if item.kind in {"phone", "ip"}:
                result.add("metadata")
            if item.state in {"unknown", "blocked", "rate_limited", "unsupported"} or item.attributes.get("conflict"):
                result.add("uncertain")
            return result

        grouped = [(item, groups(item)) for item in items]
        summary = {name: sum(name in categories for _, categories in grouped)
                   for name in ("all", "email", "metadata", "uncertain")}
        summary["pages"] = len({item.source_url for item, categories in grouped
                                if "pages" in categories and item.source_url})
        needle = query.strip().casefold()
        matching = []
        for item, categories in grouped:
            if evidence_id:
                if item.evidence_id != evidence_id:
                    continue
            else:
                if view not in categories:
                    continue
                searchable = [item.subject, item.source_url, item.summary,
                              *(str(item.attributes.get(key, "")) for key in ("site", "site_id", "provider"))]
                if needle and not any(needle in value.casefold() for value in searchable):
                    continue
            matching.append((item, categories))
        priority = ("pages", "email", "metadata", "uncertain", "all")
        matching.sort(key=lambda entry: next(index for index, name in enumerate(priority) if name in entry[1]))
        selected = [item for item, _ in matching[offset:offset + limit]]
        artifact_ids = {identifier for item in selected for identifier in item.artifact_ids}
        return {"total": len(items), "filtered_total": len(matching), "summary": summary,
                "items": [e.model_dump() for e in selected],
                "artifacts": [item.model_dump() for item in self.store.artifacts(run_id)
                              if item.artifact_id in artifact_ids]}

    def get_report(self, run_id: str, format: str = "markdown"):
        if format == "markdown":
            return render(self.store, run_id)
        if format != "json":
            raise ValueError("报告格式必须为 markdown 或 json")
        self.store.verify(run_id)
        run = self.store.run(run_id)
        return {"status": self.get_status(run_id), "goal": run["request"]["goal"],
                "report": run["report"], "evidence": [e.model_dump() for e in self.store.evidence(run_id)],
                "artifacts": [item.model_dump() for item in self.store.artifacts(run_id)],
                "decisions": self.store.decisions(run_id), "actions": self.store.actions(run_id)}

    def cancel_research(self, run_id: str) -> dict:
        status = self.get_status(run_id)
        if status["status"] not in {RunStatus.RUNNING, RunStatus.QUEUED}:
            return status
        self.store.update(run_id, cancel_requested=1)
        if run_id in self.tasks:
            self.tasks[run_id].cancel()
        return {"run_id": run_id, "cancel_requested": True}

    def resume_research(self, run_id: str, retry_interrupted_accounts: bool = False) -> dict:
        self.settings.require_model()
        status = self.get_status(run_id)
        if status["status"] in {RunStatus.RUNNING, RunStatus.QUEUED, RunStatus.COMPLETED}:
            raise ValueError("只能恢复失败、中断或取消的任务")
        with FileLock(self.store.run_dir(run_id) / "run.lock", timeout=0):
            run = self.store.run(run_id)
            if run["status"] in {RunStatus.RUNNING, RunStatus.QUEUED, RunStatus.COMPLETED}:
                raise ValueError("任务状态已经改变，请重新查询状态")
            if run["catalog_version"] != self.catalog.version:
                raise ValueError("规则版本已变化，请创建新的研究任务")
            uncertain = self.store.uncertain_requests(run_id)
            if uncertain and not retry_interrupted_accounts:
                raise ValueError(f"存在 {len(uncertain)} 项未确认完成的账户请求；重新执行需要 retry_interrupted_accounts=true")
            if uncertain:
                self.store.acknowledge_uncertain_requests(run_id)
            self.store.reset_incomplete(run_id)
            self.store.update(run_id, status=RunStatus.QUEUED, cancel_requested=0)
            self._schedule(run_id, resume=True)
        return {"run_id": run_id, "status": "queued"}

    def compare_research(self, previous: str, current: str):
        return compare(self.store, previous, current)

    async def record_confirmation(self, run_id: str, source: str, target: str, statement: str) -> dict:
        status = self.get_status(run_id)
        if status["status"] in {RunStatus.RUNNING, RunStatus.QUEUED}:
            raise ValueError("请在研究结束后记录确认声明")
        if not statement.strip() or len(statement) > 4000:
            raise ValueError("确认声明必须包含 1 到 4000 个字符")
        source, target = canonical_url(source), canonical_url(target)
        items = self.store.evidence(run_id)
        known = {item.source_url for item in items}
        known.update(relation.target for item in items for relation in item.relations)
        if source not in known or target not in known:
            raise ValueError("确认关系的两端必须已经出现在研究资料中")
        with FileLock(self.store.run_dir(run_id) / "run.lock", timeout=0):
            if self.store.run(run_id)["status"] in {RunStatus.RUNNING, RunStatus.QUEUED}:
                raise ValueError("请在研究结束后记录确认声明")
            action = Action(capability="confirmation", value=source, site=digest([target, statement]))
            if any(item.action_id == action.key for item in items):
                return {"run_id": run_id, "recorded": True, "action_id": action.key}
            self.store.add_actions(run_id, [action])
            request = ResearchRequest.model_validate(self.store.run(run_id)["request"])
            http = HttpExecutor(self.store, run_id, request.budget)
            collectors = Collectors(self.catalog, self.store, run_id, http)
            try:
                artifact = collectors.local_artifact(action, "user-confirmation://local", {
                    "source": source, "target": target, "statement": statement,
                })
                item = collectors.item(action, "confirmation", "user_confirmed", statement,
                                       source, [artifact], attributes={"statement_origin": "user"})
                item.relations.append(Relation(source=source, target=target, kind="user_confirmed",
                                               evidence_ids=[item.evidence_id]))
                self.store.finish_action(run_id, action.key, [item])
            finally:
                await http.close()
            existing = self.store.run(run_id)["report"]
            draft = ReportDraft.model_validate(existing) if existing else ReportDraft(claims=[], next_actions=[])
            draft.claims.append(Claim(text=f"用户确认：{statement}", evidence_ids=[item.evidence_id]))
            self.store.update(run_id, report=json.dumps(draft.model_dump(), ensure_ascii=False))
            export_report(self.store, run_id)
            return {"run_id": run_id, "recorded": True, "evidence_id": item.evidence_id}
