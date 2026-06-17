import logging
import os
import re
import time
import uuid

import permission_detection
import registry_client
import state
import tmux_util


AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
VALID_SWARM_ROLES = {"main", "subagent"}


def normalize_swarms(swarms: object) -> list[dict[str, str]]:
    if swarms is None:
        return []
    if not isinstance(swarms, list):
        raise ValueError("swarms must be a list")
    normalized = []
    for item in swarms:
        if not isinstance(item, dict):
            raise ValueError("swarm membership must be an object")
        name = item.get("name")
        role = item.get("role")
        if not isinstance(name, str) or not name or not AGENT_NAME_RE.match(name):
            raise ValueError("swarm name must contain only letters, numbers, dot, underscore, and dash")
        if role not in VALID_SWARM_ROLES:
            raise ValueError("swarm role must be 'main' or 'subagent'")
        normalized.append({"name": name, "role": role})
    return normalized


def generate_unique_agent_name(name: str, session: str = None, is_register: bool = False) -> str:
    if not name:
        num = 1
        while f"{session}-agent-{num}" in state.get_all_agents():
            num += 1
        return f"{session}-agent-{num}"

    agent_name = name
    base_name = name
    num = 1
    m = re.match(r'^(.*)-(\d+)$', name)
    if m:
        base_name = m.group(1)
        num = int(m.group(2))

    has_suffix = bool(m)
    while True:
        has_conflict = state.get_agent_id_by_name(agent_name)
        if not has_conflict:
            break

        is_spawning = (state.get_agent(agent_name) or {}).get("status") == "spawning"
        if is_spawning and is_register:
            break

        if not has_suffix:
            num = 1
            has_suffix = True
        else:
            num += 1
        agent_name = f"{base_name}-{num}"

    return agent_name


def _best_effort_update_tmux_metadata(tmux_pane, agent_name, agent_id, agent_type, agent_cmd, tmux_socket, no_notify_with_send_keys=False, no_registry=False):
    """Persist restart-recovery metadata in tmux without making registration depend on tmux."""
    try:
        tmux_util.set_agent_id(tmux_pane, agent_id, tmux_socket)
        tmux_util.set_agent_uuid(tmux_pane, agent_id, tmux_socket)
        tmux_util.set_agent_name(tmux_pane, agent_name, tmux_socket)
        tmux_util.set_agent_type(tmux_pane, agent_type or "unknown", tmux_socket)
        tmux_util.set_agent_cmd(tmux_pane, agent_cmd or "unknown", tmux_socket)
        tmux_util.set_agent_no_notify_with_send_keys(tmux_pane, no_notify_with_send_keys, tmux_socket)
        tmux_util.set_agent_no_registry(tmux_pane, no_registry, tmux_socket)
        tmux_util.set_pane_title(tmux_pane, agent_name, tmux_socket)
    except Exception as e:
        logging.warning("failed to update tmux metadata for agent %s pane %s: %s", agent_name, tmux_pane, e)


