import json
import sqlite3

from kudo_agent.research_core.models import Artifact, Evidence

PAGE = "json_extract(payload,'$.kind') IN ('account','profile') AND json_extract(payload,'$.state')='detected'"
EMAIL = "json_extract(payload,'$.kind')='email'"
METADATA = "json_extract(payload,'$.kind') IN ('phone','ip')"
UNCERTAIN = ("json_extract(payload,'$.state') IN ('unknown','blocked','rate_limited','unsupported') "
             "OR json_extract(payload,'$.attributes.conflict')=1")
VIEWS = {"all": "1", "pages": PAGE, "email": EMAIL, "metadata": METADATA, "uncertain": UNCERTAIN}
SOURCE = "json_extract(payload,'$.source_url')"
PRIORITY = f"CASE WHEN {PAGE} THEN 0 WHEN {EMAIL} THEN 1 WHEN {METADATA} THEN 2 WHEN {UNCERTAIN} THEN 3 ELSE 4 END"


def create_browse_indexes(db: sqlite3.Connection):
    db.create_function("kudo_casefold", 1, lambda value: str(value or "").casefold(), deterministic=True)
    db.execute(f"CREATE INDEX IF NOT EXISTS evidence_order ON evidence(run_id, {PRIORITY})")
    db.execute(f"CREATE INDEX IF NOT EXISTS evidence_page_urls ON evidence(run_id, {SOURCE}) WHERE {PAGE}")
    for name in ("email", "metadata", "uncertain"):
        db.execute(f"CREATE INDEX IF NOT EXISTS evidence_{name} ON evidence(run_id) WHERE {VIEWS[name]}")
    db.execute("CREATE INDEX IF NOT EXISTS action_status_counts ON actions(run_id, status)")
    db.execute("CREATE INDEX IF NOT EXISTS action_initial_counts ON actions(run_id, status) "
               "WHERE json_extract(payload,'$.depth')=0")
    db.execute("CREATE INDEX IF NOT EXISTS action_email_counts ON actions(run_id,status,json_extract(payload,'$.site')) "
               "WHERE json_extract(payload,'$.capability')='email'")


def evidence_page(db: sqlite3.Connection, run_id: str, offset: int, limit: int,
                  view: str, query: str, evidence_id: str) -> dict:
    summary = {}
    for name, condition in VIEWS.items():
        count = f"count(DISTINCT NULLIF({SOURCE},''))" if name == "pages" else "count(*)"
        summary[name] = db.execute(
            f"SELECT {count} FROM evidence WHERE run_id=? AND ({condition})", (run_id,),
        ).fetchone()[0]
    parameters = [run_id]
    if evidence_id:
        predicate = "run_id=? AND id=?"
        parameters.append(evidence_id)
    else:
        predicate = f"run_id=? AND ({VIEWS[view]})"
        if needle := query.strip().casefold():
            fields = ("subject", "source_url", "summary", "attributes.site", "attributes.site_id", "attributes.provider")
            matches = [f"instr(kudo_casefold(json_extract(payload,'$.{field}')),?)>0" for field in fields]
            predicate += " AND (" + " OR ".join(matches) + ")"
            parameters.extend([needle] * len(fields))
    total = db.execute(f"SELECT count(*) FROM evidence WHERE {predicate}", parameters).fetchone()[0]
    rows = db.execute(f"SELECT payload FROM evidence WHERE {predicate} ORDER BY {PRIORITY}, rowid LIMIT ? OFFSET ?",
                      [*parameters, limit, offset])
    items = [Evidence.model_validate_json(row[0]).public_record() for row in rows]
    identifiers = list(dict.fromkeys(key for item in items for key in item["artifact_ids"]))
    artifacts = db.execute(
        "SELECT payload FROM artifacts WHERE run_id=? AND id IN (SELECT value FROM json_each(?)) ORDER BY rowid",
        (run_id, json.dumps(identifiers)),
    )
    return {"total": summary["all"], "filtered_total": total, "summary": summary, "items": items,
            "artifacts": [Artifact.model_validate_json(row[0]).model_dump() for row in artifacts]}
