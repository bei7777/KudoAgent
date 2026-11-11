import argparse
import asyncio
import json
from pathlib import Path

from kudo_agent.evidence.store import Store
from kudo_agent.research_core.catalog import Catalog
from kudo_agent.research_core.collectors import Collectors
from kudo_agent.research_core.email_communities import COMMUNITIES
from kudo_agent.research_core.email_sites import EmailSample
from kudo_agent.research_core.http import HttpExecutor, QueryConnectionError, QueryTargetBlocked
from kudo_agent.research_core.models import EMAIL_SITES, Action, Identifier, ResearchRequest


async def main():
    parser = argparse.ArgumentParser(description="保存自有邮箱在指定站点的真实字段校验记录")
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--sites", nargs="+", choices=EMAIL_SITES, default=list(COMMUNITIES))
    parser.add_argument("--concurrency", type=int, default=5, choices=range(1, 11))
    parser.add_argument("--resume", action="store_true", help="复用目录中已经完成并留有验证记录的查询")
    args = parser.parse_args()
    samples = [EmailSample.model_validate(item)
               for item in json.loads(args.samples.read_text(encoding="utf-8"))]
    emails = list(dict.fromkeys(sample.email for sample in samples))
    if not emails:
        raise ValueError("验收文件缺少自有邮箱样本")
    catalog, store = Catalog(), Store(args.data_dir)
    limit = asyncio.Semaphore(args.concurrency)
    completed = set()
    if args.resume:
        for path in args.data_dir.glob("runs/*/email-check.json"):
            record = json.loads(path.read_text(encoding="utf-8"))
            previous = store.run(record["run_id"])
            if previous["status"] == "completed":
                store.verify(record["run_id"])
                completed.add((record["site"], previous["request"]["identifiers"][0]["value"]))

    async def probe(site):
        async with limit:
            for index, email in enumerate(emails):
                if (site, email) in completed:
                    continue
                request = ResearchRequest(goal="自有邮箱字段检查验收", email_sites=[site],
                                          identifiers=[Identifier(kind="email", value=email)])
                run_id = store.create(request, catalog.version)
                action = Action(capability="email", value=email, site=site)
                store.add_actions(run_id, [action])
                store.update(run_id, status="running")
                store.action_status(run_id, action.key, "running")
                http = HttpExecutor(store, run_id, request.budget)
                try:
                    evidence = await Collectors(catalog, store, run_id, http).collect(action)
                    store.finish_action(run_id, action.key, evidence)
                    store.verify(run_id)
                    store.update(run_id, status="completed")
                    result = {"site": site, "sample_index": index, "run_id": run_id,
                              "state": evidence[0].state,
                              "http_statuses": [item.status_code for item in store.artifacts(run_id)]}
                    (store.run_dir(run_id) / "email-check.json").write_text(
                        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                except (QueryConnectionError, QueryTargetBlocked) as error:
                    message = str(error)
                    state = "connection_failed" if isinstance(error, QueryConnectionError) else "blocked"
                    store.action_status(run_id, action.key, state, message)
                    store.update(run_id, status="completed", stop_reason=state)
                    store.verify(run_id)
                    result = {"site": site, "sample_index": index, "run_id": run_id, "state": state,
                              "http_statuses": [item.status_code for item in store.artifacts(run_id)]}
                    (store.run_dir(run_id) / "email-check.json").write_text(
                        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                except asyncio.CancelledError:
                    store.update(run_id, status="interrupted")
                    store.action_status(run_id, action.key, "interrupted")
                    raise
                except Exception as error:
                    message = f"{type(error).__name__}: {error}"
                    store.update(run_id, status="failed", error=message)
                    store.action_status(run_id, action.key, "failed", message)
                    raise
                finally:
                    await http.close()

    try:
        async with asyncio.TaskGroup() as group:
            for site in dict.fromkeys(args.sites):
                group.create_task(probe(site))
    finally:
        store.close()


if __name__ == "__main__":
    asyncio.run(main())
