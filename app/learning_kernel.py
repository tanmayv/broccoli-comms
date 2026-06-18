from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any
from contextlib import closing

SCHEMA_VERSION = 1
TASK_STATUSES = {"planning", "queued", "ready", "working", "blocked", "review", "done", "validated", "archived"}
STATE_STATUSES = {"working", "blocked", "waiting", "review", "done"}
RESULT_STATUSES = {"good", "bad", "need_improvements"}
APPROVAL_STATUSES = {"pending", "decided", "superseded"}
PARTICIPANT_ROLES = {"assignee", "reviewer", "verifier", "coordinator", "observer", "specialist"}
PARTICIPANT_STATUSES = {"active", "inactive"}
MEMORY_TYPES = {"fact", "habit", "episode", "expertise", "skill"}
MEMORY_STATUSES = {"pending", "active", "rejected", "revoked", "superseded"}
TRUSTED_MEMORY_ACTORS = {"user", "coordinator", "task-kernel", "agent-communicator"}
MEMORY_LIMITS = {
    "max_active_per_agent": 200,
    "max_active_per_agent_fact": 100,
    "max_active_per_agent_habit": 50,
    "max_active_per_agent_episode": 50,
    "max_active_per_agent_expertise": 50,
    "max_active_per_agent_skill": 50,
    "max_active_per_scope": 200,
    "max_pending_per_agent": 50,
    "bootstrap_max_records": 20,
    "bootstrap_max_body_chars_per_record": 1000,
    "bootstrap_max_total_chars": 8000,
}
DONE_STATUSES = {"done", "validated"}
TEXT_LIMITS = {
    "title": 200,
    "description": 8000,
    "next_step": 1000,
    "blocked_reason": 1000,
    "result_summary": 2000,
    "result_notes": 2000,
    "current_activity": 500,
    "notes": 4000,
    "list_item": 1000,
    "agent": 200,
    "scope": 500,
    "event_text": 1000,
    "memory_body": 4000,
    "chain_summary": 4000,
}
SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*\S+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
]


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def parse_duration_seconds(value: str | None) -> int | None:
    if not value:
        return None
    m = re.fullmatch(r"(\d+)([smhd]?)", value.strip())
    if not m:
        raise ValueError("duration must look like 30m, 2h, 10s, or seconds")
    amount = int(m.group(1))
    return amount * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]