def handle_register(params: dict) -> str:
    """Handles agent registration, accepting a stable agent_id when provided."""
    session = params.get("session")
    tmux_pane = params.get("tmux_pane")
    wrapper_pid = params.get("wrapper_pid")
    tmux_socket = params.get("tmux_socket")
    name = params.get("name")
    raw_agent_type = params.get("agent_type")
    raw_agent_cmd = params.get("agent_cmd")
    raw_model_type = params.get("model_type")
    agent_id = params.get("agent_id") or str(uuid.uuid4())
    no_notify_with_send_keys = bool(params.get("no_notify_with_send_keys", False))
    no_registry = bool(params.get("no_registry", False))
    cwd = params.get("cwd")
    raw_swarms = params.get("swarms") if "swarms" in params else None

    if not (session and tmux_pane and wrapper_pid and tmux_socket):
        raise ValueError("Invalid params")

    agents = state.get_all_agents()
    existing_name_for_id = state.get_agent_name_by_id(agent_id)

    # Remove any existing agent for the same pane to prevent duplicates.
    for existing_name, info in list(agents.items()):
        if info.get("tmux_pane") == tmux_pane and existing_name != existing_name_for_id:
            logging.info(f"Removing existing agent {existing_name} for pane {tmux_pane} before re-registering")
            state.delete_agent(existing_name)
            agents = state.get_all_agents()

    if existing_name_for_id:
        agent_name = existing_name_for_id
    else:
        agent_name = generate_unique_agent_name(name, session, is_register=True)

    existing_info = state.get_agent(existing_name_for_id) if existing_name_for_id else None
    resolved_agent_type = raw_agent_type if "agent_type" in params else (existing_info or {}).get("agent_type", "unknown")
    resolved_agent_cmd = raw_agent_cmd if "agent_cmd" in params else (existing_info or {}).get("agent_cmd", "unknown")
    model_type = state.normalize_model_type(raw_model_type, resolved_agent_type, resolved_agent_cmd)

    swarms = normalize_swarms(raw_swarms) if raw_swarms is not None else (existing_info or {}).get("swarms", [])
    state.set_agent(agent_name, {
        **(existing_info or {}),
        "session": session,
        "tmux_pane": tmux_pane,
        "wrapper_pid": wrapper_pid,
        "tmux_socket": tmux_socket,
        "pid": (existing_info or {}).get("pid"),
        "status": (existing_info or {}).get("status", "idle"),
        "waiting_approval": (existing_info or {}).get("waiting_approval", False),
        "agent_id": agent_id,
        "uuid": agent_id,
        "agent_type": resolved_agent_type or (existing_info or {}).get("agent_type", "unknown"),
        "agent_cmd": resolved_agent_cmd or (existing_info or {}).get("agent_cmd", "unknown"),
        "model_type": model_type,
        "no_notify_with_send_keys": no_notify_with_send_keys,
        "no_registry": no_registry,
        "cwd": cwd or (existing_info or {}).get("cwd"),
        "swarms": swarms,
        "is_mailbox": False,
        "direct_input_disabled": False,
        "last_heartbeat": time.time(),
        "recovered_at": None,
        "pending_notifications": (existing_info or {}).get("pending_notifications", [])
    })

    _best_effort_update_tmux_metadata(tmux_pane, agent_name, agent_id, resolved_agent_type, resolved_agent_cmd, tmux_socket, no_notify_with_send_keys, no_registry)
    registered_info = state.get_agent(agent_name) or {}
    state.publish_event("agent_registered", agent_event_payload(agent_name, registered_info))

    return agent_name


def _fetch_raw_registry_data(client, timeout=1.5) -> tuple:
    """Fetches raw agents and trackers from a single registry.
    
    Returns (registry_name, agents_list, trackers_list, error_msg).
    """
    registry_name = client.name or "default"
    agents = []
    trackers = []
    error = None
    
    # Fetch agents
    try:
        status, body = client.fetch_agents(timeout=timeout)
        if status == 200:
            agents = (body or {}).get("agents") or []
        else:
            error = f"HTTP {status}"
    except Exception as e:
        logging.warning("failed to fetch agents from registry %s: %s", registry_name, e)
        error = str(e)
        
    # Fetch trackers
    if not error:
        try:
            status, body = client.fetch_trackers(timeout=timeout)
            if status == 200:
                trackers = (body or {}).get("trackers") or []
            else:
                error = f"HTTP {status}"
        except Exception as e:
            logging.warning("failed to fetch trackers from registry %s: %s", registry_name, e)
            error = str(e)
        
    return registry_name, agents, trackers, error


