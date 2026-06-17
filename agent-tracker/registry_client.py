import fcntl, hashlib, json, logging, os, socket, threading, time, urllib.error, urllib.parse, urllib.request, uuid, shlex, subprocess
import state
import rpc_handler
import config
import pane_output_registry

LOG = logging.getLogger("agent-tracker.registry")

TOKEN = os.environ.get("AGENT_REGISTRY_TOKEN") or config.get("registry", "token", "")
HOSTNAME = os.environ.get("AGENT_TRACKER_HOSTNAME") or config.get("tracker", "hostname", socket.gethostname())
TRACKER_ID = config.get("tracker", "tracker_id", str(uuid.uuid5(uuid.NAMESPACE_DNS, HOSTNAME)))
HTTP_PORT = int(os.environ.get("AGENT_TRACKER_HTTP_PORT") or config.get("tracker", "http_port", 19876))
HEARTBEAT_INTERVAL = config.get("registry", "heartbeat_seconds", 30)
DELIVERY_WAIT_SECONDS = config.get("registry", "delivery_wait_seconds", 25)
DELIVERY_TARGET_GRACE_SECONDS = config.get("registry", "delivery_target_grace_seconds", 60)
REMOTE_PANE_INPUT_MAX_TEXT_BYTES = config.get("registry", "remote_pane_input_max_text_bytes", 4096)
REMOTE_PANE_INPUT_MAX_KEYS = config.get("registry", "remote_pane_input_max_keys", 16)
STATUS_PATH = os.path.join(state.CACHE_DIR, "registry-status.json")
PANE_OUTPUT_EVENT_DEDUPE_MAX = int(os.environ.get("AGENT_PANE_OUTPUT_EVENT_DEDUPE_MAX", "1000"))
_pane_output_event_dedupe = {}
_pane_output_event_dedupe_lock = threading.Lock()
MESSAGE_METADATA_FIELDS = (
    "content_type", "approval_id", "task_id", "task_title", "task_status", "task_assigned_agent", "task_next_step",
    "result_summary", "task_chain_id", "root_task_id", "task_version_at_submission",
    "created_event_seq", "event_seq_at_submission", "source", "sender_source",
    "memory_id", "memory_type", "memory_title", "memory_scope", "memory_status",
    "memory_version", "source_task_id", "recipient_agent", "recipient_kind",
    "delivery_scope", "delivery_id", "target_logical_identity",
)


def _message_metadata(source: dict) -> dict:
    return {key: source.get(key) for key in MESSAGE_METADATA_FIELDS if key in source}


class RegistryClient:
    def __init__(self, name="default", url="", token="", tracker_id=None, hostname=None, http_port=None):
        self.name = name or "default"
        self.url = (url or "").rstrip("/")
        self.token = token or ""
        self.tracker_id = tracker_id or TRACKER_ID
        self.hostname = hostname or HOSTNAME
        self.http_port = HTTP_PORT if http_port is None else int(http_port)

    def request(self, method, path, payload=None, timeout=3):
        if not self.url:
            return None, None
        req = urllib.request.Request(
            f"{self.url}{path}",
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {self.token}"} if self.token else {})},
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode()
                return resp.status, json.loads(body) if body else None
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read().decode())
            except Exception:
                body = None
            LOG.warning("registry[%s] request %s %s returned HTTP %s body=%s", self.name, method, path, e.code, body)
            return e.code, body
        except Exception as e:
            LOG.warning("registry[%s] request %s %s failed: %s", self.name, method, path, e)
            return None, None

    def register(self):
        return self.request("POST", "/trackers", {
            "tracker_id": self.tracker_id,
            "hostname": self.hostname,
            "address": config.get("tracker", "address", self.hostname),
            "http_port": self.http_port,
            "agents": state.get_agents_for_registry(),
            "agent_configs": state.get_local_configs_for_registry(),
        })[0]

    def heartbeat(self):
        return self.request("POST", f"/trackers/{self.tracker_id}/heartbeat", {
            "agents": state.get_agents_for_registry(),
            "agent_configs": state.get_local_configs_for_registry(),
        })[0]

    def fetch_deliveries(self):
        return self.request("GET", f"/trackers/{self.tracker_id}/deliveries?wait={DELIVERY_WAIT_SECONDS}", timeout=DELIVERY_WAIT_SECONDS + 5)

    def ack_delivery(self, message_id):
        return self.request("POST", f"/trackers/{self.tracker_id}/deliveries/{message_id}/ack", {})[0]

    def fetch_events(self):
        return self.request("GET", f"/trackers/{self.tracker_id}/events?wait={DELIVERY_WAIT_SECONDS}", timeout=DELIVERY_WAIT_SECONDS + 5)

    def ack_event(self, event_id):
        return self.request("POST", f"/trackers/{self.tracker_id}/events/{event_id}/ack", {})[0]

    def publish_event(self, target_tracker_id, event_type, payload):
        return self.request("POST", "/tracker-events", {"event_type": event_type, "source_tracker_id": self.tracker_id, "target_tracker_id": target_tracker_id, "payload": payload})[0]

    def publish_message_event(self, event):
        return self.request("POST", "/message-events", event)

    def publish_pane_output_event(self, event):
        return self.request("POST", "/pane-output-events", event)

    def fetch_pane_output_events(self, limit=200):
        query = urllib.parse.urlencode({"limit": int(limit)})
        return self.request("GET", f"/pane-output-events?{query}")

    def fetch_message_events(self, swarm_name, limit=200):
        query = urllib.parse.urlencode({"swarm": swarm_name, "limit": int(limit)})
        return self.request("GET", f"/message-events?{query}")

    def push_agent_update(self, agent_id, status):
        return self.request("POST", f"/trackers/{self.tracker_id}/agent-update", {"agent_id": agent_id, "status": status})[0]

    def send_remote_message(self, sender_name, sender_agent_id, sender_tracker_id, target_hostname, target_name_or_id, message=None, attachments=None, message_id=None, sender_metadata=None):
        return self.request("POST", "/messages", _remote_message_payload(sender_name, sender_agent_id, sender_tracker_id, target_hostname, target_name_or_id, message, attachments, message_id, sender_metadata))

    def send_remote_pane_input(self, payload):
        return self.request("POST", "/pane-inputs", payload)

    def fetch_agents(self, timeout=3):
        return self.request("GET", "/agents", timeout=timeout)

    def fetch_trackers(self, timeout=3):
        return self.request("GET", "/trackers", timeout=timeout)

    def set_remote_watch_leases(self, client_id: str, watch_targets: list[str], lease_seconds: float, scope: str = "narrow") -> int:
        status, _ = self.request("POST", f"/trackers/{self.tracker_id}/watch-leases", {
            "client_id": client_id,
            "watch_targets": watch_targets,
            "lease_seconds": lease_seconds,
            "scope": scope,
            "token": self.token,
        })
        return status or 500

    def clear_remote_watch_leases(self, client_id: str) -> int:
        status, _ = self.request("DELETE", f"/trackers/{self.tracker_id}/watch-leases/{client_id}", {})
        return status or 500


