import phonenumbers
import pytest

from kudo_agent.application.config import Settings
from kudo_agent.application.service import Application
from kudo_agent.research_core.models import (
    ActionPlan,
    Budget,
    BudgetStop,
    Identifier,
    ResearchRequest,
    RunStatus,
)


def test_model_stop_with_budget_wording_keeps_completed_status(store, catalog):
    run_id = store.create(ResearchRequest(goal="核查项目主页"), catalog.version)
    decision = ActionPlan(source="model", actions=[], stop_reason=BudgetStop.PLANNING.value)
    store.save_decision(run_id, 0, decision.model_dump())
    store.update(run_id, status=RunStatus.COMPLETED, stop_reason=decision.stop_reason)
    assert store.run(run_id)["status"] == RunStatus.COMPLETED


async def test_run_deadline_saves_partial_status_and_report(tmp_path):
    example = phonenumbers.example_number("CH")
    phone = phonenumbers.format_number(example, phonenumbers.PhoneNumberFormat.E164)
    request = ResearchRequest(goal="查询号码号段", identifiers=[Identifier(kind="phone", value=phone)],
                              budget=Budget(run_timeout=0.000001))
    async with Application(Settings(data_dir=tmp_path)) as app:
        run_id = app.start_research(request)["run_id"]
        result = await app.wait(run_id)
        assert result["status"] == RunStatus.PARTIAL
        assert result["stop_reason"] == BudgetStop.TIME
        assert not result["can_resume"] and not result["error"]
        assert app.get_report(run_id, "json")["status"] == result
        markdown = app.get_report(run_id, "markdown")
        assert markdown == (app.store.run_dir(run_id) / "report.md").read_text(encoding="utf-8")
        assert "`partial`" in markdown and BudgetStop.TIME in markdown
        with pytest.raises(ValueError, match=BudgetStop.TIME):
            app.resume_research(run_id)
    async with Application(Settings(data_dir=tmp_path)) as reopened:
        assert reopened.get_status(run_id)["status"] == RunStatus.PARTIAL