def _fetch_registry_agents_for_list() -> dict:
    """Best-effort fetch of remote agents from configured registries."""
    import concurrent.futures
    remote_agents = {}
    clients = registry_client.load_registry_clients()
    if not clients:
        return {}
        
    results = []
    warnings = []
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=len(clients))
    try:
        futures = {executor.submit(_fetch_raw_registry_data, client, timeout=1.5): client for client in clients}
        done, not_done = concurrent.futures.wait(futures.keys(), timeout=2.0)
        
        for future in done:
            try:
                registry_name, agents, trackers, error = future.result()
                results.append((registry_name, agents, trackers))
                if error:
                    warnings.append((registry_name, f"Registry error: {error}"))
            except Exception as e:
                client = futures[future]
                logging.warning("registry fetch thread raised exception for %s: %s", client.name, e)
                warnings.append((client.name, f"Exception: {e}"))
                
        for future in not_done:
            client = futures[future]
            logging.warning("registry fetch timed out for registry %s", client.name)
            warnings.append((client.name, "Connection timed out"))
    finally:
        executor.shutdown(wait=False)
        
    # 1. Process successful registry results
    for registry_name, agents, trackers in results:
        for agent in agents:
            hostname = agent.get("hostname")
            name = agent.get("name")
            if not hostname or not name:
                continue
            base_key = f"{hostname}/{name}"
            key = base_key
            if base_key in remote_agents and remote_agents[base_key].get("agent_id") != agent.get("agent_id"):
                existing = remote_agents.pop(base_key)
                existing_registry = existing.get("registry_name") or "default"
                existing_key = f"{existing_registry}:{base_key}"
                remote_agents[existing_key] = {**existing, "name": existing_key, "target_address": existing_key}
                key = f"{registry_name}:{base_key}"
            elif base_key not in remote_agents and any(k.endswith(f":{base_key}") for k in remote_agents):
                key = f"{registry_name}:{base_key}"
            remote_agents[key] = {
                **agent,
                **state.sanitize_current_task_fields(agent),
                "name": key,
                "profile_name": name,
                "scope": "remote",
                "target_address": key,
                "registry_name": registry_name,
                "running": True,
                "model_type": state.normalize_model_type(agent.get("model_type"), agent.get("agent_type"), agent.get("agent_cmd")),
            }
            
        for tracker in trackers:
            tracker_id = tracker.get("tracker_id")
            hostname = tracker.get("hostname")
            if not tracker_id or not hostname:
                continue
            if tracker_id == registry_client.TRACKER_ID:
                continue
            for config in tracker.get("agent_configs") or []:
                agent_name = config.get("name")
                if not agent_name:
                    continue
                base_key = f"{hostname}/{agent_name}"
                if base_key in remote_agents:
                    remote_agents[base_key]["is_configured"] = True
                    remote_agents[base_key]["profile_name"] = agent_name
                    continue
                
                key = base_key if base_key not in remote_agents else f"{registry_name}:{base_key}"
                remote_agents[key] = {
                    "name": key,
                    "profile_name": agent_name,
                    "hostname": hostname,
                    "tracker_id": tracker_id,
                    "scope": "remote",
                    "target_address": key,
                    "registry_name": registry_name,
                    "is_configured": True,
                    "running": False,
                    "status": "configured",
                    "launchable": True,
                    "copyable": True,
                    "description": config.get("description", ""),
                }

    # 2. Inject virtual error agents for any warnings/timeouts
    for registry_name, error_msg in warnings:
        key = f"registry-error/{registry_name}"
        remote_agents[key] = {
            "name": f"⚠️ Registry '{registry_name}' offline",
            "agent_id": f"error-{registry_name}",
            "uuid": f"error-{registry_name}",
            "scope": "remote",
            "status": "offline",
            "registry_name": registry_name,
            "agent_type": "error",
            "agent_cmd": "error",
            "model_type": "error",
            "cwd": error_msg,
            "running": False,
            "is_configured": True,
        }

    return remote_agents


