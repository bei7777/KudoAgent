import json

import pytest

from kudo_agent.evidence.report import compare
from kudo_agent.research_core.collectors import Collectors
from kudo_agent.research_core.http import HttpExecutor
from kudo_agent.research_core.models import Action, Identifier, ResearchRequest

pytestmark = pytest.mark.live


async def test_comparison_of_two_real_collections(store, catalog):
    identifiers = [Identifier(kind="profile", value="https://github.com/sherlock-project"),
                   Identifier(kind="profile", value="https://github.com/signup"),
                   Identifier(kind="phone", value="+41446681800")]
    runs = []
    for round_number in range(2):
        selected = identifiers if round_number == 0 else [*identifiers[1:], Identifier(kind="ip", value="1.1.1.1")]
        request = ResearchRequest(goal="比较公开项目页面、实际受限页面和号码资料", identifiers=selected)
        run_id = store.create(request, catalog.version)
        runs.append(run_id)
        actions = [Action(capability=item.kind, value=item.value) for item in selected]
        store.add_actions(run_id, actions)
        http = HttpExecutor(store, run_id, request.budget)
        try:
            for action in actions:
                results = await Collectors(catalog, store, run_id, http).collect(action)
                store.finish_action(run_id, action.key, results)
            store.verify(run_id)
        finally:
            await http.close()
    comparison = compare(store, *runs)
    (store.root / "comparison.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    assert len(comparison["changes"]["not_rechecked"]) == 1
    assert comparison["changes"]["not_rechecked"][0]["previous"]["subject"] == identifiers[0].value
    assert len(comparison["changes"]["unverified"]) == 1
    assert len(comparison["changes"]["unchanged"]) == 1
    assert not comparison["changes"]["disappeared"]
    assert len(comparison["changes"]["added"]) == 1
    assert comparison["changes"]["added"][0]["current"]["subject"] == "1.1.1.1"
