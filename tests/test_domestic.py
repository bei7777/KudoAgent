import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from kudo_agent.evidence.store import Store
from kudo_agent.research_core.collectors import Collectors
from kudo_agent.research_core.http import HttpExecutor
from kudo_agent.research_core.models import Action, ResearchRequest


async def test_captured_domestic_profiles_preserve_access_state(tmp_path, catalog):
    directory = os.environ.get("KUDO_CAPTURED_DOMESTIC_PROFILES")
    if not directory:
        pytest.skip("需要国内公开主页的真实采集记录")
    source = Path(directory)
    target = tmp_path / "profiles"
    target.mkdir()
    with sqlite3.connect(f"{(source / 'research.sqlite').resolve().as_uri()}?mode=ro", uri=True) as original:
        with sqlite3.connect(target / "research.sqlite") as copied:
            original.backup(copied)
    shutil.copytree(source / "runs", target / "runs")
    store = Store(target)
    run_id = store.db.execute("SELECT id FROM runs WHERE status='completed' ORDER BY created_at DESC LIMIT 1").fetchone()[0]
    request = ResearchRequest.model_validate(store.run(run_id)["request"])
    before = store.run(run_id)["request_count"]
    http = HttpExecutor(store, run_id, request.budget)
    try:
        collector = Collectors(catalog, store, run_id, http)
        for url, expected in (("https://gitee.com/oschina", "detected"),
                              ("https://weibo.com/weibokefu", "blocked")):
            evidence = await collector.profile(Action(capability="profile", value=url))
            assert evidence.state == expected
            assert evidence.source_url == url
            if expected == "blocked":
                assert evidence.attributes["title"] == "Sina Visitor System"
                assert evidence.relations == []
            else:
                assert evidence.attributes["text_excerpt"]
            for artifact_id in evidence.artifact_ids:
                assert store.artifact(run_id, artifact_id).status_code == 200
        assert store.run(run_id)["request_count"] == before
        store.verify(run_id)
    finally:
        await http.close()
        store.close()