def _read_token_config(config):
    if config.get("token"):
        return config.get("token")
    token_file = config.get("token-file") or config.get("tokenFile")
    if token_file:
        try:
            with open(token_file, "r") as f:
                return f.read().strip()
        except Exception as e:
            LOG.warning("failed to read registry token file %s: %s", token_file, e)
    return TOKEN


def _normalize_registries_json(raw: str) -> str:
    raw = (raw or "").strip().replace('\\"', '"')
    # Some launchd/Home Manager paths can preserve shell-style quoting as part of
    # the environment value, e.g. '"[{\'name\':\'local\',...}]"'.  Unwrap a
    # JSON string wrapper before parsing the actual registry list.
    for _ in range(2):
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
            if raw[0] == '"':
                try:
                    decoded = json.loads(raw)
                    if isinstance(decoded, str):
                        raw = decoded.strip()
                        continue
                except json.JSONDecodeError:
                    pass
            raw = raw[1:-1].strip()
        break
    if "'" in raw and '"' not in raw:
        raw = raw.replace("'", '"')
    return raw


def load_registry_clients():
    raw = os.environ.get("AGENT_REGISTRIES_JSON") or config.get("registry", "endpoints", [])
    configs = []
    if isinstance(raw, str):
        raw = _normalize_registries_json(raw)
        if raw:
            try:
                decoded = json.loads(raw)
                configs = decoded.get("registries") if isinstance(decoded, dict) else decoded
            except json.JSONDecodeError:
                LOG.warning("invalid AGENT_REGISTRIES_JSON; registry sync disabled")
                configs = []
    elif isinstance(raw, list):
        configs = raw
    clients = []
    for cfg in configs or []:
        if not isinstance(cfg, dict) or not cfg.get("url"):
            continue
        clients.append(RegistryClient(cfg.get("name") or "default", cfg.get("url"), _read_token_config(cfg)))
    return clients


def _default_client():
    clients = load_registry_clients()
    return clients[0] if clients else None


def register():
    client = _default_client()
    return None if client is None else client.register()


def heartbeat():
    client = _default_client()
    return None if client is None else client.heartbeat()


def fetch_deliveries():
    client = _default_client()
    return (None, None) if client is None else client.fetch_deliveries()


def fetch_events():
    client = _default_client()
    return (None, None) if client is None else client.fetch_events()


def ack_event(event_id):
    client = _default_client()
    return None if client is None else client.ack_event(event_id)


def set_remote_watch_leases(client_id: str, watch_targets: list[str], lease_seconds: float, scope: str = "narrow"):
    client = _default_client()
    if client is None:
        return 500
    return client.set_remote_watch_leases(client_id, watch_targets, lease_seconds, scope=scope)


def clear_remote_watch_leases(client_id: str):
    client = _default_client()
    if client is None:
        return 500
    return client.clear_remote_watch_leases(client_id)


def ack_delivery(message_id):
    client = _default_client()
    if client is None:
        return None
    status = client.ack_delivery(message_id)
    if status != 200:
        LOG.warning("failed to ack registry delivery message_id=%s tracker_id=%s status=%s", message_id, client.tracker_id, status)
    return status

def push_agent_update(agent_id, status):
    for client in load_registry_clients():
        threading.Thread(target=lambda c=client: c.push_agent_update(agent_id, status), daemon=True).start()


def _remote_message_payload(sender_name, sender_agent_id, sender_tracker_id, target_hostname, target_name_or_id, message=None, attachments=None, message_id=None, sender_metadata=None):
    payload = {
        **(sender_metadata or {}),
        "sender_agent_id": sender_agent_id,
        "sender_agent_name": sender_name,
        "sender_tracker_id": sender_tracker_id,
        "message": message,
    }
    if message_id:
        payload["message_id"] = message_id
    if attachments:
        payload["attachments"] = attachments
    try:
        uuid.UUID(target_name_or_id)
        payload["target_agent_id"] = target_name_or_id
    except (ValueError, TypeError):
        payload["target_agent_name"] = target_name_or_id
        payload["target_hostname"] = target_hostname
    return payload


def _env_truthy(name):
    return os.environ.get(name, "").lower() in {"1", "true", "yes", "on"}


def remote_pane_input_send_enabled():
    return _env_truthy("AGENT_TRACKER_REMOTE_PANE_INPUT_SEND_ENABLED") or _env_truthy("BROCCOLI_COMMS_REMOTE_PANE_INPUT_SEND_ENABLED") or _env_truthy("BROCCOLI_COMMS_REMOTE_PANE_INPUT_ENABLED")


def pane_output_registry_events_enabled():
    if os.environ.get("AGENT_PANE_OUTPUT_REGISTRY_EVENTS_ENABLED") is not None:
        return _env_truthy("AGENT_PANE_OUTPUT_REGISTRY_EVENTS_ENABLED")
    if os.environ.get("BROCCOLI_COMMS_PANE_OUTPUT_REGISTRY_EVENTS_ENABLED") is not None:
        return _env_truthy("BROCCOLI_COMMS_PANE_OUTPUT_REGISTRY_EVENTS_ENABLED")
    return bool(config.get("registry", "pane_output_events_enabled", True))


def remote_pane_input_receive_enabled():
    return _env_truthy("AGENT_TRACKER_REMOTE_PANE_INPUT_RECEIVE_ENABLED") or _env_truthy("BROCCOLI_COMMS_REMOTE_PANE_INPUT_RECEIVE_ENABLED") or _env_truthy("BROCCOLI_COMMS_REMOTE_PANE_INPUT_ENABLED")


def _redacted_pane_input_audit(payload, result=None):
    mode = (payload.get("input_type") or payload.get("mode") or "").lower()
    fields = {
        "pane_input_id": payload.get("pane_input_id"),
        "request_id": payload.get("request_id"),
        "sender_tracker_id": payload.get("sender_tracker_id"),
        "target_agent_id": payload.get("target_agent_id"),
        "target_agent_name": payload.get("target_agent_name"),
        "target_hostname": payload.get("target_hostname"),
        "mode": mode,
        "result": result,
    }
    if mode == "text" and isinstance(payload.get("text"), str):
        encoded = payload["text"].encode("utf-8")
        fields["text_bytes"] = len(encoded)
        fields["text_sha256"] = hashlib.sha256(encoded).hexdigest()[:16]
    elif mode == "keys":
        keys = payload.get("keys") if isinstance(payload.get("keys"), list) else [payload.get("keys")]
        fields["key_count"] = len([k for k in keys if k is not None])
    return fields