def agent_event_payload(name: str, info: dict) -> dict:
    return {
        "target_agent_id": info.get("agent_id") or info.get("uuid"),
        "target_agent_name": name,
        "hostname": info.get("hostname") or registry_client.HOSTNAME,
        "tracker_id": info.get("tracker_id") or registry_client.TRACKER_ID,
        "status": info.get("status", "unknown"),
        "model_type": state.normalize_model_type(info.get("model_type"), info.get("agent_type"), info.get("agent_cmd")),
        "agent_type": info.get("agent_type"),
        "agent_cmd": info.get("agent_cmd"),
    }


def _local_agent_list_row(name: str, info: dict, durable_tasks: dict | None = None) -> dict:
    display_name = info.get("name") or name
    return {
        **info,
        "name": display_name,
        "scope": "local",
        "hostname": info.get("hostname") or registry_client.HOSTNAME,
        "tracker_id": info.get("tracker_id") or registry_client.TRACKER_ID,
        "target_address": info.get("target_address") or name,
        **state.shared_service_metadata(display_name, info),
        "model_type": state.normalize_model_type(info.get("model_type"), info.get("agent_type"), info.get("agent_cmd")),
        "swarms": normalize_swarms(info.get("swarms", [])),
        **state.current_task_fields_for_agent(display_name, info, durable_tasks),
    }


def _merge_registry_agents_for_list(local_agents: dict, remote_agents: dict) -> dict:
    durable_tasks = state.durable_current_tasks_by_agent()
    merged = {name: _local_agent_list_row(name, info, durable_tasks) for name, info in (local_agents or {}).items()}
    local_agent_ids = {info.get("agent_id") for info in (local_agents or {}).values() if info.get("agent_id")}
    for name, info in (remote_agents or {}).items():
        if info.get("agent_id") in local_agent_ids:
            continue
        merged[name] = info
    return merged


def handle_list(params: dict, caller_pid: int = None, identify_agent=None) -> dict:
    """Returns agents in state, marking the caller if identified.

    Remote registry agents are opt-in so status-bar callers keep rendering only
    local active agents.
    """
    agents = state.get_all_agents()
    if params.get("include_remote"):
        agents = _merge_registry_agents_for_list(agents, _fetch_registry_agents_for_list())
    else:
        durable_tasks = state.durable_current_tasks_by_agent()
        agents = {name: _local_agent_list_row(name, info, durable_tasks) for name, info in (agents or {}).items()}
    caller_name = identify_agent(params, caller_pid) if identify_agent else None

    detection_status = permission_detection.detection_status_snapshot()
    for name, detection in detection_status.items():
        if name in agents:
            agents[name]["detection"] = detection

    if caller_name and caller_name in agents:
        agents[caller_name]["is_this_me"] = True

    return agents


