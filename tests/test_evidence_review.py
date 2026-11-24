import os
import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path

import pytest

from kudo_agent.evidence.review import UNCERTAIN, review_evidence, review_key
from kudo_agent.research_core.models import Evidence


def capture(variable: str):
    source = os.environ.get(variable)
    if not source:
        pytest.skip(f"设置 {variable} 以读取已有真实证据")
    path = Path(source)
    database = path / "research.sqlite" if path.is_dir() else path
    return sqlite3.connect(database.resolve(strict=True).as_uri() + "?mode=ro", uri=True)


def test_contradictory_observations_across_report_batches():
    compared = 0
    with closing(capture("KUDO_CAPTURED_REVIEW")) as db:
        run_ids = db.execute("SELECT run_id FROM evidence GROUP BY run_id HAVING count(*)>50").fetchall()
        for (run_id,) in run_ids:
            items = [Evidence.model_validate_json(row[0]) for row in db.execute(
                "SELECT payload FROM evidence WHERE run_id=? ORDER BY rowid", (run_id,)
            )]
            matching = defaultdict(dict)
            for position, item in enumerate(items):
                if item.kind == "account" and item.state in {"detected", "not_detected"}:
                    matching[review_key(item)][item.state] = (position, item.evidence_id)
            review = review_evidence(items)
            known = {item.evidence_id for item in items}
            for states in matching.values():
                if states.keys() != {"detected", "not_detected"}:
                    continue
                (i, positive), (j, negative) = states["detected"], states["not_detected"]
                if abs(i - j) < 50:
                    continue
                assert positive not in review.observations and negative not in review.observations
                assert any(positive in claim.evidence_ids and negative in claim.conflicting_evidence_ids
                           for claim in review.required)
                compared += 1
            for entry in review.required:
                assert set(entry.evidence_ids + entry.conflicting_evidence_ids) <= known
    assert compared, "真实存档需要包含同一任务、同一平台与用户名的跨批次矛盾观察"


def test_missing_public_avatar_and_profile_leave_registration_unverified():
    with closing(capture("KUDO_CAPTURED_REVIEW")) as db:
        items = [Evidence.model_validate_json(row[0]) for row in db.execute(
            "SELECT payload FROM evidence WHERE json_extract(payload,'$.attributes.provider') IN ('gravatar','duolingo') "
            "AND json_extract(payload,'$.state')='not_detected'"
        )]
    assert {item.attributes["interaction"] for item in items} == {"public_avatar", "public_profile"}
    for item in items:
        text = review_evidence([item]).observations[item.evidence_id].text
        assert "账户注册状态未核实" in text
        assert ("未返回公开头像" if item.attributes["interaction"] == "public_avatar" else "0 条公开资料记录") in text


def test_email_registration_field_and_uncertainty_use_observed_scope():
    with closing(capture("KUDO_CAPTURED_EMAIL_COMMUNITIES")) as db:
        items = [Evidence.model_validate_json(row[0]) for row in db.execute(
            "SELECT payload FROM evidence WHERE json_extract(payload,'$.attributes.field_available')=1 "
            "AND json_extract(payload,'$.attributes.registration_status')='unknown'"
        )]
    assert items
    review = review_evidence(items)
    assert all("字段允许继续填写，账户注册状态未核实" in claim.text for claim in review.observations.values())
    assert any("未能判定" in claim.text for claim in review.required) == any(i.state in UNCERTAIN for i in items)
    offered = list(review.observations)
    with pytest.raises(ValueError, match="未提供"):
        review.validate_selection([offered[0]], [])
    with pytest.raises(ValueError, match="重复"):
        review.validate_selection([offered[0], offered[0]], offered)
