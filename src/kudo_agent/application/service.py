import asyncio
import json
import logging
import time
from collections import Counter
from contextlib import nullcontext
from contextvars import Context

from filelock import FileLock, Timeout
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langsmith import tracing_context

from kudo_agent.application.config import Settings
from kudo_agent.evidence.browse import VIEWS, evidence_page
from kudo_agent.evidence.report import compare, export_report, render
from kudo_agent.evidence.store import Store
from kudo_agent.research_agent.planner import Planner
from kudo_agent.research_agent.reporting import generate_report
from kudo_agent.research_agent.workflow import Workflow
from kudo_agent.research_core.catalog import Catalog
from kudo_agent.research_core.collectors import Collectors
from kudo_agent.research_core.domestic import DOMESTIC_PLATFORMS
from kudo_agent.research_core.email_sites import EMAIL_PROVIDERS, email_capabilities
from kudo_agent.research_core.goal_scope import bind_goal_site
from kudo_agent.research_core.http import HttpExecutor
from kudo_agent.research_core.models import (
    EMAIL_SITES,
    Action,
    Artifact,
    Budget,
    BudgetIncrease,
    BudgetStop,
    Claim,
    Relation,
    ReportDraft,
    ResearchRequest,
    RunStatus,
    canonical_url,
    digest,
)
from kudo_agent.research_core.scope import resolve_sites
from kudo_agent.research_core.site_categories import (
    SITE_CATEGORIES,
    SiteCategory,
    checked_categories,
)

logger = logging.getLogger(__name__)