def _remote_pane_input_payload(sender_name, sender_agent_id, sender_tracker_id, target_hostname, target_name_or_id, input_type, text=None, keys=None, submit=True, pane_input_id=None, request_id=None):
    pane_input_id = pane_input_id or request_id or str(uuid.uuid4())
    request_id = request_id or pane_input_id
    payload = {
        "pane_input_id": pane_input_id,
        "request_id": request_id,
        "sender_agent_id": sender_agent_id,
        "sender_agent_name": sender_name,
        "sender_tracker_id": sender_tracker_id,
        "input_type": input_type,
        "submit": submit,
    }
    try:
        uuid.UUID(target_name_or_id)
        payload["target_agent_id"] = target_name_or_id
    except (ValueError, TypeError):
        payload["target_agent_name"] = target_name_or_id
        payload["target_hostname"] = target_hostname
    if input_type == "text":
        payload["text"] = text
    else:
        payload["keys"] = keys
    return payload


def _client_has_hostname(client, hostname):
    status, body = client.fetch_agents()
    if status != 200:
        return False
    return any(agent.get("hostname") == hostname for agent in (body or {}).get("agents") or [])


def send_remote_message(sender_name, sender_agent_id, sender_tracker_id, target_hostname, target_name_or_id, message=None, attachments=None, message_id=None, sender_metadata=None):
    clients = load_registry_clients()
    if clients:
        if len(clients) == 1:
            return clients[0].send_remote_message(sender_name, sender_agent_id, sender_tracker_id, target_hostname, target_name_or_id, message, attachments, message_id, sender_metadata)
        matches = [client for client in clients if _client_has_hostname(client, target_hostname)]
        if len(matches) > 1:
            choices = ", ".join(f"{client.name}:{target_hostname}/{target_name_or_id}" for client in matches)
            return 409, {"message": f"Ambiguous remote target; use one of: {choices}"}
        client = matches[0] if matches else clients[0]
        return client.send_remote_message(sender_name, sender_agent_id, sender_tracker_id, target_hostname, target_name_or_id, message, attachments, message_id, sender_metadata)
    return 404, {"message": "registry not configured"}


def send_remote_message_to_registry(registry_name, sender_name, sender_agent_id, sender_tracker_id, target_hostname, target_name_or_id, message=None, attachments=None, message_id=None, sender_metadata=None):
    for client in load_registry_clients():
        if client.name == registry_name:
            return client.request("POST", "/messages", _remote_message_payload(sender_name, sender_agent_id, sender_tracker_id, target_hostname, target_name_or_id, message, attachments, message_id, sender_metadata))
    return 404, {"message": f"registry not configured: {registry_name}"}


def publish_message_event(event, registry_name=None):
    clients = [client for client in load_registry_clients() if registry_name is None or client.name == registry_name]
    if not clients:
        return []
    results = []
    for client in clients:
        try:
            status, body = client.publish_message_event(event)
            results.append((client.name, status, body))
            if status not in {200, 201}:
                LOG.warning("registry[%s] message-event publish returned status=%s body=%s", client.name, status, body)
        except Exception as e:
            LOG.warning("registry[%s] message-event publish failed: %s", client.name, e)
            results.append((client.name, None, None))
    return results


def _remember_pane_output_event(event_id: str) -> bool:
    if not event_id:
        return False
    now = time.time()
    with _pane_output_event_dedupe_lock:
        expired = [eid for eid, expires_at in _pane_output_event_dedupe.items() if expires_at <= now]
        for eid in expired:
            _pane_output_event_dedupe.pop(eid, None)
        if event_id in _pane_output_event_dedupe:
            return False
        if len(_pane_output_event_dedupe) >= PANE_OUTPUT_EVENT_DEDUPE_MAX:
            for eid, _expires_at in sorted(_pane_output_event_dedupe.items(), key=lambda item: item[1])[:100]:
                _pane_output_event_dedupe.pop(eid, None)
        _pane_output_event_dedupe[event_id] = now + pane_output_registry.MAX_TTL_SECONDS
        return True


def publish_pane_output_event(local_event: dict, registry_name=None):
    """Publishes a normalized, registry-safe pane output event for a local agent."""
    if not pane_output_registry_events_enabled():
        return []
    agent_id = (local_event or {}).get("agent_id") or (local_event or {}).get("target_agent_id")
    info = state.get_agent(agent_id) if agent_id else None
    if not info or info.get("scope") == "remote":
        LOG.debug("skipping pane-output registry publish for non-local agent metadata agent_id=%s", agent_id)
        return []
    registry_event = pane_output_registry.from_local_event(
        local_event,
        source_tracker_id=TRACKER_ID,
        source_hostname=HOSTNAME,
        ttl_seconds=config.get("registry", "pane_output_event_ttl_seconds", pane_output_registry.DEFAULT_TTL_SECONDS),
    )
    clients = [client for client in load_registry_clients() if registry_name is None or client.name == registry_name]
    results = []
    for client in clients:
        try:
            status, body = client.publish_pane_output_event(registry_event)
            results.append((client.name, status, body))
            if status not in {200, 201, 202}:
                LOG.warning("registry[%s] pane-output event publish returned status=%s reason=%s", client.name, status, (body or {}).get("error") if isinstance(body, dict) else None)
        except Exception as e:
            LOG.warning("registry[%s] pane-output event publish failed metadata event_id=%s error=%s", client.name, registry_event.get("event_id"), e)
            results.append((client.name, None, None))
    return results


def _ingest_registry_pane_output_event(event: dict) -> bool:
    try:
        if event.get("source_tracker_id") == TRACKER_ID:
            return False
        observer_event = pane_output_registry.to_remote_observer_event(event)
    except Exception as exc:
        LOG.info("rejected registry pane-output event metadata reason=%s", pane_output_registry.safe_error_code(exc))
        return False
    event_id = observer_event.get("registry_event_id")
    if not _remember_pane_output_event(event_id):
        return False
    state.publish_event("agent_output_event", observer_event)
    return True


def fetch_message_events(swarm_name, limit=200):
    events_by_id = {}
    events_without_id = []
    for client in load_registry_clients():
        status, body = client.fetch_message_events(swarm_name, limit)
        if status != 200:
            continue
        for event in (body or {}).get("events") or []:
            if not isinstance(event, dict):
                continue
            event = {**event, "registry_name": client.name}
            message_id = event.get("message_id")
            if message_id:
                events_by_id[message_id] = event
            else:
                events_without_id.append(event)
    events = list(events_by_id.values()) + events_without_id
    events.sort(key=lambda event: event.get("timestamp") or "")
    return events[-max(1, int(limit)):]


def find_remote_agent(target_hostname, target_name_or_id, registry_name=None):
    clients = [client for client in load_registry_clients() if registry_name is None or client.name == registry_name]
    for client in clients:
        status, body = client.fetch_agents()
        if status != 200:
            continue
        for agent in (body or {}).get("agents") or []:
            if agent.get("hostname") != target_hostname:
                continue
            if agent.get("agent_id") == target_name_or_id or agent.get("name") == target_name_or_id or target_name_or_id in (agent.get("aliases") or []):
                return {**agent, "registry_name": client.name, "scope": "remote"}
    return None


