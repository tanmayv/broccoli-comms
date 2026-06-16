import base64
import binascii
import errno
import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import sys
from pathlib import Path

# Provide access to agent-tracker config parser
_repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_repo_root / "agent-tracker"))
import config
import pane_output_registry
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOG = logging.getLogger("agent-registry")

MESSAGE_METADATA_FIELDS = (
    "content_type", "approval_id", "task_id", "task_title", "task_status", "task_assigned_agent", "task_next_step",
    "result_summary", "task_chain_id", "root_task_id", "task_version_at_submission",
    "created_event_seq", "event_seq_at_submission", "source", "sender_source",
    "memory_id", "memory_type", "memory_title", "memory_scope", "memory_status",
    "memory_version", "source_task_id", "recipient_agent", "recipient_kind",
    "delivery_scope", "delivery_id", "target_logical_identity",
)


def _message_metadata(body):
    return {key: body.get(key) for key in MESSAGE_METADATA_FIELDS if key in body}


def _env_bool(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"", "0", "false", "no", "off"}


def _env_int(name, default):
    value = os.environ.get(name)
    if value is None or str(value).strip() == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        LOG.warning("invalid integer env %s=%r; using default %r", name, value, default)
        return default


def _env_str(name, default):
    return os.environ.get(name) or default


TOKEN = os.environ.get("AGENT_REGISTRY_TOKEN") or config.get("registry", "token", "")
AUTH_REQUIRED = _env_bool("AGENT_REGISTRY_AUTH", config.get("registry", "auth_enabled", True))
AGENT_REGISTRY_BROAD_WATCH_ALLOWED = _env_bool("AGENT_REGISTRY_BROAD_WATCH_ALLOWED", config.get("registry", "broad_watch_allowed", False))
MAX_BODY_BYTES = _env_int("AGENT_REGISTRY_MAX_BODY_BYTES", config.get("tracker", "max_delivery_bytes", 5242880))
STALE = _env_int("TRACKER_STALE_SECONDS", config.get("tracker", "heartbeat_stale_seconds", 60))
GONE = _env_int("TRACKER_GONE_SECONDS", config.get("tracker", "heartbeat_gone_seconds", 180))
DELIVERY_WAIT_SECONDS = _env_int("AGENT_REGISTRY_DELIVERY_WAIT_SECONDS", config.get("registry", "delivery_wait_seconds", 25))
REMOTE_PANE_INPUT_MAX_TEXT_BYTES = _env_int("AGENT_REGISTRY_REMOTE_PANE_INPUT_MAX_TEXT_BYTES", config.get("registry", "remote_pane_input_max_text_bytes", 4096))
REMOTE_PANE_INPUT_MAX_KEYS = _env_int("AGENT_REGISTRY_REMOTE_PANE_INPUT_MAX_KEYS", config.get("registry", "remote_pane_input_max_keys", 16))
STATE_PATH = _env_str("AGENT_REGISTRY_STATE_PATH", config.get("paths", "registry_state", os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "agent-registry", "state.json")))
CURRENT_TASK_FIELD_LIMITS = {
    "current_task": 200,
    "current_task_id": 200,
    "current_task_status": 50,
    "current_task_next_step": 1000,
}


def _bounded_public_text(value, max_len):
    if not isinstance(value, str):
        return ""
    cleaned = "".join(ch if ord(ch) >= 32 else " " for ch in value).strip()
    return cleaned[:max_len]


def _sanitize_current_task_fields(agent):
    agent = agent or {}
    return {key: _bounded_public_text(agent.get(key), limit) for key, limit in CURRENT_TASK_FIELD_LIMITS.items()}


def _sanitize_public_agent_fields(agent):
    return {**agent, **_sanitize_current_task_fields(agent)}


