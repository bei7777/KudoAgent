import asyncio
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from uuid import UUID, uuid4

from kudo_agent.research_core.models import (
    Action,
    Artifact,
    Evidence,
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
        """)

    def close(self):
        self.db.close()

    def run_dir(self, run_id: str) -> Path:
        UUID(run_id)
        return self.root / "runs" / run_id

    def create(self, request: ResearchRequest, catalog_version: str) -> str:
        run_id = str(uuid4())
        self.run_dir(run_id).mkdir(parents=True, mode=0o700)
        now = timestamp()
        with self.db:
            self.db.execute(
                "INSERT INTO runs (id, request, status, created_at, updated_at, catalog_version) VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, request.model_dump_json(), RunStatus.QUEUED, now, now, catalog_version),
            )
        return run_id

    def run(self, run_id: str) -> dict:
        UUID(run_id)
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"研究任务不存在：{run_id}")
        result = dict(row)
        result["request"] = json.loads(result["request"])
        if result["report"]:
            result["report"] = json.loads(result["report"])
        return result

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

    def failed_queries(self, run_id: str) -> list[dict]:
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in self.db.execute(
            "SELECT * FROM actions WHERE run_id=? AND status IN ('unreachable', 'blocked') ORDER BY rowid",
            (run_id,),
        )]

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

    def reset_incomplete(self, run_id: str):
        with self.db:
            self.db.execute(
                "UPDATE actions SET status='pending', error='' WHERE run_id=? AND status NOT IN ('completed', 'unreachable', 'blocked')",
                (run_id,),
            )
            self.db.execute(
                "UPDATE model_calls SET status='interrupted', finished_at=? WHERE run_id=? AND status='running'",
                (timestamp(), run_id),
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