def handle_ensure_mailbox(params: dict) -> dict:
    """Ensures a local UI/mailbox identity exists without tmux pane control.

    Native frontends need a stable inbox
    identity but should not masquerade as a controllable coding-agent pane.
    Mailbox identities are no-notify records that can be registry-visible for
    normal cross-host messages but cannot receive direct pane input.
    """
    name = params.get("agent_name") or params.get("name")
    if not isinstance(name, str) or not name.strip() or "/" in name or name.startswith("registry:"):
        raise ValueError("agent_name must be a local name")
    name = name.strip()
    agent_id = params.get("agent_id")
    if agent_id is not None and not isinstance(agent_id, str):
        raise ValueError("agent_id must be a string")
    if not agent_id:
        agent_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{registry_client.TRACKER_ID}:mailbox:{name}"))

    existing = state.get_agent(name) or {}
    preserve_pane = bool(params.get("preserve_pane", False)) and bool(existing.get("tmux_pane"))
    pane_fields = {
        "session": existing.get("session") if preserve_pane else None,
        "tmux_pane": existing.get("tmux_pane") if preserve_pane else None,
        "wrapper_pid": existing.get("wrapper_pid") if preserve_pane else None,
        "tmux_socket": existing.get("tmux_socket") if preserve_pane else None,
        "pid": existing.get("pid") if preserve_pane else None,
    }
    state.set_agent(name, {
        **existing,
        **pane_fields,
        "status": existing.get("status", "idle"),
        "waiting_approval": existing.get("waiting_approval", False),
        "agent_id": existing.get("agent_id") or agent_id,
        "uuid": existing.get("uuid") or existing.get("agent_id") or agent_id,
        "agent_type": "agent-communicator-ui",
        "agent_cmd": existing.get("agent_cmd", "agent-communicator"),
        "model_type": state.normalize_model_type("agent-communicator-ui", "agent-communicator-ui", existing.get("agent_cmd", "agent-communicator")),
        **state.shared_service_metadata(name, {"name": name}),
        "no_notify_with_send_keys": True,
        "no_registry": existing.get("no_registry", params.get("no_registry", False)) if preserve_pane else params.get("no_registry", False),
        "direct_input_disabled": True,
        "cwd": params.get("cwd") or existing.get("cwd"),
        "last_heartbeat": time.time(),
        "recovered_at": None,
        "pending_notifications": existing.get("pending_notifications", []),
        "is_mailbox": True,
    })
    info = state.get_agent(name) or {}
    return {"name": name, "agent_id": info.get("agent_id"), "uuid": info.get("uuid")}


def _identify_or_error(params: dict, caller_pid: int, identify_agent):
    agent_name = identify_agent(params, caller_pid) if identify_agent else None
    if not agent_name:
        raise ValueError("Agent not identified")
    return agent_name


def handle_update_agent(params: dict, caller_pid: int = None, identify_agent=None) -> bool:
    """Updates agent state fields."""
    agent_name = _identify_or_error(params, caller_pid, identify_agent)

    kwargs = {k: v for k, v in params.items() if k not in ["agent_id", "agent_name", "tmux_pane"]}
    if "swarms" in kwargs:
        kwargs["swarms"] = normalize_swarms(kwargs.get("swarms"))
    old_info = state.get_agent(agent_name) or {}
    old_status = old_info.get("status")
    if state.update_agent(agent_name, **kwargs):
        info = state.get_agent(agent_name) or {}
        if "status" in kwargs:
            registry_client.push_agent_update(info["agent_id"], kwargs["status"])
            if kwargs.get("status") != old_status:
                state.publish_event("agent_status_changed", {**agent_event_payload(agent_name, info), "old_status": old_status})
        return True
    raise ValueError(f"Agent '{agent_name}' not found")


def handle_heartbeat(params: dict, caller_pid: int = None, identify_agent=None) -> bool:
    """Records a liveness heartbeat for an identified agent."""
    agent_name = _identify_or_error(params, caller_pid, identify_agent)

    old_info = state.get_agent(agent_name) or {}
    old_status = old_info.get("status")
    kwargs = {k: v for k, v in params.items() if k not in ["agent_id", "agent_name"]}
    if "swarms" in kwargs:
        kwargs["swarms"] = normalize_swarms(kwargs.get("swarms"))
    kwargs["last_heartbeat"] = time.time()
    kwargs["recovered_at"] = None
    if state.update_agent(agent_name, **kwargs):
        if "status" in kwargs and kwargs.get("status") != old_status:
            info = state.get_agent(agent_name) or {}
            state.publish_event("agent_status_changed", {**agent_event_payload(agent_name, info), "old_status": old_status})
        return True
    raise ValueError(f"Agent '{agent_name}' not found")