def send_remote_pane_input(sender_name, sender_agent_id, sender_tracker_id, target_hostname, target_name_or_id, input_type, text=None, keys=None, submit=True, pane_input_id=None, request_id=None):
    payload = _remote_pane_input_payload(sender_name, sender_agent_id, sender_tracker_id, target_hostname, target_name_or_id, input_type, text, keys, submit, pane_input_id, request_id)
    clients = load_registry_clients()
    if clients:
        if len(clients) == 1:
            LOG.info("routing remote pane input audit=%s registry=%s", _redacted_pane_input_audit(payload, result="send"), clients[0].name)
            return clients[0].send_remote_pane_input(payload)
        matches = [client for client in clients if _client_has_hostname(client, target_hostname)]
        if len(matches) > 1:
            choices = ", ".join(f"{client.name}:{target_hostname}/{target_name_or_id}" for client in matches)
            return 409, {"message": f"Ambiguous remote target; use one of: {choices}"}
        client = matches[0] if matches else clients[0]
        LOG.info("routing remote pane input audit=%s registry=%s", _redacted_pane_input_audit(payload, result="send"), client.name)
        return client.send_remote_pane_input(payload)
    return 404, {"message": "registry not configured"}


def send_remote_pane_input_to_registry(registry_name, sender_name, sender_agent_id, sender_tracker_id, target_hostname, target_name_or_id, input_type, text=None, keys=None, submit=True, pane_input_id=None, request_id=None):
    payload = _remote_pane_input_payload(sender_name, sender_agent_id, sender_tracker_id, target_hostname, target_name_or_id, input_type, text, keys, submit, pane_input_id, request_id)
    for client in load_registry_clients():
        if client.name == registry_name:
            LOG.info("routing remote pane input audit=%s registry=%s", _redacted_pane_input_audit(payload, result="send"), client.name)
            return client.send_remote_pane_input(payload)
    return 404, {"message": f"registry not configured: {registry_name}"}


def fetch_trackers():
    clients = load_registry_clients()
    if clients:
        trackers = []
        last_status, last_body = None, {}
        for client in clients:
            status, body = client.fetch_trackers()
            last_status, last_body = status, body
            if status != 200:
                continue
            for tracker in body.get("trackers") or []:
                trackers.append({**tracker, "registry_name": client.name})
        if trackers:
            return 200, {"trackers": trackers}
        return last_status, last_body
    return 404, {"message": "registry not configured"}


def _configured_registry_names() -> set[str]:
    return {client.name for client in load_registry_clients()}


def _registry_status_payload(status_code, operation, existing, client=None):
    now = time.time()
    connected = isinstance(status_code, int) and 200 <= status_code < 300
    name = "default" if client is None else client.name
    registry_url = None if client is None else client.url
    tracker_id = TRACKER_ID if client is None else client.tracker_id
    hostname = HOSTNAME if client is None else client.hostname
    entry = {
        "connected": connected,
        "registry_url": registry_url,
        "tracker_id": tracker_id,
        "hostname": hostname,
        "last_operation": operation,
        "last_attempt": now,
        "status_code": status_code,
    }
    previous = (existing.get("registries") or {}).get(name, existing)
    if connected:
        entry["last_success"] = now
    elif "last_success" in previous:
        entry["last_success"] = previous["last_success"]
        entry["last_error"] = f"{operation}:{status_code if status_code is not None else 'unreachable'}"
    else:
        entry["last_error"] = f"{operation}:{status_code if status_code is not None else 'unreachable'}"
    configured_names = _configured_registry_names()
    if name:
        configured_names.add(name)
    registries = {k: v for k, v in dict(existing.get("registries") or {}).items() if k in configured_names}
    registries[name] = entry
    payload = {**entry, "connected": any(r.get("connected") for r in registries.values()), "registries": registries}
    if payload["connected"]:
        successes = [r.get("last_success") for r in registries.values() if r.get("last_success")]
        if successes:
            payload["last_success"] = max(successes)
    return payload


def _reset_registry_status_if_unconfigured():
    if load_registry_clients():
        return
    try:
        if os.path.exists(STATUS_PATH):
            os.remove(STATUS_PATH)
    except Exception as e:
        logging.debug(f"failed to remove stale registry status: {e}")


def _record_sync_result(status_code, operation, client=None):
    try:
        os.makedirs(os.path.dirname(STATUS_PATH), exist_ok=True)
        with open(STATUS_PATH, "a+") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.seek(0)
            try:
                existing = json.load(f)
            except Exception:
                existing = {}
            payload = _registry_status_payload(status_code, operation, existing, client)
            f.seek(0)
            f.truncate()
            json.dump(payload, f)
            f.flush()
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except Exception as e:
        logging.debug(f"failed to write registry status: {e}")


def _heartbeat_loop(client=None):
    tracker_id = TRACKER_ID if client is None else client.tracker_id
    do_register = register if client is None else client.register
    do_heartbeat = heartbeat if client is None else client.heartbeat
    _record_sync_result(do_register(), "register", client)
    while True:
        status = do_heartbeat()
        if status == 404:
            LOG.warning("registry heartbeat got 404 for tracker_id=%s; re-registering", tracker_id)
            _record_sync_result(do_register(), "register", client)
        else:
            if status != 200:
                LOG.warning("registry heartbeat failed for tracker_id=%s status=%s", tracker_id, status)
            _record_sync_result(status, "heartbeat", client)
        time.sleep(HEARTBEAT_INTERVAL)


def _ack(client, message_id):
    return ack_delivery(message_id) if client is None else client.ack_delivery(message_id)


def _local_tracker_event_payload(event_type, payload):
    sender_name = state.get_agent_name_by_id(payload.get("sender_agent_id")) or payload.get("sender_agent_id") or "unknown"
    target_agent_id = payload.get("reader_agent_id") or payload.get("receiver_agent_id")
    target_agent_name = payload.get("reader_agent_name") or payload.get("receiver_agent_name")
    local_payload = {
        "target_agent_id": target_agent_id,
        "target_agent_name": target_agent_name,
        "sender": sender_name,
        "message_id": payload.get("message_id"),
    }
    if payload.get("delivery_id"):
        local_payload["delivery_id"] = payload.get("delivery_id")
    return sender_name, local_payload


def publish_tracker_event(target_tracker_id, event_type, payload):
    LOG.info("publish_tracker_event target_tracker_id=%s event_type=%s payload=%s", target_tracker_id, event_type, payload)
    if target_tracker_id == TRACKER_ID:
        if event_type in {"message_delivered", "message_notified", "message_read"}:
            sender_name, local_payload = _local_tracker_event_payload(event_type, payload)
            LOG.info("publish_tracker_event local fast-path type=%s sender=%s message_id=%s", event_type, sender_name, payload.get("message_id"))
            state.publish_event(event_type, local_payload)
        else:
            state.publish_event(event_type, payload)
        return 200
    clients = load_registry_clients()
    for client in clients:
        status = client.publish_event(target_tracker_id, event_type, payload)
        if status in (200, 202):
            return status
    return None


