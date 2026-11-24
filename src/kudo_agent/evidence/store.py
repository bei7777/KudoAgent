import asyncio
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from uuid import UUID, uuid4

from kudo_agent.evidence.browse import create_browse_indexes
from kudo_agent.research_core.models import (
    Action,
    Artifact,
    BudgetStop,
    Evidence,
    ReportDraft,
    RequestBudgetExceeded,
    ResearchRequest,
    RunStatus,
    timestamp,
)


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.root / "research.sqlite", timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, request TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                stop_reason TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                cancel_requested INTEGER NOT NULL DEFAULT 0, request_count INTEGER NOT NULL DEFAULT 0,
                elapsed REAL NOT NULL DEFAULT 0, report TEXT, catalog_version TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS actions (
                run_id TEXT NOT NULL REFERENCES runs(id), id TEXT NOT NULL, payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', error TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (run_id, id)
            );
            CREATE TABLE IF NOT EXISTS artifacts (
                run_id TEXT NOT NULL REFERENCES runs(id), id TEXT NOT NULL, request_key TEXT NOT NULL,
                payload TEXT NOT NULL, PRIMARY KEY (run_id, id), UNIQUE (run_id, request_key)
            );
            CREATE TABLE IF NOT EXISTS evidence (
                run_id TEXT NOT NULL REFERENCES runs(id), id TEXT NOT NULL, action_id TEXT NOT NULL,
                payload TEXT NOT NULL, PRIMARY KEY (run_id, id)
            );
            CREATE TABLE IF NOT EXISTS decisions (
                run_id TEXT NOT NULL REFERENCES runs(id), round INTEGER NOT NULL,
                payload TEXT NOT NULL, PRIMARY KEY (run_id, round)
            );
            CREATE TABLE IF NOT EXISTS request_intents (
                run_id TEXT NOT NULL REFERENCES runs(id), request_key TEXT NOT NULL,
                account_request INTEGER NOT NULL, completed INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (run_id, request_key)
            );
            CREATE TABLE IF NOT EXISTS acknowledged_requests (
                run_id TEXT NOT NULL, request_key TEXT NOT NULL, acknowledged_at TEXT NOT NULL,
                PRIMARY KEY (run_id, request_key),
                FOREIGN KEY (run_id, request_key) REFERENCES request_intents(run_id, request_key)
            );
            CREATE TABLE IF NOT EXISTS model_calls (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                purpose TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL,
                finished_at TEXT, request TEXT NOT NULL, response TEXT,
                error TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS report_progress (
                run_id TEXT PRIMARY KEY REFERENCES runs(id), status TEXT NOT NULL, error TEXT NOT NULL,
                collection_status TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS budget_extensions (
                run_id TEXT NOT NULL REFERENCES runs(id), revision INTEGER NOT NULL, first_round INTEGER NOT NULL,
                budget TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (run_id, revision)
            );
            CREATE TABLE IF NOT EXISTS scope_counts (
                run_id TEXT PRIMARY KEY REFERENCES runs(id), username_sites INTEGER NOT NULL, email_sites INTEGER NOT NULL
            );
        """)
        create_browse_indexes(self.db)

    def close(self):
        self.db.close()

    def report_progress(self, run_id: str) -> dict:
        row = self.db.execute(
            """SELECT COALESCE(p.status, CASE WHEN r.report IS NULL THEN 'pending'
                     WHEN json_extract(r.report, '$.evidence_review_version')=1 THEN 'ready' ELSE 'outdated' END) AS status,
                      COALESCE(p.error, '') AS error, COALESCE(p.collection_status, '') AS collection_status
               FROM runs r LEFT JOIN report_progress p ON p.run_id=r.id WHERE r.id=?""", (run_id,)
        ).fetchone()
        return dict(row)

    def begin_report(self, run_id: str, collection_status: str, reason: str):
        with self.db:
            self.db.execute("UPDATE runs SET stop_reason=?, updated_at=? WHERE id=?", (reason, timestamp(), run_id))
            self.db.execute(
                "INSERT INTO report_progress VALUES (?, 'generating', '', ?) "
                "ON CONFLICT(run_id) DO UPDATE SET status='generating', error='', collection_status=excluded.collection_status",
                (run_id, collection_status),
            )

    def report_failed(self, run_id: str, error: str, *, restore_collection: bool = False):
        with self.db:
            self.db.execute("UPDATE report_progress SET status='failed', error=? WHERE run_id=?", (error, run_id))
            if restore_collection:
                self.db.execute("UPDATE runs SET status=(SELECT collection_status FROM report_progress WHERE run_id=?), "
                                "updated_at=? WHERE id=?", (run_id, timestamp(), run_id))
            else:
                self.db.execute("UPDATE runs SET updated_at=? WHERE id=?", (timestamp(), run_id))

    def save_report(self, run_id: str, draft: ReportDraft):
        with self.db:
            self.db.execute("UPDATE runs SET report=?, updated_at=? WHERE id=?", (draft.model_dump_json(), timestamp(), run_id))
            self.db.execute("UPDATE report_progress SET status='ready', error='' WHERE run_id=?", (run_id,))

    def run_dir(self, run_id: str) -> Path:
        UUID(run_id)
        return self.root / "runs" / run_id

    def create(self, request: ResearchRequest, catalog_version: str, *, scope: dict[str, int] | None = None) -> str:
        run_id = str(uuid4())
        self.run_dir(run_id).mkdir(parents=True, mode=0o700)
        now = timestamp()
        with self.db:
            self.db.execute(
                "INSERT INTO runs (id, request, status, created_at, updated_at, catalog_version) VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, request.model_dump_json(), RunStatus.QUEUED, now, now, catalog_version),
            )
            if scope is not None:
                self.db.execute("INSERT INTO scope_counts VALUES (?, ?, ?)",
                                (run_id, scope["username_sites"], scope["email_sites"]))
        return run_id

    def run(self, run_id: str, *, include_report: bool = True) -> dict:
        UUID(run_id)
        fields = ("*" if include_report else
                  "id,request,status,created_at,updated_at,stop_reason,error,cancel_requested,request_count,elapsed,catalog_version")
        row = self.db.execute(f"SELECT {fields} FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"研究任务不存在：{run_id}")
        result = dict(row)
        result["request"] = json.loads(result["request"])
        if result.get("report"):
            result["report"] = json.loads(result["report"])
        self.normalize_legacy_status(result)
        return result

    def normalize_legacy_status(self, result: dict):
        if result["status"] == RunStatus.COMPLETED and self.legacy_budget_stop(result["id"], result["stop_reason"]):
            result["status"] = RunStatus.PARTIAL
        if (result["status"] == RunStatus.FAILED and result["stop_reason"] == "执行错误"
                and result["error"] == f"TimeoutError: {BudgetStop.TIME}"):
            result.update(status=RunStatus.PARTIAL, stop_reason=BudgetStop.TIME.value, error="")

    def legacy_budget_stop(self, run_id: str, reason: str) -> bool:
        if reason not in {BudgetStop.REQUESTS, BudgetStop.PLANNING, "网络请求预算已耗尽"}:
            return False
        # 老版本仅保存停止文案；模型主动结束时有对应的决策记录。
        model_stop = self.db.execute(
            "SELECT 1 FROM decisions WHERE run_id=? AND COALESCE(json_extract(payload,'$.source'),'model')='model' "
            "AND json_extract(payload,'$.stop_reason')=? LIMIT 1", (run_id, reason),
        ).fetchone()
        return model_stop is None

    def update(self, run_id: str, **fields):
        allowed = {"status", "stop_reason", "error", "cancel_requested", "elapsed", "report", "request"}
        if not fields or set(fields) - allowed:
            raise ValueError("无效的任务更新字段")
        fields["updated_at"] = timestamp()
        columns = ", ".join(f"{name}=?" for name in fields)
        with self.db:
            self.db.execute(f"UPDATE runs SET {columns} WHERE id=?", (*fields.values(), run_id))

    def actions(self, run_id: str, status: str | None = None) -> list[dict]:
        query, params = "SELECT * FROM actions WHERE run_id=?", [run_id]
        if status:
            query += " AND status=?"
            params.append(status)
        return [{**dict(row), "payload": json.loads(row["payload"])}
                for row in self.db.execute(query + " ORDER BY rowid", params)]

    def add_actions(self, run_id: str, actions: list[Action]):
        with self.db:
            self.db.executemany(
                "INSERT OR IGNORE INTO actions (run_id, id, payload) VALUES (?, ?, ?)",
                [(run_id, action.key, action.model_dump_json()) for action in actions],
            )

    def action_counts(self, run_id: str) -> dict[str, int]:
        return dict(self.db.execute(
            "SELECT status, count(*) FROM actions WHERE run_id=? GROUP BY status", (run_id,)
        ))

    def failed_queries(self, run_id: str, limit: int | None = None) -> list[dict]:
        query = "SELECT * FROM actions WHERE run_id=? AND status IN ('unreachable','blocked') ORDER BY rowid"
        rows = (self.db.execute(query, (run_id,)) if limit is None else
                reversed(self.db.execute(query + " DESC LIMIT ?", (run_id, limit)).fetchall()))
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def coverage(self, run_id: str) -> dict:
        counts = dict(self.db.execute(
            "SELECT status, count(*) FROM actions WHERE run_id=? AND json_extract(payload, '$.depth')=0 GROUP BY status",
            (run_id,),
        ))
        planned = sum(counts.values())
        checked = counts.get("completed", 0)
        failed = counts.get("unreachable", 0)
        blocked = counts.get("blocked", 0)
        remaining = planned - checked - failed - blocked
        return {"planned": planned, "checked": checked, "failed": failed, "blocked": blocked, "remaining": remaining,
                "complete": planned > 0 and remaining == 0}

    def action_status(self, run_id: str, action_id: str, status: str, error: str = ""):
        with self.db:
            self.db.execute("UPDATE actions SET status=?, error=? WHERE run_id=? AND id=?",
                            (status, error, run_id, action_id))

    def reset_incomplete(self, run_id: str, *, new_request: ResearchRequest | None = None):
        with self.db:
            self.db.execute(
                "UPDATE actions SET status='pending', error='' WHERE run_id=? AND status NOT IN ('completed', 'unreachable', 'blocked')",
                (run_id,),
            )
            self.db.execute(
                "UPDATE model_calls SET status='interrupted', finished_at=? WHERE run_id=? AND status='running'",
                (timestamp(), run_id),
            )
            if new_request is not None:
                # 每次追加从新的检查点继续读取同一任务保存的查询、证据和已完成请求。
                self.db.execute(
                    "INSERT INTO budget_extensions SELECT ?, coalesce(max(revision)+1,1), "
                    "(SELECT coalesce(max(round)+1,0) FROM decisions WHERE run_id=?), ?, ? "
                    "FROM budget_extensions WHERE run_id=?",
                    (run_id, run_id, new_request.budget.model_dump_json(), timestamp(), run_id),
                )
                self.db.execute(
                    "INSERT INTO report_progress SELECT id, CASE WHEN report IS NULL THEN 'pending' ELSE 'outdated' END, '', '' "
                    "FROM runs WHERE id=? ON CONFLICT(run_id) DO UPDATE SET status=excluded.status, error='', collection_status=''",
                    (run_id,),
                )
                self.db.execute(
                    "UPDATE runs SET request=?, status=?, stop_reason='', error='', cancel_requested=0, updated_at=? WHERE id=?",
                    (new_request.model_dump_json(), RunStatus.QUEUED, timestamp(), run_id),
                )

    def uncertain_requests(self, run_id: str) -> list[str]:
        return [row[0] for row in self.db.execute(
            """SELECT request_key FROM request_intents AS intent
               WHERE run_id=? AND account_request=1 AND completed=0
               AND NOT EXISTS (SELECT 1 FROM acknowledged_requests AS acknowledgement
                               WHERE acknowledgement.run_id=intent.run_id
                               AND acknowledgement.request_key=intent.request_key)""", (run_id,)
        )]

    def acknowledge_uncertain_requests(self, run_id: str):
        keys = self.uncertain_requests(run_id)
        with self.db:
            self.db.executemany("INSERT INTO acknowledged_requests VALUES (?, ?, ?)",
                                [(run_id, key, timestamp()) for key in keys])

    def reserve_request(self, run_id: str, key: str, account: bool, maximum: int):
        with self.db:
            updated = self.db.execute(
                "UPDATE runs SET request_count=request_count+1 WHERE id=? AND request_count<? AND cancel_requested=0",
                (run_id, maximum),
            )
            if updated.rowcount != 1:
                if self.run(run_id)["cancel_requested"]:
                    raise asyncio.CancelledError()
                raise RequestBudgetExceeded("网络请求预算已耗尽")
            self.db.execute(
                "INSERT INTO request_intents VALUES (?, ?, ?, 0) ON CONFLICT(run_id, request_key) DO UPDATE SET completed=0",
                (run_id, key, account),
            )
            self.db.execute("DELETE FROM acknowledged_requests WHERE run_id=? AND request_key=?", (run_id, key))

    def begin_model_call(self, run_id: str, purpose: str, request: dict) -> str:
        call_id = str(uuid4())
        with self.db:
            self.db.execute(
                "INSERT INTO model_calls (id, run_id, purpose, status, started_at, request) VALUES (?, ?, ?, 'running', ?, ?)",
                (call_id, run_id, purpose, timestamp(), json.dumps(request, ensure_ascii=False)),
            )
        return call_id

    def finish_model_call(self, call_id: str, status: str, response: dict | None, error: str = ""):
        with self.db:
            self.db.execute(
                "UPDATE model_calls SET status=?, finished_at=?, response=?, error=? WHERE id=?",
                (status, timestamp(), json.dumps(response, ensure_ascii=False) if response else None, error, call_id),
            )

    def model_calls(self, run_id: str) -> list[dict]:
        return [{**dict(row), "request": json.loads(row["request"]),
                 "response": json.loads(row["response"]) if row["response"] else None}
                for row in self.db.execute("SELECT * FROM model_calls WHERE run_id=? ORDER BY rowid", (run_id,))]

    def model_usage(self, run_id: str) -> dict:
        calls = self.db.execute("SELECT purpose, status, response FROM model_calls WHERE run_id=?", (run_id,)).fetchall()
        usage = {"attempts": len(calls), "planning_attempts": 0, "completed": 0,
                 "running": 0, "failed": 0, "interrupted": 0,
                 "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        for call in calls:
            usage[call["status"]] += 1
            usage["planning_attempts"] += call["purpose"] == "plan"
            response = json.loads(call["response"]) if call["response"] else {}
            tokens = response.get("usage") or {}
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                usage[key] += tokens.get(key) or 0
        return usage

    def artifact(self, run_id: str, key: str) -> Artifact | None:
        row = self.db.execute("SELECT payload FROM artifacts WHERE run_id=? AND request_key=?", (run_id, key)).fetchone()
        return Artifact.model_validate_json(row[0]) if row else None

    def artifacts(self, run_id: str) -> list[Artifact]:
        return [Artifact.model_validate_json(row[0]) for row in self.db.execute(
            "SELECT payload FROM artifacts WHERE run_id=? ORDER BY rowid", (run_id,)
        )]

    def save_artifact(self, run_id: str, artifact: Artifact, body: bytes):
        destination = self.run_dir(run_id) / artifact.body_path
        destination.parent.mkdir(exist_ok=True, mode=0o700)
        temporary = destination.with_suffix(".partial")
        with temporary.open("wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(destination)
        with self.db:
            self.db.execute("INSERT INTO artifacts VALUES (?, ?, ?, ?)",
                            (run_id, artifact.artifact_id, artifact.request_key, artifact.model_dump_json()))
            self.db.execute("UPDATE request_intents SET completed=1 WHERE run_id=? AND request_key=?",
                            (run_id, artifact.request_key))

    def body(self, run_id: str, artifact: Artifact) -> bytes:
        path = (self.run_dir(run_id) / artifact.body_path).resolve()
        if not path.is_relative_to(self.run_dir(run_id)):
            raise ValueError("资料路径超出任务目录")
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != artifact.sha256:
            raise ValueError(f"原始资料校验失败：{artifact.artifact_id}")
        return body

    def finish_action(self, run_id: str, action_id: str, evidence: list[Evidence]):
        with self.db:
            for item in evidence:
                self.db.execute("INSERT OR REPLACE INTO evidence VALUES (?, ?, ?, ?)",
                                (run_id, item.evidence_id, action_id, item.model_dump_json()))
            self.db.execute("UPDATE actions SET status='completed', error='' WHERE run_id=? AND id=?",
                            (run_id, action_id))

    def evidence(self, run_id: str) -> list[Evidence]:
        return [Evidence.model_validate_json(row[0]) for row in self.db.execute(
            "SELECT payload FROM evidence WHERE run_id=? ORDER BY rowid", (run_id,)
        )]

    def save_decision(self, run_id: str, round_number: int, payload: dict):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO decisions VALUES (?, ?, ?)",
                            (run_id, round_number, json.dumps(payload, ensure_ascii=False)))

    def decision(self, run_id: str, round_number: int) -> dict | None:
        row = self.db.execute("SELECT payload FROM decisions WHERE run_id=? AND round=?",
                              (run_id, round_number)).fetchone()
        return json.loads(row[0]) if row else None

    def decisions(self, run_id: str) -> list[dict]:
        return [{"round": row[0], **json.loads(row[1])} for row in self.db.execute(
            "SELECT round, payload FROM decisions WHERE run_id=? ORDER BY round", (run_id,)
        )]

    def verify(self, run_id: str):
        for row in self.db.execute("SELECT payload FROM artifacts WHERE run_id=?", (run_id,)):
            self.body(run_id, Artifact.model_validate_json(row[0]))
        artifacts = {row[0] for row in self.db.execute("SELECT id FROM artifacts WHERE run_id=?", (run_id,))}
        items = self.evidence(run_id)
        ids = {item.evidence_id for item in items}
        for item in items:
            if set(item.artifact_ids) - artifacts:
                raise ValueError(f"证据引用不存在的原始资料：{item.evidence_id}")
            for relation in item.relations:
                if set(relation.evidence_ids) - ids:
                    raise ValueError("关联引用不存在的证据")