def handle_rename(params: dict, caller_pid: int = None, identify_agent=None) -> bool:
    """Renames an agent with safety checks.
    Users can rename themselves by providing new_name.
    Renaming others requires old_name, new_name, and force=True.
    """
    old_name = params.get("old_name")
    new_name = params.get("new_name")
    force = params.get("force", False)

    caller_name = identify_agent(params, caller_pid) if identify_agent else None

    if not caller_name and not force:
        raise ValueError("Could not identify caller. Use --force and provide old_name to rename.")

    # If old_name is not provided, assume self-rename
    if not old_name:
        old_name = caller_name

    if not old_name or not new_name:
        raise ValueError("Invalid params: new_name is required.")

    if old_name != caller_name and not force:
        raise ValueError(f"Cannot rename '{old_name}' (you are '{caller_name}'). Use --force to override.")

    logging.info(f"Attempting to rename agent from {old_name} to {new_name}")
    if state.rename_agent(old_name, new_name):
        info = state.get_agent(new_name)
        tmux_pane = info.get("tmux_pane")
        tmux_socket = info.get("tmux_socket")
        logging.info(f"Renamed {old_name} to {new_name} in state. Updating tmux pane {tmux_pane}")
        if tmux_pane:
            try:
                tmux_util.set_agent_name_sync(tmux_pane, new_name, tmux_socket)
                tmux_util.set_pane_title_sync(tmux_pane, new_name, tmux_socket)
            except Exception as e:
                logging.error(f"Failed to update tmux pane for {new_name}: {e}")
        return True
    logging.error(f"Failed to rename {old_name} to {new_name}. Agent not found or new name exists.")
    raise ValueError("Agent not found or new name exists")


def handle_unregister(params: dict, caller_pid: int = None, identify_agent=None) -> bool:
    """Unregisters an agent from state."""
    agent_name = identify_agent(params, caller_pid) if identify_agent else None
    if not agent_name:
        # Try to find by tmux_pane if provided
        tmux_pane = params.get("tmux_pane")
        if tmux_pane:
            agent_name = state.get_agent_name_by_pane(tmux_pane)

    if not agent_name:
        raise ValueError("Agent not identified")

    info = state.get_agent(agent_name)
    if not info:
        raise ValueError(f"Agent '{agent_name}' not found")

    logging.info(f"Unregistering agent: {agent_name}")

    # Remove inbox file
    uuid_str = info.get("uuid") or agent_name
    inbox_file = os.path.join(state.INBOX_DIR, f"{uuid_str}.inbox")
    if os.path.exists(inbox_file):
        try:
            os.remove(inbox_file)
        except OSError as e:
            logging.error(f"Failed to remove inbox file for {agent_name}: {e}")

    state.publish_event("agent_unregistered", agent_event_payload(agent_name, info))
    state.delete_agent(agent_name)
    return True


def _resolve_local_agent_name(target: str) -> str | None:
    if not target:
        return None
    if state.get_agent(target):
        return target
    name = state.get_agent_name_by_id(target)
    if name:
        return name
    return None


def _route_remote_stop_command(target_address: str, timeout_str: str, force: bool) -> bool:
    registry_name = None
    hostname, target = target_address.split("/", 1)
    if ":" in hostname:
        registry_name, hostname = hostname.split(":", 1)
        
    remote_recipient = registry_client.find_remote_agent(hostname, target, registry_name=registry_name)
    if not remote_recipient or not remote_recipient.get("tracker_id"):
        raise RuntimeError(f"Remote agent '{target_address}' not found or has no active tracker")
        
    target_tracker_id = remote_recipient["tracker_id"]
    payload = {
        "target_agent_id": remote_recipient.get("agent_id"),
        "target_agent_name": remote_recipient.get("profile_name") or target,
        "timeout": timeout_str,
        "force": force
    }
    
    logging.info(f"Routing remote stop command to tracker {target_tracker_id} for agent {target} (timeout: {timeout_str}, force: {force})")
    status = registry_client.publish_tracker_event(target_tracker_id, "remote_stop_request", payload)
    if status in (200, 202):
        return True
    raise RuntimeError(f"Failed to route remote stop command via registry: status {status}")