def remote_run_enabled() -> bool:
    """Remote run is enabled by default; env/config can explicitly disable it."""
    env = os.environ.get("AGENT_TRACKER_REMOTE_RUN_ENABLED") or os.environ.get("BROCCOLI_COMMS_REMOTE_RUN_ENABLED")
    if env is not None:
        return env.strip().lower() not in {"0", "false", "no", "off"}
    return bool(config.get("registry", "remote_run_enabled", True))


def _remote_run_base_env(scope=None) -> dict:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "TERM": os.environ.get("TERM", "xterm-256color"),
        "HOME": os.environ.get("HOME", os.path.expanduser("~")),
        "USER": os.environ.get("USER", os.environ.get("LOGNAME", "")),
    }
    if scope:
        env["BROCCOLI_COMMS_SCOPE"] = str(scope)
        env["BROCCOLI_COMMS_REMOTE_RUN_SCOPE"] = str(scope)
    return env


def _legacy_agent_config_path(agent: str) -> str:
    return os.path.join(os.path.expanduser("~"), ".config", "agent-tracker", "agents", agent, "config.json")


def _load_remote_run_config(agent: str):
    config_path = _legacy_agent_config_path(agent)
    if not os.path.isfile(config_path):
        return None, None
    with open(config_path, "r") as f:
        cfg = json.load(f)
    directory = cfg.get("directory") or os.path.expanduser("~")
    directory = os.path.abspath(os.path.expanduser(directory))
    agent_command = cfg.get("agent-command")
    agent_args = cfg.get("agent-args") or []
    if not agent_command:
        LOG.warning("remote_run_request config missing agent-command: %s", agent)
        return directory, None
    return directory, shlex.join([agent_command] + agent_args)


REMOTE_RUN_ID_MAX = 200
REMOTE_RUN_PATH_MAX = 1000
REMOTE_RUN_SCOPE_MAX = 500
REMOTE_RUN_COMMAND_MAX = 4096
REMOTE_RUN_COMMAND_PART_MAX = 1000
REMOTE_RUN_COMMAND_PARTS_MAX = 64


def _remote_run_optional_str(payload: dict, key: str, *, max_len: int):
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    value = value.strip()
    if len(value) > max_len:
        raise ValueError(f"{key} exceeds {max_len} characters")
    return value or None


def _remote_run_first_str(payload: dict, keys: list[str], *, max_len: int):
    for key in keys:
        value = _remote_run_optional_str(payload, key, max_len=max_len)
        if value:
            return value
    return None


def _remote_run_command(payload: dict):
    command = payload.get("command")
    if command is None:
        return None
    if isinstance(command, str):
        command = command.strip()
        if len(command) > REMOTE_RUN_COMMAND_MAX:
            raise ValueError(f"command exceeds {REMOTE_RUN_COMMAND_MAX} characters")
        return command or None
    if isinstance(command, list):
        if len(command) > REMOTE_RUN_COMMAND_PARTS_MAX:
            raise ValueError(f"command list exceeds {REMOTE_RUN_COMMAND_PARTS_MAX} parts")
        parts = []
        for part in command:
            if not isinstance(part, str):
                raise ValueError("command list entries must be strings")
            if len(part) > REMOTE_RUN_COMMAND_PART_MAX:
                raise ValueError(f"command list entry exceeds {REMOTE_RUN_COMMAND_PART_MAX} characters")
            parts.append(part)
        joined = shlex.join(parts)
        if len(joined) > REMOTE_RUN_COMMAND_MAX:
            raise ValueError(f"command exceeds {REMOTE_RUN_COMMAND_MAX} characters")
        return joined or None
    raise ValueError("command must be a string or list of strings")


def _normalize_remote_run_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    request_id = _remote_run_optional_str(payload, "request_id", max_len=REMOTE_RUN_ID_MAX)
    agent = _remote_run_first_str(payload, ["agent", "name", "config_name"], max_len=REMOTE_RUN_ID_MAX)
    cwd = _remote_run_first_str(payload, ["cwd", "directory"], max_len=REMOTE_RUN_PATH_MAX)
    scope = _remote_run_optional_str(payload, "scope", max_len=REMOTE_RUN_SCOPE_MAX)
    command = _remote_run_command(payload)
    reply_to_tracker_id = _remote_run_first_str(payload, ["reply_to_tracker_id", "source_tracker_id"], max_len=REMOTE_RUN_ID_MAX)
    source_tracker_id = _remote_run_first_str(payload, ["source_tracker_id", "reply_to_tracker_id"], max_len=REMOTE_RUN_ID_MAX)
    session = _remote_run_optional_str(payload, "session", max_len=REMOTE_RUN_ID_MAX)
    return {
        "request_id": request_id,
        "agent": agent,
        "cwd": cwd,
        "scope": scope,
        "command": command,
        "reply_to_tracker_id": reply_to_tracker_id,
        "source_tracker_id": source_tracker_id,
        "session": session,
    }


def _bounded_remote_run_error(value, limit=1000):
    text = str(value or "")
    return text[:limit]


def _broccoli_comms_run_argv(req: dict) -> list[str]:
    agent = req.get("agent")
    argv = [os.environ.get("BROCCOLI_COMMS_CLI") or "broccoli-comms", "run", agent, "--json"]
    if req.get("cwd"):
        argv.extend(["--cwd", req["cwd"]])
    if req.get("scope"):
        argv.extend(["--scope", req["scope"]])
    command = req.get("command")
    if command:
        argv.append("--")
        argv.extend(shlex.split(command))
    return argv


def _remote_run_result_payload(req: dict, ok: bool, *, launch_result=None, error=None) -> dict:
    payload = {
        "request_id": req.get("request_id"),
        "ok": bool(ok),
        "agent": req.get("agent"),
        "host": HOSTNAME,
        "tracker_id": TRACKER_ID,
    }
    if launch_result is not None:
        payload["launch_result"] = launch_result
    if error:
        payload["error"] = _bounded_remote_run_error(error)
    return {k: v for k, v in payload.items() if v is not None}


def _publish_remote_run_result(req: dict, result: dict) -> None:
    target_tracker_id = req.get("reply_to_tracker_id") or req.get("source_tracker_id")
    if not target_tracker_id:
        return
    status = publish_tracker_event(target_tracker_id, "remote_run_result", result)
    if status not in (200, 202):
        LOG.warning("failed to publish remote_run_result request_id=%s target_tracker_id=%s status=%s", req.get("request_id"), target_tracker_id, status)