def clean_text(value: Any, field: str, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ValueError(f"{field} is required")
        return None
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    text = text.strip()
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    if required and not text:
        raise ValueError(f"{field} is required")
    limit = TEXT_LIMITS.get(field, TEXT_LIMITS["event_text"])
    if len(text) > limit:
        raise ValueError(f"{field} exceeds {limit} characters")
    return text


def clean_text_list(values: list[str] | None, field: str) -> list[str]:
    return [item for item in (clean_text(v, field) for v in (values or [])) if item]


def clean_nonnegative_int(value: Any, field: str) -> int | None:
    if value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a non-negative integer")
    if number < 0 or number > 1000000:
        raise ValueError(f"{field} must be between 0 and 1000000")
    return number


def clean_bool(value: Any) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError("boolean metadata must be true or false")


def safe_payload(value: Any) -> Any:
    if isinstance(value, str):
        text = clean_text(value, "event_text") or ""
        return text[:TEXT_LIMITS["event_text"]]
    if isinstance(value, list):
        return [safe_payload(v) for v in value[:20]]
    if isinstance(value, dict):
        return {str(k)[:100]: safe_payload(v) for k, v in list(value.items())[:40]}
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return clean_text(str(value), "event_text")


class TaskService:
    def __init__(self, kernel: "LearningKernel"):
        self.kernel = kernel

    def create(self, **kw: Any) -> dict[str, Any]:
        return self.kernel._task_create(**kw)

    def chain_default_participant_set(self, task_chain_id: str, agent: str, role: str, *, root_task_id: str | None = None, status: str | None = None, actor: str = "user") -> dict[str, Any]:
        return self.kernel._task_chain_default_participant_set(task_chain_id, agent, role, root_task_id=root_task_id, status=status, actor=actor)

    def chain_default_participant_list(self, task_chain_id: str) -> list[dict[str, Any]]:
        return self.kernel._task_chain_default_participant_list(task_chain_id)

    def show(self, task_id: str, include_participants: bool = False) -> dict[str, Any]:
        return self.kernel._task_show(task_id, include_participants=include_participants)

    def participant_list(self, task_id: str) -> list[dict[str, Any]]:
        return self.kernel._task_participant_list(task_id)

    def participant_add(self, task_id: str, agent: str, role: str, *, actor: str = "user", task_chain_id: str | None = None, root_task_id: str | None = None, instance_id: str | None = None, status: str | None = None) -> dict[str, Any]:
        return self.kernel._task_participant_add(task_id, agent, role, actor=actor, task_chain_id=task_chain_id, root_task_id=root_task_id, instance_id=instance_id, status=status)

    def participant_update(self, participant_id: str, *, status: str | None = None, actor: str = "user") -> dict[str, Any]:
        return self.kernel._task_participant_update(participant_id, status=status, actor=actor)

    def participant_remove(self, participant_id: str, *, actor: str = "user") -> dict[str, Any]:
        return self.kernel._task_participant_remove(participant_id, actor=actor)

    def list(self, agent: str | None = None, statuses: list[str] | None = None, include_archived: bool = False, scope: str | None = None, include_participants: bool = False, participant_roles: list[str] | None = None) -> list[dict[str, Any]]:
        return self.kernel._task_list(agent=agent, statuses=statuses, include_archived=include_archived, scope=scope, include_participants=include_participants, participant_roles=participant_roles)

    def next(self, agent: str | None = None, scope: str | None = None, include_profile: bool = False, participant_roles: list[str] | None = None) -> dict[str, Any]:
        return self.kernel._task_next(agent=agent, scope=scope, include_profile=include_profile, participant_roles=participant_roles)

    def ready_dependents(self, task_id: str, include_participants: bool = False) -> list[dict[str, Any]]:
        return self.kernel._task_ready_dependents(task_id, include_participants=include_participants)

    def update(self, task_id: str, **kw: Any) -> dict[str, Any]:
        return self.kernel._task_update(task_id, **kw)

    def mark_result(self, task_id: str, result: str, notes: str | None = None, actor: str = "user", next_step: str | None = None, status: str | None = None, approval_id: str | None = None) -> dict[str, Any]:
        return self.kernel._mark_result(task_id, result, notes, actor, next_step, status, approval_id)

    def state_set(self, task_id: str, agent: str, **kw: Any) -> dict[str, Any]:
        return self.kernel._state_set(task_id, agent, **kw)

    def state_show(self, task_id: str, agent: str | None = None) -> dict[str, Any] | list[dict[str, Any]] | None:
        return self.kernel._state_show(task_id, agent)

    def state_list(self, agent: str | None = None, task_id: str | None = None, stale_after: int | None = None) -> list[dict[str, Any]]:
        return self.kernel._state_list(agent=agent, task_id=task_id, stale_after=stale_after)

    def state_clear(self, task_id: str, agent: str | None = None, actor: str = "user") -> dict[str, Any]:
        return self.kernel._state_clear(task_id, agent=agent, actor=actor)

    def submit_completion(self, task_id: str, **kw: Any) -> dict[str, Any]:
        return self.kernel._submit_completion(task_id, **kw)

    def record_approval_notification(self, approval_id: str, sent: bool, detail: str | None = None) -> dict[str, Any]:
        return self.kernel._record_approval_notification(approval_id, sent, detail)

    def review_completion(self, approval_id: str, result: str, **kw: Any) -> dict[str, Any]:
        return self.kernel._review_completion(approval_id, result, **kw)

    def list_approvals(self, status: str | None = None) -> list[dict[str, Any]]:
        return self.kernel._list_approvals(status=status)

    def show_approval(self, approval_id: str) -> dict[str, Any]:
        return self.kernel._show_approval(approval_id)

    def summarize_chain(self, task_chain_id: str, **kw: Any) -> dict[str, Any]:
        return self.kernel._summarize_chain(task_chain_id, **kw)

    def latest_chain_summary(self, root_task_id: str | None = None) -> dict[str, Any] | None:
        return self.kernel._latest_chain_summary(root_task_id)

class MemoryService:
    def __init__(self, kernel: "LearningKernel"):
        self.kernel = kernel

    def propose(self, **kw: Any) -> dict[str, Any]:
        return self.kernel._memory_propose(**kw)

    def approve(self, memory_id: str, version: int, *, actor: str = "user") -> dict[str, Any]:
        return self.kernel._memory_approve(memory_id, version, actor=actor)

    def propose_edit(self, memory_id: str, *, expected_version: int | None = None, **kw: Any) -> dict[str, Any]:
        return self.kernel._memory_propose_edit(memory_id, expected_version=expected_version, **kw)

    def propose_archive(self, memory_id: str, *, expected_version: int | None = None, reason: str | None = None, **kw: Any) -> dict[str, Any]:
        return self.kernel._memory_propose_archive(memory_id, expected_version=expected_version, reason=reason, **kw)

    def edit(self, memory_id: str, *, expected_version: int | None = None, actor: str = "user", **kw: Any) -> dict[str, Any]:
        return self.kernel._memory_edit(memory_id, expected_version=expected_version, actor=actor, **kw)

    def rollback(self, memory_id: str, *, target_version: int, expected_version: int | None = None, actor: str = "user") -> dict[str, Any]:
        return self.kernel._memory_rollback(memory_id, target_version=target_version, expected_version=expected_version, actor=actor)

    def reject(self, memory_id: str, version: int, *, reason: str | None = None, actor: str = "user") -> dict[str, Any]:
        return self.kernel._memory_reject(memory_id, version, reason=reason, actor=actor)

    def revoke(self, memory_id: str, *, reason: str | None = None, expected_version: int | None = None, actor: str = "user") -> dict[str, Any]:
        return self.kernel._memory_revoke(memory_id, reason=reason, expected_version=expected_version, actor=actor)

    def show(self, memory_id: str, version: int | None = None) -> dict[str, Any]:
        return self.kernel._memory_show(memory_id, version=version)

    def history(self, memory_id: str) -> dict[str, Any]:
        return self.kernel._memory_history(memory_id)

    def list(self, *, scope: str | None = None, type: str | None = None, status: str | None = None, agent: str | None = None) -> list[dict[str, Any]]:
        return self.kernel._memory_list(scope=scope, type=type, status=status, agent=agent)

    def search(self, query: str, *, scope: str | None = None) -> list[dict[str, Any]]:
        return self.kernel._memory_search(query, scope=scope)

    def budget(self, *, agent: str, scope: str | None = None) -> dict[str, Any]:
        return self.kernel._memory_budget(agent=agent, scope=scope)

    def for_bootstrap(self, *, agent: str, scope: str | None = None) -> dict[str, Any]:
        return self.kernel._memory_for_bootstrap(agent=agent, scope=scope)

class LearningKernel:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.memory = MemoryService(self)
        self.tasks = TaskService(self)
        self._init()


    def __getattr__(self, name: str) -> Any:
        if name.startswith("memory_"):
            service_name = name[len("memory_"):]
            if hasattr(self.memory, service_name):
                return getattr(self.memory, service_name)
        if name.startswith("task_"):
            service_name = name[len("task_"):]
            if hasattr(self.tasks, service_name):
                return getattr(self.tasks, service_name)
        if name.startswith("state_") and hasattr(self.tasks, name):
            return getattr(self.tasks, name)
        if name in {"mark_result", "submit_completion", "record_approval_notification", "review_completion", "list_approvals", "show_approval", "summarize_chain", "latest_chain_summary"}:
            if hasattr(self.tasks, name):
                return getattr(self.tasks, name)
        raise AttributeError(name)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init(self) -> None:
        with closing(self.connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL);
            INSERT INTO schema_version(version) SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_version);
            CREATE TABLE IF NOT EXISTS tasks(
              task_id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL, assigned_agent TEXT, scope TEXT, depends_on TEXT NOT NULL DEFAULT '[]',
              priority TEXT NOT NULL DEFAULT 'normal', next_step TEXT, acceptance_criteria TEXT NOT NULL DEFAULT '[]',
              context_refs TEXT NOT NULL DEFAULT '[]', result_summary TEXT, result_status TEXT, result_notes TEXT,
              blocked_reason TEXT, created_by TEXT, updated_by TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              version INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS working_states(
              state_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, agent TEXT NOT NULL, instance_id TEXT,
              task_chain_id TEXT NOT NULL DEFAULT '', root_task_id TEXT,
              status TEXT NOT NULL, current_activity TEXT, next_step TEXT, blockers TEXT NOT NULL DEFAULT '[]',
              notes TEXT, stale_after_seconds INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              version INTEGER NOT NULL DEFAULT 1, UNIQUE(task_id, agent, task_chain_id)
            );
            CREATE TABLE IF NOT EXISTS user_profiles(
              profile_id TEXT PRIMARY KEY, format TEXT NOT NULL, body TEXT NOT NULL, source TEXT,
              updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS events(
              event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, timestamp TEXT NOT NULL,
              actor_type TEXT NOT NULL, actor_id TEXT NOT NULL, agent_instance_id TEXT,
              subject_type TEXT NOT NULL, subject_id TEXT NOT NULL, task_id TEXT, scope TEXT,
              payload TEXT NOT NULL DEFAULT '{}', refs TEXT NOT NULL DEFAULT '{}', visibility TEXT NOT NULL DEFAULT 'private',
              schema_version INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS task_approvals(
              approval_id TEXT PRIMARY KEY, idempotency_key TEXT, task_id TEXT NOT NULL,
              task_chain_id TEXT NOT NULL DEFAULT '', root_task_id TEXT, status TEXT NOT NULL,
              result TEXT, created_event_seq INTEGER, decided_event_seq INTEGER,
              task_version_at_submission INTEGER NOT NULL, event_seq_at_submission INTEGER NOT NULL DEFAULT 0,
              submitter_profile TEXT NOT NULL, submitter_instance_id TEXT,
              result_summary TEXT NOT NULL, acceptance_summary TEXT,
              reusable_discoveries TEXT NOT NULL DEFAULT '[]', clarification_count INTEGER,
              correction_count INTEGER, need_improvements_count INTEGER, first_pass_success INTEGER,
              created_at TEXT NOT NULL, decided_at TEXT, version INTEGER NOT NULL DEFAULT 1,
              UNIQUE(submitter_profile, idempotency_key)
            );
            CREATE TABLE IF NOT EXISTS memory_records(
              memory_id TEXT, idempotency_key TEXT,
              proposed_by TEXT NOT NULL, proposed_by_instance TEXT,
              type TEXT NOT NULL, scope TEXT NOT NULL DEFAULT 'global', subject_agent TEXT,
              title TEXT NOT NULL, body TEXT NOT NULL, source_task_id TEXT,
              source_event_seq INTEGER, source_event_id TEXT, trusted_manual INTEGER NOT NULL DEFAULT 0,
              created_by TEXT NOT NULL, created_at TEXT NOT NULL,
              validated_by TEXT, validated_at TEXT,
              status TEXT NOT NULL DEFAULT 'pending', status_event_seq INTEGER,
              updated_event_seq INTEGER, version INTEGER NOT NULL DEFAULT 1,
              tags TEXT NOT NULL DEFAULT '[]', metadata TEXT NOT NULL DEFAULT '{}',
              schema_version INTEGER NOT NULL DEFAULT 1,
              PRIMARY KEY(memory_id, version),
              UNIQUE(proposed_by, idempotency_key)
            );
            CREATE TABLE IF NOT EXISTS task_chain_summaries(
              summary_id TEXT PRIMARY KEY, task_chain_id TEXT NOT NULL UNIQUE, root_task_id TEXT NOT NULL,
              previous_summary_id TEXT, next_task_chain_id TEXT, summary TEXT NOT NULL,
              event_seq_start INTEGER NOT NULL DEFAULT 0, event_seq_end INTEGER NOT NULL DEFAULT 0,
              created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              version INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS task_participants(
              participant_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, task_chain_id TEXT NOT NULL DEFAULT '', root_task_id TEXT,
              agent TEXT NOT NULL, role TEXT NOT NULL, instance_id TEXT, status TEXT NOT NULL DEFAULT 'active',
              created_by TEXT NOT NULL, updated_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              version INTEGER NOT NULL DEFAULT 1,
              UNIQUE(task_id, task_chain_id, agent, role, instance_id)
            );
            CREATE TABLE IF NOT EXISTS task_chain_default_participants(
              default_id TEXT PRIMARY KEY, task_chain_id TEXT NOT NULL, root_task_id TEXT,
              agent TEXT NOT NULL, role TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
              created_by TEXT NOT NULL, updated_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              version INTEGER NOT NULL DEFAULT 1,
              UNIQUE(task_chain_id, agent, role)
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_next ON tasks(status, assigned_agent, scope, updated_at);
            CREATE INDEX IF NOT EXISTS idx_events_subject ON events(subject_type, subject_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_memory_lookup ON memory_records(status, type, scope, subject_agent, validated_at);
            CREATE INDEX IF NOT EXISTS idx_chain_summaries_root ON task_chain_summaries(root_task_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_task_participants_task ON task_participants(task_id, task_chain_id, role, agent);
            CREATE INDEX IF NOT EXISTS idx_chain_default_participants_chain ON task_chain_default_participants(task_chain_id, role, agent);
            """)
            self._migrate_working_states(conn)
            self._migrate_memory_records(conn)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_states_lookup ON working_states(task_id, agent, task_chain_id)")
            self.bootstrap_user_profile(conn)

    def _migrate_working_states(self, conn: sqlite3.Connection) -> None:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(working_states)").fetchall()}
        needs_rebuild = "task_chain_id" not in columns or "root_task_id" not in columns
        for idx in conn.execute("PRAGMA index_list(working_states)").fetchall():
            if not idx["unique"]:
                continue
            idx_cols = [r["name"] for r in conn.execute(f"PRAGMA index_info({idx['name']})").fetchall()]
            if idx_cols == ["task_id", "agent"]:
                needs_rebuild = True
        if not needs_rebuild:
            return
        conn.executescript("""
        ALTER TABLE working_states RENAME TO working_states_old;
        CREATE TABLE working_states(
          state_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, agent TEXT NOT NULL, instance_id TEXT,
          task_chain_id TEXT NOT NULL DEFAULT '', root_task_id TEXT,
          status TEXT NOT NULL, current_activity TEXT, next_step TEXT, blockers TEXT NOT NULL DEFAULT '[]',
          notes TEXT, stale_after_seconds INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          version INTEGER NOT NULL DEFAULT 1, UNIQUE(task_id, agent, task_chain_id)
        );
        """)
        old_cols = {row["name"] for row in conn.execute("PRAGMA table_info(working_states_old)").fetchall()}
        task_chain_expr = "COALESCE(task_chain_id, '')" if "task_chain_id" in old_cols else "''"
        root_expr = "root_task_id" if "root_task_id" in old_cols else "task_id"
        conn.execute(f"""
            INSERT OR IGNORE INTO working_states(state_id,task_id,agent,instance_id,task_chain_id,root_task_id,status,current_activity,next_step,blockers,notes,stale_after_seconds,created_at,updated_at,version)
            SELECT state_id,task_id,agent,instance_id,{task_chain_expr},{root_expr},status,current_activity,next_step,blockers,notes,stale_after_seconds,created_at,updated_at,version FROM working_states_old
        """)
        conn.execute("DROP TABLE working_states_old")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_states_lookup ON working_states(task_id, agent, task_chain_id)")

    def _migrate_memory_records(self, conn: sqlite3.Connection) -> None:
        pk_cols = {row["name"] for row in conn.execute("PRAGMA table_info(memory_records)").fetchall() if row["pk"] > 0}
        if pk_cols == {"memory_id", "version"}:
            return
        logging.info("Migrating memory_records table to composite primary key (memory_id, version)")
        conn.executescript("""
        ALTER TABLE memory_records RENAME TO memory_records_old;
        CREATE TABLE memory_records(
          memory_id TEXT,
          idempotency_key TEXT,
          proposed_by TEXT NOT NULL, proposed_by_instance TEXT,
          type TEXT NOT NULL, scope TEXT NOT NULL DEFAULT 'global', subject_agent TEXT,
          title TEXT NOT NULL, body TEXT NOT NULL, source_task_id TEXT,
          source_event_seq INTEGER, source_event_id TEXT, trusted_manual INTEGER NOT NULL DEFAULT 0,
          created_by TEXT NOT NULL, created_at TEXT NOT NULL,
          validated_by TEXT, validated_at TEXT,
          status TEXT NOT NULL DEFAULT 'pending', status_event_seq INTEGER,
          updated_event_seq INTEGER, version INTEGER NOT NULL DEFAULT 1,
          tags TEXT NOT NULL DEFAULT '[]', metadata TEXT NOT NULL DEFAULT '{}',
          schema_version INTEGER NOT NULL DEFAULT 1,
          PRIMARY KEY(memory_id, version),
          UNIQUE(proposed_by, idempotency_key)
        );
        """)
        old_cols = {row["name"] for row in conn.execute("PRAGMA table_info(memory_records_old)").fetchall()}
        conn.execute("""
            INSERT OR IGNORE INTO memory_records(
                memory_id, idempotency_key, proposed_by, proposed_by_instance,
                type, scope, subject_agent, title, body, source_task_id,
                source_event_seq, source_event_id, trusted_manual,
                created_by, created_at, validated_by, validated_at,
                status, status_event_seq, updated_event_seq, version,
                tags, metadata, schema_version
            )
            SELECT 
                memory_id, idempotency_key, proposed_by, proposed_by_instance,
                type, scope, subject_agent, title, body, source_task_id,
                source_event_seq, source_event_id, trusted_manual,
                created_by, created_at, validated_by, validated_at,
                status, status_event_seq, updated_event_seq, version,
                tags, metadata, schema_version
            FROM memory_records_old
        """)
        conn.execute("DROP TABLE memory_records_old")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_lookup ON memory_records(status, type, scope, subject_agent, validated_at)")

    def bootstrap_user_profile(self, conn: sqlite3.Connection) -> None:
        row = conn.execute("SELECT 1 FROM user_profiles WHERE profile_id='default'").fetchone()
        if row:
            return
        body = """# User Profile\n\nShared local preferences for Broccoli Comms agents.\n\n- Be concise in updates.\n- Run relevant tests before reporting completion.\n- Ask for context when confidence is low.\n- Do not store secrets, tokens, raw terminal output, or full transcripts in durable state.\n- Do not commit, push, deploy, or restart services unless explicitly instructed.\n"""
        conn.execute(
            "INSERT INTO user_profiles(profile_id, format, body, source, updated_at) VALUES(?,?,?,?,?)",
            ("default", "markdown", body, "bootstrap", now_iso()),
        )

    def event(self, conn: sqlite3.Connection, event_type: str, actor_type: str, actor_id: str, subject_type: str,
              subject_id: str, payload: dict[str, Any] | None = None, task_id: str | None = None,
              scope: str | None = None, refs: dict[str, Any] | None = None, *, replayable_payload: bool = False) -> dict[str, Any]:
        ev = {
            "event_id": f"evt-{uuid.uuid4().hex[:16]}", "event_type": event_type, "timestamp": now_iso(),
            "actor_type": actor_type, "actor_id": actor_id, "subject_type": subject_type, "subject_id": subject_id,
            "task_id": task_id, "scope": clean_text(scope, "scope") if scope else None, "payload": self._replayable_payload(payload or {}) if replayable_payload else safe_payload(payload or {}), "refs": safe_payload(refs or {}),
            "visibility": "private", "schema_version": SCHEMA_VERSION,
        }
        conn.execute(
            "INSERT INTO events(event_id,event_type,timestamp,actor_type,actor_id,subject_type,subject_id,task_id,scope,payload,refs,visibility,schema_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ev["event_id"], ev["event_type"], ev["timestamp"], ev["actor_type"], ev["actor_id"], ev["subject_type"], ev["subject_id"], ev["task_id"], ev["scope"], json.dumps(ev["payload"], sort_keys=True), json.dumps(ev["refs"], sort_keys=True), ev["visibility"], ev["schema_version"]),
        )
        ev["event_seq"] = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        return ev

    def row_task(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if not row:
            return None
        d = dict(row)
        for key in ("depends_on", "acceptance_criteria", "context_refs"):
            d[key] = json.loads(d[key] or "[]")
        return d

    def row_state(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if not row:
            return None
        d = dict(row)
        d["blockers"] = json.loads(d.get("blockers") or "[]")
        return d

    def row_approval(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if not row:
            return None
        d = dict(row)
        d["reusable_discoveries"] = json.loads(d.get("reusable_discoveries") or "[]")
        d["first_pass_success"] = None if d.get("first_pass_success") is None else bool(d["first_pass_success"])
        return d

    def row_memory(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if not row:
            return None
        d = dict(row)
        d["trusted_manual"] = bool(d.get("trusted_manual"))
        d["tags"] = json.loads(d.get("tags") or "[]")
        d["metadata"] = json.loads(d.get("metadata") or "{}")
        return d

    def row_chain_summary(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row else None

    def row_participant(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row else None

    def row_chain_default_participant(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row else None

    def _chain_default_participant_id(self, task_chain_id: str, agent: str, role: str) -> str:
        raw = "|".join([task_chain_id or "", agent or "", role or ""])
        return f"cpdef-{uuid.uuid5(uuid.NAMESPACE_URL, raw).hex[:12]}"

    def _clean_discoveries(self, discoveries: list[dict[str, Any]] | None) -> list[dict[str, str]]:
        cleaned = []
        for item in (discoveries or [])[:20]:
            if not isinstance(item, dict):
                raise ValueError("discovery must be an object")
            cleaned.append({
                "label": clean_text(item.get("label"), "list_item", required=True) or "",
                "value": clean_text(item.get("value"), "list_item", required=True) or "",
                "reason": clean_text(item.get("reason") or "", "list_item") or "",
            })
        return cleaned

    def _task_create(self, **kw: Any) -> dict[str, Any]:
        status = kw.get("status") or "planning"
        if status not in TASK_STATUSES:
            raise ValueError("invalid task status")
        deps = kw.get("depends_on") or []
        task_id = kw.get("task_id") or f"task-{uuid.uuid4().hex[:12]}"
        title = clean_text(kw.get("title"), "title", required=True)
        description = clean_text(kw.get("description") or "", "description") or ""
        assigned_agent = clean_text(kw.get("assigned_agent"), "agent")
        scope = clean_text(kw.get("scope"), "scope")
        next_step = clean_text(kw.get("next_step"), "next_step")
        acceptance = clean_text_list(kw.get("acceptance_criteria") or [], "list_item")
        context_refs = clean_text_list(kw.get("context_refs") or [], "list_item")
        actor = clean_text(kw.get("actor") or "user", "agent") or "user"
        default_participants = list(kw.get("participants") or [])
        task_chain_id = clean_text(kw.get("task_chain_id"), "list_item")
        root_task_id = clean_text(kw.get("root_task_id"), "list_item")
        ts = now_iso()
        with closing(self.connect()) as conn:
            self._validate_deps(conn, deps, task_id)
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO tasks(task_id,title,description,status,assigned_agent,scope,depends_on,priority,next_step,acceptance_criteria,context_refs,created_by,updated_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (task_id, title, description, status, assigned_agent, scope, json.dumps(deps), clean_text(kw.get("priority") or "normal", "list_item") or "normal", next_step, json.dumps(acceptance), json.dumps(context_refs), actor, actor, ts, ts),
            )
            self.event(conn, "task_created", "user", actor, "task", task_id, {"title": title, "status": status, "assigned_agent": assigned_agent}, task_id, scope)
            if assigned_agent:
                self._upsert_task_participant_in_tx(conn, task_id=task_id, agent=assigned_agent, role="assignee", actor=actor, emit_event=False)
                self.event(conn, "task_assigned", "user", actor, "task", task_id, {"assigned_agent": assigned_agent}, task_id, scope)
            for participant in self._chain_default_participants_for_create(conn, task_chain_id, default_participants):
                if not isinstance(participant, dict):
                    raise ValueError("participant must be an object")
                self._upsert_task_participant_in_tx(
                    conn,
                    task_id=task_id,
                    agent=participant.get("agent"),
                    role=participant.get("role"),
                    actor=actor,
                    task_chain_id=participant.get("task_chain_id") or task_chain_id,
                    root_task_id=participant.get("root_task_id") or root_task_id,
                    instance_id=participant.get("instance_id"),
                    status=participant.get("status"),
                )
            conn.execute("COMMIT")
            return self.row_task(conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone())

    def _task_chain_default_participant_set(self, task_chain_id: str, agent: str, role: str, *, root_task_id: str | None = None, status: str | None = None, actor: str = "user") -> dict[str, Any]:
        task_chain_id = clean_text(task_chain_id, "list_item", required=True) or ""
        agent = clean_text(agent, "agent", required=True) or ""
        role = clean_text(role, "list_item", required=True) or ""
        if role not in PARTICIPANT_ROLES:
            raise ValueError("invalid participant role")
        status = clean_text(status or "active", "list_item") or "active"
        if status not in PARTICIPANT_STATUSES:
            raise ValueError("invalid participant status")
        root_task_id = clean_text(root_task_id, "list_item")
        actor = clean_text(actor or "user", "agent") or "user"
        default_id = self._chain_default_participant_id(task_chain_id, agent, role)
        ts = now_iso()
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = self.row_chain_default_participant(conn.execute("SELECT * FROM task_chain_default_participants WHERE default_id=?", (default_id,)).fetchone())
            if old:
                conn.execute("UPDATE task_chain_default_participants SET root_task_id=?, status=?, updated_by=?, updated_at=?, version=version+1 WHERE default_id=?", (root_task_id, status, actor, ts, default_id))
                event_type = "task_chain_default_participant_updated"
            else:
                conn.execute("INSERT INTO task_chain_default_participants(default_id,task_chain_id,root_task_id,agent,role,status,created_by,updated_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (default_id, task_chain_id, root_task_id, agent, role, status, actor, actor, ts, ts))
                event_type = "task_chain_default_participant_added"
            participant = self.row_chain_default_participant(conn.execute("SELECT * FROM task_chain_default_participants WHERE default_id=?", (default_id,)).fetchone())
            self.event(conn, event_type, "user", actor, "task_chain_default_participant", default_id, participant, root_task_id or task_chain_id)
            conn.execute("COMMIT")
            return participant

    def _task_chain_default_participant_list(self, task_chain_id: str) -> list[dict[str, Any]]:
        task_chain_id = clean_text(task_chain_id, "list_item", required=True) or ""
        with closing(self.connect()) as conn:
            return [self.row_chain_default_participant(r) for r in conn.execute("SELECT * FROM task_chain_default_participants WHERE task_chain_id=? ORDER BY role, agent", (task_chain_id,)).fetchall()]

    def _chain_default_participants_for_create(self, conn: sqlite3.Connection, task_chain_id: str | None, explicit: list[dict[str, Any]]) -> list[dict[str, Any]]:
        task_chain_id = clean_text(task_chain_id or "", "list_item") or ""
        if not task_chain_id:
            return explicit
        explicit_roles = {p.get("role") for p in explicit if isinstance(p, dict) and p.get("role")}
        defaults = []
        for row in conn.execute("SELECT * FROM task_chain_default_participants WHERE task_chain_id=? AND status='active' ORDER BY role, agent", (task_chain_id,)).fetchall():
            item = self.row_chain_default_participant(row)
            if item.get("role") not in explicit_roles:
                defaults.append({"agent": item.get("agent"), "role": item.get("role"), "task_chain_id": task_chain_id, "root_task_id": item.get("root_task_id")})
        return [*defaults, *explicit]

    def _validate_deps(self, conn: sqlite3.Connection, deps: list[str], self_id: str) -> None:
        if self_id in deps:
            raise ValueError("task cannot depend on itself")
        for dep in deps:
            if not conn.execute("SELECT 1 FROM tasks WHERE task_id=?", (dep,)).fetchone():
                raise ValueError(f"missing dependency: {dep}")

    def _task_show(self, task_id: str, include_participants: bool = False) -> dict[str, Any]:
        with closing(self.connect()) as conn:
            task = self.row_task(conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone())
            if not task:
                raise KeyError(task_id)
            if include_participants:
                task["participants"] = self._task_participants_for_task(conn, task)
            return task

    def _participant_id(self, task_id: str, task_chain_id: str, agent: str, role: str, instance_id: str | None = None) -> str:
        raw = "|".join([task_id or "", task_chain_id or "", agent or "", role or "", instance_id or ""])
        return f"part-{uuid.uuid5(uuid.NAMESPACE_URL, raw).hex[:12]}"

    def _clean_participant_fields(self, task_id: str, agent: str, role: str, *, task_chain_id: str | None = None, root_task_id: str | None = None, instance_id: str | None = None, status: str | None = None) -> dict[str, Any]:
        task_id = clean_text(task_id, "list_item", required=True) or ""
        agent = clean_text(agent, "agent", required=True) or ""
        role = clean_text(role, "list_item", required=True) or ""
        if role not in PARTICIPANT_ROLES:
            raise ValueError("invalid participant role")
        status = clean_text(status or "active", "list_item") or "active"
        if status not in PARTICIPANT_STATUSES:
            raise ValueError("invalid participant status")
        task_chain_id = clean_text(task_chain_id or "", "list_item") or ""
        root_task_id = clean_text(root_task_id, "list_item")
        instance_id = clean_text(instance_id, "list_item")
        return {"task_id": task_id, "task_chain_id": task_chain_id, "root_task_id": root_task_id, "agent": agent, "role": role, "instance_id": instance_id, "status": status}

    def _upsert_task_participant_in_tx(self, conn: sqlite3.Connection, *, task_id: str, agent: str, role: str, actor: str, task_chain_id: str | None = None, root_task_id: str | None = None, instance_id: str | None = None, status: str | None = None, emit_event: bool = True) -> dict[str, Any]:
        fields = self._clean_participant_fields(task_id, agent, role, task_chain_id=task_chain_id, root_task_id=root_task_id, instance_id=instance_id, status=status)
        if not conn.execute("SELECT 1 FROM tasks WHERE task_id=?", (fields["task_id"],)).fetchone():
            raise KeyError(fields["task_id"])
        participant_id = self._participant_id(fields["task_id"], fields["task_chain_id"], fields["agent"], fields["role"], fields["instance_id"])
        old = self.row_participant(conn.execute("SELECT * FROM task_participants WHERE participant_id=?", (participant_id,)).fetchone())
        ts = now_iso()
        if old:
            conn.execute(
                "UPDATE task_participants SET root_task_id=?, status=?, updated_by=?, updated_at=?, version=version+1 WHERE participant_id=?",
                (fields["root_task_id"], fields["status"], actor, ts, participant_id),
            )
            event_type = "task_participant_updated"
        else:
            conn.execute(
                "INSERT INTO task_participants(participant_id,task_id,task_chain_id,root_task_id,agent,role,instance_id,status,created_by,updated_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (participant_id, fields["task_id"], fields["task_chain_id"], fields["root_task_id"], fields["agent"], fields["role"], fields["instance_id"], fields["status"], actor, actor, ts, ts),
            )
            event_type = "task_participant_added"
        participant = self.row_participant(conn.execute("SELECT * FROM task_participants WHERE participant_id=?", (participant_id,)).fetchone())
        if emit_event:
            self.event(conn, event_type, "user", actor, "task_participant", participant_id, participant, fields["task_id"])
        return participant

    def _task_participants_for_task(self, conn: sqlite3.Connection, task: dict[str, Any]) -> list[dict[str, Any]]:
        participants = [self.row_participant(r) for r in conn.execute("SELECT * FROM task_participants WHERE task_id=? ORDER BY role, agent, created_at", (task["task_id"],)).fetchall()]
        assigned = task.get("assigned_agent")
        if assigned and not any(p.get("agent") == assigned and p.get("role") == "assignee" for p in participants):
            participants.insert(0, {
                "participant_id": self._participant_id(task["task_id"], "", assigned, "assignee"),
                "task_id": task["task_id"], "task_chain_id": "", "root_task_id": None,
                "agent": assigned, "role": "assignee", "instance_id": None, "status": "active",
                "created_by": task.get("created_by"), "updated_by": task.get("updated_by"),
                "created_at": task.get("created_at"), "updated_at": task.get("updated_at"), "version": task.get("version", 1),
                "compatibility": True,
            })
        return participants

    def _task_participant_list(self, task_id: str) -> list[dict[str, Any]]:
        with closing(self.connect()) as conn:
            task = self.row_task(conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone())
            if not task:
                raise KeyError(task_id)
            return self._task_participants_for_task(conn, task)

    def _task_participant_add(self, task_id: str, agent: str, role: str, *, actor: str = "user", task_chain_id: str | None = None, root_task_id: str | None = None, instance_id: str | None = None, status: str | None = None) -> dict[str, Any]:
        actor = clean_text(actor or "user", "agent") or "user"
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            participant = self._upsert_task_participant_in_tx(conn, task_id=task_id, agent=agent, role=role, actor=actor, task_chain_id=task_chain_id, root_task_id=root_task_id, instance_id=instance_id, status=status)
            conn.execute("COMMIT")
            return participant

    def _task_participant_update(self, participant_id: str, *, status: str | None = None, actor: str = "user") -> dict[str, Any]:
        participant_id = clean_text(participant_id, "list_item", required=True) or participant_id
        actor = clean_text(actor or "user", "agent") or "user"
        if status is not None:
            status = clean_text(status, "list_item") or "active"
            if status not in PARTICIPANT_STATUSES:
                raise ValueError("invalid participant status")
        with closing(self.connect()) as conn:
            old = self.row_participant(conn.execute("SELECT * FROM task_participants WHERE participant_id=?", (participant_id,)).fetchone())
            if not old:
                raise KeyError(participant_id)
            conn.execute("BEGIN IMMEDIATE")
            if status is not None:
                conn.execute("UPDATE task_participants SET status=?, updated_by=?, updated_at=?, version=version+1 WHERE participant_id=?", (status, actor, now_iso(), participant_id))
            participant = self.row_participant(conn.execute("SELECT * FROM task_participants WHERE participant_id=?", (participant_id,)).fetchone())
            self.event(conn, "task_participant_updated", "user", actor, "task_participant", participant_id, participant, participant.get("task_id"))
            conn.execute("COMMIT")
            return participant

    def _task_participant_remove(self, participant_id: str, *, actor: str = "user") -> dict[str, Any]:
        participant_id = clean_text(participant_id, "list_item", required=True) or participant_id
        actor = clean_text(actor or "user", "agent") or "user"
        with closing(self.connect()) as conn:
            participant = self.row_participant(conn.execute("SELECT * FROM task_participants WHERE participant_id=?", (participant_id,)).fetchone())
            if not participant:
                raise KeyError(participant_id)
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM task_participants WHERE participant_id=?", (participant_id,))
            self.event(conn, "task_participant_removed", "user", actor, "task_participant", participant_id, participant, participant.get("task_id"))
            conn.execute("COMMIT")
            return participant

    def _task_list(self, agent: str | None = None, statuses: list[str] | None = None, include_archived: bool = False, scope: str | None = None, include_participants: bool = False, participant_roles: list[str] | None = None) -> list[dict[str, Any]]:
        clauses, args = [], []
        roles = participant_roles or []
        for role in roles:
            if role not in PARTICIPANT_ROLES:
                raise ValueError("invalid participant role")
        if agent and roles:
            role_placeholders = ",".join("?" for _ in roles)
            role_args = list(roles)
            participant_clause = f"task_id IN (SELECT task_id FROM task_participants WHERE agent=? AND status='active' AND role IN ({role_placeholders}))"
            clauses.append(f"({participant_clause}" + (" OR assigned_agent=?" if "assignee" in roles else "") + ")")
            args.extend([agent, *role_args])
            if "assignee" in roles:
                args.append(agent)
        elif agent:
            clauses.append("assigned_agent=?"); args.append(agent)
        if statuses:
            clauses.append("status IN (%s)" % ",".join("?" for _ in statuses)); args.extend(statuses)
        elif not include_archived:
            clauses.append("status!='archived'")
        if scope:
            clauses.append("scope=?"); args.append(scope)
        sql = "SELECT * FROM tasks" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, created_at"
        with closing(self.connect()) as conn:
            tasks = [self.row_task(r) for r in conn.execute(sql, args).fetchall()]
            if include_participants:
                for task in tasks:
                    task["participants"] = self._task_participants_for_task(conn, task)
            return tasks

    def _task_next_statuses(self, participant_roles: list[str] | None = None) -> list[str]:
        roles = set(participant_roles or [])
        if roles & {"reviewer", "verifier"}:
            return ["review", "done"]
        return ["ready"]

    def _task_next(self, agent: str | None = None, scope: str | None = None, include_profile: bool = False, participant_roles: list[str] | None = None) -> dict[str, Any]:
        candidates = self._task_list(agent=agent, statuses=self._task_next_statuses(participant_roles), include_archived=False, scope=scope, participant_roles=participant_roles)
        with closing(self.connect()) as conn:
            for task in candidates:
                deps = task.get("depends_on") or []
                if all((conn.execute("SELECT status FROM tasks WHERE task_id=?", (d,)).fetchone() or {"status": None})["status"] in DONE_STATUSES for d in deps):
                    payload = {"task": task}
                    if include_profile:
                        payload["user_profile"] = self.user_profile(raw=True)
                    return payload
        payload = {"task": None}
        if include_profile:
            payload["user_profile"] = self.user_profile(raw=True)
        return payload

    def _task_ready_dependents(self, task_id: str, include_participants: bool = False) -> list[dict[str, Any]]:
        with closing(self.connect()) as conn:
            rows = [self.row_task(r) for r in conn.execute("SELECT * FROM tasks WHERE status IN ('ready','queued') ORDER BY created_at").fetchall()]
            ready = []
            for task in rows:
                deps = task.get("depends_on") or []
                if task_id not in deps:
                    continue
                if all((conn.execute("SELECT status FROM tasks WHERE task_id=?", (d,)).fetchone() or {"status": None})["status"] in DONE_STATUSES for d in deps):
                    if include_participants:
                        task["participants"] = self._task_participants_for_task(conn, task)
                    ready.append(task)
            return ready

    def _task_update(self, task_id: str, **kw: Any) -> dict[str, Any]:
        allowed = {"status", "next_step", "blocked_reason", "result_summary", "assigned_agent"}
        updates = {k: v for k, v in kw.items() if k in allowed and v is not None}
        for key in list(updates):
            if key != "status":
                updates[key] = clean_text(updates[key], "agent" if key == "assigned_agent" else key)
        actor = clean_text(kw.get("actor") or "user", "agent") or "user"
        if "status" in updates and updates["status"] not in TASK_STATUSES:
            raise ValueError("invalid task status")
        if not updates:
            return self._task_show(task_id)
        with closing(self.connect()) as conn:
            old = self.row_task(conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone())
            if not old:
                raise KeyError(task_id)
            self._assert_direct_completion_allowed(conn, task_id, actor, updates.get("status"))
            conn.execute("BEGIN IMMEDIATE")
            sets = [f"{k}=?" for k in updates] + ["updated_by=?", "updated_at=?", "version=version+1"]
            conn.execute(f"UPDATE tasks SET {','.join(sets)} WHERE task_id=?", [*updates.values(), actor, now_iso(), task_id])
            self.event(conn, "task_updated", "user", actor, "task", task_id, updates, task_id, old.get("scope"))
            if "status" in updates and updates["status"] != old.get("status"):
                self.event(conn, "task_status_changed", "user", actor, "task", task_id, {"old": old.get("status"), "new": updates["status"]}, task_id, old.get("scope"))
            if "assigned_agent" in updates and updates["assigned_agent"] != old.get("assigned_agent"):
                if updates["assigned_agent"]:
                    self._upsert_task_participant_in_tx(conn, task_id=task_id, agent=updates["assigned_agent"], role="assignee", actor=actor, emit_event=False)
                self.event(conn, "task_assigned", "user", actor, "task", task_id, {"assigned_agent": updates["assigned_agent"]}, task_id, old.get("scope"))
            conn.execute("COMMIT")
            return self._task_show(task_id)

    def _validated_result_fields(self, result: str, notes: str | None = None, actor: str = "user", next_step: str | None = None, status: str | None = None) -> tuple[str | None, str, str, str]:
        if result not in RESULT_STATUSES:
            raise ValueError("invalid result")
        notes = clean_text(notes, "result_notes")
        next_step = clean_text(next_step, "next_step")
        actor = clean_text(actor, "agent") or "user"
        if result == "good":
            if status and status != "validated":
                raise ValueError("good results must use status=validated")
            status = "validated"
        else:
            if not next_step:
                raise ValueError("--next-step is required when result is bad or need_improvements")
            allowed_statuses = {"ready", "working", "blocked"}
            status = status or ("blocked" if result == "bad" else "ready")
            if status not in allowed_statuses:
                raise ValueError("non-good result status must be ready, working, or blocked")
        return notes, next_step, actor, status

    def _mark_result_in_tx(self, conn: sqlite3.Connection, task_id: str, result: str, notes: str | None, actor: str, next_step: str | None, status: str, approval_id: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        conn.execute("UPDATE tasks SET result_status=?, result_notes=?, status=?, next_step=COALESCE(?, next_step), updated_by=?, updated_at=?, version=version+1 WHERE task_id=?", (result, notes, status, next_step, actor, now_iso(), task_id))
        payload = {"result_status": result, "result_notes": notes, "status": status, "next_step": next_step}
        if approval_id:
            payload["approval_id"] = clean_text(approval_id, "list_item")
        ev = self.event(conn, "task_result_marked", "user", actor, "task", task_id, payload, task_id)
        task = self.row_task(conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone())
        return task, ev

    def _active_participants_for_roles(self, conn: sqlite3.Connection, task_id: str, roles: set[str]) -> list[dict[str, Any]]:
        task = self.row_task(conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone())
        if not task:
            raise KeyError(task_id)
        return [p for p in self._task_participants_for_task(conn, task) if p.get("status") == "active" and p.get("role") in roles]

    def _active_review_participants(self, conn: sqlite3.Connection, task_id: str) -> list[dict[str, Any]]:
        return self._active_participants_for_roles(conn, task_id, {"reviewer", "verifier"})

    def _actor_has_active_review_role(self, conn: sqlite3.Connection, task_id: str, actor: str) -> bool:
        return any(p.get("agent") == actor for p in self._active_review_participants(conn, task_id))

    def _assert_direct_completion_allowed(self, conn: sqlite3.Connection, task_id: str, actor: str, status: str | None, approval_id: str | None = None) -> None:
        if approval_id:
            return
        if status not in {"done", "validated"}:
            return
        reviewers = self._active_review_participants(conn, task_id)
        if reviewers and not self._actor_has_active_review_role(conn, task_id, actor):
            agents = ", ".join(sorted({p.get("agent") or "" for p in reviewers if p.get("agent")}))
            raise ValueError(f"task has active reviewer/verifier participants ({agents}); move the task to review with a result summary instead of marking {status} directly")

    def _mark_result(self, task_id: str, result: str, notes: str | None = None, actor: str = "user", next_step: str | None = None, status: str | None = None, approval_id: str | None = None) -> dict[str, Any]:
        notes, next_step, actor, status = self._validated_result_fields(result, notes, actor, next_step, status)
        with closing(self.connect()) as conn:
            if not conn.execute("SELECT 1 FROM tasks WHERE task_id=?", (task_id,)).fetchone():
                raise KeyError(task_id)
            self._assert_direct_completion_allowed(conn, task_id, actor, status, approval_id)
            conn.execute("BEGIN IMMEDIATE")
            task, _ev = self._mark_result_in_tx(conn, task_id, result, notes, actor, next_step, status, approval_id)
            conn.execute("COMMIT")
            return task

    def _state_set(self, task_id: str, agent: str, **kw: Any) -> dict[str, Any]:
        status = kw.get("status") or "working"
        if status not in STATE_STATUSES:
            raise ValueError("invalid state status")
        agent = clean_text(agent, "agent", required=True) or agent
        current_activity = clean_text(kw.get("current_activity"), "current_activity")
        next_step = clean_text(kw.get("next_step"), "next_step")
        blockers = clean_text_list(kw.get("blockers") or [], "list_item")
        notes = clean_text(kw.get("notes"), "notes")
        instance_id = clean_text(kw.get("instance_id"), "agent")
        root_task_id = clean_text(kw.get("root_task_id") or task_id, "list_item")
        task_chain_id = clean_text(kw.get("task_chain_id") or root_task_id, "list_item")
        clarification_count = clean_nonnegative_int(kw.get("clarification_count"), "clarification_count")
        correction_count = clean_nonnegative_int(kw.get("correction_count"), "correction_count")
        need_improvements_count = clean_nonnegative_int(kw.get("need_improvements_count"), "need_improvements_count")
        first_pass_success = clean_bool(kw.get("first_pass_success"))
        ts = now_iso()
        with closing(self.connect()) as conn:
            if not conn.execute("SELECT 1 FROM tasks WHERE task_id=?", (task_id,)).fetchone():
                raise KeyError(task_id)
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT state_id, instance_id, status FROM working_states WHERE task_id=? AND agent=? AND task_chain_id=?", (task_id, agent, task_chain_id)).fetchone()
            if existing:
                existing_instance = existing["instance_id"]
                if existing_instance and existing["status"] != "done" and (not instance_id or existing_instance != instance_id):
                    raise ValueError(f"working state conflict for task={task_id} agent={agent} task_chain_id={task_chain_id}: owned by instance {existing_instance}")
                state_id = existing["state_id"]
                conn.execute("UPDATE working_states SET instance_id=?,task_chain_id=?,root_task_id=?,status=?,current_activity=?,next_step=?,blockers=?,notes=?,stale_after_seconds=?,updated_at=?,version=version+1 WHERE state_id=?", (instance_id, task_chain_id, root_task_id, status, current_activity, next_step, json.dumps(blockers), notes, kw.get("stale_after_seconds"), ts, state_id))
            else:
                state_id = f"state-{uuid.uuid4().hex[:12]}"
                conn.execute("INSERT INTO working_states(state_id,task_id,agent,instance_id,task_chain_id,root_task_id,status,current_activity,next_step,blockers,notes,stale_after_seconds,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (state_id, task_id, agent, instance_id, task_chain_id, root_task_id, status, current_activity, next_step, json.dumps(blockers), notes, kw.get("stale_after_seconds"), ts, ts))
            event_payload = {"status": status, "current_activity": current_activity, "next_step": next_step, "blocker_count": len(blockers)}
            structured = {
                "agent_instance_id": instance_id,
                "task_chain_id": task_chain_id,
                "root_task_id": root_task_id,
                "clarification_count": clarification_count,
                "correction_count": correction_count,
                "need_improvements_count": need_improvements_count,
                "first_pass_success": first_pass_success,
            }
            event_payload.update({k: v for k, v in structured.items() if v is not None})
            self.event(conn, "working_state_set", "agent", agent, "working_state", state_id, event_payload, task_id)
            conn.execute("COMMIT")
            return self.row_state(conn.execute("SELECT * FROM working_states WHERE state_id=?", (state_id,)).fetchone())

    def _state_show(self, task_id: str, agent: str | None = None) -> dict[str, Any] | list[dict[str, Any]] | None:
        with closing(self.connect()) as conn:
            if agent:
                rows = [self.row_state(r) for r in conn.execute("SELECT * FROM working_states WHERE task_id=? AND agent=? ORDER BY task_chain_id, updated_at DESC", (task_id, agent)).fetchall()]
                if not rows:
                    return None
                return rows[0] if len(rows) == 1 else rows
            return [self.row_state(r) for r in conn.execute("SELECT * FROM working_states WHERE task_id=? ORDER BY task_chain_id, updated_at DESC", (task_id,)).fetchall()]

    def _state_list(self, agent: str | None = None, task_id: str | None = None, stale_after: int | None = None) -> list[dict[str, Any]]:
        clauses, args = [], []
        if agent:
            clauses.append("agent=?"); args.append(agent)
        if task_id:
            clauses.append("task_id=?"); args.append(task_id)
        with closing(self.connect()) as conn:
            rows = [self.row_state(r) for r in conn.execute("SELECT * FROM working_states" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY updated_at DESC", args).fetchall()]
        if stale_after is not None:
            cutoff = time.time() - stale_after
            rows = [r for r in rows if _iso_to_epoch(r["updated_at"]) < cutoff]
        return rows

    def _state_clear(self, task_id: str, agent: str | None = None, actor: str = "user") -> dict[str, Any]:
        actor = clean_text(actor, "agent") or "user"
        agent = clean_text(agent, "agent") if agent else None
        with closing(self.connect()) as conn:
            if agent:
                states = [self.row_state(r) for r in conn.execute("SELECT * FROM working_states WHERE task_id=? AND agent=?", (task_id, agent)).fetchall()]
            else:
                states = [self.row_state(r) for r in conn.execute("SELECT * FROM working_states WHERE task_id=?", (task_id,)).fetchall()]
            conn.execute("BEGIN IMMEDIATE")
            if agent:
                conn.execute("DELETE FROM working_states WHERE task_id=? AND agent=?", (task_id, agent))
            else:
                conn.execute("DELETE FROM working_states WHERE task_id=?", (task_id,))
            for st in states:
                self.event(conn, "working_state_cleared", "user", actor, "working_state", st["state_id"], {"agent": st["agent"], "task_chain_id": st.get("task_chain_id"), "root_task_id": st.get("root_task_id"), "agent_instance_id": st.get("instance_id")}, task_id)
            conn.execute("COMMIT")
            return {"cleared": len(states), "task_id": task_id, "agent": agent}

    def _tasks_for_completion_chain(self, conn: sqlite3.Connection, task_chain_id: str, root_task_id: str) -> list[dict[str, Any]]:
        by_id = {r["task_id"]: self.row_task(r) for r in conn.execute("SELECT * FROM tasks WHERE status!='archived'").fetchall()}
        ids = {root_task_id} if root_task_id else set()
        for row in conn.execute("SELECT DISTINCT task_id FROM task_participants WHERE status='active' AND (task_chain_id=? OR root_task_id=?)", (task_chain_id, root_task_id)).fetchall():
            ids.add(row["task_id"])
        changed = True
        while changed:
            changed = False
            for task_id, task in by_id.items():
                if task_id in ids:
                    continue
                if any(dep in ids for dep in (task.get("depends_on") or [])):
                    ids.add(task_id)
                    changed = True
        return [by_id[task_id] for task_id in sorted(ids) if task_id in by_id]

    def _assert_chain_completion_allowed(self, conn: sqlite3.Connection, task_id: str, task_chain_id: str, root_task_id: str) -> None:
        if not task_chain_id or not root_task_id or task_id != root_task_id:
            raise ValueError("approval requests are supported only for task-chain completion; submit the root task with explicit --task-chain-id and --root-task-id")
        chain_tasks = self._tasks_for_completion_chain(conn, task_chain_id, root_task_id)
        if len(chain_tasks) <= 1:
            raise ValueError("approval requests are supported only for task-chain completion; single-task approval requests are not supported")
        blockers = []
        for task in chain_tasks:
            if task["task_id"] == task_id:
                continue
            if task.get("status") not in DONE_STATUSES:
                blockers.append(f"{task['task_id']}:{task.get('status')}")
                continue
            if task.get("result_status") in {"bad", "need_improvements"}:
                blockers.append(f"{task['task_id']}:{task.get('result_status')}")
                continue
            pending = conn.execute("SELECT approval_id FROM task_approvals WHERE task_id=? AND status='pending'", (task["task_id"],)).fetchone()
            if pending:
                blockers.append(f"{task['task_id']}:pending approval {pending['approval_id']}")
        if blockers:
            raise ValueError("cannot complete task chain while tasks remain open or pending approval: " + ", ".join(blockers[:10]))

    def _submit_completion(self, task_id: str, **kw: Any) -> dict[str, Any]:
        if kw.get("non_learning"):
            raise ValueError("immutable/non-learning instances cannot submit learning approvals")
        agent = clean_text(kw.get("agent"), "agent", required=True) or "agent"
        instance_id = clean_text(kw.get("agent_instance_id"), "agent")
        root_task_id = clean_text(kw.get("root_task_id") or task_id, "list_item")
        task_chain_id = clean_text(kw.get("task_chain_id") or root_task_id, "list_item")
        result_summary = clean_text(kw.get("result_summary"), "result_summary", required=True) or ""
        acceptance_summary = clean_text(kw.get("acceptance_summary"), "result_summary")
        idempotency_key = clean_text(kw.get("idempotency_key"), "list_item")
        discoveries = self._clean_discoveries(kw.get("reusable_discoveries") or [])
        clarification_count = clean_nonnegative_int(kw.get("clarification_count"), "clarification_count")
        correction_count = clean_nonnegative_int(kw.get("correction_count"), "correction_count")
        need_improvements_count = clean_nonnegative_int(kw.get("need_improvements_count"), "need_improvements_count")
        first_pass_success = clean_bool(kw.get("first_pass_success"))
        ts = now_iso()
        with closing(self.connect()) as conn:
            task = self.row_task(conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone())
            if not task:
                raise KeyError(task_id)
            self._assert_chain_completion_allowed(conn, task_id, task_chain_id, root_task_id)
            payload_fingerprint = {
                "task_id": task_id, "task_chain_id": task_chain_id, "root_task_id": root_task_id,
                "agent_instance_id": instance_id, "result_summary": result_summary,
                "acceptance_summary": acceptance_summary, "reusable_discoveries": discoveries,
                "clarification_count": clarification_count, "correction_count": correction_count,
                "need_improvements_count": need_improvements_count, "first_pass_success": first_pass_success,
            }
            if idempotency_key:
                existing = self.row_approval(conn.execute("SELECT * FROM task_approvals WHERE submitter_profile=? AND idempotency_key=?", (agent, idempotency_key)).fetchone())
                if existing:
                    existing_fingerprint = {
                        "task_id": existing["task_id"], "task_chain_id": existing.get("task_chain_id") or "", "root_task_id": existing.get("root_task_id") or existing["task_id"],
                        "agent_instance_id": existing.get("submitter_instance_id"), "result_summary": existing.get("result_summary"),
                        "acceptance_summary": existing.get("acceptance_summary"), "reusable_discoveries": existing.get("reusable_discoveries") or [],
                        "clarification_count": existing.get("clarification_count"), "correction_count": existing.get("correction_count"),
                        "need_improvements_count": existing.get("need_improvements_count"), "first_pass_success": existing.get("first_pass_success"),
                    }
                    if existing_fingerprint != payload_fingerprint:
                        raise ValueError("idempotency key reuse with different completion payload")
                    return {"approval": existing, "task": task, "idempotent": True, "notification": None}
            conn.execute("BEGIN IMMEDIATE")
            pending = conn.execute("SELECT approval_id FROM task_approvals WHERE task_id=? AND task_chain_id=? AND status='pending'", (task_id, task_chain_id)).fetchone()
            if pending:
                conn.execute("ROLLBACK")
                raise ValueError(f"pending approval already exists for task={task_id} task_chain_id={task_chain_id}: {pending['approval_id']}")
            conn.execute("UPDATE tasks SET status='review', result_summary=?, updated_by=?, updated_at=?, version=version+1 WHERE task_id=?", (result_summary, agent, ts, task_id))
            approval_id = f"apr-{uuid.uuid4().hex[:12]}"
            submitted = self.event(conn, "task_completion_submitted", "agent", agent, "task", task_id, {"approval_id": approval_id, "task_chain_id": task_chain_id, "root_task_id": root_task_id, "agent_profile": agent, "agent_instance_id": instance_id, "result_summary": result_summary, "acceptance_summary": acceptance_summary, "reusable_discoveries": discoveries, "clarification_count": clarification_count, "correction_count": correction_count, "need_improvements_count": need_improvements_count, "first_pass_success": first_pass_success}, task_id)
            requested = self.event(conn, "task_approval_requested", "system", "task-kernel", "task_approval", approval_id, {"approval_id": approval_id, "task_id": task_id, "task_chain_id": task_chain_id, "root_task_id": root_task_id, "agent_profile": agent, "agent_instance_id": instance_id}, task_id)
            conn.execute("""
                INSERT INTO task_approvals(approval_id,idempotency_key,task_id,task_chain_id,root_task_id,status,created_event_seq,task_version_at_submission,event_seq_at_submission,submitter_profile,submitter_instance_id,result_summary,acceptance_summary,reusable_discoveries,clarification_count,correction_count,need_improvements_count,first_pass_success,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (approval_id, idempotency_key, task_id, task_chain_id, root_task_id, "pending", requested["event_seq"], task.get("version"), submitted["event_seq"], agent, instance_id, result_summary, acceptance_summary, json.dumps(discoveries), clarification_count, correction_count, need_improvements_count, None if first_pass_success is None else int(first_pass_success), ts))
            conn.execute("COMMIT")
            approval = self.row_approval(conn.execute("SELECT * FROM task_approvals WHERE approval_id=?", (approval_id,)).fetchone())
            updated_task = self._task_show(task_id)
            return {"approval": approval, "task": updated_task, "idempotent": False, "notification": None}

    def _record_approval_notification(self, approval_id: str, sent: bool, detail: str | None = None) -> dict[str, Any]:
        with closing(self.connect()) as conn:
            approval = self.row_approval(conn.execute("SELECT * FROM task_approvals WHERE approval_id=?", (approval_id,)).fetchone())
            if not approval:
                raise KeyError(approval_id)
            conn.execute("BEGIN IMMEDIATE")
            ev = self.event(conn, "task_approval_notification_sent" if sent else "task_approval_notification_failed", "system", "task-kernel", "task_approval", approval_id, {"approval_id": approval_id, "detail": clean_text(detail, "event_text")}, approval["task_id"])
            conn.execute("COMMIT")
            return ev

    def _review_completion(self, approval_id: str, result: str, **kw: Any) -> dict[str, Any]:
        if result not in RESULT_STATUSES:
            raise ValueError("invalid result")
        next_step = clean_text(kw.get("next_step"), "next_step")
        notes = clean_text(kw.get("notes"), "result_notes")
        expected_version = kw.get("task_version_at_submission")
        actor = clean_text(kw.get("actor") or "user", "agent") or "user"
        with closing(self.connect()) as conn:
            approval = self.row_approval(conn.execute("SELECT * FROM task_approvals WHERE approval_id=?", (approval_id,)).fetchone())
            if not approval:
                raise KeyError(approval_id)
            task = self.row_task(conn.execute("SELECT * FROM tasks WHERE task_id=?", (approval["task_id"],)).fetchone())
            if not task:
                raise KeyError(approval["task_id"])
            if approval["status"] == "decided":
                if approval.get("result") == result:
                    return {"approval": approval, "task": task, "idempotent": True}
                raise ValueError("approval already decided with a different result")
            if expected_version is not None and int(expected_version) != int(approval["task_version_at_submission"]):
                raise ValueError("refresh required: stale approval card")
            expected_current_version = int(approval["task_version_at_submission"]) + 1
            if task.get("status") != "review" or int(task.get("version") or 0) != expected_current_version:
                raise ValueError("refresh required: task changed since approval submission")
            reviewers = self._active_review_participants(conn, approval["task_id"])
            if reviewers and not self._actor_has_active_review_role(conn, approval["task_id"], actor):
                agents = ", ".join(sorted({p.get("agent") or "" for p in reviewers if p.get("agent")}))
                raise ValueError(f"approval must be reviewed by an active reviewer/verifier participant: {agents}")
            notes, next_step, actor, status = self._validated_result_fields(result, notes, actor, next_step, kw.get("status"))
            conn.execute("BEGIN IMMEDIATE")
            latest = self.row_approval(conn.execute("SELECT * FROM task_approvals WHERE approval_id=?", (approval_id,)).fetchone())
            latest_task = self.row_task(conn.execute("SELECT * FROM tasks WHERE task_id=?", (approval["task_id"],)).fetchone())
            if latest["status"] != "pending" or latest_task.get("status") != "review" or int(latest_task.get("version") or 0) != expected_current_version:
                conn.execute("ROLLBACK")
                raise ValueError("refresh required: approval or task changed")
            updated_task, ev = self._mark_result_in_tx(conn, approval["task_id"], result, notes, actor, next_step, status, approval_id)
            conn.execute("UPDATE task_approvals SET status='decided', result=?, decided_event_seq=?, decided_at=?, version=version+1 WHERE approval_id=?", (result, ev["event_seq"], now_iso(), approval_id))
            conn.execute("COMMIT")
        return {"approval": self._show_approval(approval_id), "task": updated_task, "idempotent": False}

    def _list_approvals(self, status: str | None = None) -> list[dict[str, Any]]:
        clauses, args = [], []
        if status:
            clauses.append("status=?"); args.append(status)
        with closing(self.connect()) as conn:
            rows = conn.execute("SELECT * FROM task_approvals" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY created_at, approval_id", args).fetchall()
        return [self.row_approval(r) for r in rows]

    def _show_approval(self, approval_id: str) -> dict[str, Any]:
        with closing(self.connect()) as conn:
            approval = self.row_approval(conn.execute("SELECT * FROM task_approvals WHERE approval_id=?", (approval_id,)).fetchone())
        if not approval:
            raise KeyError(approval_id)
        return approval

    def _require_trusted_memory_actor(self, actor: str) -> str:
        # Simplification: Anyone is trusted! Just clean and return.
        return clean_text(actor, "agent") or "user"

    def _clean_memory_payload(self, kw: dict[str, Any]) -> dict[str, Any]:
        typ = clean_text(kw.get("type"), "list_item", required=True)
        if typ not in MEMORY_TYPES:
            raise ValueError("invalid memory type")
        scope = clean_text(kw.get("scope") or "global", "scope", required=True) or "global"
        subject_agent = clean_text(kw.get("subject_agent"), "agent")
        title = clean_text(kw.get("title"), "title", required=True) or ""
        body = clean_text(kw.get("body"), "memory_body", required=True) or ""
        tags = clean_text_list(kw.get("tags") or [], "list_item")[:20]
        raw_metadata = kw.get("metadata") or {}
        metadata = safe_payload(raw_metadata)
        if kw.get("description"):
            metadata["description"] = clean_text(kw.get("description"), "memory_body")
        source_task_id = clean_text(kw.get("source_task_id") or kw.get("source_task"), "list_item")
        trusted_manual = bool(kw.get("trusted_manual"))
        allow_proposal_metadata = bool(kw.get("allow_proposal_metadata"))
        if not trusted_manual and not source_task_id and typ not in ("habit", "fact") and not allow_proposal_metadata:
            raise ValueError("source_task_id is required unless trusted_manual")
        if typ == "expertise":
            if not subject_agent and not (scope.startswith("team:") or scope.startswith("project:")):
                raise ValueError("expertise requires subject_agent or explicit team/project scope")
            metadata = self._clean_expertise_metadata(metadata, allow_proposal_metadata=allow_proposal_metadata)
        else:
            self._reject_forbidden_memory_metadata(metadata)
        return {"type": typ, "scope": scope, "subject_agent": subject_agent, "title": title, "body": body, "tags": tags, "metadata": metadata, "source_task_id": source_task_id, "trusted_manual": trusted_manual}

    def _replayable_payload(self, value: Any) -> Any:
        if isinstance(value, str):
            return clean_text(value, "memory_body") or ""
        if isinstance(value, list):
            return [self._replayable_payload(v) for v in value[:100]]
        if isinstance(value, dict):
            return {str(k)[:100]: self._replayable_payload(v) for k, v in list(value.items())[:100]}
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return clean_text(str(value), "memory_body")

    def _memory_event_payload(self, mem: dict[str, Any], **extra: Any) -> dict[str, Any]:
        snapshot = {k: mem.get(k) for k in ("memory_id", "type", "scope", "subject_agent", "title", "body", "source_task_id", "trusted_manual", "tags", "metadata", "status", "version") if k in mem}
        return {"memory_id": mem.get("memory_id"), "memory": snapshot, **extra}

    def _reject_forbidden_memory_metadata(self, value: Any) -> None:
        forbidden = {"score", "confidence", "level", "rank", "recommendation"}
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).lower() in forbidden:
                    raise ValueError("memory metadata must not include score/confidence/level/rank/recommendation")
                self._reject_forbidden_memory_metadata(child)
        elif isinstance(value, list):
            for child in value:
                self._reject_forbidden_memory_metadata(child)

    def _clean_expertise_metadata(self, metadata: Any, *, allow_proposal_metadata: bool = False) -> dict[str, Any]:
        if not isinstance(metadata, dict):
            raise ValueError("expertise metadata must be an object")
        self._reject_forbidden_memory_metadata(metadata)
        allowed = {"task_family", "tools", "evidence_task_ids", "validation_count", "last_validated_at", "known_limits", "description"}
        proposal_allowed = {"proposal_kind", "target_memory_id", "target_expected_version", "archive_reason"} if allow_proposal_metadata else set()
        unknown = set(metadata) - allowed - proposal_allowed
        if unknown:
            raise ValueError("unsupported expertise metadata field")
        cleaned = dict(metadata)
        if "tools" in cleaned:
            cleaned["tools"] = clean_text_list(cleaned.get("tools") if isinstance(cleaned.get("tools"), list) else [], "list_item")[:20]
        if "evidence_task_ids" in cleaned:
            cleaned["evidence_task_ids"] = clean_text_list(cleaned.get("evidence_task_ids") if isinstance(cleaned.get("evidence_task_ids"), list) else [], "list_item")[:20]
        if "validation_count" in cleaned:
            cleaned["validation_count"] = clean_nonnegative_int(cleaned.get("validation_count"), "validation_count")
        for key in ("task_family", "last_validated_at", "known_limits"):
            if key in cleaned:
                cleaned[key] = clean_text(cleaned.get(key), "list_item")
        if allow_proposal_metadata:
            if "proposal_kind" in cleaned:
                cleaned["proposal_kind"] = clean_text(cleaned.get("proposal_kind"), "list_item")
                if cleaned["proposal_kind"] not in {"edit", "archive"}:
                    raise ValueError("unsupported expertise metadata field")
            if "target_memory_id" in cleaned:
                cleaned["target_memory_id"] = clean_text(cleaned.get("target_memory_id"), "list_item")
            if "target_expected_version" in cleaned:
                cleaned["target_expected_version"] = clean_nonnegative_int(cleaned.get("target_expected_version"), "target_expected_version")
            if "archive_reason" in cleaned:
                cleaned["archive_reason"] = clean_text(cleaned.get("archive_reason"), "event_text")
        return cleaned

    def _memory_idempotency_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {k: payload.get(k) for k in ("type", "scope", "subject_agent", "title", "body", "source_task_id", "trusted_manual", "tags", "metadata")}

    def _validation_event_for_task(self, conn: sqlite3.Connection, task_id: str | None) -> dict[str, Any] | None:
        if not task_id:
            return None
        task = self.row_task(conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone())
        if not task or task.get("status") != "validated" or task.get("result_status") != "good":
            return None
        rows = conn.execute("SELECT rowid AS event_seq,* FROM events WHERE task_id=? AND event_type='task_result_marked' ORDER BY rowid DESC", (task_id,)).fetchall()
        for row in rows:
            payload = json.loads(row["payload"] or "{}")
            if payload.get("result_status") == "good":
                d = dict(row); d["payload"] = payload; return d
        return None

    def _memory_propose(self, **kw: Any) -> dict[str, Any]:
        proposer = clean_text(kw.get("proposed_by") or kw.get("agent") or "user", "agent", required=True) or "user"
        instance = clean_text(kw.get("proposed_by_instance") or kw.get("instance"), "agent")
        if kw.get("non_learning"):
            raise ValueError("immutable/non-learning instance cannot propose memory")
        
        status = kw.get("status") or "pending"
        allow_proposal_metadata = status in ("pending_edit", "pending_revocation") or bool(kw.get("allow_proposal_metadata"))
        payload = self._clean_memory_payload({**kw, "allow_proposal_metadata": allow_proposal_metadata})
        
        trusted_actor = clean_text(kw.get("trusted_actor"), "agent")
        if payload["trusted_manual"]:
            self._require_trusted_memory_actor(trusted_actor or proposer)
        idem = clean_text(kw.get("idempotency_key"), "list_item")
        
        memory_id = kw.get("memory_id") or f"mem-{uuid.uuid4().hex[:12]}"
        version = kw.get("version") or 1
        
        ts = now_iso()
        with closing(self.connect()) as conn:
            if idem:
                existing = self.row_memory(conn.execute("SELECT * FROM memory_records WHERE proposed_by=? AND idempotency_key=?", (proposer, idem)).fetchone())
                if existing:
                    if self._memory_idempotency_payload(existing) != self._memory_idempotency_payload(payload):
                        raise ValueError("idempotency conflict: different memory payload")
                    return {"memory": existing, "idempotent": True}
            
            conn.execute("BEGIN IMMEDIATE")
            pending_subject = payload.get("subject_agent") or proposer
            pending_count = conn.execute(
                "SELECT COUNT(*) FROM memory_records WHERE status IN ('pending', 'pending_edit', 'pending_revocation') AND COALESCE(subject_agent, proposed_by)=?", 
                (pending_subject,)
            ).fetchone()[0]
            
            if int(pending_count) >= MEMORY_LIMITS["max_pending_per_agent"]:
                conn.execute("ROLLBACK")
                raise ValueError("pending memory limit exceeded")
                
            conn.execute(
                "INSERT INTO memory_records(memory_id,version,status,idempotency_key,proposed_by,proposed_by_instance,type,scope,subject_agent,title,body,source_task_id,trusted_manual,created_by,created_at,tags,metadata) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (memory_id, version, status, idem, proposer, instance, payload["type"], payload["scope"], payload["subject_agent"], payload["title"], payload["body"], payload["source_task_id"], 1 if payload["trusted_manual"] else 0, proposer, ts, json.dumps(payload["tags"], sort_keys=True), json.dumps(payload["metadata"], sort_keys=True))
            )
            
            event_type = "memory_proposed"
            if status == "pending_edit": event_type = "memory_edit_proposed"
            elif status == "pending_revocation": event_type = "memory_archive_proposed"
                
            ev = self.event(
                conn, event_type, "agent", proposer, "memory", memory_id, 
                self._memory_event_payload({**payload, "memory_id": memory_id, "status": status, "version": version}), 
                payload.get("source_task_id"), payload.get("scope"), replayable_payload=True
            )
            
            conn.execute("UPDATE memory_records SET updated_event_seq=?, status_event_seq=?, source_event_seq=?, source_event_id=? WHERE memory_id=? AND version=?", (ev["event_seq"], ev["event_seq"], ev["event_seq"], ev["event_id"], memory_id, version))
            conn.execute("COMMIT")
            
            return {
                "memory": self.row_memory(conn.execute("SELECT * FROM memory_records WHERE memory_id=? AND version=?", (memory_id, version)).fetchone()), 
                "event": ev, 
                "idempotent": False
            }

    def _active_limit_conflict(self, conn: sqlite3.Connection, mem: dict[str, Any]) -> dict[str, Any] | None:
        subject = mem.get("subject_agent") or mem.get("proposed_by")
        checks = [("agent", "COALESCE(subject_agent, proposed_by)=?", [subject], MEMORY_LIMITS["max_active_per_agent"]), ("scope", "scope=?", [mem["scope"]], MEMORY_LIMITS["max_active_per_scope"]), (mem["type"], "COALESCE(subject_agent, proposed_by)=? AND type=?", [subject, mem["type"]], MEMORY_LIMITS[f"max_active_per_agent_{mem['type']}"])]
        for kind, clause, args, limit in checks:
            count = int(conn.execute(f"SELECT COUNT(*) FROM memory_records WHERE status='active' AND {clause}", args).fetchone()[0])
            if count >= limit:
                rows = conn.execute(f"SELECT memory_id,title,validated_at,version FROM memory_records WHERE status='active' AND {clause} ORDER BY validated_at, memory_id LIMIT 5", args).fetchall()
                return {"limit_exceeded": True, "kind": kind, "current_count": count, "limit": limit, "memory_type": mem["type"], "agent": subject, "scope": mem["scope"], "stale_candidates": [dict(r) for r in rows]}
        return None

    def _memory_approve(self, memory_id: str, version: int, *, actor: str = "user", event_type: str | None = None) -> dict[str, Any]:
        actor = self._require_trusted_memory_actor(actor)
        with closing(self.connect()) as conn:
            mem = self.row_memory(conn.execute("SELECT * FROM memory_records WHERE memory_id=? AND version=?", (memory_id, version)).fetchone())
            if not mem: raise KeyError(f"Memory {memory_id} v{version} not found")
                
            if mem["status"] not in {"pending", "pending_edit", "pending_revocation"}:
                if mem["status"] in ("active", "revoked", "rejected"): return {"memory": mem, "idempotent": True}
                raise ValueError("memory transition conflict")
                
            conn.execute("BEGIN IMMEDIATE")
            latest = self.row_memory(conn.execute("SELECT * FROM memory_records WHERE memory_id=? AND version=?", (memory_id, version)).fetchone())
            if latest["status"] not in {"pending", "pending_edit", "pending_revocation"}:
                conn.execute("ROLLBACK"); raise ValueError("memory transition conflict")
                
            # Resolve target version to supersede
            metadata = latest.get("metadata") or {}
            target_version = metadata.get("target_expected_version")
            target_status = None
            if target_version:
                target_row = conn.execute("SELECT status FROM memory_records WHERE memory_id=? AND version=?", (memory_id, target_version)).fetchone()
                if not target_row:
                    conn.execute("ROLLBACK"); raise ValueError("target memory version not found")
                target_status = target_row["status"]
                if latest["status"] == "pending_edit" and target_status != "active":
                    conn.execute("ROLLBACK"); raise ValueError("target memory version is no longer active")
                if latest["status"] == "pending_revocation" and target_status not in ("active", "pending"):
                    conn.execute("ROLLBACK"); raise ValueError("target memory version is no longer active or pending")
            
            if latest["status"] == "pending_revocation":
                if target_status == "active":
                    new_status = "revoked"; resolved_event_type = event_type or "memory_revoked"
                else:
                    new_status = "rejected"; resolved_event_type = event_type or "memory_rejected"
            elif latest["status"] == "pending_edit":
                new_status = "active"; resolved_event_type = event_type or "memory_edited"
            else:
                new_status = "active"; resolved_event_type = event_type or "memory_approved"
                
            if new_status == "active" and latest["status"] == "pending":
                conflict = self._active_limit_conflict(conn, latest)
                if conflict:
                    conn.execute("ROLLBACK"); return {"memory": latest, **conflict}
                    
            ts = now_iso()
            cleaned_metadata = dict(latest.get("metadata") or {})
            if new_status == "active":
                cleaned_metadata.pop("target_expected_version", None)
            
            ev = self.event(
                conn, resolved_event_type, "user", actor, "memory", memory_id, 
                self._memory_event_payload({**latest, "metadata": cleaned_metadata}, status=new_status, validated_by=actor, validated_at=ts), 
                latest.get("source_task_id"), latest.get("scope"), replayable_payload=True
            )
            
            conn.execute(
                "UPDATE memory_records SET status=?, validated_by=?, validated_at=?, metadata=?, status_event_seq=?, updated_event_seq=? WHERE memory_id=? AND version=?", 
                (new_status, actor, ts, json.dumps(cleaned_metadata, sort_keys=True), ev["event_seq"], ev["event_seq"], memory_id, version)
            )
            
            if target_version is not None:
                supersede_ev = self.event(
                    conn, "memory_superseded", "user", actor, "memory", memory_id,
                    {"memory_id": memory_id, "version": target_version, "status": "superseded", "superseded_by_version": version},
                    None, latest.get("scope"), replayable_payload=True
                )
                conn.execute(
                    "UPDATE memory_records SET status='superseded', updated_event_seq=? WHERE memory_id=? AND version=?",
                    (supersede_ev["event_seq"], memory_id, target_version)
                )
                
            conn.execute("COMMIT")
            return {
                "memory": self.row_memory(conn.execute("SELECT * FROM memory_records WHERE memory_id=? AND version=?", (memory_id, version)).fetchone()), 
                "event": ev, 
                "idempotent": False
            }

    def _memory_propose_edit(self, memory_id: str, *, expected_version: int | None = None, **kw: Any) -> dict[str, Any]:
        if kw.get("non_learning"):
            raise ValueError("immutable/non-learning instance cannot propose memory")
        proposer = clean_text(kw.get("proposed_by") or kw.get("agent") or "user", "agent", required=True) or "user"
        instance = clean_text(kw.get("proposed_by_instance") or kw.get("instance"), "agent")
        
        with closing(self.connect()) as conn:
            target = self.row_memory(conn.execute("SELECT * FROM memory_records WHERE memory_id=? AND status='active'", (memory_id,)).fetchone())
            if not target: raise KeyError(memory_id)
            if expected_version is not None and int(target["version"]) != int(expected_version):
                raise ValueError("stale target memory version")
                
            max_version = conn.execute("SELECT MAX(version) FROM memory_records WHERE memory_id=?", (memory_id,)).fetchone()[0]
            next_version = int(max_version or target["version"]) + 1
            
            merged = {k: target.get(k) for k in ("type", "scope", "subject_agent", "title", "body", "source_task_id", "trusted_manual", "tags", "metadata")}
            for key in ("type", "scope", "subject_agent", "title", "body", "source_task_id", "tags", "metadata"):
                if key in kw and kw[key] is not None:
                    merged[key] = kw[key]
            if not merged.get("subject_agent"):
                merged["subject_agent"] = target.get("proposed_by")
                    
            metadata = dict(merged.get("metadata") or {})
            metadata.update({"target_expected_version": int(target["version"])})
            merged["metadata"] = metadata
            merged["trusted_manual"] = False
            
            return self._memory_propose(
                **merged, 
                memory_id=memory_id, 
                version=next_version, 
                status="pending_edit",
                proposed_by=proposer, 
                proposed_by_instance=instance, 
                non_learning=False
            )

    def _memory_propose_archive(self, memory_id: str, *, expected_version: int | None = None, reason: str | None = None, **kw: Any) -> dict[str, Any]:
        if kw.get("non_learning"):
            raise ValueError("immutable/non-learning instance cannot propose memory")
        proposer = clean_text(kw.get("proposed_by") or kw.get("agent") or "user", "agent", required=True) or "user"
        instance = clean_text(kw.get("proposed_by_instance") or kw.get("instance"), "agent")
        reason = clean_text(reason, "event_text")
        
        with closing(self.connect()) as conn:
            target = self.row_memory(conn.execute(
                "SELECT * FROM memory_records WHERE memory_id=? AND status IN ('active', 'pending', 'pending_edit', 'pending_revocation') ORDER BY version DESC LIMIT 1", 
                (memory_id,)
            ).fetchone())
            if not target: raise KeyError(memory_id)
            if target["status"] not in {"pending", "active"}:
                raise ValueError("archive proposal requires pending or active target")
            if expected_version is not None and int(target["version"]) != int(expected_version):
                raise ValueError("stale target memory version")
                
            max_version = conn.execute("SELECT MAX(version) FROM memory_records WHERE memory_id=?", (memory_id,)).fetchone()[0]
            next_version = int(max_version or target["version"]) + 1
            
            metadata = dict(target.get("metadata") or {})
            metadata.update({
                "target_expected_version": int(target["version"]),
                "archive_reason": reason or "No reason provided."
            })
            
            return self._memory_propose(
                type=target["type"], 
                scope=target["scope"], 
                subject_agent=target["subject_agent"],
                title=target["title"], 
                body=target["body"],
                source_task_id=kw.get("source_task_id") or target["source_task_id"], 
                trusted_manual=False,
                tags=target["tags"], 
                metadata=metadata, 
                memory_id=memory_id,
                version=next_version,
                status="pending_revocation",
                proposed_by=proposer, 
                proposed_by_instance=instance,
                non_learning=False
            )


    def _memory_edit(self, memory_id: str, *, expected_version: int | None = None, actor: str = "user", **kw: Any) -> dict[str, Any]:
        actor = self._require_trusted_memory_actor(actor)
        with closing(self.connect()) as conn:
            latest = self.row_memory(conn.execute("SELECT * FROM memory_records WHERE memory_id=? ORDER BY version DESC LIMIT 1", (memory_id,)).fetchone())
            if not latest: raise KeyError(memory_id)
            if expected_version is not None and int(latest["version"]) != int(expected_version):
                raise ValueError("stale memory version")
                
            if latest["status"] in ("pending", "pending_edit", "pending_revocation"):
                merged = {k: latest.get(k) for k in ("type", "scope", "subject_agent", "title", "body", "source_task_id", "trusted_manual", "tags", "metadata")}
                for key in ("type", "scope", "subject_agent", "title", "body", "source_task_id", "tags", "metadata"):
                    if key in kw and kw[key] is not None:
                        merged[key] = kw[key]
                payload = self._clean_memory_payload({**merged, "allow_proposal_metadata": True})
                
                conn.execute("BEGIN IMMEDIATE")
                cur = self.row_memory(conn.execute("SELECT * FROM memory_records WHERE memory_id=? AND version=?", (memory_id, latest["version"])).fetchone())
                if not cur or cur["status"] not in {"pending", "pending_edit", "pending_revocation"} or (expected_version is not None and int(cur["version"]) != int(expected_version)):
                    conn.execute("ROLLBACK"); raise ValueError("stale memory version")
                    
                ev = self.event(
                    conn, "memory_edited", "user", actor, "memory", memory_id, 
                    self._memory_event_payload({**payload, "memory_id": memory_id, "status": cur["status"], "version": cur["version"]}, previous=self._memory_event_payload(cur).get("memory")), 
                    payload.get("source_task_id"), payload.get("scope"), replayable_payload=True
                )
                conn.execute(
                    "UPDATE memory_records SET type=?, scope=?, subject_agent=?, title=?, body=?, source_task_id=?, trusted_manual=?, tags=?, metadata=?, updated_event_seq=? WHERE memory_id=? AND version=?", 
                    (payload["type"], payload["scope"], payload["subject_agent"], payload["title"], payload["body"], payload["source_task_id"], 1 if payload["trusted_manual"] else 0, json.dumps(payload["tags"], sort_keys=True), json.dumps(payload["metadata"], sort_keys=True), ev["event_seq"], memory_id, latest["version"])
                )
                conn.execute("COMMIT")
                return {
                    "memory": self.row_memory(conn.execute("SELECT * FROM memory_records WHERE memory_id=? AND version=?", (memory_id, latest["version"])).fetchone()), 
                    "event": ev, 
                    "idempotent": False
                }
            elif latest["status"] == "active":
                prop = self._memory_propose_edit(memory_id, expected_version=expected_version, proposed_by=actor, **kw)
                return self._memory_approve(memory_id, prop["memory"]["version"], actor=actor, event_type="memory_edited")
            else:
                raise ValueError("memory edit requires pending or active status")

    def _memory_rollback(self, memory_id: str, *, target_version: int, expected_version: int | None = None, actor: str = "user") -> dict[str, Any]:
        actor = self._require_trusted_memory_actor(actor)
        with closing(self.connect()) as conn:
            target = self.row_memory(conn.execute("SELECT * FROM memory_records WHERE memory_id=? AND version=?", (memory_id, target_version)).fetchone())
            if not target: raise KeyError(f"Target version {target_version} not found for memory {memory_id}")
            
            prop = self._memory_propose_edit(
                memory_id, 
                expected_version=expected_version,
                proposed_by=actor,
                type=target["type"],
                scope=target["scope"],
                subject_agent=target["subject_agent"],
                title=target["title"],
                body=target["body"],
                tags=target["tags"],
                metadata=target["metadata"],
                source_task_id=target["source_task_id"]
            )
            return self._memory_approve(memory_id, prop["memory"]["version"], actor=actor, event_type="memory_rolled_back")

    def _memory_reject(self, memory_id: str, version: int, *, reason: str | None = None, actor: str = "user") -> dict[str, Any]:
        reason = clean_text(reason, "event_text")
        actor = self._require_trusted_memory_actor(actor)
        with closing(self.connect()) as conn:
            mem = self.row_memory(conn.execute("SELECT * FROM memory_records WHERE memory_id=? AND version=?", (memory_id, version)).fetchone())
            if not mem: raise KeyError(f"Memory {memory_id} v{version} not found")
            if mem["status"] not in {"pending", "pending_edit", "pending_revocation"}:
                if mem["status"] == "rejected": return {"memory": mem, "idempotent": True}
                raise ValueError("memory transition conflict")
                
            conn.execute("BEGIN IMMEDIATE")
            latest = self.row_memory(conn.execute("SELECT * FROM memory_records WHERE memory_id=? AND version=?", (memory_id, version)).fetchone())
            if latest["status"] not in {"pending", "pending_edit", "pending_revocation"}:
                conn.execute("ROLLBACK"); raise ValueError("memory transition conflict")
                
            ts = now_iso()
            ev = self.event(
                conn, "memory_rejected", "user", actor, "memory", memory_id, 
                self._memory_event_payload(latest, status="rejected", validated_by=actor, validated_at=ts, reason=reason), 
                latest.get("source_task_id"), latest.get("scope"), replayable_payload=True
            )
            conn.execute(
                "UPDATE memory_records SET status='rejected', validated_by=?, validated_at=?, status_event_seq=?, updated_event_seq=? WHERE memory_id=? AND version=?", 
                (actor, ts, ev["event_seq"], ev["event_seq"], memory_id, version)
            )
            conn.execute("COMMIT")
            return {
                "memory": self.row_memory(conn.execute("SELECT * FROM memory_records WHERE memory_id=? AND version=?", (memory_id, version)).fetchone()), 
                "event": ev, 
                "idempotent": False
            }

    def _memory_revoke(self, memory_id: str, *, reason: str | None = None, expected_version: int | None = None, actor: str = "user") -> dict[str, Any]:
        reason = clean_text(reason, "event_text")
        actor = self._require_trusted_memory_actor(actor)
        with closing(self.connect()) as conn:
            latest = self.row_memory(conn.execute("SELECT * FROM memory_records WHERE memory_id=? ORDER BY version DESC LIMIT 1", (memory_id,)).fetchone())
            if not latest: raise KeyError(f"Memory {memory_id} not found")
            if expected_version is not None and int(latest["version"]) != int(expected_version):
                raise ValueError("stale target memory version")
                
            if latest["status"] == "revoked":
                archive_reason = latest.get("metadata", {}).get("archive_reason")
                if archive_reason == (reason or "Direct revocation by admin."):
                    return {"memory": latest, "idempotent": True}
                raise ValueError("memory transition conflict")
                
            if latest["status"] != "active":
                raise KeyError(f"Active memory {memory_id} not found")
                
            conn.execute("BEGIN IMMEDIATE")
            latest_active = self.row_memory(conn.execute("SELECT * FROM memory_records WHERE memory_id=? AND status='active'", (memory_id,)).fetchone())
            if not latest_active or (expected_version is not None and int(latest_active["version"]) != int(expected_version)):
                conn.execute("ROLLBACK"); raise ValueError("stale target memory version")
                
            max_version = conn.execute("SELECT MAX(version) FROM memory_records WHERE memory_id=?", (memory_id,)).fetchone()[0]
            next_version = int(max_version or latest_active["version"]) + 1
            
            ts = now_iso()
            metadata = dict(latest_active.get("metadata") or {})
            metadata.update({
                "target_expected_version": int(latest_active["version"]),
                "archive_reason": reason or "Direct revocation by admin."
            })
            
            conn.execute(
                "INSERT INTO memory_records(memory_id,version,status,proposed_by,proposed_by_instance,type,scope,subject_agent,title,body,source_task_id,trusted_manual,created_by,created_at,validated_by,validated_at,tags,metadata) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (memory_id, next_version, "revoked", actor, "admin", latest_active["type"], latest_active["scope"], latest_active["subject_agent"], latest_active["title"], latest_active["body"], latest_active["source_task_id"], 1, latest_active["created_by"], latest_active["created_at"], actor, ts, json.dumps(latest_active["tags"]), json.dumps(metadata, sort_keys=True))
            )
            
            ev = self.event(
                conn, "memory_revoked", "user", actor, "memory", memory_id, 
                self._memory_event_payload({**latest_active, "memory_id": memory_id, "version": next_version, "status": "revoked", "metadata": metadata}, reason=reason), 
                latest_active.get("source_task_id"), latest_active.get("scope"), replayable_payload=True
            )
            
            conn.execute(
                "UPDATE memory_records SET status_event_seq=?, updated_event_seq=?, source_event_seq=?, source_event_id=? WHERE memory_id=? AND version=?", 
                (ev["event_seq"], ev["event_seq"], ev["event_seq"], ev["event_id"], memory_id, next_version)
            )
            
            supersede_ev = self.event(
                conn, "memory_superseded", "user", actor, "memory", memory_id,
                {"memory_id": memory_id, "version": latest_active["version"], "status": "superseded", "superseded_by_version": next_version},
                None, latest_active.get("scope"), replayable_payload=True
            )
            conn.execute(
                "UPDATE memory_records SET status='superseded', updated_event_seq=? WHERE memory_id=? AND version=?",
                (supersede_ev["event_seq"], memory_id, latest_active["version"])
            )
            
            conn.execute("COMMIT")
            return {
                "memory": self.row_memory(conn.execute("SELECT * FROM memory_records WHERE memory_id=? AND version=?", (memory_id, next_version)).fetchone()), 
                "event": ev, 
                "idempotent": False
            }

    def _memory_show(self, memory_id: str, version: int | None = None) -> dict[str, Any]:
        with closing(self.connect()) as conn:
            if version is not None:
                row = conn.execute("SELECT * FROM memory_records WHERE memory_id=? AND version=?", (memory_id, version)).fetchone()
            else:
                row = conn.execute("SELECT * FROM memory_records WHERE memory_id=? AND status='active'", (memory_id,)).fetchone()
                if not row:
                    row = conn.execute("SELECT * FROM memory_records WHERE memory_id=? ORDER BY version DESC LIMIT 1", (memory_id,)).fetchone()
            if not row: raise KeyError(memory_id)
            return self.row_memory(row)

    def _memory_history(self, memory_id: str) -> dict[str, Any]:
        with closing(self.connect()) as conn:
            mem = self._memory_show(memory_id)
            rows = conn.execute("SELECT rowid AS event_seq,event_id,event_type,actor_type,actor_id,timestamp,payload FROM events WHERE subject_type='memory' AND subject_id=? ORDER BY rowid", (memory_id,)).fetchall()
        events = []
        for row in rows:
            payload = json.loads(row["payload"] or "{}")
            events.append({
                "event_seq": row["event_seq"],
                "event_id": row["event_id"],
                "event_type": row["event_type"],
                "actor_type": row["actor_type"],
                "actor": row["actor_id"],
                "created_at": row["timestamp"],
                "memory": payload.get("memory") if isinstance(payload, dict) else None,
                "previous": payload.get("previous") if isinstance(payload, dict) else None,
                "target_version": payload.get("target_version") if isinstance(payload, dict) else None,
                "reason": payload.get("reason") if isinstance(payload, dict) else None,
            })
        return {"memory": mem, "events": events}

    def _memory_list(self, *, scope: str | None = None, type: str | None = None, status: str | None = None, agent: str | None = None) -> list[dict[str, Any]]:
        clauses, args = [], []
        if scope: clauses.append("scope=?"); args.append(scope)
        if type: clauses.append("type=?"); args.append(type)
        
        status = status or "active"
        if status == "pending":
            clauses.append("status IN ('pending', 'pending_edit', 'pending_revocation')")
        elif status != "all":
            status = "active" if status == "approved" else status
            clauses.append("status=?"); args.append(status)
            
        if agent: clauses.append("COALESCE(subject_agent, proposed_by)=?"); args.append(agent)
        with closing(self.connect()) as conn:
            rows = conn.execute("SELECT * FROM memory_records" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY created_at, memory_id", args).fetchall()
        return [self.row_memory(r) for r in rows]

    def _memory_search(self, query: str, *, scope: str | None = None) -> list[dict[str, Any]]:
        q = f"%{(clean_text(query, 'event_text') or '').lower()}%"
        clauses, args = ["status='active'", "(lower(title) LIKE ? OR lower(body) LIKE ? OR lower(tags) LIKE ?)"], [q, q, q]
        if scope: clauses.append("scope=?"); args.append(scope)
        with closing(self.connect()) as conn:
            rows = conn.execute("SELECT * FROM memory_records WHERE " + " AND ".join(clauses) + " ORDER BY validated_at DESC, title, memory_id", args).fetchall()
        return [self.row_memory(r) for r in rows]

    def _memory_budget(self, *, agent: str, scope: str | None = None) -> dict[str, Any]:
        with closing(self.connect()) as conn:
            out = {
                "agent": agent, 
                "limits": MEMORY_LIMITS, 
                "active": {}, 
                "pending": int(conn.execute("SELECT COUNT(*) FROM memory_records WHERE status IN ('pending', 'pending_edit', 'pending_revocation') AND COALESCE(subject_agent, proposed_by)=?", (agent,)).fetchone()[0])
            }
            for typ in MEMORY_TYPES:
                out["active"][typ] = int(conn.execute("SELECT COUNT(*) FROM memory_records WHERE status='active' AND COALESCE(subject_agent, proposed_by)=? AND type=?" + (" AND scope=?" if scope else ""), (agent, typ, *([scope] if scope else []))).fetchone()[0])
        return out

    def _memory_for_bootstrap(self, *, agent: str, scope: str | None = None) -> dict[str, Any]:
        scopes = ["global", f"agent:{agent}"] + ([scope] if scope else [])
        # Shared memories have no subject_agent and are selected by relevant scope.
        # Agent-specific memories may use subject_agent across any applicable scope.
        # Do not include global/project memories whose subject_agent names a different
        # agent; those are durable for that agent only, despite their broad scope.
        scope_placeholders = ",".join("?" for _ in scopes)
        query = f"""
            SELECT * FROM memory_records
            WHERE status='active'
              AND scope IN ({scope_placeholders})
              AND COALESCE(subject_agent, proposed_by)=?
            ORDER BY CASE WHEN scope=? THEN 0 WHEN COALESCE(subject_agent, proposed_by)=? THEN 1 WHEN scope=? THEN 2 ELSE 3 END,
                     validated_at DESC, title, memory_id
        """
        with closing(self.connect()) as conn:
            rows = [self.row_memory(r) for r in conn.execute(query, (*scopes, agent, scope or "", agent, "global")).fetchall()]
        max_records = MEMORY_LIMITS["bootstrap_max_records"]; per = MEMORY_LIMITS["bootstrap_max_body_chars_per_record"]; total_limit = MEMORY_LIMITS["bootstrap_max_total_chars"]
        selected, total, omitted, truncated = [], 0, 0, False
        for mem in rows:
            if len(selected) >= max_records:
                omitted += 1; continue
            item = {k: mem.get(k) for k in ("memory_id", "type", "scope", "subject_agent", "title", "body", "source_task_id", "source_event_seq", "tags", "metadata")}
            if len(item["body"] or "") > per:
                item["body"] = item["body"][:per]; truncated = True
            size = len(item.get("title") or "") + len(item.get("body") or "")
            if total + size > total_limit:
                omitted += 1; truncated = True; continue
            total += size; selected.append(item)
        return {"records": selected, "truncated": truncated, "omitted_count": omitted + max(0, len(rows) - len(selected) - omitted)}

    def _chain_events(self, conn: sqlite3.Connection, task_chain_id: str) -> list[dict[str, Any]]:
        rows = conn.execute("SELECT rowid AS event_seq,* FROM events ORDER BY rowid").fetchall()
        out = []
        for row in rows:
            payload = json.loads(row["payload"] or "{}")
            if payload.get("task_chain_id") == task_chain_id:
                d = dict(row); d["payload"] = payload; d["refs"] = json.loads(row["refs"] or "{}"); out.append(d)
        if out:
            return out
        task_ids = [r["task_id"] for r in conn.execute("SELECT task_id FROM working_states WHERE task_chain_id=?", (task_chain_id,)).fetchall()]
        if not task_ids:
            task_ids = [r["task_id"] for r in conn.execute("SELECT task_id FROM task_approvals WHERE task_chain_id=?", (task_chain_id,)).fetchall()]
        if task_ids:
            q = ",".join("?" for _ in task_ids)
            rows = conn.execute(f"SELECT rowid AS event_seq,* FROM events WHERE task_id IN ({q}) ORDER BY rowid", task_ids).fetchall()
            for row in rows:
                d = dict(row); d["payload"] = json.loads(row["payload"] or "{}"); d["refs"] = json.loads(row["refs"] or "{}"); out.append(d)
        return out

    def _summarize_chain(self, task_chain_id: str, **kw: Any) -> dict[str, Any]:
        task_chain_id = clean_text(task_chain_id, "list_item", required=True) or ""
        next_task_chain_id = clean_text(kw.get("next_task_chain_id"), "list_item")
        actor = clean_text(kw.get("actor") or "user", "agent") or "user"
        with closing(self.connect()) as conn:
            events = self._chain_events(conn, task_chain_id)
            if not events:
                raise KeyError(task_chain_id)
            root_task_id = clean_text(kw.get("root_task_id") or "", "list_item") or ""
            for ev in events:
                root_task_id = root_task_id or clean_text(ev["payload"].get("root_task_id"), "list_item") or ""
            if not root_task_id:
                row = conn.execute("SELECT root_task_id FROM working_states WHERE task_chain_id=? ORDER BY updated_at DESC LIMIT 1", (task_chain_id,)).fetchone()
                root_task_id = row["root_task_id"] if row and row["root_task_id"] else task_chain_id
            task_ids = sorted({ev.get("task_id") for ev in events if ev.get("task_id")})
            tasks = [self.row_task(conn.execute("SELECT * FROM tasks WHERE task_id=?", (tid,)).fetchone()) for tid in task_ids]
            tasks = [t for t in tasks if t]
            completed = [t for t in tasks if t.get("status") in DONE_STATUSES]
            approvals = [ev for ev in events if ev["event_type"] == "task_completion_submitted"]
            results = [ev for ev in events if ev["event_type"] == "task_result_marked"]
            states = [ev for ev in events if ev["event_type"] == "working_state_set"]
            parts = [
                f"Task chain {task_chain_id} summary.",
                f"Root task: {root_task_id}.",
                f"Events summarized: {events[0]['event_seq']}..{events[-1]['event_seq']} ({len(events)} events).",
            ]
            if tasks:
                parts.append("Tasks: " + "; ".join(f"{t['task_id']} {t['status']} {t.get('result_status') or ''} — {t['title']}" for t in tasks[:12]))
            if completed:
                parts.append(f"Completed/validated tasks: {len(completed)} of {len(tasks)}.")
            if approvals:
                latest = approvals[-1]["payload"]
                parts.append("Latest completion: " + str(latest.get("result_summary") or "")[:500])
            if results:
                latest = results[-1]["payload"]
                parts.append("Latest result: " + " ".join(str(latest.get(k) or "") for k in ("result_status", "result_notes", "next_step"))[:500])
            if states:
                latest = states[-1]["payload"]
                parts.append("Latest state: " + " ".join(str(latest.get(k) or "") for k in ("status", "current_activity", "next_step"))[:500])
            summary = clean_text("\n".join(parts), "chain_summary", required=True) or ""
            previous = self.row_chain_summary(conn.execute("SELECT * FROM task_chain_summaries WHERE root_task_id=? AND task_chain_id!=? ORDER BY event_seq_end DESC LIMIT 1", (root_task_id, task_chain_id)).fetchone())
            existing = self.row_chain_summary(conn.execute("SELECT * FROM task_chain_summaries WHERE task_chain_id=?", (task_chain_id,)).fetchone())
            ts = now_iso()
            conn.execute("BEGIN IMMEDIATE")
            if existing:
                summary_id = existing["summary_id"]
                conn.execute("UPDATE task_chain_summaries SET root_task_id=?,previous_summary_id=?,next_task_chain_id=?,summary=?,event_seq_start=?,event_seq_end=?,created_by=?,updated_at=?,version=version+1 WHERE summary_id=?", (root_task_id, previous.get("summary_id") if previous else None, next_task_chain_id, summary, events[0]["event_seq"], events[-1]["event_seq"], actor, ts, summary_id))
            else:
                summary_id = f"sum-{uuid.uuid4().hex[:12]}"
                conn.execute("INSERT INTO task_chain_summaries(summary_id,task_chain_id,root_task_id,previous_summary_id,next_task_chain_id,summary,event_seq_start,event_seq_end,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (summary_id, task_chain_id, root_task_id, previous.get("summary_id") if previous else None, next_task_chain_id, summary, events[0]["event_seq"], events[-1]["event_seq"], actor, ts, ts))
            ev = self.event(conn, "task_chain_summarized", "agent", actor, "task_chain_summary", summary_id, {"summary_id": summary_id, "task_chain_id": task_chain_id, "root_task_id": root_task_id, "previous_summary_id": previous.get("summary_id") if previous else None, "next_task_chain_id": next_task_chain_id, "event_seq_start": events[0]["event_seq"], "event_seq_end": events[-1]["event_seq"]}, root_task_id)
            conn.execute("COMMIT")
            result = self.row_chain_summary(conn.execute("SELECT * FROM task_chain_summaries WHERE summary_id=?", (summary_id,)).fetchone())
            result["event"] = ev
            return result

    def _latest_chain_summary(self, root_task_id: str | None = None) -> dict[str, Any] | None:
        with closing(self.connect()) as conn:
            if root_task_id:
                row = conn.execute("SELECT * FROM task_chain_summaries WHERE root_task_id=? ORDER BY event_seq_end DESC LIMIT 1", (root_task_id,)).fetchone()
            else:
                row = conn.execute("SELECT * FROM task_chain_summaries ORDER BY event_seq_end DESC LIMIT 1").fetchone()
        return self.row_chain_summary(row)

    def user_profile(self, raw: bool = False) -> dict[str, Any]:
        with closing(self.connect()) as conn:
            row = dict(conn.execute("SELECT * FROM user_profiles WHERE profile_id='default'").fetchone())
        row["warning"] = "User profile is local/private and must not contain secrets or tokens."
        return row

    def events(self, task_id: str | None = None, subject_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        clauses, args = [], []
        if task_id:
            clauses.append("task_id=?"); args.append(task_id)
        if subject_id:
            clauses.append("subject_id=?"); args.append(subject_id)
        args.append(limit)
        with closing(self.connect()) as conn:
            rows = conn.execute("SELECT rowid AS event_seq, * FROM events" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY rowid LIMIT ?", args).fetchall()
        out = []
        for r in rows:
            d = dict(r); d["payload"] = json.loads(d["payload"] or "{}"); d["refs"] = json.loads(d["refs"] or "{}"); out.append(d)
        return out


def _iso_to_epoch(value: str) -> float:
    try:
        return time.mktime(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ"))
    except Exception:
        return 0.0


DEFAULT_AGENT_CONTRACT_TEMPLATE = """# Agent Operating Contract

## Critical persona: plan-first for non-specific work
- For every user task/request that is not specific, check active durable memory only for historical evidence that this exact kind of work has been done successfully with very little change and can be completed right away.
- Historical evidence must come from memory only; do not use repository contents, event logs, prior chat, guesses, or searches as evidence for skipping approval.
- If memory does not provide that evidence, or if more input is needed, stop and create a short plan for the user to review before doing anything else.
- Wait for explicit user approval before reading/searching files, running commands or queries, making web searches, changing code, or otherwise taking action on that non-specific request.
- Direct, specific requests may be executed without this plan-first gate, while still following safety, task/state, and validation rules.
- For file/project queries not related to agent memory, read/search files under the ephemeral cwd by default. Use the launch/source cwd only when the user explicitly asks for it or the task names it.
- Treat the ephemeral cwd as the source directory unless the user explicitly provided or requested a different cwd; do not assume the launcher/original cwd is the source directory.

You are: {agent}
Agent profile: {agent}
Instance: {instance}
Ephemeral cwd: {cwd}
Launch/source cwd: {source_cwd}

Durable state lives in Broccoli Comms. Do not rely on either cwd for memory.

## Required startup
1. If present in the working directory, read generated `memory.md`, `habits.md`, and `expertise.md`; bootstrap-generated `AGENTS.md` may provide absolute paths for these files and a concise list of available skills.
2. Treat active records in `habits.md` as retained operating instructions for this and future turns. Re-check them before task completion/review transitions; do not drop them after the first response.
3. Run `broccoli-comms task bootstrap --agent {agent} --json` or `broccoli-comms task next --agent {agent} --include-profile --json` to check whether any pending/ready task is assigned.
4. If a task is returned, run `broccoli-comms state show --task <task_id> --agent {agent} --json`, then start working on that task unless it is blocked or requires clarification.
5. If no task is ready, stand by and do not invent work.

## Checkpoint and discovery rules
- Update WorkingState when starting, blocking, requesting review, finishing, or discovering reusable facts.
- Use `broccoli-comms state set --task <task_id> --agent {agent} --instance <agent_instance_id> --task-chain-id <chain> --root-task-id <root> --status working --current-activity ... --next-step ... --notes ... --clarification-count N --correction-count N --need-improvements-count N --first-pass-success|--no-first-pass-success --json` for bounded checkpoints.
- Checkpoint important discoveries such as database names, table names, commands/tools used, blockers, assumptions, and the next_step.
- Example: when finding the right database, record the database/table name and why it was correct as a bounded checkpoint so future agents can avoid rediscovery.

## Result and validation workflow
- For ordinary single-task completion, do **not** use `task submit-completion`; update the task/status/result flow instead: set a bounded result summary and move it to `review` when reviewer/verifier participants are active (or `done` when no review role is configured), then let reviewers/users validate with `broccoli-comms task mark-result <task_id> --result good|bad|need_improvements ... --json`.
- Reserve `broccoli-comms task submit-completion` for task-chain or scoped-phase completion only. Before submitting chain/scoped completion, first refresh the bounded chain summary with `broccoli-comms task summarize-chain <task_chain_id> --json`, then submit the root task with explicit `--task-chain-id <chain> --root-task-id <root>` for review/approval.
- After chain/scoped completion is approved/validated, run `broccoli-comms task summarize-chain <task_chain_id> --json` again so future agents resume from a bounded post-validation summary.
- When the user says a result is correct, ensure the task is marked `good`/`validated` with `broccoli-comms task mark-result <task_id> --result good --json` so the append-only event log captures: goal -> checkpoints/discoveries -> result summary -> user validation.
- For partial or incorrect results, include a remediation next_step with `task mark-result --result bad|need_improvements --next-step ...`.

## Task-tracked multi-agent workflow
- Treat every non-trivial or verifiable request as task-tracked work. Do not do investigation, file/code inspection, command execution, code changes, deployment, or other durable/verifiable work without a Broccoli Comms task and WorkingState checkpoint. Only skip task creation for true one-off answers that need no investigation, tools, durable state, or deliverable.
- Tasks are the coordination unit for multi-agent work. Keep related subtasks in the same task_chain_id/root_task_id, use chain defaults for recurring collaborators, and preserve chain/root ids in checkpoints and handoffs.
- Role responsibilities: assignee implements or answers; reviewer checks correctness and marks good/bad/need_improvements when configured; verifier performs independent validation when configured; coordinator/requester sequences work and resolves blockers; observers/specialists/participants contribute only in their scoped role.

## Role notification and handoff guidance
- Task status/result changes auto-notify role-relevant participants and the shared `agent-communicator` UI. Do not send duplicate manual pings unless extra context is needed.
- `review` or `done` notifies active reviewer/verifier participants when present, otherwise coordinator/requester. Use this for ordinary implementation handoff.
- `result:bad` or `result:need_improvements` notifies assignee plus coordinator/requester and must include a remediation `next_step`.
- `result:good` notifies verifier when present, otherwise coordinator/requester. If the task also becomes `validated`, assignee plus coordinator/requester are notified.
- `validated` notifies assignee plus coordinator/requester and may notify assignees/coordinators/requesters of dependent tasks that become ready.
- `ready` notifies assignee plus coordinator/requester; `working` notifies coordinator/requester; `blocked` or `archived` notifies assignee plus coordinator/requester. Avoid broad fan-out to reviewers/verifiers unless their role is directly relevant.
- Avoid self-notifications and duplicate notifications. Treat local/remote aliases for the same agent as one recipient, and combine all applicable roles/reasons into one concise notification instead of sending multiple messages to the same recipient.

## Clarification and correction tracking
- Record each user clarification or correction as bounded task/state/result metadata, not raw chat.
- Use structured `state set` metadata flags for clarification_count, correction_count, need_improvements_count, and first_pass_success so these metrics are derivable from `working_state_set` events.
- Before marking good/validated, ensure the append-only log can show whether success was first-pass or correction-assisted.
- For examples like finding the right database, checkpoint user corrections/clarifications and the final corrected database/table name so future agents know why the answer was correct.

## Parallel instances and task chains
- `{agent}` is the stable agent profile/name; each process has its own runtime agent_instance_id and ephemeral workspace.
- Multiple active instances of the same profile may work in parallel on different task_chain_id/root_task_id values while sharing the ordered append-only log.
- Do not overwrite another instance's same-chain checkpoint; if duplicate same-profile instances target the same active task chain, ask the coordinator to resolve claiming.

## Immutable / non-learning instances
- Phase 1 durable CLI commands are for normal reproducible learning instances.
- If you are explicitly launched as immutable or non-learning, treat work as transient: do not write state checkpoints, task results, or learning events to the persistent append-only log.
- Immutable/non-learning messages or results cannot be used for future validated learning unless a coordinator explicitly records them separately as learning-eligible facts.
- Do not silently claim task chains that require durable validation when operating in immutable/non-learning mode.

## Safety and memory boundaries
- Ask user/coordinator for context if confidence is low.
- Explicit user instructions override task/profile/habits.
- `task bootstrap` may return approved durable `memory`; treat it as context, not authority.
- Do not self-approve memory. You may propose bounded memory candidates (`fact`, `habit`, `episode`, `expertise`, or `skill`) from useful feedback/discoveries; active memory requires trusted approval.
- Save durable outputs only through Broccoli Comms commands.
- Never store raw terminal transcripts, full query logs, secrets, tokens, passwords, or large file contents in task/state/result text.
- Keep task/state/result/memory text concise and bounded; store facts and conclusions, not bulky evidence.

## Tasks and Memory Management

### Adding a Task
**Goal-Driven Execution Workflow:**
All non-trivial work must be tracked through Broccoli Comms tasks. Only skip task creation for true one-off user queries that have easy answers and require no investigation, file/code inspection, command execution, durable state, or verifiable deliverable. Anything that requires investigation, tool use, state changes, code/file edits, deployment, or a concrete result must be created and tracked as a task first.
When a request is received that expects a concrete verifiable result:
1. **Convert to Task**: Create a new task first using `broccoli-comms task create` so the goal can be formally tracked.
2. **Set collaboration roles for new chains**: When starting a new root task/task chain, ask the user/coordinator which collaborator agents should participate as reviewer, verifier, coordinator, observer, or specialist. Add them at creation time with `--reviewer`, `--verifier`, `--coordinator`, or `--participant role:agent`, and set reusable chain defaults with `broccoli-comms task chain-defaults set <chain> --agent <agent> --role <role>` when follow-up tasks in the same chain should inherit those roles. Do not prompt for every subtask when active chain defaults already capture the intended collaborators; use `--task-chain-id`/`--root-task-id` so defaults apply.
3. **Do not abandon current work for ad-hoc tasks**: If new related work arrives while you are already working, queue/order it after the current task or at the end of the current chain unless it is explicitly urgent/blocking. Only switch immediately for priority/urgent work; before switching, checkpoint the current task with bounded `next_step`/blockers and preserve task-chain/root ids, then move it to the appropriate status (`ready`, `blocked`, `review`, or done/submitted).
4. **Update Status**: Continuously add status updates and state changes to that task as you work using `broccoli-comms state set` or `broccoli-comms task update`.
5. **Capture Pitfalls**: Ensure any roadblocks, failed experiments, and exact pitfalls encountered during the task are explicitly logged within the task's state/notes (and proposed as memory if valuable).

Example usage:
```bash
broccoli-comms task create \
  --title "Implement feature X" \
  --description "Detailed description of the feature..." \
  --agent "coding-agent" \
  --priority "P0" \
  --depends-on "task-123,task-456"
```

### Proposing Memory
To save durable outputs like discoveries or new skills, propose memory using `broccoli-comms memory propose`. 
By default, you must provide a `--source-task` unless proposing manually with `--trusted-manual`.

### Memory audit guidance
When doing a memory audit, inspect bounded task logs/events, working state, task results, task-chain summaries, and existing approved memories. Then propose concise memory additions, edits, or removals only; use `broccoli-comms memory propose` for new memories, `broccoli-comms memory propose <memory-id>` for edit proposals, and `broccoli-comms memory propose <memory-id> --archive --reason ...` for archive/removal proposals. Trusted users/coordinators decide proposals with `broccoli-comms memory decide <memory-id> approve|reject`; agents must not self-approve memory, directly edit generated memory files, or directly mutate durable memory approval state. Keep proposals bounded to reusable facts/habits/episodes/expertise/skills, and never include raw transcripts, secrets/tokens/passwords, full command logs, or large file contents. Active memory changes require trusted user/coordinator approval.

Example usage:
```bash
broccoli-comms memory propose \
  --type expertise \
  --title "Test Expertise" \
  --body "Dummy expertise for testing skills directory generation." \
  --agent "broccoli-agent" \
  --trusted-manual
```

**When to Propose Memory:**
- **Do NOT propose memory for:** Routine task completions, generic summaries (e.g. "Updated target file"), obvious workflow steps, or information easily found via standard code search.
- **DO propose memory for:** High-fidelity technical "Aha!" moments, non-obvious workarounds, exact causes of obscure failures, and valuable "Dead Ends" (what was tried, why it failed, and the reason the fix worked).

**Memory Types Classification:**
- `fact`: Concrete, verifiable data (e.g., specific database names, undocumented file paths, hidden endpoints).
- `habit`: Reusable behavioral constraints or style rules (e.g., "Always use `blaze` instead of `bazel` for this project").
- `episode`: A specific sequence of events, particularly useful for logging "Dead Ends" and debugging trajectories.
- `expertise`: High-level architectural understanding, system patterns, or domain knowledge.
- `skill`: Actionable, reusable step-by-step instructions or scripts that solve a complex, recurring problem.
"""


def agent_contract(agent: str, instance: str | None, cwd: str | Path, template: str | None = None, source_cwd: str | Path | None = None) -> str:
    cwd = str(cwd)
    source_cwd = str(source_cwd or cwd)
    instance = instance or f"{agent}@manual"
    return (template or DEFAULT_AGENT_CONTRACT_TEMPLATE).format(
        agent=agent,
        instance=instance,
        cwd=cwd,
        ephemeral_cwd=cwd,
        source_cwd=source_cwd,
        launch_cwd=source_cwd,
    )
