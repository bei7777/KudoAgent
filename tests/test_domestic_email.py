import json
import os
import shutil
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from kudo_agent.evidence.store import Store


@pytest.fixture(params=["gitee", "tencent-cloud"])
def domestic_records(request, tmp_path):
    configured = os.environ.get("KUDO_CAPTURED_DOMESTIC_EMAIL")
    if not configured:
        pytest.skip("设置 KUDO_CAPTURED_DOMESTIC_EMAIL，使用已完成的国内邮箱真实采集记录")
    source = (Path(configured) / request.param).resolve(strict=True)
    destination = tmp_path / request.param
    destination.mkdir()
    original = sqlite3.connect((source / "research.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True)
    copied = sqlite3.connect(destination / "research.sqlite")
    try:
        original.backup(copied)
    finally:
        original.close()
        copied.close()
    shutil.copytree(source / "runs", destination / "runs")
    store = Store(destination)
    try:
        yield request.param, store
    finally:
        store.close()


def test_domestic_email_states_match_verified_original_responses(domestic_records):
    provider, store = domestic_records
    rows = store.db.execute("SELECT id FROM runs WHERE status='completed' ORDER BY created_at, id").fetchall()
    assert len(rows) == 2
    subjects, observed = set(), set()
    for row in rows:
        run_id = row["id"]
        store.verify(run_id)
        evidence = store.evidence(run_id)
        assert len(evidence) == 1
        item = evidence[0]
        assert item.kind == "email"
        subjects.add(item.subject)
        expected_site = "gitee" if provider == "gitee" else "tencent_cloud"
        assert item.attributes["provider"] == expected_site
        endpoint = "/check_oversea_email" if provider == "gitee" else "/auth-api/register/checkAccountExist"
        responses = [artifact for artifact in store.artifacts(run_id) if urlsplit(artifact.url).path == endpoint]
        assert len(responses) == 1
        response = responses[0]
        assert response.status_code == 200
        assert response.artifact_id in item.artifact_ids
        payload = json.loads(store.body(run_id, response))
        if provider == "gitee":
            code = str(payload["status"])
            assert code in {"0", "1"}
            assert item.state == {"0": "not_detected", "1": "detected"}[code]
            observed.add(code)
        else:
            assert payload["success"] == 1
            assert item.state == "not_detected"
            observed.add(item.state)
        assert store.run(run_id)["request_count"] > 0
    assert len(subjects) == 2
    assert observed == ({"0", "1"} if provider == "gitee" else {"not_detected"})