def _handle_remote_run(payload: dict):
    """Run a remote_run_request via canonical local `broccoli-comms run`."""
    try:
        req = _normalize_remote_run_payload(payload)
    except ValueError as e:
        LOG.warning("dropping invalid remote_run_request: %s", e)
        return
    request_id = req.get("request_id")
    agent = req.get("agent")
    if not remote_run_enabled():
        LOG.warning("remote_run_request disabled request_id=%s agent=%s", request_id, agent)
        _publish_remote_run_result(req, _remote_run_result_payload(req, False, error="remote run disabled"))
        return
    if not agent:
        LOG.warning("remote_run_request missing agent request_id=%s", request_id)
        return

    try:
        argv = _broccoli_comms_run_argv(req)
        proc = subprocess.run(argv, text=True, capture_output=True, timeout=60)
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        launch_result = None
        if stdout:
            try:
                launch_result = json.loads(stdout)
            except Exception:
                launch_result = {"stdout": _bounded_remote_run_error(stdout)}
        if proc.returncode == 0:
            result = _remote_run_result_payload(req, True, launch_result=launch_result or {})
            _publish_remote_run_result(req, result)
            LOG.info("remote_run_request completed request_id=%s agent=%s", request_id, agent)
            return result
        error = stderr or stdout or f"broccoli-comms run exited {proc.returncode}"
        result = _remote_run_result_payload(req, False, launch_result=launch_result, error=error)
        _publish_remote_run_result(req, result)
        LOG.warning("remote_run_request failed request_id=%s agent=%s code=%s error=%s", request_id, agent, proc.returncode, _bounded_remote_run_error(error, 300))
        return result
    except Exception as e:
        result = _remote_run_result_payload(req, False, error=e)
        _publish_remote_run_result(req, result)
        LOG.error("failed remote_run_request request_id=%s agent=%s: %s", request_id, agent, e)
        return result


def _handle_remote_run_request(payload: dict):
    """Compatibility wrapper for the P1 handler name."""
    return _handle_remote_run(payload)


def _handle_remote_spin(config_name):
    """Deprecated compatibility wrapper for legacy spin_request config names."""
    LOG.warning("spin_request is deprecated; routing config_name=%s through remote_run_request", config_name)
    _handle_remote_run_request({"request_id": f"legacy-spin-{uuid.uuid4()}", "agent": config_name})


def _handle_remote_save(agent_to_save, agent_name=None, command=None, description=None, cwd=None):
    """Saves an active agent locally securely."""
    LOG.info("remote save request: agent_to_save=%s agent_name=%s command=%s description=%s cwd=%s", agent_to_save, agent_name, command, description, cwd)
    import state as local_state
    
    agent_id = local_state._resolve_agent_id(agent_to_save)
    if not agent_id:
        all_agents = local_state.get_all_agents()
        matched_name = None
        for name in all_agents.keys():
            if name.startswith(f"{agent_to_save}-agent-") or name == agent_to_save:
                matched_name = name
                break
        if matched_name:
            agent_id = local_state._resolve_agent_id(matched_name)
            
    if not agent_id:
        LOG.warning("remote save request failed: agent not found: %s", agent_to_save)
        return
        
    agent_info = local_state.state.get(agent_id)
    if not agent_info:
        LOG.warning("remote save request failed: agent state not found for ID %s", agent_id)
        return
        
    working_dir = cwd if cwd else agent_info.get("cwd")
    save_command = command if command else agent_info.get("agent_cmd")
    
    save_name = agent_name
    if not save_name:
        save_name = agent_info.get("name")
        if "-agent-" in save_name:
            save_name = save_name.split("-agent-")[0]
            
    if not save_name:
        LOG.warning("remote save request failed: could not determine save name")
        return
        
    if not working_dir or not save_command:
        pane_id = agent_info.get("tmux_pane")
        if pane_id:
            try:
                from ctl_commands.save import query_tmux_path, query_tmux_option
                tmux_socket = agent_info.get("tmux_socket")
                if not working_dir:
                    working_dir = query_tmux_path(pane_id, tmux_socket)
                if not save_command:
                    save_command = query_tmux_option(pane_id, "@agent_cmd", tmux_socket)
            except Exception as e:
                LOG.warning("failed to query tmux for remote save: %s", e)
                
    if not working_dir or not save_command:
        LOG.warning("remote save request failed: working_dir or command missing for %s", save_name)
        return
        
    working_dir = os.path.abspath(os.path.expanduser(working_dir))
    
    try:
        parts = shlex.split(save_command)
        agent_command = parts[0]
        agent_args = parts[1:]
    except Exception as e:
        LOG.error("failed to parse command string %s: %s", save_command, e)
        return
        
    save_description = description if description else f"Remote-saved configuration for agent {save_name} in {working_dir}"
    
    home = os.path.expanduser("~")
    config_dir = os.path.join(home, ".config", "agent-tracker", "agents", save_name)
    os.makedirs(config_dir, exist_ok=True)
    config_file = os.path.join(config_dir, "config.json")
    
    payload = {
        "directory": working_dir,
        "agent-command": agent_command,
        "agent-args": agent_args,
        "description": save_description,
    }
    
    try:
        with open(config_file, "w") as f:
            json.dump(payload, f, indent=2)
        LOG.info("remote save request successfully executed for %s: saved to %s", save_name, config_file)
    except Exception as e:
        LOG.error("failed to write remote-saved config: %s", e)


def _handle_remote_pane_capture(payload: dict):
    """Handles a remote request to capture a local pane and send the snapshot to a target."""
    source = payload.get("source")
    target = payload.get("target")
    try:
        default_capture_lines = config.get("ui", "capture_pane_default_lines", 20)
    except (TypeError, ValueError):
        default_capture_lines = 20
    if default_capture_lines <= 0:
        default_capture_lines = 20
    last = payload.get("last", default_capture_lines)
    fmt = payload.get("format", "markdown")
    note = payload.get("note")
    include_ansi = bool(payload.get("include_ansi", False))
    request_id = payload.get("request_id")

    LOG.info("remote pane capture request: source=%s target=%s last=%s format=%s note=%s request_id=%s", source, target, last, fmt, note, request_id)
    if not source or not target:
        LOG.warning("remote pane capture request failed: source or target missing")
        return

    try:
        # 1. Trigger handle_capture_pane internally to query local state and tmux pane
        from rpc_handler import handle_capture_pane
        
        capture_params = {
            "last_lines": last,
            "include_ansi": include_ansi
        }
        
        from ctl_commands.common import is_uuid
        if is_uuid(source):
            capture_params["agent_id"] = source
        elif source.startswith("%") and source[1:].isdigit():
            capture_params["tmux_pane"] = source
        else:
            capture_params["agent_name"] = source

        # Capture the pane snapshot locally
        snapshot = handle_capture_pane(capture_params)
        
        # 2. Format snapshot output
        if fmt == "json":
            msg_payload = {
                "type": "pane_snapshot",
                "source_agent_name": snapshot.get("agent_name"),
                "source_agent_id": snapshot.get("agent_id"),
                "tmux_pane": snapshot.get("tmux_pane"),
                "session": snapshot.get("session"),
                "copy_mode": snapshot.get("copy_mode"),
                "captured_at": snapshot.get("captured_at"),
                "lines_requested": snapshot.get("lines_requested"),
                "note": note,
                "request_id": request_id,
                "content": snapshot.get("content", "")
            }
            message_text = json.dumps(msg_payload)
        else: # markdown
            note_block = ""
            if note:
                note_block = f"- **User Note:** {note}\n"
                
            source_display = snapshot.get("agent_name") or "Unnamed Agent"
            if snapshot.get("agent_id"):
                source_display += f" ({snapshot.get('agent_id')})"
                
            message_text = (
                f"### Pane Capture Snapshot from {source_display}\n"
                f"- **Pane:** {snapshot.get('tmux_pane') or 'unknown'}\n"
                f"- **Session:** {snapshot.get('session') or 'unknown'}\n"
                f"- **Copy Mode:** {'Active' if snapshot.get('copy_mode') else 'Inactive'}\n"
                f"- **Captured At:** {snapshot.get('captured_at')}\n"
                f"{note_block}"
                f"\n```\n"
                f"{snapshot.get('content', '')}\n"
                f"```\n"
            )

        # 3. Deliver formatted snapshot to the target address via handle_send_message
        from rpc_handler import handle_send_message
        send_params = {
            "target_address": target,
            "message": message_text,
            "sender_id": snapshot.get("agent_id"),
            "sender_name": snapshot.get("agent_name")
        }
        
        success = handle_send_message(send_params)
        if success:
            LOG.info("remote pane capture request successfully processed and snapshot sent to %s", target)
        else:
            LOG.warning("failed to deliver snapshot message to %s", target)
            
    except Exception as e:
        LOG.error("failed to execute remote pane capture request: %s", e)


