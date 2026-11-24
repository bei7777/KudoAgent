from kudo_agent.evidence.report import local_phone_report, validate_draft
from kudo_agent.evidence.review import review_evidence
from kudo_agent.evidence.store import Store
from kudo_agent.research_agent.planner import Planner
from kudo_agent.research_core.models import ReportDraft


async def generate_report(store: Store, run_id: str, planner: Planner | None):
    store.verify(run_id)
    items = store.evidence(run_id)
    run = store.run(run_id)
    if planner is None:
        draft = local_phone_report(items)
    else:
        review = review_evidence(items)
        offered = list(review.observations)
        selected = []
        while offered:
            selected = []
            for offset in range(0, len(offered), 50):
                batch = offered[offset:offset + 50]
                part = await planner.report({
                    "goal": run["request"]["goal"], "stop_reason": run["stop_reason"],
                    "observations": [{"id": key, "text": review.observations[key].text} for key in batch],
                })
                review.validate_selection(part.observation_ids, batch)
                selected.extend(part.observation_ids)
            if len(selected) <= 8:
                break
            offered = selected
        draft = ReportDraft(claims=[review.observations[key] for key in selected] + review.required,
                            next_actions=review.next_actions, evidence_review_version=1)
    validate_draft(draft, items)
    store.save_report(run_id, draft)