class Application:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.catalog = Catalog()
        self.category_version = digest([self.catalog.version, {s.site_id: s.category for s in self.catalog.sites.values()},
                                        {site: provider.category for site, provider in EMAIL_PROVIDERS.items()}])
        username_counts = Counter(site.category for site in self.catalog.sites.values() if site.supported)
        email_counts = Counter(provider.category for provider in EMAIL_PROVIDERS.values())
        self.site_categories = [
            {"id": category, "label": label, "username_count": username_counts[category],
             "email_count": email_counts[category]} for category, label in SITE_CATEGORIES.items()
        ]
        self.store = Store(settings.data_dir)
        self.tasks: dict[str, asyncio.Task] = {}
        self.report_tasks: dict[str, asyncio.Task] = {}
        self.owned: set[str] = set()
        self.shutting_down = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.shutting_down = True
        tasks = [*self.tasks.values(), *self.report_tasks.values()]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.store.close()

    def list_capabilities(self, offset: int = 0, limit: int = 50, query: str = "", region: str = "",
                          supported_only: bool = False, categories: list[SiteCategory] | None = None) -> dict:
        if offset < 0 or not 1 <= limit <= 500:
            raise ValueError("offset 必须非负，limit 必须介于 1 和 500")
        if region not in {"", "CN"}:
            raise ValueError("region 只能为空或 CN")
        sites = sorted(self.catalog.sites.values(), key=lambda s: s.sort_key)
        catalog_total = len(sites)
        domestic = [site for site in sites if site.region == "CN"]
        providers = email_capabilities()
        if categories:
            selected = checked_categories(categories)
            sites = [site for site in sites if site.category in selected]
            providers = [provider for provider in providers if provider["category"] in selected]
        if region:
            sites = [site for site in sites if site.region == region]
        if query:
            sites = [site for site in sites if site.matches(query)]
        if supported_only:
            sites = [site for site in sites if site.supported]
        return {
            "inputs": ["username", "profile", "email", "phone", "ip"],
            "catalog_version": self.catalog.version, "total_sites": len(sites),
            "supported_sites": sum(site.supported for site in sites),
            "catalog_total": catalog_total, "cn_sites": len(domestic),
            "cn_supported_sites": sum(site.supported for site in domestic),
            "domestic_platforms": DOMESTIC_PLATFORMS,
            "site_categories": self.site_categories,
            "sites": [site.public() for site in sites[offset:offset + limit]],
            "email": {"sites": [provider["site"] for provider in providers], "providers": providers,
                      "validation": "需要使用自有邮箱核对当前接口结果"},
            "phone": "单独提交号码时，在本机生成号段资料、搜索建议和报告", "ip": "ipwho.is HTTPS 资料查询",
            "model": {"protocol": "OpenAI compatible Chat Completions tools",
                      "required_settings": ["KUDO_BASE_URL", "KUDO_MODEL", self.settings.api_key_env]},
            "behavior": {"start": "立即返回 run_id，在当前服务进程内执行",
                         "resume": "复用已完成资料；未确认完成的账户请求需要明确允许重新执行",
                         "scope": "国内站点优先，默认查询全部已适配用户名规则和全部邮箱查询站点；按输入类型执行"},
        }

    def start_research(self, request: ResearchRequest) -> dict:
        request = bind_goal_site(self.catalog, request)
        scope = resolve_sites(self.catalog, request).counts()
        if request.follow_profile_links and not scope["username_sites"]:
            raise ValueError("核查页面外链前，请选择站点类别或指定允许的站点")
        if request.requires_model:
            self.settings.require_model()
        if request.site_categories is None and not request.email_sites:
            request = request.model_copy(update={"email_sites": list(EMAIL_SITES)})
        run_id = self.store.create(request, self._catalog_version(request.site_categories), scope=scope)
        self._schedule(run_id, resume=False, requires_model=request.requires_model)
        return {"run_id": run_id, "status": "queued"}

    def _catalog_version(self, categories: list[SiteCategory] | None) -> str:
        return self.category_version if categories else self.catalog.version

    def list_research(self, offset: int = 0, limit: int = 50, query: str = "", *, brief: bool = False) -> dict:
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("offset 必须非负，limit 必须介于 1 和 100")
        condition = "instr(lower(json_extract(request, '$.goal')), lower(?)) > 0"
        total = self.store.db.execute(f"SELECT count(*) FROM runs WHERE {condition}", (query,)).fetchone()[0]
        if brief:
            summaries = self.store.db.execute(
                "SELECT id,status,stop_reason,error,created_at,json_extract(request,'$.goal') AS goal,"
                "json_extract(request,'$.identifiers') AS identifiers,"
                "(SELECT count(*) FROM evidence WHERE run_id=runs.id) AS evidence_count "
                f"FROM runs WHERE {condition} ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?", (query, limit, offset),
            )
            items = []
            for row in summaries.fetchall():
                item = dict(row)
                self.store.normalize_legacy_status(item)
                if item["status"] in {RunStatus.QUEUED, RunStatus.RUNNING}:
                    item["status"] = self.get_status(item["id"], include_usage=False)["status"]
                item["request"] = {"goal": item.pop("goal"), "identifiers": json.loads(item.pop("identifiers") or "[]")}
                del item["stop_reason"], item["error"]
                items.append(item)
            return {"items": items, "total": total, "offset": offset, "limit": limit}
        rows = self.store.db.execute(
            f"SELECT id,request FROM runs WHERE {condition} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (query, limit, offset),
        ).fetchall()
        items = [self.get_status(row["id"]) | {"request": json.loads(row["request"])} for row in rows]
        return {"items": items, "total": total, "offset": offset, "limit": limit}

    def get_research(self, run_id: str, *, include_timeline: bool = True) -> dict:
        request = ResearchRequest.model_validate(self.store.run(run_id, include_report=False)["request"])
        counts = self.store.db.execute("SELECT username_sites, email_sites FROM scope_counts WHERE run_id=?", (run_id,)).fetchone()
        result = {**self.get_status(run_id, include_usage=include_timeline), "request": request.model_dump(),
                "scope": dict(counts) if counts is not None else resolve_sites(self.catalog, request).counts(),
                "uncertain_accounts": len(self.store.uncertain_requests(run_id))}
        if include_timeline:
            result.update(timeline=self.store.decisions(run_id), query_failures=self._recent_failures(run_id))
        return result

    def _recent_failures(self, run_id: str) -> list[dict]:
        return [{"site": self._query_name(item["payload"]), "error": item["error"], "state": item["status"]}
                for item in self.store.failed_queries(run_id, limit=20)]

    def execution_summary(self, run_id: str) -> dict:
        self.store.run(run_id, include_report=False)
        batches, queries = self.store.db.execute(
            "SELECT count(*), coalesce(sum(json_array_length(payload,'$.actions')),0) FROM decisions "
            "WHERE run_id=? AND json_extract(payload,'$.source')='catalog'", (run_id,),
        ).fetchone()
        decisions = self.store.db.execute(
            "SELECT round, payload FROM decisions WHERE run_id=? "
            "AND coalesce(json_extract(payload,'$.source'),'model')!='catalog' ORDER BY round", (run_id,),
        )
        return {"catalog_batches": batches, "catalog_queries": queries,
                "timeline": [{"round": row[0], **json.loads(row[1])} for row in decisions],
                "query_failures": self._recent_failures(run_id)}

    def _query_name(self, payload: dict) -> str:
        if payload["capability"] == "email":
            return EMAIL_PROVIDERS[payload["site"]].name
        if payload["site"] in self.catalog.sites:
            return self.catalog.sites[payload["site"]].name
        return payload["capability"]

    def get_artifact(self, run_id: str, artifact_id: str):
        self.store.run(run_id, include_report=False)
        row = self.store.db.execute("SELECT payload FROM artifacts WHERE run_id=? AND id=?",
                                    (run_id, artifact_id)).fetchone()
        if row is None:
            raise KeyError("原始资料不存在")
        artifact = Artifact.model_validate_json(row[0])
        return artifact, self.store.body(run_id, artifact)

    def _schedule(self, run_id: str, resume: bool, requires_model: bool, *, held_lock: FileLock | None = None):
        if run_id in self.tasks and not self.tasks[run_id].done():
            raise ValueError("任务已经运行")
        lock = held_lock or FileLock(self.store.run_dir(run_id) / "run.lock", timeout=0)
        lock.acquire()
        try:
            task = asyncio.create_task(self._run(run_id, resume, requires_model), name=f"research-{run_id}",
                                       context=None if requires_model else Context())
        except BaseException:
            lock.release()
            raise
        self.tasks[run_id] = task

        def finish(completed):
            try:
                self._settled(run_id, completed)
            finally:
                lock.release()
        task.add_done_callback(finish)

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

    async def _run(self, run_id: str, resume: bool, requires_model: bool):
        tracing = nullcontext() if requires_model else tracing_context(enabled=False, parent=False)
        with tracing:
            self.owned.add(run_id)
            self.store.update(run_id, status=RunStatus.RUNNING, error="", stop_reason="", cancel_requested=0)
            request = ResearchRequest.model_validate(self.store.run(run_id)["request"])
            elapsed = self.store.run(run_id)["elapsed"]
            remaining = request.budget.run_timeout - elapsed
            if remaining <= 0:
                self._finish_time_budget(run_id)
                return
            started = time.monotonic()
            planner = Planner(self.settings, self.store, run_id) if requires_model else None
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

            deadline = asyncio.timeout(remaining)
            try:
                async with deadline:
                    async with asyncio.TaskGroup() as group:
                        watcher = group.create_task(watch_cancel())
                        try:
                            async with AsyncSqliteSaver.from_conn_string(str(self.store.root / "checkpoints.sqlite")) as saver:
                                graph = workflow.compile(saver)
                                scope_size = max(1, *resolve_sites(self.catalog, request).counts().values())
                                initial_batches = (scope_size * 20 + 49) // 50
                                extension = self.store.db.execute(
                                    "SELECT revision, first_round FROM budget_extensions WHERE run_id=? ORDER BY revision DESC LIMIT 1",
                                    (run_id,),
                                ).fetchone()
                                thread = f"{run_id}-budget-{extension['revision']}" if extension else run_id
                                config = {"configurable": {"thread_id": thread},
                                          "recursion_limit": (initial_batches + request.budget.max_rounds) * 3 + 10}
                                snapshot = await graph.aget_state(config) if resume else None
                                if resume and snapshot and snapshot.values:
                                    result = snapshot.values
                                    if snapshot.next:
                                        result = await graph.ainvoke(None, config, durability="sync")
                                else:
                                    next_round = extension["first_round"] if extension else 0
                                    result = await graph.ainvoke({"run_id": run_id, "round": next_round, "selected": [], "stop_reason": ""}, config,
                                                                 durability="sync")
                        finally:
                            watcher.cancel()
                reason = result["stop_reason"]
                exhausted = (result["budget_exhausted"] if "budget_exhausted" in result
                             else self.store.legacy_budget_stop(run_id, reason))
                self.store.update(run_id, status=RunStatus.PARTIAL if exhausted else RunStatus.COMPLETED,
                                  stop_reason=reason)
                export_report(self.store, run_id)
            except TimeoutError:
                if self._report_interrupted(run_id, "报告生成超过任务期限" if deadline.expired() else "报告生成超时", export=True):
                    return
                if not deadline.expired():
                    raise
                self._finish_time_budget(run_id)
            except asyncio.CancelledError:
                if self._report_interrupted(run_id, "报告生成已中断", export=True):
                    return
                cancelled = self.store.run(run_id)["cancel_requested"]
                self.store.update(run_id, status=RunStatus.CANCELLED if cancelled else RunStatus.INTERRUPTED,
                                  stop_reason="用户取消" if cancelled else "执行被中断")
                raise
            except Exception as error:
                if self._report_interrupted(run_id, self._error_text(error), export=True, failed=True):
                    return
                self.store.update(run_id, status=RunStatus.FAILED, error=self._error_text(error), stop_reason="执行错误")
                raise
            finally:
                await http.close()
                if planner is not None:
                    await planner.close()
                self.store.update(run_id, elapsed=elapsed + time.monotonic() - started)

    def _finish_time_budget(self, run_id: str):
        for action in self.store.actions(run_id, "running"):
            self.store.action_status(run_id, action["id"], "interrupted", BudgetStop.TIME.value)
        self.store.update(run_id, status=RunStatus.PARTIAL, stop_reason=BudgetStop.TIME.value)
        export_report(self.store, run_id)

    def _report_interrupted(self, run_id: str, error: str, *, export: bool = False, failed: bool = False) -> bool:
        progress = self.store.report_progress(run_id)
        if progress["status"] not in {"ready", "generating"} or not progress["collection_status"]:
            return False
        if progress["status"] == "generating" or failed:
            self.store.report_failed(run_id, error, restore_collection=True)
        else:
            self.store.update(run_id, status=progress["collection_status"])
        if export and progress["status"] == "generating":
            try:
                export_report(self.store, run_id)
            except (OSError, ValueError) as export_error:
                self.store.report_failed(run_id, f"{error}。报告文件保存失败：{self._error_text(export_error)}")
        return True

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

    def get_status(self, run_id: str, *, include_usage: bool = True) -> dict:
        run = self.store.run(run_id, include_report=False)
        report = self.store.report_progress(run_id)
        active = run["status"] in {RunStatus.RUNNING, RunStatus.QUEUED}
        local = (self.tasks.get(run_id), self.report_tasks.get(run_id))
        if ((active or report["status"] == "generating") and not any(task and not task.done() for task in local)
                and not self._locked(run_id)):
            if not self._report_interrupted(run_id, "生成报告的进程已经退出"):
                self.store.update(run_id, status=RunStatus.INTERRUPTED, stop_reason="执行进程已经退出")
            run = self.store.run(run_id, include_report=False)
            report = self.store.report_progress(run_id)
            active = False
        generating = report["status"] == "generating"
        count = self.store.db.execute("SELECT count(*) FROM evidence WHERE run_id=?", (run_id,)).fetchone()[0]
        return {key: run[key] for key in ("id", "status", "created_at", "updated_at", "stop_reason", "error", "request_count", "elapsed")} | {
            "phase": "report" if generating else "collection" if active else "finished",
            "report_status": report["status"], "report_error": report["error"],
            "can_retry_report": not active and report["status"] in {"pending", "failed", "outdated"} and count > 0,
            "can_extend_budget": not generating and self._can_extend(run),
            "can_resume": not generating and self._can_resume(run),
            "actions": self.store.action_counts(run_id),
            "coverage": self.store.coverage(run_id),
            "latest_round": self.store.db.execute("SELECT max(round) FROM decisions WHERE run_id=?", (run_id,)).fetchone()[0],
            "evidence_count": count,
            **({"model_usage": self.store.model_usage(run_id)} if include_usage else {}),
        }

    def _can_resume(self, run: dict) -> bool:
        return (run["status"] in {RunStatus.FAILED, RunStatus.INTERRUPTED, RunStatus.CANCELLED}
                and run["catalog_version"] == self._catalog_version(run["request"].get("site_categories"))
                and run["elapsed"] < ResearchRequest.model_validate(run["request"]).budget.run_timeout)

    def _extracted_identifiers(self, run_id: str) -> bool:
        return self.store.db.execute("SELECT 1 FROM model_calls WHERE run_id=? AND purpose='input' LIMIT 1",
                                     (run_id,)).fetchone() is not None

    def _can_extend(self, run: dict) -> bool:
        if (run["status"] != RunStatus.PARTIAL or run["catalog_version"] != self._catalog_version(run["request"].get("site_categories"))
                or run["stop_reason"] not in {BudgetStop.TIME, BudgetStop.REQUESTS, "网络请求预算已耗尽"}):
            return False
        request = ResearchRequest.model_validate(run["request"])
        kinds = {identifier.kind for identifier in request.identifiers}
        if not kinds or kinds - {"phone", "profile"} or ("profile" in kinds and request.budget.max_depth):
            return False
        if (run["elapsed"] >= 86400 or (run["stop_reason"] == BudgetStop.TIME and request.budget.run_timeout >= 86400)
                or (run["request_count"] >= request.budget.max_requests and request.budget.max_requests >= 1_000_000)):
            return False
        inputs = {(identifier.kind, identifier.value) for identifier in request.identifiers}
        return (not self._extracted_identifiers(run["id"]) and all(
            row["payload"]["depth"] == 0 and (row["payload"]["capability"], row["payload"]["value"]) in inputs
            for row in self.store.actions(run["id"])
        ))

    def retry_report(self, run_id: str) -> dict:
        self.get_status(run_id, include_usage=False)
        lock = FileLock(self.store.run_dir(run_id) / "run.lock", timeout=0)
        try:
            lock.acquire()
        except Timeout as error:
            raise ValueError("任务已有操作正在执行") from error
        try:
            run = self.store.run(run_id, include_report=False)
            progress = self.store.report_progress(run_id)
            if (run["status"] in {RunStatus.RUNNING, RunStatus.QUEUED} or progress["status"] not in {"pending", "failed", "outdated"}
                    or not self.store.db.execute("SELECT 1 FROM evidence WHERE run_id=? LIMIT 1", (run_id,)).fetchone()):
                raise ValueError("当前任务没有可以重新生成的证据报告")
            requires_model = ResearchRequest.model_validate(run["request"]).requires_model or self._extracted_identifiers(run_id)
            if requires_model:
                self.settings.require_model()
            self.store.begin_report(run_id, run["status"], run["stop_reason"])
            task = asyncio.create_task(self._retry_saved_report(run_id, requires_model),
                                       context=None if requires_model else Context())
            self.report_tasks[run_id] = task
        except BaseException:
            lock.release()
            raise

        def finish(completed):
            try:
                if completed.cancelled():
                    self.store.report_failed(run_id, "报告生成已中断")
                elif completed.exception() is not None:
                    self.store.report_failed(run_id, self._error_text(completed.exception()))
            finally:
                lock.release()
        task.add_done_callback(finish)
        return {"run_id": run_id, "report_status": "generating"}

    async def _retry_saved_report(self, run_id: str, requires_model: bool):
        tracing = nullcontext() if requires_model else tracing_context(enabled=False, parent=False)
        with tracing:
            planner = Planner(self.settings, self.store, run_id) if requires_model else None
            try:
                await generate_report(self.store, run_id, planner)
                export_report(self.store, run_id)
            finally:
                if planner is not None:
                    await planner.close()

    def extend_budget(self, run_id: str, increase: BudgetIncrease) -> dict:
        self.get_status(run_id, include_usage=False)
        lock = FileLock(self.store.run_dir(run_id) / "run.lock", timeout=0)
        try:
            lock.acquire()
        except Timeout as error:
            raise ValueError("任务已有操作正在执行") from error
        try:
            run = self.store.run(run_id, include_report=False)
            if not self._can_extend(run) or self.store.report_progress(run_id)["status"] == "generating":
                raise ValueError("仅支持为预算耗尽的本机号码或关闭关联扩展的指定主页任务追加预算")
            request = ResearchRequest.model_validate(run["request"])
            if run["stop_reason"] == BudgetStop.TIME and not increase.additional_seconds:
                raise ValueError("请追加已耗尽的运行时间")
            if run["request_count"] >= request.budget.max_requests and not increase.additional_requests:
                raise ValueError("请追加已耗尽的请求次数")
            budget = request.budget.model_dump()
            budget["run_timeout"] += increase.additional_seconds
            budget["max_requests"] += increase.additional_requests
            if budget["run_timeout"] <= run["elapsed"]:
                raise ValueError("新的时间预算必须大于已经使用的运行时间")
            request.budget = Budget.model_validate(budget)
            if request.requires_model:
                self.settings.require_model()
            self.store.reset_incomplete(run_id, new_request=request)
            self._schedule(run_id, resume=False, requires_model=request.requires_model, held_lock=lock)
        finally:
            lock.release()
        return {"run_id": run_id, "status": "queued"}

    def get_evidence(
        self, run_id: str, offset: int = 0, limit: int = 50, *,
        view: str = "all", query: str = "", evidence_id: str = "",
    ) -> dict:
        self.store.run(run_id, include_report=False)
        if offset < 0 or not 1 <= limit <= 500:
            raise ValueError("offset 必须非负，limit 必须介于 1 和 500")
        if view not in VIEWS:
            raise ValueError("view 必须是 all、pages、email、metadata 或 uncertain")
        return evidence_page(self.store.db, run_id, offset, limit, view, query, evidence_id) | {
            "email_progress": self._email_progress(run_id),
        }

    def _email_progress(self, run_id: str) -> dict:
        scope = "FROM actions WHERE run_id=? AND json_extract(payload,'$.capability')='email'"
        counts = dict(self.store.db.execute(f"SELECT status, count(*) {scope} GROUP BY status", (run_id,)))
        site_count = self.store.db.execute(f"SELECT count(DISTINCT json_extract(payload,'$.site')) {scope}",
                                           (run_id,)).fetchone()[0]
        failures = self.store.db.execute(f"SELECT json_extract(payload,'$.site') AS site, status, error {scope} "
                                         "AND status IN ('unreachable','blocked','failed') ORDER BY rowid", (run_id,))
        terminal = {"completed", "unreachable", "blocked", "failed"}
        return {
            "planned": sum(counts.values()), "returned": counts.get("completed", 0),
            "connection_failed": counts.get("unreachable", 0), "blocked": counts.get("blocked", 0),
            "failed": counts.get("failed", 0),
            "pending": sum(count for status, count in counts.items() if status not in terminal),
            "site_count": site_count,
            "failures": [{"site": item["site"], "name": EMAIL_PROVIDERS[item["site"]].name,
                          "state": item["status"], "error": item["error"]}
                         for item in failures],
        }

    def report_summary(self, run_id: str) -> dict:
        run = self.store.run(run_id)
        return {"report": run["report"], "status": self.get_status(run_id, include_usage=False)}

    def confirmation_sources(self, run_id: str) -> list[str]:
        self.store.run(run_id, include_report=False)
        return [url for (url,) in self.store.db.execute(
            "SELECT json_extract(payload,'$.source_url') AS url FROM evidence WHERE run_id=? AND json_extract(payload,'$.kind')!='email' "
            "UNION SELECT json_extract(rel.value,'$.target') FROM evidence, json_each(payload,'$.relations') AS rel "
            "WHERE run_id=? AND json_extract(payload,'$.kind')!='email' ORDER BY 1", (run_id, run_id),
        ) if url and url.startswith(("https://", "http://"))]

    def get_report(self, run_id: str, format: str = "markdown"):
        if format == "markdown":
            return render(self.store, run_id)
        if format != "json":
            raise ValueError("报告格式必须为 markdown 或 json")
        self.store.verify(run_id)
        run = self.store.run(run_id)
        return {"status": self.get_status(run_id), "goal": run["request"]["goal"],
                "report": run["report"], "evidence": [e.public_record() for e in self.store.evidence(run_id)],
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
        status = self.get_status(run_id)
        if status["status"] == RunStatus.PARTIAL:
            raise ValueError(f"{status['stop_reason']}，当前任务无法继续")
        if status["status"] not in {RunStatus.FAILED, RunStatus.INTERRUPTED, RunStatus.CANCELLED}:
            raise ValueError("只能恢复失败、中断或取消的任务")
        with FileLock(self.store.run_dir(run_id) / "run.lock", timeout=0) as lock:
            run = self.store.run(run_id)
            if run["catalog_version"] != self._catalog_version(run["request"].get("site_categories")):
                raise ValueError("规则版本已变化，请创建新的研究任务")
            request = ResearchRequest.model_validate(run["request"])
            if run["elapsed"] >= request.budget.run_timeout:
                raise ValueError(f"{BudgetStop.TIME}，当前任务无法继续")
            if run["status"] != status["status"]:
                raise ValueError("任务状态已经改变，请重新查询状态")
            # 自然语言提取会将标识符写回请求，恢复时仍需沿用原任务的模型设置。
            requires_model = request.requires_model or self._extracted_identifiers(run_id)
            if requires_model:
                self.settings.require_model()
            uncertain = self.store.uncertain_requests(run_id)
            if uncertain and not retry_interrupted_accounts:
                raise ValueError(f"存在 {len(uncertain)} 项未确认完成的账户请求；重新执行需要 retry_interrupted_accounts=true")
            if uncertain:
                self.store.acknowledge_uncertain_requests(run_id)
            self.store.reset_incomplete(run_id)
            self.store.update(run_id, status=RunStatus.QUEUED, cancel_requested=0)
            self._schedule(run_id, resume=True, requires_model=requires_model, held_lock=lock)
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
        known = set(self.confirmation_sources(run_id))
        if source not in known or target not in known:
            raise ValueError("确认关系的两端必须已经出现在研究资料中")
        with FileLock(self.store.run_dir(run_id) / "run.lock", timeout=0):
            if self.store.run(run_id)["status"] in {RunStatus.RUNNING, RunStatus.QUEUED}:
                raise ValueError("请在研究结束后记录确认声明")
            action = Action(capability="confirmation", value=source, site=digest([target, statement]))
            if any(item.action_id == action.key for item in self.store.evidence(run_id)):
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