def _route_remote_lifecycle_command(target_address: str, event_type: str, timeout: str = None) -> bool:
    registry_name = None
    hostname, target = target_address.split("/", 1)
    if ":" in hostname:
        registry_name, hostname = hostname.split(":", 1)
        
    remote_recipient = registry_client.find_remote_agent(hostname, target, registry_name=registry_name)
    if not remote_recipient or not remote_recipient.get("tracker_id"):
        raise RuntimeError(f"Remote agent '{target_address}' not found or has no active tracker")
        
    target_tracker_id = remote_recipient["tracker_id"]
    payload = {
        "target_agent_id": remote_recipient.get("agent_id"),
        "target_agent_name": remote_recipient.get("profile_name") or target,
    }
    if timeout:
        payload["timeout"] = timeout
    
    logging.info(f"Routing remote event '{event_type}' to tracker {target_tracker_id} for agent {target}")
    status = registry_client.publish_tracker_event(target_tracker_id, event_type, payload)
    if status in (200, 202):
        return True
    raise RuntimeError(f"Failed to route remote command via registry: status {status}")


def _kill_local_agent(info: dict) -> bool:
    """Sends SIGTERM to the agent wrapper or child process to force terminate it."""
    wrapper_pid = info.get("wrapper_pid")
    pid = info.get("pid")
    agent_name = info.get("name")
    
    import signal
    if wrapper_pid:
        logging.info(f"Force terminating agent {agent_name} by sending SIGTERM to wrapper PID {wrapper_pid}")
        try:
            os.kill(wrapper_pid, signal.SIGTERM)
            return True
        except ProcessLookupError:
            logging.warning(f"Wrapper process {wrapper_pid} not found for agent {agent_name}")
            
    if pid:
        logging.info(f"Force terminating agent {agent_name} by sending SIGTERM to child PID {pid}")
        try:
            os.kill(pid, signal.SIGTERM)
            return True
        except ProcessLookupError:
            logging.warning(f"Child process {pid} not found for agent {agent_name}")
            
    return False


def _parse_timeout_to_seconds(timeout) -> float:
    """Parses a timeout value (int, float, or string like '60s') into float seconds."""
    if isinstance(timeout, (int, float)):
        return float(timeout)
    s = str(timeout).strip().lower()
    if s.endswith("s"):
        s = s[:-1]
    try:
        return float(s)
    except ValueError:
        logging.warning(f"Failed to parse timeout '{timeout}', falling back to 60.0s")
        return 60.0


def _enforce_stop_timeout(agent_name: str, timeout_seconds: float):
    """Waits for the timeout, then force kills the agent if it is still registered."""
    logging.info(f"Started stop timeout enforcement thread for agent {agent_name} (timeout: {timeout_seconds}s)")
    time.sleep(timeout_seconds)
    
    # Check if the agent is still registered in state
    info = state.get_agent(agent_name)
    if info:
        logging.warning(f"Agent {agent_name} did not exit within graceful timeout. Falling back to SIGTERM.")
        _kill_local_agent(info)
    else:
        logging.info(f"Agent {agent_name} successfully exited gracefully within timeout.")