class Store:
    def __init__(self, state_path=None, message_events_path=None, pane_output_events_path=None):
        self.state_path = state_path if state_path is not None else STATE_PATH
        if message_events_path is not None:
            self.message_events_path = message_events_path
        elif self.state_path:
            self.message_events_path = os.path.join(os.path.dirname(self.state_path) or ".", "message-events.sqlite")
        else:
            self.message_events_path = None
        if pane_output_events_path is not None:
            self.pane_output_events_path = pane_output_events_path
        elif self.state_path:
            self.pane_output_events_path = os.path.join(os.path.dirname(self.state_path) or ".", "pane-output-events.sqlite")
        else:
            self.pane_output_events_path = None
        self.trackers = {}
        self.agents = {}
        self.deliveries = {}
        self.tracker_events = {}
        self.remote_watch_leases = {}  # target_tracker_id -> {(source_tracker_id, client_id): {expires_at, source_tracker_id, watch_targets}}
        self.lock = threading.RLock()
        self.cv = threading.Condition(self.lock)
        self._load_locked()
        self._init_message_events_store()
        self._init_pane_output_events_store()

    def _load_locked(self):
        with self.lock:
            try:
                with open(self.state_path, "r") as f:
                    data = json.load(f)
            except FileNotFoundError:
                LOG.info("registry state file not found yet at %s; starting empty", self.state_path)
                return
            except Exception as e:
                LOG.warning("failed to load registry state from %s: %s", self.state_path, e)
                return
            self.trackers = data.get("trackers") or {}
            self.agents = data.get("agents") or {}
            self.deliveries = {
                tracker_id: {item.get("delivery_id") or item["message_id"]: item for item in queue}
                for tracker_id, queue in (data.get("deliveries") or {}).items()
                if isinstance(queue, list)
            }
            self.tracker_events = {
                tracker_id: {item["event_id"]: item for item in queue}
                for tracker_id, queue in (data.get("tracker_events") or {}).items()
                if isinstance(queue, list)
            }
            LOG.info(
                "loaded registry state from %s trackers=%s agents=%s queued_trackers=%s",
                self.state_path,
                len(self.trackers),
                len(self.agents),
                len(self.deliveries),
            )

    def _init_message_events_store(self):
        if not self.message_events_path:
            return
        db_dir = os.path.dirname(self.message_events_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        conn = None
        try:
            conn = sqlite3.connect(self.message_events_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS message_events (
                    message_id TEXT PRIMARY KEY,
                    timestamp TEXT,
                    event_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_message_events_timestamp ON message_events(timestamp)")
            conn.commit()
        except Exception as e:
            LOG.warning("failed to initialize message events store %s: %s", self.message_events_path, e)
        finally:
            if conn is not None:
                conn.close()

    def _message_event_connection(self):
        self._init_message_events_store()
        return sqlite3.connect(self.message_events_path)

    def _init_pane_output_events_store(self):
        if not self.pane_output_events_path:
            return
        db_dir = os.path.dirname(self.pane_output_events_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        conn = None
        try:
            conn = sqlite3.connect(self.pane_output_events_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pane_output_events (
                    event_id TEXT PRIMARY KEY,
                    source_tracker_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    event_json TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pane_output_events_expires ON pane_output_events(expires_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pane_output_events_created ON pane_output_events(created_at)")
            conn.commit()
        except Exception as e:
            LOG.warning("failed to initialize pane output events store %s: %s", self.pane_output_events_path, e)
        finally:
            if conn is not None:
                conn.close()

    def _pane_output_event_connection(self):
        self._init_pane_output_events_store()
        return sqlite3.connect(self.pane_output_events_path)

    def append_message_event(self, event):
        message_id = event.get("message_id")
        if not isinstance(message_id, str) or not message_id:
            raise ValueError("message_id is required")
        swarms = event.get("swarms")
        if not isinstance(swarms, list):
            raise ValueError("swarms must be a list")
        normalized = {
            "schema_version": event.get("schema_version") or 1,
            "message_id": message_id,
            "delivery_id": event.get("delivery_id"),
            "timestamp": event.get("timestamp") or time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
            "sender_tracker_id": event.get("sender_tracker_id"),
            "sender_hostname": event.get("sender_hostname"),
            "sender_agent_id": event.get("sender_agent_id"),
            "sender_agent_name": event.get("sender_agent_name"),
            "recipient_tracker_id": event.get("recipient_tracker_id"),
            "recipient_hostname": event.get("recipient_hostname"),
            "recipient_agent_id": event.get("recipient_agent_id"),
            "recipient_agent_name": event.get("recipient_agent_name"),
            "swarms": swarms,
            "message": event.get("message"),
            "attachments": event.get("attachments") or [],
        }
        for key in ("membership_snapshot", "direction", "source"):
            if key in event:
                normalized[key] = event.get(key)
        encoded = json.dumps(normalized, sort_keys=True)
        conn = None
        with self.lock:
            try:
                conn = self._message_event_connection()
                cur = conn.execute(
                    "INSERT OR IGNORE INTO message_events(message_id, timestamp, event_json, created_at) VALUES (?, ?, ?, ?)",
                    (message_id, normalized["timestamp"], encoded, time.time()),
                )
                inserted = cur.rowcount > 0
                conn.commit()
            finally:
                if conn is not None:
                    conn.close()
        if inserted:
            LOG.info("stored registry message event message_id=%s swarms=%s", message_id, [s.get("name") for s in swarms if isinstance(s, dict)])
        return inserted, normalized

    def query_message_events(self, swarm_name, limit=200):
        if not isinstance(swarm_name, str) or not swarm_name:
            raise ValueError("swarm is required")
        limit = max(1, min(int(limit), 1000))
        conn = None
        with self.lock:
            try:
                conn = self._message_event_connection()
                rows = conn.execute(
                    "SELECT event_json FROM message_events ORDER BY timestamp ASC, created_at ASC"
                ).fetchall()
            finally:
                if conn is not None:
                    conn.close()
        events = []
        for (encoded,) in rows:
            try:
                event = json.loads(encoded)
            except json.JSONDecodeError:
                continue
            if any(isinstance(item, dict) and item.get("name") == swarm_name for item in event.get("swarms") or []):
                events.append(event)
        return events[-limit:]

    def append_pane_output_event(self, event):
        normalized = pane_output_registry.validate_registry_event(event)
        agent = self.get_agent(normalized["agent_id"])
        if not agent:
            raise ValueError("agent_not_found")
        if agent.get("tracker_id") != normalized["source_tracker_id"]:
            raise PermissionError("wrong_tracker")
        tracker = self.get_tracker(normalized["source_tracker_id"]) or {}
        if tracker.get("hostname") != normalized["source_hostname"]:
            raise PermissionError("wrong_tracker")
        encoded = json.dumps(normalized, sort_keys=True)
        inserted = False
        conn = None
        with self.lock:
            try:
                conn = self._pane_output_event_connection()
                now = time.time()
                conn.execute("DELETE FROM pane_output_events WHERE expires_at <= ?", (now,))
                cur = conn.execute(
                    "INSERT OR IGNORE INTO pane_output_events(event_id, source_tracker_id, agent_id, created_at, expires_at, event_json) VALUES (?, ?, ?, ?, ?, ?)",
                    (normalized["event_id"], normalized["source_tracker_id"], normalized["agent_id"], normalized["created_at"], normalized["expires_at"], encoded),
                )
                inserted = cur.rowcount > 0
                conn.commit()
            finally:
                if conn is not None:
                    conn.close()
        if inserted:
            LOG.info("stored pane-output event metadata event_id=%s source_tracker_id=%s agent_id=%s event_type=%s", normalized["event_id"], normalized["source_tracker_id"], normalized["agent_id"], normalized["event_type"])
            self._fanout_pane_output_event(normalized, agent)
        return inserted, normalized

    def query_pane_output_events(self, limit=200):
        limit = max(1, min(int(limit), 1000))
        now = time.time()
        conn = None
        with self.lock:
            try:
                conn = self._pane_output_event_connection()
                conn.execute("DELETE FROM pane_output_events WHERE expires_at <= ?", (now,))
                rows = conn.execute(
                    "SELECT event_json FROM pane_output_events WHERE expires_at > ? ORDER BY created_at ASC LIMIT ?",
                    (now, limit),
                ).fetchall()
                conn.commit()
            finally:
                if conn is not None:
                    conn.close()
        events = []
        for (encoded,) in rows:
            try:
                events.append(json.loads(encoded))
            except json.JSONDecodeError:
                continue
        return events

    def _fanout_pane_output_event(self, event, agent):
        now = time.time()
        target_tracker_id = event["source_tracker_id"]
        with self.lock:
            leases = self.remote_watch_leases.get(target_tracker_id) or {}
            for (watcher_tracker_id, client_id), lease in list(leases.items()):
                if lease["expires_at"] < now:
                    continue
                matched = False
                for watched in lease.get("watch_targets") or []:
                    if "/" not in watched:
                        continue
                    host, agent_ref = watched.split("/", 1)
                    if ":" in host:
                        _, host = host.split(":", 1)
                    if host == agent.get("hostname") and agent_ref in {agent.get("agent_id"), agent.get("name"), event.get("agent_id"), event.get("agent_name")}:
                        matched = True
                        break
                if matched:
                    self.enqueue_tracker_event(watcher_tracker_id, "pane_output_event", "registry", event)
                    LOG.info("fanned out pane-output event metadata event_id=%s watcher_tracker_id=%s client_id=%s", event.get("event_id"), watcher_tracker_id, client_id)

    def _persist_locked(self):
        if not self.state_path:
            return
        state_dir = os.path.dirname(self.state_path)
        if state_dir:
            os.makedirs(state_dir, exist_ok=True)
        tmp = f"{self.state_path}.tmp"
        with open(tmp, "w") as f:
            json.dump(
                {
                    "trackers": self.trackers,
                    "agents": self.agents,
                    "deliveries": {
                        tracker_id: sorted(queue.values(), key=lambda item: item.get("queued_at", 0))
                        for tracker_id, queue in self.deliveries.items()
                    },
                    "tracker_events": {
                        tracker_id: sorted(queue.values(), key=lambda item: item.get("created_at", 0))
                        for tracker_id, queue in self.tracker_events.items()
                    },
                },
                f,
            )
        os.replace(tmp, self.state_path)

    def sweep(self, now=None):
        with self.lock:
            now = time.time() if now is None else now
            changed = False
            for tracker in self.trackers.values():
                age = now - tracker["last_heartbeat"]
                status = "active" if age <= STALE else "stale" if age <= GONE else "gone"
                if tracker.get("status") != status:
                    LOG.info(
                        "tracker_id=%s hostname=%s status transition %s -> %s age=%.1fs",
                        tracker.get("tracker_id"),
                        tracker.get("hostname"),
                        tracker.get("status"),
                        status,
                        age,
                    )
                    tracker["status"] = status
                    changed = True
            
            # Sweep expired remote watch leases
            for target_tid, leases in list(self.remote_watch_leases.items()):
                for key, lease in list(leases.items()):
                    if lease["expires_at"] < now:
                        leases.pop(key)
                        LOG.info("AUDIT: Purged expired remote watch lease for client %s on target_tid=%s", key[1], target_tid)
                if not leases:
                    self.remote_watch_leases.pop(target_tid, None)

            agents = {
                agent_id: info
                for agent_id, info in self.agents.items()
                if self.trackers.get(info["tracker_id"], {}).get("status") != "gone"
            }
            deliveries = {
                tracker_id: queue
                for tracker_id, queue in self.deliveries.items()
                if self.trackers.get(tracker_id, {}).get("status") != "gone"
            }
            if agents != self.agents:
                self.agents = agents
                changed = True
            if deliveries != self.deliveries:
                self.deliveries = deliveries
                changed = True
            tracker_events = {
                tracker_id: queue
                for tracker_id, queue in self.tracker_events.items()
                if self.trackers.get(tracker_id, {}).get("status") != "gone"
            }
            if tracker_events != self.tracker_events:
                self.tracker_events = tracker_events
                changed = True
            if self.pane_output_events_path:
                conn = None
                try:
                    conn = self._pane_output_event_connection()
                    cur = conn.execute("DELETE FROM pane_output_events WHERE expires_at <= ?", (now,))
                    if cur.rowcount:
                        LOG.info("registry sweep removed expired pane-output events count=%s", cur.rowcount)
                    conn.commit()
                except Exception as e:
                    LOG.debug("failed to sweep pane-output events: %s", e)
                finally:
                    if conn is not None:
                        conn.close()
            if changed:
                LOG.info("registry sweep updated state trackers=%s agents=%s queued_trackers=%s", len(self.trackers), len(self.agents), len(self.deliveries))
                self._persist_locked()

    def _resolve_tracker_id_by_host(self, hostname: str) -> str | None:
        for tid, tracker in self.trackers.items():
            if tracker["hostname"] == hostname and tracker["status"] != "gone":
                return tid
        return None

    def put_watch_lease(self, source_tracker_id: str, client_id: str, watch_targets: list[str], lease_seconds: float, scope: str = "narrow") -> None:
        """Registers or replaces a lease-bound remote watch lease atomically."""
        now = time.time()
        expires_at = now + lease_seconds
        
        with self.lock:
            # First clear previous watches for this specific client
            self.clear_watch_leases(source_tracker_id, client_id)
            
            for target in watch_targets:
                if "/" not in target:
                    continue
                host, _ = target.split("/", 1)
                if ":" in host:
                    _, host = host.split(":", 1)
                target_tid = self._resolve_tracker_id_by_host(host)
                if not target_tid:
                    LOG.warning("put_watch_lease: could not resolve tracker_id for hostname=%s", host)
                    continue
                
                self.remote_watch_leases.setdefault(target_tid, {})
                self.remote_watch_leases[target_tid][(source_tracker_id, client_id)] = {
                    "expires_at": expires_at,
                    "source_tracker_id": source_tracker_id,
                    "watch_targets": set(watch_targets),
                    "scope": scope
                }
            LOG.info("AUDIT: Registered remote watch lease: source_tracker_id=%s client_id=%s scope=%s lease_seconds=%.1fs targets=%s", source_tracker_id, client_id, scope, lease_seconds, watch_targets)

    def clear_watch_leases(self, source_tracker_id: str, client_id: str) -> None:
        """Atomically clears all remote watch leases for a given client."""
        with self.lock:
            for target_tid, leases in list(self.remote_watch_leases.items()):
                key = (source_tracker_id, client_id)
                if key in leases:
                    leases.pop(key)
                    LOG.info("AUDIT: Cleared remote watch lease for source_tracker_id=%s client_id=%s on target_tid=%s", source_tracker_id, client_id, target_tid)

    def list_agents(self):
        with self.lock:
            return [_sanitize_public_agent_fields(agent) for agent in self.agents.values()]

    def get_agent(self, agent_id):
        with self.lock:
            agent = self.agents.get(agent_id)
            return _sanitize_public_agent_fields(dict(agent)) if agent else None

    def has_tracker(self, tracker_id):
        with self.lock:
            return tracker_id in self.trackers

    def get_tracker(self, tracker_id):
        with self.lock:
            tracker = self.trackers.get(tracker_id)
            return dict(tracker) if tracker else None

    def list_trackers(self):
        with self.lock:
            return [
                {
                    "tracker_id": t["tracker_id"],
                    "hostname": t["hostname"],
                    "address": t["address"],
                    "http_port": t["http_port"],
                    "status": t["status"],
                    "agent_configs": t.get("agent_configs") or [],
                }
                for t in self.trackers.values()
                if t["status"] != "gone"
            ]

    def put_tracker(self, body):
        with self.cv:
            existing = next((tid for tid, t in self.trackers.items() if t["hostname"] == body["hostname"] and tid != body["tracker_id"]), None)
            if existing:
                LOG.warning("replacing existing tracker_id=%s for hostname=%s with tracker_id=%s", existing, body["hostname"], body["tracker_id"])
                self.trackers.pop(existing, None)
                self.agents = {k: v for k, v in self.agents.items() if v["tracker_id"] != existing}
                self.deliveries.pop(existing, None)
                self.tracker_events.pop(existing, None)
            created = body["tracker_id"] not in self.trackers
            self.trackers[body["tracker_id"]] = {
                "tracker_id": body["tracker_id"],
                "hostname": body["hostname"],
                "address": body["address"],
                "http_port": body["http_port"],
                "last_heartbeat": time.time(),
                "status": "active",
                "agent_configs": body.get("agent_configs") or [],
            }
            self._replace_agents_locked(body["tracker_id"], body.get("agents", []))
            self.deliveries.setdefault(body["tracker_id"], {})
            self.tracker_events.setdefault(body["tracker_id"], {})
            self._persist_locked()
            self.cv.notify_all()
            LOG.info(
                "tracker %s tracker_id=%s hostname=%s http_port=%s agents=%s",
                "registered" if created else "updated",
                body["tracker_id"],
                body["hostname"],
                body["http_port"],
                len(body.get("agents", [])),
            )
            return created

    def _replace_agents_locked(self, tracker_id, agents):
        tracker, now = self.trackers[tracker_id], time.time()
        self.agents = {k: v for k, v in self.agents.items() if v["tracker_id"] != tracker_id}
        for agent in agents:
            self.agents[agent["agent_id"]] = {
                **_sanitize_public_agent_fields(agent),
                "tracker_id": tracker_id,
                "hostname": tracker["hostname"],
                "last_seen": now,
                "address": tracker["address"],
                "http_port": tracker["http_port"],
            }

    def heartbeat(self, tracker_id, agents, agent_configs=None):
        with self.cv:
            if tracker_id not in self.trackers:
                LOG.warning("heartbeat for unknown tracker_id=%s agents=%s", tracker_id, len(agents))
                return False
            self.trackers[tracker_id]["last_heartbeat"] = time.time()
            self.trackers[tracker_id]["status"] = "active"
            self.trackers[tracker_id]["agent_configs"] = agent_configs or []
            self._replace_agents_locked(tracker_id, agents)
            self._persist_locked()
            self.cv.notify_all()
            return True

    def update_agent(self, tracker_id, agent_id, status):
        with self.cv:
            agent = self.agents.get(agent_id)
            if not agent:
                LOG.warning("agent-update for missing agent_id=%s tracker_id=%s status=%s", agent_id, tracker_id, status)
                return 404
            if agent["tracker_id"] != tracker_id:
                LOG.warning("agent-update wrong tracker agent_id=%s expected_tracker_id=%s got_tracker_id=%s", agent_id, agent["tracker_id"], tracker_id)
                return 403
            agent["status"], agent["last_seen"] = status, time.time()
            self._persist_locked()
            return 200

    def enqueue_delivery(self, tracker_id, payload):
        entry = {**payload, "message_id": payload.get("message_id") or str(uuid.uuid4()), "queued_at": time.time()}
        delivery_key = entry.get("delivery_id") or entry["message_id"]
        with self.cv:
            self.deliveries.setdefault(tracker_id, {})[delivery_key] = entry
            self._persist_locked()
            self.cv.notify_all()
            LOG.info(
                "queued delivery message_id=%s delivery_id=%s tracker_id=%s target_agent_id=%s sender_tracker=%s",
                entry["message_id"],
                entry.get("delivery_id"),
                tracker_id,
                entry.get("target_agent_id"),
                entry.get("sender_tracker"),
            )
        return entry

    def wait_for_deliveries(self, tracker_id, timeout):
        deadline = time.time() + max(timeout, 0)
        with self.cv:
            while True:
                queue = sorted(self.deliveries.get(tracker_id, {}).values(), key=lambda item: item.get("queued_at", 0))
                if queue:
                    LOG.info("returning %s queued deliveries to tracker_id=%s", len(queue), tracker_id)
                    return [dict(item) for item in queue]
                remaining = deadline - time.time()
                if remaining <= 0:
                    return []
                self.cv.wait(timeout=remaining)

    def ack_delivery(self, tracker_id, message_id):
        with self.cv:
            queue = self.deliveries.get(tracker_id)
            ack_key = message_id
            if queue and ack_key not in queue:
                ack_key = next((key for key, item in queue.items() if item.get("message_id") == message_id), message_id)
            if not queue or ack_key not in queue:
                LOG.warning("ack for unknown delivery tracker_id=%s message_id=%s", tracker_id, message_id)
                return False
            queue.pop(ack_key, None)
            if not queue:
                self.deliveries.pop(tracker_id, None)
            self._persist_locked()
            LOG.info("acked delivery tracker_id=%s ack_key=%s message_id=%s remaining=%s", tracker_id, ack_key, message_id, len(self.deliveries.get(tracker_id, {})))
            return True

    def enqueue_tracker_event(self, target_tracker_id, event_type, source_tracker_id, payload):
        LOG.info("queueing tracker event type=%s source=%s target=%s payload=%s", event_type, source_tracker_id, target_tracker_id, payload)
        entry = {
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "source_tracker_id": source_tracker_id,
            "target_tracker_id": target_tracker_id,
            "payload": payload or {},
            "created_at": time.time(),
        }
        with self.cv:
            self.tracker_events.setdefault(target_tracker_id, {})[entry["event_id"]] = entry
            self._persist_locked()
            self.cv.notify_all()
            return entry

    def wait_for_tracker_events(self, tracker_id, timeout):
        deadline = time.time() + max(timeout, 0)
        with self.cv:
            while True:
                queue = sorted(self.tracker_events.get(tracker_id, {}).values(), key=lambda item: item.get("created_at", 0))
                if queue:
                    LOG.info("returning %s queued tracker events to tracker_id=%s", len(queue), tracker_id)
                    return [dict(item) for item in queue]
                remaining = deadline - time.time()
                if remaining <= 0:
                    return []
                self.cv.wait(timeout=remaining)

    def ack_tracker_event(self, tracker_id, event_id):
        with self.cv:
            queue = self.tracker_events.get(tracker_id)
            if not queue or event_id not in queue:
                LOG.warning("ack for unknown tracker event tracker_id=%s event_id=%s", tracker_id, event_id)
                return False
            queue.pop(event_id, None)
            LOG.info("acked tracker event tracker_id=%s event_id=%s remaining=%s", tracker_id, event_id, len(queue))
            if not queue:
                self.tracker_events.pop(tracker_id, None)
            self._persist_locked()
            return True


def _fanout_remote_message_delivered(store, target_tracker_id, target_agent, msg_payload):
    """Dispatches remote_agent_event to all active remote watchers of the delivered message target."""
    now = time.time()
    with store.lock:
        leases = store.remote_watch_leases.get(target_tracker_id) or {}
        for (source_tracker_id, client_id), lease in list(leases.items()):
            if lease["expires_at"] < now:
                continue
            
            scope = lease.get("scope", "narrow")
            is_broad = True
            
            if msg_payload.get("sender_tracker_id") == source_tracker_id:
                is_broad = False
            elif target_agent.get("tracker_id") == source_tracker_id:
                is_broad = False
                
            if scope == "narrow" and is_broad:
                continue
                
            if scope == "broad" and is_broad and not AGENT_REGISTRY_BROAD_WATCH_ALLOWED:
                LOG.warning("AUDIT: Fanout blocked unauthorized passive observation event from %s to watcher t_id=%s", target_agent["hostname"], source_tracker_id)
                continue

            match = False
            for wt in lease["watch_targets"]:
                if "/" in wt:
                    _, agent_ref = wt.split("/", 1)
                    if agent_ref in {target_agent["agent_id"], target_agent["name"]}:
                        match = True
                        break
            
            if match:
                event_payload = {
                    "target_agent_id": f"{target_agent['hostname']}/{target_agent['agent_id']}",
                    "target_agent_name": f"{target_agent['hostname']}/{target_agent['name']}",
                    "sender": msg_payload.get("sender_name"),
                    "message_id": msg_payload.get("message_id"),
                    "message": msg_payload.get("message"),
                    "timestamp": msg_payload.get("sent_at"),
                    **_message_metadata(msg_payload),
                }
                store.enqueue_tracker_event(source_tracker_id, "remote_agent_event", "registry", event_payload)
                LOG.info("AUDIT: Fanned out remote watch event to source_tracker_id=%s client_id=%s (scope=%s, is_broad=%s)", source_tracker_id, client_id, scope, is_broad)


def _validate_attachments(body):
    seen_names = set()
    for att in body.get("attachments") or []:
        safe_name = os.path.basename(att.get("name") or "")
        if not safe_name or "content_b64" not in att:
            return "invalid attachments"
        if safe_name in seen_names:
            return "duplicate attachment name"
        seen_names.add(safe_name)
        try:
            base64.b64decode(att["content_b64"], validate=True)
        except (binascii.Error, ValueError):
            return "invalid attachment payload"
    return None


_SIMPLE_KEY_ALIASES = {
    "esc": "Escape", "escape": "Escape", "enter": "Enter", "return": "Enter", "ret": "Enter",
    "space": "Space", "spc": "Space", "tab": "Tab", "btab": "BTab", "backtab": "BTab",
    "bs": "Backspace", "backspace": "Backspace", "del": "Delete", "delete": "Delete",
    "ins": "Insert", "insert": "Insert", "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "home": "Home", "end": "End", "pgup": "PageUp", "pageup": "PageUp", "ppage": "PageUp",
    "pgdn": "PageDown", "pagedown": "PageDown", "npage": "PageDown",
}
_SIMPLE_KEYS = set(_SIMPLE_KEY_ALIASES.values()) | {f"F{i}" for i in range(1, 25)}
_MODIFIER_ALIASES = {"c": "C", "ctrl": "C", "control": "C", "m": "M", "meta": "M", "alt": "M", "s": "S", "shift": "S"}
_SHELL_LIKE_KEY_CHARS = set("|&;<>$`(){}[]*?~!#\"'\\\n\r")


def _remote_pane_input_registry_enabled():
    return config.get("registry", "remote_pane_input_enabled", False)


def _normalize_key_token(key):
    if not isinstance(key, str):
        raise ValueError("key must be a string")
    token = key.strip()
    if not token:
        raise ValueError("key must not be empty")
    if token != key or any(ch.isspace() for ch in token):
        raise ValueError("key must not contain whitespace")
    if any(ch in _SHELL_LIKE_KEY_CHARS for ch in token):
        raise ValueError("key contains unsupported characters")
    if token.endswith("-"):
        raise ValueError("key has trailing modifier")
    lower = token.lower()
    if lower in _SIMPLE_KEY_ALIASES:
        return _SIMPLE_KEY_ALIASES[lower]
    if re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", lower):
        return "F" + lower[1:]
    parts = token.split("-")
    if len(parts) == 1:
        if token in _SIMPLE_KEYS:
            return token
        raise ValueError(f"unknown key: {key}")
    if len(parts) > 3:
        raise ValueError("too many key modifiers")
    modifiers = []
    for raw_modifier in parts[:-1]:
        modifier = _MODIFIER_ALIASES.get(raw_modifier.lower())
        if not modifier:
            raise ValueError(f"unknown key modifier: {raw_modifier}")
        if modifier in modifiers:
            raise ValueError(f"duplicate key modifier: {raw_modifier}")
        modifiers.append(modifier)
    base_raw = parts[-1]
    if not base_raw:
        raise ValueError("key has empty base")
    base_lower = base_raw.lower()
    if base_lower in _SIMPLE_KEY_ALIASES:
        base = _SIMPLE_KEY_ALIASES[base_lower]
    elif re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", base_lower):
        base = "F" + base_lower[1:]
    elif len(base_raw) == 1 and base_raw.isalnum():
        base = base_raw.lower() if "C" in modifiers else base_raw
    else:
        raise ValueError(f"unknown key: {key}")
    return "-".join(modifiers + [base])


def _valid_request_id(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 200


def _pane_input_payload_error(body):
    if not body.get("sender_tracker_id"):
        return "sender_tracker_id is required"
    pane_input_id = body.get("pane_input_id")
    request_id = body.get("request_id")
    if not _valid_request_id(pane_input_id) or not _valid_request_id(request_id):
        return "pane_input_id and request_id must be non-empty strings up to 200 characters"
    mode = (body.get("input_type") or body.get("mode") or "").lower()
    if mode not in {"text", "keys"}:
        return "input_type must be text or keys"
    if mode == "text":
        text = body.get("text")
        if not isinstance(text, str) or not text:
            return "text must be a non-empty string"
        if len(text.encode("utf-8")) > REMOTE_PANE_INPUT_MAX_TEXT_BYTES:
            return f"text exceeds max bytes ({REMOTE_PANE_INPUT_MAX_TEXT_BYTES})"
        submit = body.get("submit", True)
        if not isinstance(submit, bool):
            return "submit must be a boolean"
    else:
        keys = body.get("keys")
        if isinstance(keys, str):
            keys = [keys]
        if not isinstance(keys, list) or not keys:
            return "keys must be a non-empty list"
        if len(keys) > REMOTE_PANE_INPUT_MAX_KEYS:
            return f"keys exceed max count ({REMOTE_PANE_INPUT_MAX_KEYS})"
        try:
            [_normalize_key_token(key) for key in keys]
        except ValueError as e:
            return str(e)
    if not body.get("target_agent_id") and not body.get("target_agent_name"):
        return "provide target_agent_id or target_agent_name"
    if body.get("target_agent_name") and not body.get("target_hostname"):
        return "target_hostname is required when using target_agent_name; bare-name global resolution is not supported"
    return None


def _pane_input_audit_fields(body, target=None, result=None):
    mode = (body.get("input_type") or body.get("mode") or "").lower()
    fields = {
        "pane_input_id": body.get("pane_input_id") or body.get("request_id"),
        "request_id": body.get("request_id") or body.get("pane_input_id"),
        "sender_tracker_id": body.get("sender_tracker_id"),
        "target_agent_id": (target or {}).get("agent_id") or body.get("target_agent_id"),
        "target_hostname": (target or {}).get("hostname") or body.get("target_hostname"),
        "mode": mode,
        "result": result,
    }
    if mode == "text" and isinstance(body.get("text"), str):
        encoded = body["text"].encode("utf-8")
        fields["text_bytes"] = len(encoded)
        fields["text_sha256"] = hashlib.sha256(encoded).hexdigest()[:16]
    elif mode == "keys":
        keys = body.get("keys") if isinstance(body.get("keys"), list) else [body.get("keys")]
        fields["key_count"] = len([k for k in keys if k is not None])
    return fields


def make_handler(store=None, token=None, auth_required=None, remote_pane_input_enabled=None):
    store, token = store or Store(), TOKEN if token is None else token
    auth_required = AUTH_REQUIRED if auth_required is None else auth_required
    remote_pane_input_enabled = _remote_pane_input_registry_enabled() if remote_pane_input_enabled is None else remote_pane_input_enabled

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def _json(self, code, payload):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                self.wfile.write(json.dumps(payload).encode())
            except (BrokenPipeError, ConnectionResetError) as e:
                LOG.debug("client disconnected while writing response path=%s error=%s", self.path, e)
            except OSError as e:
                if getattr(e, "errno", None) in (errno.EPIPE, errno.ECONNRESET):
                    LOG.debug("client disconnected while writing response path=%s error=%s", self.path, e)
                    return
                raise

        def _parts(self):
            return [p for p in urlparse(self.path).path.split("/") if p]

        def _body(self):
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length > MAX_BODY_BYTES:
                return "__too_large__"
            try:
                return json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return None

        def _check(self):
            store.sweep()
            if self.path == "/healthz":
                return True
            if (not auth_required) or (token and self.headers.get("Authorization") == f"Bearer {token}"):
                return True
            self._json(401, {"error": "unauthorized", "message": "invalid or missing token"})
            return False

        def do_GET(self):
            if not self._check():
                return
            if self.path == "/healthz":
                return self._json(200, {"ok": True})
            parts = self._parts()
            query = parse_qs(urlparse(self.path).query)
            agents = store.list_agents()
            if parts == ["trackers"]:
                return self._json(200, {"trackers": store.list_trackers()})
            if parts == ["agents"]:
                for key in ("name", "hostname", "status", "logical_identity", "service_kind"):
                    if query.get(key):
                        agents = [agent for agent in agents if agent.get(key) == query[key][0]]
                public_keys = ("agent_id", "name", "aliases", "tracker_id", "hostname", "status", "agent_type", "agent_cmd", "model_type", "cwd", "swarms", "logical_identity", "service_kind", "capabilities", "current_task", "current_task_id", "current_task_status", "current_task_next_step", "last_seen")
                agents = [{k: agent[k] for k in public_keys if k in agent} for agent in agents]
                return self._json(200, {"agents": agents})
            if parts == ["message-events"]:
                swarm = (query.get("swarm") or [None])[0]
                if not swarm:
                    return self._json(400, {"error": "invalid_request", "message": "swarm query parameter is required"})
                try:
                    limit = int((query.get("limit") or [200])[0])
                except (TypeError, ValueError):
                    return self._json(400, {"error": "invalid_request", "message": "limit must be an integer"})
                return self._json(200, {"events": store.query_message_events(swarm, limit)})
            if parts == ["pane-output-events"]:
                try:
                    limit = int((query.get("limit") or [200])[0])
                except (TypeError, ValueError):
                    return self._json(400, {"error": "invalid_request", "message": "limit must be an integer"})
                return self._json(200, {"events": store.query_pane_output_events(limit)})
            if len(parts) == 2 and parts[0] == "agents":
                agent = store.get_agent(parts[1])
                return self._json(200, agent) if agent else self._json(404, {"error": "agent_not_found", "message": "no agent with that ID is registered"})
            if len(parts) == 3 and parts[0] == "trackers" and parts[2] == "deliveries":
                tracker_id = parts[1]
                if not store.has_tracker(tracker_id):
                    LOG.warning("delivery poll for unknown tracker_id=%s", tracker_id)
                    return self._json(404, {"error": "tracker_not_found", "message": "tracker not registered; call POST /trackers first"})
                wait = min(max(int((query.get("wait") or [DELIVERY_WAIT_SECONDS])[0]), 0), DELIVERY_WAIT_SECONDS)
                return self._json(200, {"deliveries": store.wait_for_deliveries(tracker_id, wait)})
            if len(parts) == 3 and parts[0] == "trackers" and parts[2] == "events":
                tracker_id = parts[1]
                if not store.has_tracker(tracker_id):
                    return self._json(404, {"error": "tracker_not_found", "message": "tracker not registered; call POST /trackers first"})
                wait = min(max(int((query.get("wait") or [DELIVERY_WAIT_SECONDS])[0]), 0), DELIVERY_WAIT_SECONDS)
                return self._json(200, {"events": store.wait_for_tracker_events(tracker_id, wait)})
            self._json(404, {"error": "not_found", "message": "no such endpoint"})

        def do_POST(self):
            if not self._check():
                return
            parts, body = self._parts(), self._body()
            if body == "__too_large__":
                return self._json(413, {"error": "payload_too_large", "message": "request body exceeds limit"})
            if body is None:
                return self._json(400, {"error": "invalid_request", "message": "malformed JSON body"})
            if parts == ["trackers"]:
                if not {"tracker_id", "hostname", "address", "http_port"}.issubset(body):
                    return self._json(400, {"error": "invalid_request", "message": "tracker_id, hostname, address, http_port are required"})
                return self._json(201 if store.put_tracker(body) else 200, {"tracker_id": body["tracker_id"]})
            if len(parts) == 3 and parts[0] == "trackers" and parts[2] == "watch-leases":
                tracker_id = parts[1]
                if not store.has_tracker(tracker_id):
                    return self._json(404, {"error": "tracker_not_found", "message": "tracker not registered; call POST /trackers first"})
                required = {"client_id", "watch_targets", "lease_seconds"}
                if not required.issubset(body):
                    return self._json(400, {"error": "invalid_request", "message": "client_id, watch_targets, lease_seconds are required"})
                watch_targets = body["watch_targets"]
                if not isinstance(watch_targets, list):
                    return self._json(400, {"error": "invalid_request", "message": "watch_targets must be a list"})
                if len(watch_targets) > 50:
                    return self._json(400, {"error": "limit_exceeded", "message": "max 50 watched agents per lease"})
                try:
                    lease_seconds = float(body["lease_seconds"])
                except ValueError:
                    return self._json(400, {"error": "invalid_request", "message": "lease_seconds must be a number"})
                
                scope = body.get("scope", "narrow")
                if scope not in {"narrow", "broad"}:
                    return self._json(400, {"error": "invalid_request", "message": "scope must be narrow or broad"})
                
                # Security policy check: reject unauthorized broad passive observation
                if scope == "broad" and not AGENT_REGISTRY_BROAD_WATCH_ALLOWED:
                    LOG.warning("AUDIT: Denied unauthorized broad watch lease request: tracker_id=%s client_id=%s targets=%s", tracker_id, body["client_id"], watch_targets)
                    return self._json(403, {"error": "unauthorized_scope", "message": "Broad passive remote observation is disabled on this registry"})
                
                store.put_watch_lease(tracker_id, body["client_id"], watch_targets, lease_seconds, scope=scope)
                return self._json(200, {"ok": True})

            if len(parts) == 3 and parts[0] == "trackers" and parts[2] in {"heartbeat", "agent-update"}:
                tracker_id = parts[1]
                if not store.has_tracker(tracker_id):
                    LOG.warning("tracker write %s for unknown tracker_id=%s", parts[2], tracker_id)
                    return self._json(404, {"error": "tracker_not_found", "message": "tracker not registered; call POST /trackers first"})
                if parts[2] == "heartbeat":
                    store.heartbeat(tracker_id, body.get("agents", []), body.get("agent_configs", []))
                    return self._json(200, {"ok": True})
                if not {"agent_id", "status"}.issubset(body):
                    return self._json(400, {"error": "invalid_request", "message": "agent_id and status are required"})
                code = store.update_agent(tracker_id, body["agent_id"], body["status"])
                if code == 200:
                    return self._json(200, {"ok": True})
                if code == 403:
                    return self._json(403, {"error": "wrong_tracker", "message": "agent does not belong to this tracker"})
                return self._json(404, {"error": "agent_not_found", "message": "agent not in registry cache; wait for next heartbeat"})
            if len(parts) == 5 and parts[0] == "trackers" and parts[2] == "deliveries" and parts[4] == "ack":
                tracker_id, message_id = parts[1], parts[3]
                if not store.has_tracker(tracker_id):
                    LOG.warning("ack for unknown tracker_id=%s message_id=%s", tracker_id, message_id)
                    return self._json(404, {"error": "tracker_not_found", "message": "tracker not registered; call POST /trackers first"})
                if store.ack_delivery(tracker_id, message_id):
                    return self._json(200, {"ok": True})
                return self._json(404, {"error": "delivery_not_found", "message": "no queued delivery with that message_id"})
            if len(parts) == 5 and parts[0] == "trackers" and parts[2] == "events" and parts[4] == "ack":
                tracker_id, event_id = parts[1], parts[3]
                if not store.has_tracker(tracker_id):
                    return self._json(404, {"error": "tracker_not_found", "message": "tracker not registered; call POST /trackers first"})
                if store.ack_tracker_event(tracker_id, event_id):
                    return self._json(200, {"ok": True})
                return self._json(404, {"error": "event_not_found", "message": "no queued event with that event_id"})
            if parts == ["message-events"]:
                try:
                    inserted, event = store.append_message_event(body)
                except ValueError as e:
                    return self._json(400, {"error": "invalid_request", "message": str(e)})
                return self._json(201 if inserted else 200, {"ok": True, "inserted": inserted, "message_id": event["message_id"]})
            if parts == ["pane-output-events"]:
                try:
                    inserted, event = store.append_pane_output_event(body)
                except PermissionError:
                    return self._json(403, {"error": "wrong_tracker", "message": "source tracker does not own this agent"})
                except ValueError as e:
                    code = str(e) or "invalid_request"
                    if code == "agent_not_found":
                        return self._json(404, {"error": "agent_not_found", "message": "agent not in registry cache; wait for next heartbeat"})
                    return self._json(400, {"error": "invalid_request", "message": pane_output_registry.safe_error_code(e)})
                except pane_output_registry.RegistryEventValidationError as e:
                    return self._json(400, {"error": "invalid_request", "message": pane_output_registry.safe_error_code(e)})
                return self._json(201 if inserted else 200, {"ok": True, "inserted": inserted, "event_id": event["event_id"]})
            if parts == ["tracker-events"]:
                required = {"event_type", "source_tracker_id", "target_tracker_id", "payload"}
                if not required.issubset(body) or not isinstance(body.get("payload"), dict):
                    return self._json(400, {"error": "invalid_request", "message": "event_type, source_tracker_id, target_tracker_id, payload object are required"})
                if not store.has_tracker(body["source_tracker_id"]) or not store.has_tracker(body["target_tracker_id"]):
                    return self._json(404, {"error": "tracker_not_found", "message": "source or target tracker not registered"})
                event = store.enqueue_tracker_event(body["target_tracker_id"], body["event_type"], body["source_tracker_id"], body["payload"])
                LOG.info("accepted tracker event event_id=%s type=%s source=%s target=%s", event["event_id"], body["event_type"], body["source_tracker_id"], body["target_tracker_id"])
                return self._json(202, {"ok": True, "event_id": event["event_id"]})
            if parts == ["save-agent"]:
                if not body.get("agent_to_save"):
                    return self._json(400, {"error": "invalid_request", "message": "agent_to_save is required"})
                agent_to_save = body["agent_to_save"]
                agent_name = body.get("agent_name")
                command = body.get("command")
                description = body.get("description")
                cwd = body.get("cwd")
                
                target = store.get_agent(agent_to_save)
                if not target:
                    target = next((agent for agent in store.list_agents() if agent["name"] == agent_to_save or agent_to_save in agent.get("aliases", [])), None)
                
                if not target:
                    return self._json(404, {"error": "agent_not_found", "message": "no agent with that ID or name is registered globally"})
                    
                tracker = store.get_tracker(target["tracker_id"]) or {}
                if tracker.get("status") != "active":
                    return self._json(503, {"error": "tracker_offline", "message": "target tracker is stale or gone", "tracker_status": tracker.get("status", "gone")})
                    
                event = store.enqueue_tracker_event(
                    target["tracker_id"],
                    "save_request",
                    "registry",
                    {
                        "agent_to_save": target["agent_id"],
                        "agent_name": agent_name,
                        "command": command,
                        "description": description,
                        "cwd": cwd
                    }
                )
                return self._json(202, {"ok": True, "queued": True, "event_id": event["event_id"], "target_tracker": target["hostname"]})

            if parts == ["pane-inputs"]:
                if not remote_pane_input_enabled:
                    LOG.warning("rejected remote pane input while registry gate disabled audit=%s", _pane_input_audit_fields(body, result="registry_disabled"))
                    return self._json(403, {"error": "remote_pane_input_disabled", "message": "remote direct pane input is disabled on this registry"})
                validation_error = _pane_input_payload_error(body)
                if validation_error:
                    LOG.warning("rejected invalid remote pane input: %s audit=%s", validation_error, _pane_input_audit_fields(body, result="invalid"))
                    return self._json(400, {"error": "invalid_request", "message": validation_error})
                if not store.has_tracker(body.get("sender_tracker_id")):
                    LOG.warning("remote pane input source tracker not found audit=%s", _pane_input_audit_fields(body, result="source_tracker_not_found"))
                    return self._json(404, {"error": "tracker_not_found", "message": "sender tracker not registered"})
                target = store.get_agent(body.get("target_agent_id")) if body.get("target_agent_id") else None
                if not target and body.get("target_agent_name"):
                    target = next((agent for agent in store.list_agents() if agent["hostname"] == body["target_hostname"] and (agent["name"] == body["target_agent_name"] or body["target_agent_name"] in agent.get("aliases", []))), None)
                if not target:
                    LOG.warning("remote pane input target not found audit=%s", _pane_input_audit_fields(body, result="target_not_found"))
                    return self._json(404, {"error": "agent_not_found", "message": "no agent with that ID or name is registered on the specified tracker"})
                if body.get("sender_tracker_id") == target["tracker_id"]:
                    LOG.warning("remote pane input same-tracker rejection audit=%s", _pane_input_audit_fields(body, target, result="same_tracker"))
                    return self._json(400, {"error": "same_tracker", "message": "target agent is on the same tracker; use local send"})
                tracker = store.get_tracker(target["tracker_id"]) or {}
                if tracker.get("status") != "active":
                    LOG.warning("remote pane input target tracker not active audit=%s tracker_status=%s", _pane_input_audit_fields(body, target, result="tracker_offline"), tracker.get("status", "gone"))
                    return self._json(503, {"error": "tracker_offline", "message": "target tracker is stale or gone", "tracker_status": tracker.get("status", "gone")})
                pane_input_id = body.get("pane_input_id") or body.get("request_id")
                request_id = body.get("request_id") or pane_input_id
                mode = (body.get("input_type") or body.get("mode") or "").lower()
                entry_payload = {
                    "delivery_type": "pane_input",
                    "message_id": pane_input_id,
                    "pane_input_id": pane_input_id,
                    "request_id": request_id,
                    "target_agent_id": target["agent_id"],
                    "target_agent_name": target.get("name"),
                    "sender_agent_id": body.get("sender_agent_id"),
                    "sender_agent_name": body.get("sender_agent_name"),
                    "sender_tracker_id": body.get("sender_tracker_id"),
                    "sender_tracker": (store.get_tracker(body.get("sender_tracker_id")) or {}).get("hostname", body.get("sender_tracker_id")),
                    "input_type": mode,
                    "submit": body.get("submit", True),
                    "sent_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
                }
                if mode == "text":
                    entry_payload["text"] = body.get("text")
                else:
                    keys = body.get("keys") if isinstance(body.get("keys"), list) else [body.get("keys")]
                    entry_payload["keys"] = [_normalize_key_token(key) for key in keys]
                entry = store.enqueue_delivery(target["tracker_id"], entry_payload)
                LOG.info("accepted remote pane input audit=%s", _pane_input_audit_fields(body, target, result="queued"))
                return self._json(202, {"ok": True, "queued": True, "pane_input_id": entry["pane_input_id"], "request_id": entry["request_id"], "target_agent_id": target["agent_id"], "target_name": target["name"], "target_tracker": target["hostname"]})

            if parts == ["messages"]:
                if not body.get("message") and not body.get("attachments"):
                    return self._json(400, {"error": "invalid_request", "message": "message text or attachments are required"})
                attachment_error = _validate_attachments(body)
                if attachment_error:
                    return self._json(400, {"error": "invalid_request", "message": attachment_error})
                target = store.get_agent(body.get("target_agent_id")) if body.get("target_agent_id") else None
                if not target and body.get("target_agent_name"):
                    if not body.get("target_hostname"):
                        return self._json(400, {"error": "hostname_required", "message": "target_hostname is required when using target_agent_name; bare-name global resolution is not supported"})
                    target = next((agent for agent in store.list_agents() if agent["hostname"] == body["target_hostname"] and (agent["name"] == body["target_agent_name"] or body["target_agent_name"] in agent.get("aliases", []))), None)
                if not target:
                    if body.get("target_agent_name") or body.get("target_agent_id"):
                        LOG.warning("message target not found target_agent_id=%s target_agent_name=%s target_hostname=%s sender_tracker_id=%s", body.get("target_agent_id"), body.get("target_agent_name"), body.get("target_hostname"), body.get("sender_tracker_id"))
                        return self._json(404, {"error": "agent_not_found", "message": "no agent with that ID or name is registered on the specified tracker"})
                    return self._json(400, {"error": "missing_target", "message": "provide target_agent_id or target_agent_name"})
                if body.get("sender_tracker_id") == target["tracker_id"]:
                    return self._json(400, {"error": "same_tracker", "message": "target agent is on the same tracker; use local send"})
                tracker = store.get_tracker(target["tracker_id"]) or {}
                if tracker.get("status") != "active":
                    LOG.warning("message target tracker not active target_tracker_id=%s status=%s target_agent_id=%s", target["tracker_id"], tracker.get("status", "gone"), target["agent_id"])
                    return self._json(503, {"error": "tracker_offline", "message": "target tracker is stale or gone", "tracker_status": tracker.get("status", "gone")})
                sender_tracker = store.get_tracker(body.get("sender_tracker_id")) or {}
                entry = store.enqueue_delivery(target["tracker_id"], {
                    "delivery_type": "message",
                    "target_agent_id": target["agent_id"],
                    "sender_name": body.get("sender_agent_name", "unknown"),
                    "sender_agent_id": body.get("sender_agent_id"),
                    "sender_tracker": sender_tracker.get("hostname", body.get("sender_tracker_id")),
                    "message": body.get("message"),
                    "attachments": body.get("attachments"),
                    "sender_tracker_id": body.get("sender_tracker_id"),
                    "sender_hostname": body.get("sender_hostname") or sender_tracker.get("hostname"),
                    "sender_model_type": body.get("sender_model_type"),
                    "sender_agent_type": body.get("sender_agent_type"),
                    "sender_agent_cmd": body.get("sender_agent_cmd"),
                    "kind": body.get("kind"),
                    **_message_metadata(body),
                    "message_id": body.get("message_id"),
                    "delivery_id": body.get("delivery_id"),
                    "swarms": body.get("swarms") or [],
                    "membership_snapshot": body.get("membership_snapshot") or {},
                    "swarm_context": body.get("swarm_context") or body.get("swarm"),
                    "sent_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
                })
                _fanout_remote_message_delivered(store, target["tracker_id"], target, entry)
                return self._json(202, {"ok": True, "queued": True, "message_id": entry["message_id"], "delivery_id": entry.get("delivery_id"), "target_agent_id": target["agent_id"], "target_name": target["name"], "target_tracker": target["hostname"]})
            self._json(404, {"error": "not_found", "message": "no such endpoint"})

        def do_DELETE(self):
            if not self._check():
                return
            parts = self._parts()
            if len(parts) == 4 and parts[0] == "trackers" and parts[2] == "watch-leases":
                tracker_id, client_id = parts[1], parts[3]
                if not store.has_tracker(tracker_id):
                    return self._json(404, {"error": "tracker_not_found", "message": "tracker not registered"})
                store.clear_watch_leases(tracker_id, client_id)
                return self._json(200, {"ok": True})
            self._json(404, {"error": "not_found", "message": "no such endpoint"})

    return Handler


def serve_forever():
    host = _env_str("AGENT_REGISTRY_HOST", config.get("tracker", "registry_host", "0.0.0.0"))
    port = _env_int("AGENT_REGISTRY_PORT", config.get("tracker", "registry_port", 8080))
    LOG.info("starting agent-registry bind=%s port=%s state_path=%s auth_required=%s", host, port, STATE_PATH, AUTH_REQUIRED)
    ThreadingHTTPServer((host, port), make_handler()).serve_forever()


if __name__ == "__main__":
    serve_forever()