def _event_loop(client=None):
    while True:
        status, body = fetch_events() if client is None else client.fetch_events()
        client_name = None if client is None else client.name
        if status != 200:
            LOG.debug("tracker event poll status=%s body=%s client=%s", status, body, client_name)
            time.sleep(2)
            continue
        for event in (body or {}).get("events") or []:
            LOG.info("tracker event received client=%s event=%s", client_name, event)
            if event.get("event_type") in {"message_delivered", "message_notified", "message_read"}:
                payload = event.get("payload") or {}
                sender_name, local_payload = _local_tracker_event_payload(event.get("event_type"), payload)
                LOG.info("mapping remote %s sender=%s message_id=%s target=%s", event.get("event_type"), sender_name, payload.get("message_id"), local_payload.get("target_agent_name"))
                state.publish_event(event.get("event_type"), local_payload)
            elif event.get("event_type") == "pane_output_event":
                payload = event.get("payload") or {}
                if _ingest_registry_pane_output_event(payload):
                    LOG.info("ingested registry pane-output event metadata event_id=%s source_tracker_id=%s", payload.get("event_id"), payload.get("source_tracker_id"))
            elif event.get("event_type") == "remote_agent_event":
                payload = event.get("payload") or {}
                LOG.info("publishing remote_agent_event locally: %s", payload)
                state.publish_event("remote_agent_event", payload)
                
                inbound_payload = {
                    "sender": payload.get("sender"),
                    "timestamp": payload.get("timestamp"),
                    "message": payload.get("message"),
                    "message_id": payload.get("message_id"),
                    "recipient": payload.get("target_agent_name"),
                    "read": False,
                    **_message_metadata(payload),
                }
                try:
                    mailbox_name = config.get("ui", "default_mailbox_name", "agent-communicator")
                    rpc_handler.deliver_local_message(mailbox_name, inbound_payload)
                    LOG.info("persisted remote watched message into mailbox inbox %s", mailbox_name)
                except Exception as e:
                    LOG.warning("failed to persist remote watched event: %s", e)
            elif event.get("event_type") == "watch_group_request":
                payload = event.get("payload") or {}
                LOG.info("received delegated watch_group_request: %s", payload)
                watch_id = payload.get("watch_id")
                group_id = payload.get("group_id")
                members = payload.get("members", [])
                lease_seconds = payload.get("lease_seconds", 120)
                include_body = payload.get("include_body", True)
                reply_to_tracker_id = payload.get("reply_to_tracker_id")
                
                if watch_id and group_id:
                    state.update_group_watch(
                        watch_id, group_id, members, lease_seconds, include_body,
                        reply_to_tracker_id=reply_to_tracker_id
                    )
            elif event.get("event_type") == "group_message_observed":
                payload = event.get("payload") or {}
                LOG.info("received remote group_message_observed event: %s", payload)
                group_id = payload.get("group_id")
                message_payload = payload.get("message")
                if group_id and message_payload:
                    state.append_to_group_timeline(group_id, message_payload)
                    state.publish_event("message_delivered", {
                        "message_id": message_payload.get("message_id"),
                        "target_agent_name": group_id,
                        "sender": message_payload.get("sender")
                    })
            elif event.get("event_type") == "remote_run_result":
                payload = event.get("payload") or {}
                LOG.info("publishing remote_run_result locally: %s", payload)
                state.publish_event("remote_run_result", payload)
            elif event.get("event_type") == "remote_run_request":
                payload = event.get("payload") or {}
                threading.Thread(target=_handle_remote_run_request, args=(payload,), daemon=True).start()
            elif event.get("event_type") == "spin_request":
                payload = event.get("payload") or {}
                LOG.warning("spin_request is deprecated; route remote launches with remote_run_request")
                if payload.get("config_name") and not payload.get("agent"):
                    payload = {**payload, "agent": payload.get("config_name")}
                threading.Thread(target=_handle_remote_run_request, args=(payload,), daemon=True).start()
            elif event.get("event_type") == "save_request":
                payload = event.get("payload") or {}
                agent_to_save = payload.get("agent_to_save")
                agent_name = payload.get("agent_name")
                command = payload.get("command")
                description = payload.get("description")
                cwd = payload.get("cwd")
                if agent_to_save:
                    def run_save_and_register(c=client):
                        _handle_remote_save(agent_to_save, agent_name, command, description, cwd)
                        try:
                            if c is None:
                                register()
                            else:
                                c.register()
                        except Exception as ex:
                            LOG.warning("failed to re-register after remote save: %s", ex)
                    threading.Thread(target=run_save_and_register, daemon=True).start()
            elif event.get("event_type") == "pane_capture_request":
                payload = event.get("payload") or {}
                threading.Thread(target=_handle_remote_pane_capture, args=(payload,), daemon=True).start()
            ack = ack_event(event.get("event_id")) if client is None else client.ack_event(event.get("event_id"))
            if ack != 200:
                LOG.warning("failed to ack tracker event event_id=%s status=%s", event.get("event_id"), ack)


