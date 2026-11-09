import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from kudo_agent.application.config import Settings
from kudo_agent.application.service import Application

pytestmark = pytest.mark.live


@pytest.mark.parametrize("phase", ["collection", "model", "planning"])
async def test_killed_process_resumes(phase, tmp_path):
    settings = Settings.from_env(data_dir=tmp_path / "restart")
    settings.require_model()
    binary = Path(sys.executable).parent / ("kudo.exe" if os.name == "nt" else "kudo")
    request = tmp_path / "request.json"
    identifiers = [{"kind": "username", "value": "sherlock-project"}]
    if phase != "planning":
        identifiers.append({"kind": "profile", "value": "https://github.com/sherlock-project"})
    request.write_text(json.dumps({
        "goal": "核查 Sherlock 开源项目的 GitHub 账户和公开主页，逐条引用证据。",
        "identifiers": identifiers,
        "sites": ["GitHub"], "budget": {"max_depth": 1 if phase == "planning" else 0,
                                        "max_rounds": 1 if phase == "planning" else 12},
    }), encoding="utf-8")
    command = [str(binary), "--data-dir", str(settings.data_dir)]
    async with Application(settings) as observer:
        with (tmp_path / "initial.log").open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                *command, "research", "公开项目中断恢复验证", "--request-file", str(request),
                stdout=log, stderr=log,
            )
            try:
                async with asyncio.timeout(300):
                    while True:
                        assert process.returncode is None, f"研究提前退出，详见 {log.name}"
                        row = observer.store.db.execute("SELECT id FROM runs").fetchone()
                        if row:
                            run_id = row[0]
                            calls = observer.store.model_calls(run_id)
                            collecting = observer.store.db.execute(
                                "SELECT COUNT(*) FROM request_intents WHERE run_id=? AND completed=0", (run_id,)
                            ).fetchone()[0]
                            reporting = any(call["purpose"] == "report" and call["status"] == "running" for call in calls)
                            planning = any(call["purpose"] == "plan" and call["status"] == "running" for call in calls)
                            ready = ((phase == "collection" and collecting) or (phase == "model" and reporting)
                                     or (phase == "planning" and planning))
                            if ready and observer.store.run(run_id)["elapsed"] > 0:
                                break
                        await asyncio.sleep(0.01)
                process.kill()
                await process.wait()
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
        before = {item.evidence_id: item.model_dump() for item in observer.store.evidence(run_id)}
        artifacts = {item.artifact_id: item.model_dump() for item in observer.store.artifacts(run_id)}
        interrupted = observer.get_status(run_id)
        assert interrupted["status"] == "interrupted"
        assert interrupted["elapsed"] > 0
        with (tmp_path / "resume.log").open("wb") as log:
            resumed = await asyncio.create_subprocess_exec(*command, "resume", run_id, stdout=log, stderr=log)
            try:
                async with asyncio.timeout(600):
                    assert await resumed.wait() == 0, f"恢复失败，详见 {log.name}"
            finally:
                if resumed.returncode is None:
                    resumed.kill()
                    await resumed.wait()
        status = observer.get_status(run_id)
        assert status["status"] == "completed"
        assert status["elapsed"] >= interrupted["elapsed"]
        if phase != "planning":
            assert status["model_usage"]["total_tokens"] > 0
        assert status["model_usage"]["planning_attempts"] <= 12
        after = {item.evidence_id: item.model_dump() for item in observer.store.evidence(run_id)}
        assert all(after[key] == value for key, value in before.items())
        current_artifacts = {item.artifact_id: item.model_dump() for item in observer.store.artifacts(run_id)}
        assert all(current_artifacts[key] == value for key, value in artifacts.items())
        observer.store.verify(run_id)
        report = observer.get_report(run_id, "json")["report"]
        assert report["claims"]
        assert status["coverage"]["complete"]
        if phase == "planning":
            assert before
            assert status["model_usage"]["planning_attempts"] == 1
            assert status["model_usage"]["interrupted"] == 1
            assert status["stop_reason"] == "模型决策轮数已达到预算"
        if phase == "model":
            assert before
            assert status["request_count"] == interrupted["request_count"]
            assert status["model_usage"]["interrupted"] == 1