def handle_request_stop(params: dict, caller_pid: int = None, identify_agent=None) -> bool:
    """Handles stopping a local or remote agent gracefully, with hard SIGTERM fallback after timeout."""
    target_address = params.get("target_address") or params.get("agent_name") or params.get("agent_id")
    if not target_address:
        raise ValueError("Invalid params: target is required")
        
    timeout = params.get("timeout", 60)
    timeout_seconds = _parse_timeout_to_seconds(timeout)
    timeout_str = f"{int(timeout_seconds)}s"
    
    force = params.get("force", False)
    if isinstance(force, str):
        force = force.lower() in ("true", "1", "yes")
        
    # Check if target is remote
    if isinstance(target_address, str) and "/" in target_address:
        return _route_remote_stop_command(target_address, timeout_str, force)
        
    # Resolve local agent
    agent_name = _resolve_local_agent_name(target_address)
    if not agent_name:
        info = state.get_agent(target_address)
        if info and info.get("scope") == "remote":
            return _route_remote_stop_command(f"{info.get('hostname')}/{info.get('profile_name')}", timeout_str, force)
        raise ValueError(f"Agent '{target_address}' not found")
        
    info = state.get_agent(agent_name)
    if not info:
        raise ValueError(f"Agent '{agent_name}' not found")
        
    if force:
        logging.info(f"Force termination requested for agent {agent_name} (bypassing graceful warning)")
        return _kill_local_agent(info)
        
    # Otherwise, perform graceful warning
    tmux_pane = info.get("tmux_pane")
    tmux_socket = info.get("tmux_socket")
    if not tmux_pane:
        raise RuntimeError(f"Agent '{agent_name}' has no registered tmux pane to send warning keys to")
    if not tmux_socket:
        raise RuntimeError(f"Agent '{agent_name}' has no registered tmux socket")
        
    import config
    default_warn_msg = "> Prepare for restart in {timeout}. Do memory audit and update task so that can takeover from the same. Call broccoli-comms agent {agent} restart when ready"
    warn_template = config.get("tracker", "restart_warn_message", default_warn_msg)
    
    try:
        warning_msg = warn_template.format(agent=agent_name, timeout=timeout_str)
    except KeyError as e:
        logging.warning(f"Invalid placeholders in configured restart_warn_message: {e}. Falling back to default.")
        warning_msg = default_warn_msg.format(agent=agent_name, timeout=timeout_str)
        
    logging.info(f"Sending graceful stop warning to agent {agent_name} in pane {tmux_pane} with timeout {timeout_str}")
    
    try:
        # 1. Send Escape key to clear any pending prompt/state
        tmux_util.send_symbolic_keys(tmux_pane, ["Escape"], socket_path=tmux_socket)
        
        # 2. Send warning message and press Enter
        tmux_util.send_literal_text(tmux_pane, warning_msg, submit=True, socket_path=tmux_socket)
        
        # 3. Spawn background thread to enforce the timeout fallback
        import threading
        threading.Thread(target=_enforce_stop_timeout, args=(agent_name, timeout_seconds), daemon=True).start()
        return True
    except Exception as e:
        raise RuntimeError(f"Failed to send graceful stop keys to agent {agent_name} pane {tmux_pane}: {e}")


def handle_restart_agent(params: dict, caller_pid: int = None, identify_agent=None) -> bool:
    """Handles restarting a local or remote agent."""
    target_address = params.get("target_address") or params.get("agent_name") or params.get("agent_id")
    if not target_address:
        raise ValueError("Invalid params: target is required")
        
    # Check if target is remote
    if isinstance(target_address, str) and "/" in target_address:
        return _route_remote_lifecycle_command(target_address, "remote_restart_request")
        
    # Resolve local agent
    agent_name = _resolve_local_agent_name(target_address)
    if not agent_name:
        info = state.get_agent(target_address)
        if info and info.get("scope") == "remote":
            return _route_remote_lifecycle_command(f"{info.get('hostname')}/{info.get('profile_name')}", "remote_restart_request")
        raise ValueError(f"Agent '{target_address}' not found")
        
    info = state.get_agent(agent_name)
    if not info:
        raise ValueError(f"Agent '{agent_name}' not found")
        
    wrapper_pid = info.get("wrapper_pid")
    if not wrapper_pid:
        raise RuntimeError("Restart is only supported for agents running under agent-wrapper")
        
    import signal
    logging.info(f"Restarting agent {agent_name} by sending SIGUSR1 to wrapper PID {wrapper_pid}")
    try:
        os.kill(wrapper_pid, signal.SIGUSR1)
        return True
    except ProcessLookupError:
        raise RuntimeError(f"Wrapper process {wrapper_pid} not found for agent {agent_name}")