def _handle_pane_input_delivery(delivery):
    request_id = delivery.get("request_id") or delivery.get("pane_input_id") or delivery.get("message_id")
    pane_input_id = delivery.get("pane_input_id") or request_id
    audit = _redacted_pane_input_audit(delivery)
    audit["request_id"] = request_id
    audit["pane_input_id"] = pane_input_id
    if not request_id:
        raise RuntimeError("remote pane input delivery missing request_id")
    if state.pane_input_was_applied(request_id):
        LOG.info("remote pane input duplicate recognized audit=%s", {**audit, "result": "duplicate"})
        return "duplicate"
    if not remote_pane_input_receive_enabled():
        LOG.warning("remote pane input receive gate disabled audit=%s", {**audit, "result": "receiver_disabled"})
        raise RuntimeError("remote direct pane input receive is disabled")

    mode = (delivery.get("input_type") or delivery.get("mode") or "").lower()
    params = {"agent_id": delivery.get("target_agent_id"), "input_type": mode}
    if mode == "text":
        params["text"] = delivery.get("text")
        params["submit"] = delivery.get("submit", True)
    elif mode == "keys":
        params["keys"] = delivery.get("keys")
    else:
        raise RuntimeError("remote pane input delivery has invalid input_type")

    from rpc_handler import handle_send_input
    result = handle_send_input(params)
    if not result or not result.get("success"):
        raise RuntimeError("remote pane input injection did not report success")
    state.mark_pane_input_applied(request_id, pane_input_id, delivery.get("target_agent_id"))
    LOG.info("remote pane input injected audit=%s", {**audit, "result": "injected"})
    return "injected"


def _delivery_loop(client=None):
    missing_target_first_seen = {}
    tracker_id = TRACKER_ID if client is None else client.tracker_id
    while True:
        status, body = fetch_deliveries() if client is None else client.fetch_deliveries()
        if status == 404:
            LOG.warning("registry delivery poll got 404 for tracker_id=%s; re-registering", tracker_id)
            register() if client is None else client.register()
            continue
        if status != 200:
            LOG.warning("registry delivery poll failed for tracker_id=%s status=%s body=%s", tracker_id, status, body)
            time.sleep(2)
            continue
        deliveries = (body or {}).get("deliveries") or []
        if not deliveries:
            continue
        LOG.info("received %s queued registry deliveries for tracker_id=%s", len(deliveries), tracker_id)
        from rpc_handler import deliver_local_message, DeliveryTargetNotFound, DeliveryValidationError
        for delivery in deliveries:
            try:
                if delivery.get("delivery_type") == "pane_input":
                    LOG.info("dispatching queued registry pane input audit=%s", _redacted_pane_input_audit(delivery, result="dispatch"))
                    _handle_pane_input_delivery(delivery)
                    ack_status = _ack(client, delivery["message_id"])
                    if ack_status == 200:
                        missing_target_first_seen.pop(delivery.get("message_id"), None)
                        LOG.info("acked queued registry pane input message_id=%s pane_input_id=%s", delivery["message_id"], delivery.get("pane_input_id"))
                    continue

                LOG.info("delivering queued registry message message_id=%s sender_agent_id=%s sender_tracker_id=%s target_agent_id=%s", delivery.get("message_id"), delivery.get("sender_agent_id"), delivery.get("sender_tracker_id"), delivery.get("target_agent_id"))
                deliver_local_message(
                    delivery["target_agent_id"],
                    {
                        "sender": f'{delivery.get("sender_name", "unknown")} (via {delivery.get("sender_tracker", "unknown")})',
                        "timestamp": delivery.get("sent_at"),
                        "message": delivery.get("message"),
                        "attachments": delivery.get("attachments"),
                        "read": False,
                        "message_id": delivery.get("message_id"),
                        "delivery_id": delivery.get("delivery_id"),
                        "sender_agent_id": delivery.get("sender_agent_id"),
                        "sender_tracker_id": delivery.get("sender_tracker_id"),
                        "sender_hostname": delivery.get("sender_hostname") or delivery.get("sender_tracker"),
                        "sender_model_type": delivery.get("sender_model_type"),
                        "sender_agent_type": delivery.get("sender_agent_type"),
                        "sender_agent_cmd": delivery.get("sender_agent_cmd"),
                        "kind": delivery.get("kind"),
                        **_message_metadata(delivery),
                        "recipient_agent_id": delivery.get("target_agent_id"),
                        "recipient_tracker_id": tracker_id,
                        "recipient_hostname": HOSTNAME,
                        "swarms": delivery.get("swarms") or [],
                        "membership_snapshot": delivery.get("membership_snapshot") or {},
                        "swarm_context": delivery.get("swarm_context"),
                    },
                )
                ack_key = delivery.get("delivery_id") or delivery["message_id"]
                ack_status = _ack(client, ack_key)
                if ack_status == 200:
                    missing_target_first_seen.pop(delivery.get("message_id"), None)
                    LOG.info("delivered and acked queued registry delivery=%s message_id=%s target_agent_id=%s", ack_key, delivery["message_id"], delivery["target_agent_id"])
            except DeliveryValidationError as e:
                logging.warning(f"dropping invalid queued registry message {delivery.get('message_id')}: {e}")
                ack_status = _ack(client, delivery["message_id"])
                if ack_status == 200:
                    missing_target_first_seen.pop(delivery.get("message_id"), None)
                    LOG.info("acked invalid queued registry message_id=%s after local validation failure", delivery["message_id"])
            except DeliveryTargetNotFound as e:
                message_id = delivery.get("message_id")
                now = time.time()
                first_seen = missing_target_first_seen.setdefault(message_id, now)
                age = now - first_seen
                if age >= DELIVERY_TARGET_GRACE_SECONDS:
                    logging.warning(
                        "dropping queued registry message %s after %.1fs target-not-found grace: %s",
                        message_id,
                        age,
                        e,
                    )
                    ack_status = _ack(client, delivery["message_id"])
                    if ack_status == 200:
                        missing_target_first_seen.pop(message_id, None)
                        LOG.info("acked undeliverable queued registry message_id=%s after target-not-found grace", message_id)
                else:
                    logging.warning(
                        "deferring queued registry message %s for missing target %s (age %.1fs < grace %ss): %s",
                        message_id,
                        delivery.get("target_agent_id"),
                        age,
                        DELIVERY_TARGET_GRACE_SECONDS,
                        e,
                    )
                    time.sleep(2)
            except RuntimeError as e:
                logging.warning(f"transient local delivery failure for queued registry message {delivery.get('message_id')}: {e}")
                time.sleep(2)
            except Exception as e:
                logging.warning(f"unexpected delivery failure for queued registry message {delivery.get('message_id')}: {e}")
                time.sleep(2)


def background_sync():
    clients = load_registry_clients()
    if not clients:
        _reset_registry_status_if_unconfigured()
        LOG.info("registry sync disabled: no registries configured")
        return
    LOG.info("starting registry sync for %s registries tracker_id=%s hostname=%s", len(clients), TRACKER_ID, HOSTNAME)
    for client in clients:
        threading.Thread(target=_heartbeat_loop, args=(client,), daemon=True).start()
        threading.Thread(target=_event_loop, args=(client,), daemon=True).start()
        threading.Thread(target=_delivery_loop, args=(client,), daemon=True).start()
    while True:
        time.sleep(3600)
