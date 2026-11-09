import argparse
import asyncio
import json
from pathlib import Path

from kudo_agent.evidence.store import Store
from kudo_agent.research_core.catalog import Catalog
from kudo_agent.research_core.collectors import Collectors
from kudo_agent.research_core.email_sites import EmailSample
from kudo_agent.research_core.http import HttpExecutor
from kudo_agent.research_core.models import Action, Identifier, ResearchRequest


async def main():
    parser = argparse.ArgumentParser(description="使用一个自有邮箱样本核查站点当前响应")
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--sample-index", type=int, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    samples = json.loads(args.samples.read_text(encoding="utf-8"))
    if not 0 <= args.sample_index < len(samples):
        raise ValueError("sample-index 超出样本范围")
    sample = EmailSample.model_validate(samples[args.sample_index])
    expected = sample.expected_presence
    identifier = Identifier(kind="email", value=sample.email)
    request = ResearchRequest(goal="自有邮箱关联资料核查", identifiers=[identifier], email_sites=[sample.site])
    catalog = Catalog()
    store = Store(args.data_dir)
    run_id = store.create(request, catalog.version)
    action = Action(capability="email", value=identifier.value, site=sample.site)
    store.add_actions(run_id, [action])
    http = HttpExecutor(store, run_id, request.budget)
    try:
        store.update(run_id, status="running")
        store.action_status(run_id, action.key, "running")
        results = await Collectors(catalog, store, run_id, http).collect(action)
        store.finish_action(run_id, action.key, results)
        store.verify(run_id)
        store.update(run_id, status="completed")
        result = {"sample_index": args.sample_index, "site": sample.site, "run_id": run_id,
                  "state": results[0].state, "expected_presence": expected,
                  "http_statuses": [item.status_code for item in store.artifacts(run_id)]}
        (store.run_dir(run_id) / "email-check.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(json.dumps(result, ensure_ascii=False))
        if expected is not None and results[0].state != ("detected" if expected else "not_detected"):
            raise AssertionError("查询结果与已知样本情况不一致，见保存的原始资料")
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        store.update(run_id, status="failed", error=message)
        store.action_status(run_id, action.key, "failed", message)
        raise
    finally:
        await http.close()
        store.close()


if __name__ == "__main__":
    asyncio.run(main())
