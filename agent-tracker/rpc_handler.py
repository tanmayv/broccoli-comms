import json
import logging
import socket
import state
import tmux_util
import message_journal
import registry_client
import permission_detection
import pane_output_lifecycle
import pane_output_parser
import datetime
import time
import threading
import uuid
import struct
import os
import hmac
import hashlib
from pathlib import Path

import config
from handlers import pane_capture
from handlers import inbox_handlers
from handlers import agent_handlers
from handlers import messaging_handlers
from handlers import memory_handlers

BUFFER_SIZE = 4096
PANE_OUTPUT_MAX_CHUNK_BYTES = int(os.environ.get("AGENT_PANE_OUTPUT_MAX_CHUNK_BYTES", "65536"))
PANE_OUTPUT_MAX_SEQUENCE_GAP = int(os.environ.get("AGENT_PANE_OUTPUT_MAX_SEQUENCE_GAP", "1000"))
PANE_OUTPUT_RATE_WINDOW_SECONDS = float(os.environ.get("AGENT_PANE_OUTPUT_RATE_WINDOW_SECONDS", "1.0"))
PANE_OUTPUT_MAX_ACCEPTED_CHUNKS_PER_WINDOW = int(os.environ.get("AGENT_PANE_OUTPUT_MAX_ACCEPTED_CHUNKS_PER_WINDOW", "200"))
LOCAL_HOSTNAME = config.get("tracker", "hostname", socket.gethostname())
REMOTE_BROAD_WATCH_ENABLED = config.get("tracker", "broad_watch_enabled", False)
PANE_OUTPUT_PARSER_STATES: dict[tuple[str, str], dict] = {}


class CursorExpiredError(ValueError):
    pass


class RPCStructuredError(ValueError):
    def __init__(self, message: str, data: dict, code: int = -32602):
        super().__init__(message)
        self.code = code
        self.data = data


def _utc_now_isoformat() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _redacted_chunk_marker(chunk) -> str:
    if isinstance(chunk, str):
        byte_count = len(chunk.encode("utf-8"))
    elif isinstance(chunk, (bytes, bytearray)):
        byte_count = len(chunk)
    else:
        byte_count = 0
    return f"<redacted {byte_count} bytes>"


def _sanitize_request_for_logging(req: dict) -> dict:
    """Redacts sensitive pane-output payloads before generic JSON-RPC logging."""
    if not isinstance(req, dict) or req.get("method") != "pane_output":
        return req
    sanitized = dict(req)
    params = sanitized.get("params")
    if isinstance(params, dict):
        safe_params = dict(params)
        if "chunk" in safe_params:
            safe_params["chunk"] = _redacted_chunk_marker(safe_params.get("chunk"))
        if "pipe_token" in safe_params:
            safe_params["pipe_token"] = "<redacted>"
        sanitized["params"] = safe_params
    return sanitized


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _generate_unique_agent_name(name: str, session: str = None, is_register: bool = False) -> str:
    return agent_handlers.generate_unique_agent_name(name, session, is_register)


def _agent_event_payload(name: str, info: dict) -> dict:
    return agent_handlers.agent_event_payload(name, info)


def handle_register(params: dict) -> str:
    return agent_handlers.handle_register(params)


def handle_ensure_mailbox(params: dict) -> dict:
    return agent_handlers.handle_ensure_mailbox(params)


def handle_list(params: dict, caller_pid: int = None) -> dict:
    return agent_handlers.handle_list(params, caller_pid, _identify_agent)


def handle_update_agent(params: dict, caller_pid: int = None) -> bool:
    return agent_handlers.handle_update_agent(params, caller_pid, _identify_agent)


def handle_heartbeat(params: dict, caller_pid: int = None) -> bool:
    return agent_handlers.handle_heartbeat(params, caller_pid, _identify_agent)


def handle_rename(params: dict, caller_pid: int = None) -> bool:
    return agent_handlers.handle_rename(params, caller_pid, _identify_agent)


def handle_unregister(params: dict, caller_pid: int = None) -> bool:
    agent_name = _identify_agent(params, caller_pid) if caller_pid or params else None
    if not agent_name and params.get("tmux_pane"):
        agent_name = state.get_agent_name_by_pane(params.get("tmux_pane"))
    if agent_name:
        pane_output_lifecycle.cleanup_pane_output_best_effort(agent_name)
    return agent_handlers.handle_unregister(params, caller_pid, _identify_agent)


def handle_request_stop(params: dict, caller_pid: int = None) -> bool:
    return agent_handlers.handle_request_stop(params, caller_pid, _identify_agent)


def handle_restart_agent(params: dict, caller_pid: int = None) -> bool:
    return agent_handlers.handle_restart_agent(params, caller_pid, _identify_agent)


DeliveryTargetNotFound = messaging_handlers.DeliveryTargetNotFound
DeliveryValidationError = messaging_handlers.DeliveryValidationError


def _publish_message_notified(info: dict, agent_name: str, pending_item):
    return messaging_handlers._publish_message_notified(info, agent_name, pending_item)


def _resolve_target_agent_name(params: dict) -> str | None:
    return messaging_handlers._resolve_target_agent_name(params)


def handle_spin_agent(params: dict, caller_pid: int = None) -> str:
    """Spins a new agent in a new tmux pane."""
    command = params.get("command")
    directory = params.get("directory")
    name = params.get("name")
    env = params.get("env") or {}

    caller_name = _identify_agent({}, caller_pid) if caller_pid else None
    caller_info = state.get_agent(caller_name) if caller_name else None

    session = params.get("session") or (caller_info or {}).get("session")
    target_pane = params.get("target_pane") or (caller_info or {}).get("tmux_pane")
    tmux_socket = params.get("tmux_socket") or (caller_info or {}).get("tmux_socket")

    if not (session and command and name):
        raise ValueError("Invalid params")

    parent_id = (caller_info or {}).get("agent_id") or (caller_info or {}).get("uuid")
    if parent_id and (env.get("AGENT_ID") == parent_id or env.get("AGENT_UUID") == parent_id or env.get("AGENT_NAME") == caller_name):
        logging.info("Stripping inherited agent identity from spun agent environment for caller %s", caller_name)
        env.pop("AGENT_ID", None)
        env.pop("AGENT_NAME", None)
        env.pop("AGENT_UUID", None)
    for key in ("AGENT_ID", "AGENT_NAME", "AGENT_UUID"):
        if env.get(key) == "":
            env.pop(key, None)

    agent_name = _generate_unique_agent_name(name, session, is_register=False)
    env["SUGGESTED_AGENT_NAME"] = agent_name

    state.set_agent(agent_name, {"status": "spawning", "timestamp": time.time(), "cwd": directory or "unknown"})

    try:
        pane_id = tmux_util.spin_agent(agent_name, command, target_pane, session=session, directory=directory, env=env, tmux_socket=tmux_socket)
        placeholder_updates = {}
        if session:
            placeholder_updates["session"] = session
        if pane_id:
            placeholder_updates["tmux_pane"] = pane_id
        if placeholder_updates:
            state.update_agent(agent_name, **placeholder_updates)
        return agent_name
    except Exception as e:
        state.delete_agent(agent_name)
        raise RuntimeError(f"Failed to spin agent: {e}")


def remote_message_focus_enabled() -> bool:
    return messaging_handlers.remote_message_focus_enabled()


def _maybe_focus_remote_delivery(info: dict, current_name: str, msg_obj: dict) -> None:
    return messaging_handlers._maybe_focus_remote_delivery(info, current_name, msg_obj)


def deliver_local_message(target_name_or_id: str, msg_obj: dict, notify_sender: str | None = None, verify: bool = False, interrupt: bool = False) -> str:
    return messaging_handlers.deliver_local_message(target_name_or_id, msg_obj, notify_sender, verify, interrupt)


def _resolve_local_target_address(params: dict, allow_remote: bool) -> dict:
    return messaging_handlers._resolve_local_target_address(params, allow_remote)


def _validate_send_input_payload(params: dict) -> tuple[str, dict]:
    return messaging_handlers._validate_send_input_payload(params)


def _route_remote_send_input(params: dict, caller_pid: int = None) -> dict | None:
    return messaging_handlers._route_remote_send_input(params, caller_pid, _identify_agent)


def _is_mailbox_or_ui_agent(info: dict) -> bool:
    return messaging_handlers._is_mailbox_or_ui_agent(info)


def handle_send_input(params: dict, caller_pid: int = None) -> dict:
    return messaging_handlers.handle_send_input(params, caller_pid, _identify_agent)


def _sender_identification_params(params: dict) -> dict:
    return messaging_handlers._sender_identification_params(params)


def _sender_metadata(sender_name: str, sender_info: dict, sender_id: str | None) -> dict:
    return messaging_handlers._sender_metadata(sender_name, sender_info, sender_id)


def handle_send_message(params: dict, caller_pid: int = None) -> bool:
    return messaging_handlers.handle_send_message(params, caller_pid, _identify_agent, deliver_local_message)


def _identify_agent(params: dict, caller_pid: int = None) -> str:
    """Identifies the agent name based on params (id/name/pane) or caller PID."""
    agent_id = params.get("sender_id") or params.get("agent_id")
    if agent_id:
        resolved_name = state.get_agent_name_by_id(agent_id)
        if resolved_name:
            return resolved_name

    agent_name = params.get("agent_name")
    if agent_name:
        return agent_name
        
    tmux_pane = params.get("tmux_pane")
    agents = state.get_all_agents()
    
    if tmux_pane:
        resolved_name = state.get_agent_name_by_pane(tmux_pane)
        if resolved_name:
            return resolved_name
                
    if caller_pid:
        # Trace up the process tree to find a match with wrapper_pid or pid
        curr_pid = caller_pid
        while curr_pid > 1:
            for name, info in agents.items():
                if info.get("wrapper_pid") == curr_pid or info.get("pid") == curr_pid:
                    return name
            try:
                with open(f"/proc/{curr_pid}/status", "r") as f:
                    for line in f:
                        if line.startswith("PPid:"):
                            curr_pid = int(line.split()[1])
                            break
                    else:
                        break
            except (IOError, ValueError):
                break
    return None


def handle_get_unread_counts(params: dict, caller_pid: int = None) -> dict:
    return inbox_handlers.handle_get_unread_counts(
        params,
        caller_pid=caller_pid,
        identify_agent=_identify_agent,
        state=state,
        registry_client=registry_client,
        RPCError=RPCStructuredError
    )


def handle_get_inbox(params: dict, caller_pid: int = None) -> dict:
    return inbox_handlers.handle_get_inbox(
        params,
        caller_pid=caller_pid,
        identify_agent=_identify_agent,
        state=state,
        registry_client=registry_client,
        RPCError=RPCStructuredError
    )


def _validate_positive_int(value, field_name: str) -> int:
    try:
        parsed = int(value)
        if parsed <= 0:
            raise ValueError(f"{field_name} must be a positive integer")
        return parsed
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be a positive integer")


def _validate_swarm_name(name: str) -> str:
    if not isinstance(name, str) or not name or not agent_handlers.AGENT_NAME_RE.match(name):
        raise ValueError("swarm name must contain only letters, numbers, dot, underscore, and dash")
    return name


def _swarm_group_id(swarm_name: str) -> str:
    return f"swarm:local:{swarm_name}"


def _broccoli_config_json_path() -> Path:
    if os.environ.get("XDG_CONFIG_HOME"):
        return Path(os.environ["XDG_CONFIG_HOME"]) / "broccoli-comms" / "config.json"
    configured = config.get("paths", "config_dir")
    if configured:
        return Path(configured).expanduser() / "config.json"
    return Path.home() / ".config" / "broccoli-comms" / "config.json"


def _load_broccoli_runtime_config() -> dict:
    path = _broccoli_config_json_path()
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        logging.warning("failed to load broccoli config for swarms from %s: %s", path, e)
        return {}


def _configured_swarm_members() -> dict[str, list[dict]]:
    cfg = _load_broccoli_runtime_config()
    agents = cfg.get("agents") if isinstance(cfg.get("agents"), dict) else {}
    swarms: dict[str, list[dict]] = {}

    top_level = cfg.get("swarms") if isinstance(cfg.get("swarms"), dict) else {}
    for swarm_name, spec in top_level.items():
        if not isinstance(spec, dict):
            continue
        try:
            _validate_swarm_name(swarm_name)
        except ValueError:
            continue
        members = spec.get("members") if isinstance(spec.get("members"), list) else []
        for member in members:
            if not isinstance(member, dict):
                continue
            agent_name = member.get("agent")
            role = member.get("role")
            if not isinstance(agent_name, str) or role not in agent_handlers.VALID_SWARM_ROLES:
                continue
            swarms.setdefault(swarm_name, []).append({
                "name": agent_name,
                "role": role,
                "agent_id": None,
                "target_address": None,
                "hostname": registry_client.HOSTNAME,
                "scope": "local",
                "configured": agent_name in agents,
                "running": False,
                "launchable": agent_name in agents,
            })

    for agent_name, spec in agents.items():
        if not isinstance(spec, dict):
            continue
        try:
            memberships = agent_handlers.normalize_swarms(spec.get("swarms", []))
        except ValueError:
            continue
        for membership in memberships:
            swarms.setdefault(membership["name"], []).append({
                "name": agent_name,
                "role": membership["role"],
                "agent_id": None,
                "target_address": None,
                "hostname": registry_client.HOSTNAME,
                "scope": "local",
                "configured": True,
                "running": False,
                "launchable": True,
            })
    return swarms


def _swarm_member_row(agent_name: str, info: dict, role: str) -> dict:
    return {
        "name": agent_name,
        "role": role,
        "agent_id": info.get("agent_id") or info.get("uuid"),
        "target_address": info.get("target_address") or agent_name,
        "hostname": info.get("hostname") or registry_client.HOSTNAME,
        "scope": info.get("scope", "local"),
        "configured": bool(info.get("configured", False)),
        "running": True,
        "launchable": bool(info.get("launchable", False)),
    }


def _agents_for_swarm_derivation(include_remote: bool = True) -> dict:
    local_agents = state.get_all_agents()
    agents = {
        name: {**info, "scope": "local"}
        for name, info in local_agents.items()
    }
    if include_remote:
        local_agent_ids = {
            info.get("agent_id") or info.get("uuid")
            for info in local_agents.values()
            if info.get("agent_id") or info.get("uuid")
        }
        try:
            for name, info in agent_handlers._fetch_registry_agents_for_list().items():
                agent_id = info.get("agent_id") or info.get("uuid")
                if agent_id and agent_id in local_agent_ids:
                    continue
                if info.get("tracker_id") == registry_client.TRACKER_ID:
                    continue
                agents[name] = info
        except Exception as e:
            logging.warning("failed to fetch remote agents for swarm derivation: %s", e)
    return agents


def _merge_swarm_member(swarm: dict, member: dict) -> dict:
    for existing in swarm["members"]:
        if existing.get("name") == member.get("name") and existing.get("role") == member.get("role"):
            configured = bool(existing.get("configured") or member.get("configured"))
            running = bool(existing.get("running") or member.get("running"))
            launchable = bool(existing.get("launchable") or member.get("launchable"))
            existing.update({k: v for k, v in member.items() if v is not None})
            existing["configured"] = configured
            existing["running"] = running
            existing["launchable"] = launchable
            return existing
    swarm["members"].append(member)
    return member


def _derive_swarms(include_remote: bool = True) -> list[dict]:
    swarms: dict[str, dict] = {}
    for swarm_name, members in _configured_swarm_members().items():
        swarm = swarms.setdefault(swarm_name, {"name": swarm_name, "main": None, "members": [], "warnings": []})
        for member in members:
            merged = _merge_swarm_member(swarm, member)
            if merged.get("role") == "main":
                swarm["main"] = merged

    for agent_name, info in _agents_for_swarm_derivation(include_remote).items():
        for membership in agent_handlers.normalize_swarms(info.get("swarms", [])):
            swarm_name = membership["name"]
            role = membership["role"]
            swarm = swarms.setdefault(swarm_name, {"name": swarm_name, "main": None, "members": [], "warnings": []})
            member = _merge_swarm_member(swarm, _swarm_member_row(agent_name, info, role))
            if role == "main":
                swarm["main"] = member
    for swarm in swarms.values():
        mains = [member for member in swarm["members"] if member.get("role") == "main"]
        if not mains:
            swarm["warnings"].append("no main agent configured/running")
        elif len(mains) > 1:
            swarm["warnings"].append("duplicate main agents: " + ", ".join(member.get("name", "") for member in mains))
            swarm["main"] = mains[-1]
    return [swarms[name] for name in sorted(swarms)]


def _find_swarm_or_error(swarm_name: str, include_remote: bool = True) -> dict:
    swarm_name = _validate_swarm_name(swarm_name)
    for swarm in _derive_swarms(include_remote):
        if swarm["name"] == swarm_name:
            return swarm
    raise ValueError(f"swarm {swarm_name!r} not found")


def handle_list_swarms(params: dict) -> dict:
    """Derives swarms from current local and registry-discovered agent metadata."""
    include_remote = bool(params.get("include_remote", True))
    return {"swarms": _derive_swarms(include_remote)}


def handle_assign_live_swarm(params: dict) -> dict:
    """Assigns an existing swarm to already-running local agents without restarting them."""
    swarm_name = _validate_swarm_name(params.get("swarm"))
    main = params.get("main")
    subagents = params.get("subagents") or []
    if not isinstance(main, str) or not main:
        raise ValueError("main is required")
    if not isinstance(subagents, list) or not all(isinstance(agent, str) and agent for agent in subagents):
        raise ValueError("subagents must be a list of agent names")
    if not subagents:
        raise ValueError("at least one subagent is required")
    ordered_agents = [main] + subagents
    if len(set(ordered_agents)) != len(ordered_agents):
        raise ValueError("swarm agents must be unique")

    updated = []
    for agent_name in ordered_agents:
        info = state.get_agent(agent_name)
        if not info or not info.get("tmux_pane"):
            raise ValueError(f"agent {agent_name!r} is not a running local agent")

    for agent_name in ordered_agents:
        info = state.get_agent(agent_name) or {}
        role = "main" if agent_name == main else "subagent"
        memberships = [m for m in agent_handlers.normalize_swarms(info.get("swarms", [])) if m.get("name") != swarm_name]
        memberships.append({"name": swarm_name, "role": role})
        if not state.update_agent(agent_name, swarms=memberships):
            raise ValueError(f"agent {agent_name!r} not found")
        updated.append({"agent": agent_name, "role": role})
        state.publish_event("agent_swarm_assigned", {**agent_handlers.agent_event_payload(agent_name, state.get_agent(agent_name) or {}), "swarms": memberships, "swarm": swarm_name, "role": role})
    return {"ok": True, "swarm": swarm_name, "members": updated, "swarms": _derive_swarms(False)}


def handle_get_group_timeline(params: dict) -> dict:
    """Handles get_group_timeline RPC call by reading directly from the group's cached timeline file."""
    group_id = params.get("group_id")
    last_n = params.get("last_n", 200)

    if not group_id:
        raise ValueError("group_id is required")
    if not isinstance(group_id, str):
        raise ValueError("group_id must be a string")

    if last_n is not None:
        last_n = _validate_positive_int(last_n, "last_n")

    messages = state.read_group_timeline(group_id, last_n)
    return {"messages": messages}


def _merge_timeline_messages(*message_lists: list[dict], last_n: int = 200) -> list[dict]:
    by_id = {}
    without_id = []
    for messages in message_lists:
        for message in messages:
            message_id = message.get("message_id")
            if message_id:
                by_id[message_id] = message
            else:
                without_id.append(message)
    merged = list(by_id.values()) + without_id
    merged.sort(key=lambda e: e.get("timestamp") or "")
    return merged[-last_n:]


def handle_get_swarm_timeline(params: dict) -> dict:
    swarm_name = _validate_swarm_name(params.get("swarm"))
    last_n = params.get("last_n", 200)
    if last_n is None:
        last_n = 200
    else:
        last_n = _validate_positive_int(last_n, "last_n")
    group_id = _swarm_group_id(swarm_name)
    group_messages = state.read_group_timeline(group_id, last_n)
    journal_messages = message_journal.read_swarm_timeline(swarm_name, last_n)
    try:
        registry_messages = [message_journal.registry_event_to_timeline_row(event) for event in registry_client.fetch_message_events(swarm_name, last_n)]
    except Exception as e:
        logging.warning("failed to fetch registry message-events for swarm timeline %s: %s", swarm_name, e)
        registry_messages = []
    return {"group_id": group_id, "messages": _merge_timeline_messages(group_messages, journal_messages, registry_messages, last_n=last_n)}


def handle_watch_swarm(params: dict) -> dict:
    include_remote = bool(params.get("include_remote", True))
    swarm = _find_swarm_or_error(params.get("swarm"), include_remote)
    watch_id = params.get("watch_id") or f"swarm:{swarm['name']}"
    if not isinstance(watch_id, str) or not watch_id:
        raise ValueError("watch_id must be a non-empty string")
    try:
        lease_seconds = float(params.get("lease_seconds", 30))
    except (TypeError, ValueError):
        raise ValueError("lease_seconds must be a number")
    include_body = bool(params.get("include_body", True))
    members = [member["target_address"] for member in swarm.get("members", []) if member.get("target_address")]
    group_id = _swarm_group_id(swarm["name"])
    state.update_group_watch(watch_id, group_id, members, lease_seconds, include_body)
    threading.Thread(
        target=_delegate_group_watch_to_remote_trackers,
        args=(watch_id, group_id, members, lease_seconds, include_body),
        daemon=True,
    ).start()
    return {"ok": True, "watch_id": watch_id, "group_id": group_id, "members": members}


def _delegate_group_watch_to_remote_trackers(watch_id: str, group_id: str, members: list[str], lease_seconds: float, include_body: bool) -> None:
    """Delegates group watch request to active remote trackers over the registry."""
    status, body = registry_client.fetch_trackers()
    if status != 200:
        logging.warning("Failed to fetch active trackers for group watch delegation")
        return

    trackers = body.get("trackers") or []
    remote_hosts = set()
    for m in members:
        norm = state.normalize_group_member(m)
        host = norm.get("hostname")
        if host and host != registry_client.HOSTNAME:
            remote_hosts.add(host)

    for host in remote_hosts:
        target_tid = None
        for t in trackers:
            if t.get("hostname") == host:
                target_tid = t.get("tracker_id")
                break

        if target_tid:
            try:
                registry_client.publish_tracker_event(target_tid, "watch_group_request", {
                    "watch_id": watch_id,
                    "group_id": group_id,
                    "members": members,
                    "include_body": include_body,
                    "lease_seconds": lease_seconds,
                    "reply_to_tracker_id": registry_client.TRACKER_ID
                })
                logging.info("delegated watch_group_request to remote tracker host=%s tid=%s", host, target_tid)
            except Exception as e:
                logging.warning("failed to delegate group watch to host %s: %s", host, e)


def handle_update_watchlist(params: dict) -> bool:
    """Handles update_watchlist RPC call supporting group watch mode with expiries."""
    watch_id = params.get("watch_id")
    mode = params.get("mode", "standard")
    lease_seconds = params.get("lease_seconds", 120)

    if not watch_id:
        raise ValueError("watch_id is required")
    if not isinstance(watch_id, str):
        raise ValueError("watch_id must be a string")

    try:
        lease_seconds = float(lease_seconds)
    except ValueError:
        raise ValueError("lease_seconds must be a number")

    if mode == "group":
        group_id = params.get("group_id")
        members = params.get("members", [])
        include_body = params.get("include_body", True)

        if not group_id:
            raise ValueError("group_id is required for group watch mode")
        if not isinstance(group_id, str):
            raise ValueError("group_id must be a string")
        if not isinstance(members, list):
            raise ValueError("members must be a list of strings")

        state.update_group_watch(watch_id, group_id, members, lease_seconds, include_body)
        
        # Asynchronously delegate watch requests to remote trackers
        threading.Thread(
            target=_delegate_group_watch_to_remote_trackers,
            args=(watch_id, group_id, members, lease_seconds, include_body),
            daemon=True
        ).start()
        return True
    else:
        watchlist = params.get("watchlist", [])
        state.update_watchlist_lease(watch_id, watchlist, lease_seconds)
        return True


def handle_wait_events(params: dict, caller_pid: int = None) -> dict:
    """Best-effort cursored, lease-bound event long-poll or legacy filters-based poll."""
    try:
        cursor = int(params.get("cursor", params.get("since", 0)) if params.get("cursor", params.get("since")) is not None else 0)
        timeout = float(params.get("timeout", 25.0) if params.get("timeout") is not None else 25.0)
    except (TypeError, ValueError):
        raise ValueError("cursor/since must be an integer and timeout must be a number")
    if cursor < 0 or timeout < 0:
        raise ValueError("cursor/since and timeout must be non-negative")

    client_id = params.get("client_id")
    watch_list = params.get("watch_list")
    lease_seconds = params.get("lease_seconds")
    scope = params.get("scope", "narrow")
    
    if client_id:
        if not isinstance(client_id, str):
            raise ValueError("client_id must be a string")
        if watch_list is not None and not isinstance(watch_list, list):
            raise ValueError("watch_list must be a list of strings")
        if not isinstance(scope, str) or scope not in {"narrow", "broad"}:
            raise ValueError("scope must be narrow or broad")
            
        if scope == "broad" and not REMOTE_BROAD_WATCH_ENABLED:
            raise ValueError("Broad passive remote observation is disabled on this tracker")

        if lease_seconds is not None:
            try:
                lease_seconds = float(lease_seconds)
            except ValueError:
                raise ValueError("lease_seconds must be a number")
        else:
            lease_seconds = 60.0  # Default lease to 60 seconds if not specified
            
        # Atomically register/renew client lease
        state.update_watchlist_lease(client_id, watch_list or [], lease_seconds)

        # Classify local vs remote watched targets
        remote_watchlist = [item for item in (watch_list or []) if "/" in item]
        if remote_watchlist:
            try:
                registry_client.set_remote_watch_leases(client_id, remote_watchlist, lease_seconds, scope=scope)
            except Exception as e:
                logging.warning(f"Failed to delegate remote watch lease to registry: {e}")
        else:
            try:
                registry_client.clear_remote_watch_leases(client_id)
            except Exception as e:
                logging.debug(f"Failed to clear remote watch lease on registry: {e}")

    # Enforce buffer queue eviction checks
    with state.event_lock:
        oldest_seq = state.events[0]["seq"] if state.events else state.event_sequence_id
        if cursor > 0 and cursor < oldest_seq - 1:
            raise CursorExpiredError("cursor_expired")

    # Extract backward-compatibility filters if client_id not used
    filters = None
    if not client_id:
        filters = {
            key: params[key]
            for key in ("target_agent_id", "target_agent_name")
            if params.get(key)
        }

    return state.wait_events(since=cursor, timeout=timeout, filters=filters, client_id=client_id, watch_list=watch_list)


def _read_registry_status() -> dict:
    try:
        with open(registry_client.STATUS_PATH, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _build_info() -> dict:
    version = os.environ.get("BROCCOLI_COMMS_VERSION", "0.1.0")
    revision = os.environ.get("BROCCOLI_COMMS_REVISION", "unknown")
    return {"version": version, "revision": revision, "display": f"{version}+{revision}" if revision and revision != "unknown" else version}


def handle_tracker_info(params: dict) -> dict:
    """Returns this tracker's registry identity and UI-friendly health snapshot."""
    agents = state.get_all_agents()
    online_statuses = {"running", "active", "online", "idle", "ready"}
    online_agents = sum(1 for info in agents.values() if str(info.get("status", "")).lower() in online_statuses)
    registry_status = _read_registry_status()
    registries = [{**value, "name": name} for name, value in (registry_status.get("registries") or {}).items()]
    remote_tracker_count = 0
    online_remote_tracker_count = 0
    try:
        tracker_status, tracker_body = registry_client.fetch_trackers()
        if tracker_status == 200:
            trackers = (tracker_body or {}).get("trackers") or []
            remote_tracker_count = len([t for t in trackers if t.get("tracker_id") != registry_client.TRACKER_ID])
            online_remote_tracker_count = len([t for t in trackers if t.get("tracker_id") != registry_client.TRACKER_ID and t.get("status") == "active"])
    except Exception:
        pass
    status = "ok"
    if registry_status and not registry_status.get("connected", False):
        status = "degraded"
    return {
        "hostname": registry_client.HOSTNAME,
        "tracker_id": registry_client.TRACKER_ID,
        "http_port": registry_client.HTTP_PORT,
        "status": status,
        "version": os.environ.get("BROCCOLI_COMMS_VERSION", "0.1.0"),
        "revision": os.environ.get("BROCCOLI_COMMS_REVISION", "unknown"),
        "build": _build_info(),
        "agent_count": len(agents),
        "online_agent_count": online_agents,
        "registry_connected": registry_status.get("connected"),
        "registries": registries,
        "remote_tracker_count": remote_tracker_count,
        "online_remote_tracker_count": online_remote_tracker_count,
    }


def handle_whoami(params: dict, caller_pid: int = None) -> dict:
    """Returns information about the calling agent."""
    agent_name = _identify_agent(params, caller_pid)
    if not agent_name:
        raise ValueError("Agent not identified. Run from an agent pane or process.")
        
    info = state.get_agent(agent_name)
    if not info:
        raise ValueError(f"Agent '{agent_name}' not found in state.")
        
    return {
        "name": agent_name,
        "agent_id": info.get("agent_id") or info.get("uuid"),
        "uuid": info.get("uuid"),
        "pid": info.get("pid"),
        "pane_id": info.get("tmux_pane")
    }


def handle_capture_pane(params: dict, caller_pid: int = None) -> dict:
    """Wrapper to call the extracted capture pane logic with required dependencies."""
    return pane_capture.handle_capture_pane(
        params,
        caller_pid=caller_pid,
        resolve_agent_name=_resolve_target_agent_name,
        identify_agent=_identify_agent,
        utc_now=_utc_now_isoformat
    )


def _pane_output_response(agent_id: str, *, accepted: bool, reason: str | None, tmux_pane: str, pipe_instance_id: str, seq: int | None, chunk_bytes: int) -> dict:
    info = state.get_agent(agent_id) or {}
    response = {
        "accepted": accepted,
        "dropped": not accepted,
        "agent_id": agent_id,
        "agent_name": state.get_agent_name_by_id(agent_id),
        "tmux_pane": tmux_pane,
        "pipe_instance_id": pipe_instance_id,
        "seq": seq,
        "chunk_bytes": chunk_bytes,
        "pipe_last_seq": int(info.get("pipe_last_seq") or 0),
        "pipe_chunks_accepted": int(info.get("pipe_chunks_accepted") or 0),
        "pipe_chunks_dropped": int(info.get("pipe_chunks_dropped") or 0),
    }
    if reason:
        response["drop_reason"] = reason
    return response


def _reject_pane_output_no_mutation(agent_id: str, *, reason: str, tmux_pane: str, pipe_instance_id: str, seq: int | None = None, chunk_bytes: int = 0) -> dict:
    logging.info(
        "rejected pane_output agent_id=%s pane=%s pipe_instance_id=%s seq=%s reason=%s chunk_bytes=%s",
        agent_id,
        tmux_pane,
        pipe_instance_id,
        seq,
        reason,
        chunk_bytes,
    )
    return _pane_output_response(
        agent_id,
        accepted=False,
        reason=reason,
        tmux_pane=tmux_pane,
        pipe_instance_id=pipe_instance_id,
        seq=seq,
        chunk_bytes=chunk_bytes,
    )


def _drop_pane_output(agent_id: str, info: dict, *, reason: str, tmux_pane: str, pipe_instance_id: str, seq: int | None, chunk_bytes: int) -> dict:
    now = time.time()
    state.update_agent(
        agent_id,
        pipe_chunks_dropped=int(info.get("pipe_chunks_dropped") or 0) + 1,
        pipe_last_drop_reason=reason,
        pipe_last_drop_at=now,
    )
    logging.info(
        "dropped pane_output agent_id=%s pane=%s pipe_instance_id=%s seq=%s reason=%s chunk_bytes=%s",
        agent_id,
        tmux_pane,
        pipe_instance_id,
        seq,
        reason,
        chunk_bytes,
    )
    return _pane_output_response(
        agent_id,
        accepted=False,
        reason=reason,
        tmux_pane=tmux_pane,
        pipe_instance_id=pipe_instance_id,
        seq=seq,
        chunk_bytes=chunk_bytes,
    )


def _expected_pipe_token_hash(info: dict) -> str | None:
    token_hash = info.get("pipe_token_hash") or info.get("pipe_token_sha256")
    if token_hash:
        return str(token_hash)
    raw_token = info.get("pipe_token")
    if isinstance(raw_token, str) and raw_token:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    return None


def _authorized_pane_output_or_reject(agent_id: str, tmux_pane: str, pipe_instance_id: str, pipe_token: str) -> tuple[dict | None, dict | None]:
    """Validates spoof-resistance metadata before any per-agent counter mutation."""
    info = state.get_agent(agent_id) or {}
    current_pane = info.get("tmux_pane")
    configured_pane = info.get("pipe_tmux_pane")
    expected_token_hash = _expected_pipe_token_hash(info)

    if not info.get("pipe_output_enabled", False) or not info.get("pipe_instance_id") or not configured_pane or not expected_token_hash:
        return None, _reject_pane_output_no_mutation(agent_id, reason="pipe_output_disabled", tmux_pane=tmux_pane, pipe_instance_id=pipe_instance_id)
    if tmux_pane != current_pane:
        return None, _reject_pane_output_no_mutation(agent_id, reason="pane_mismatch", tmux_pane=tmux_pane, pipe_instance_id=pipe_instance_id)
    if configured_pane != current_pane or configured_pane != tmux_pane:
        return None, _reject_pane_output_no_mutation(agent_id, reason="stale_pipe_metadata", tmux_pane=tmux_pane, pipe_instance_id=pipe_instance_id)
    if pipe_instance_id != info.get("pipe_instance_id"):
        return None, _reject_pane_output_no_mutation(agent_id, reason="stale_pipe_instance", tmux_pane=tmux_pane, pipe_instance_id=pipe_instance_id)

    provided_token_hash = hashlib.sha256(pipe_token.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(provided_token_hash, expected_token_hash):
        return None, _reject_pane_output_no_mutation(agent_id, reason="token_mismatch", tmux_pane=tmux_pane, pipe_instance_id=pipe_instance_id)
    return info, None


def _pane_output_rate_limited(info: dict, now: float) -> tuple[bool, float, int]:
    window_started = info.get("pipe_rate_window_started_at")
    try:
        window_started = float(window_started) if window_started is not None else now
    except (TypeError, ValueError):
        window_started = now
    try:
        window_chunks = int(info.get("pipe_rate_window_chunks") or 0)
    except (TypeError, ValueError):
        window_chunks = 0

    if now - window_started >= PANE_OUTPUT_RATE_WINDOW_SECONDS:
        window_started = now
        window_chunks = 0
    return window_chunks >= PANE_OUTPUT_MAX_ACCEPTED_CHUNKS_PER_WINDOW, window_started, window_chunks


def _observer_safe_output_event(agent_id: str, event: dict) -> dict:
    """Adds stable local observer routing metadata to a validated parser event."""
    agent_name = event.get("agent_name") or state.get_agent_name_by_id(agent_id)
    return {
        **event,
        "schema_version": 1,
        "target_agent_id": agent_id,
        "target_agent_name": agent_name,
    }


def _apply_pane_output_event(agent_id: str, event: dict) -> None:
    """Publishes a local normalized parser event and applies validated patches."""
    patch = event.get("state_patch") if isinstance(event.get("state_patch"), dict) else {}
    if patch:
        old_info = state.get_agent(agent_id) or {}
        old_status = old_info.get("status")
        if state.update_agent(agent_id, **patch):
            new_info = state.get_agent(agent_id) or {}
            if "status" in patch and patch.get("status") != old_status:
                try:
                    registry_client.push_agent_update(agent_id, patch["status"])
                except Exception as exc:
                    logging.debug("failed to push parser-derived status update for %s: %s", agent_id, exc)
                state.publish_event("agent_status_changed", {
                    **_agent_event_payload(event.get("agent_name") or agent_id, new_info),
                    "old_status": old_status,
                })
    local_event = state.publish_event("agent_output_event", _observer_safe_output_event(agent_id, event))
    try:
        registry_client.publish_pane_output_event(local_event)
    except Exception as exc:
        logging.debug("failed to publish registry pane-output event metadata agent_id=%s reason=%s", agent_id, type(exc).__name__)


def _process_pane_output_parser(agent_id: str, pipe_instance_id: str, chunk: str, now: float) -> int:
    agent_name = state.get_agent_name_by_id(agent_id)
    parser_key = (agent_id, pipe_instance_id)
    parser_state = PANE_OUTPUT_PARSER_STATES.get(parser_key)
    try:
        next_parser_state, events, errors = pane_output_parser.parse_chunk(
            parser_state,
            chunk,
            agent_id=agent_id,
            agent_name=agent_name,
            pipe_instance_id=pipe_instance_id,
            now=now,
        )
    except pane_output_parser.ParserValidationError as exc:
        logging.info("pane_output parser rejected chunk metadata agent_id=%s pipe_instance_id=%s reason=%s", agent_id, pipe_instance_id, pane_output_parser.safe_error_code(exc))
        return 0
    PANE_OUTPUT_PARSER_STATES[parser_key] = next_parser_state
    for error in errors:
        logging.info("pane_output parser rejected line metadata agent_id=%s pipe_instance_id=%s reason=%s", agent_id, pipe_instance_id, error)
    for event in events:
        _apply_pane_output_event(agent_id, event)
    return len(events)


def handle_pane_output(params: dict) -> dict:
    """Internal Phase-1 tmux pipe-pane ingestion path.

    This validates local pipe metadata and records only safe counters/timestamps.
    Raw chunks are never logged, published, or persisted by this handler.
    """
    if not isinstance(params, dict):
        raise ValueError("Invalid params")

    agent_id = params.get("agent_id")
    tmux_pane = params.get("tmux_pane")
    pipe_instance_id = params.get("pipe_instance_id")
    pipe_token = params.get("pipe_token")
    chunk = params.get("chunk")

    if not isinstance(agent_id, str) or not agent_id:
        raise ValueError("agent_id is required")
    if not state.get_agent_name_by_id(agent_id):
        raise ValueError("agent_id does not resolve to a local agent")
    if not isinstance(tmux_pane, str) or not tmux_pane:
        raise ValueError("tmux_pane is required")
    if not isinstance(pipe_instance_id, str) or not pipe_instance_id:
        raise ValueError("pipe_instance_id is required")
    if not isinstance(pipe_token, str) or not pipe_token:
        raise ValueError("pipe_token is required")

    info, rejection = _authorized_pane_output_or_reject(agent_id, tmux_pane, pipe_instance_id, pipe_token)
    if rejection is not None:
        return rejection
    info = info or {}

    seq = None
    try:
        seq = int(params.get("seq"))
        if seq <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return _drop_pane_output(agent_id, info, reason="invalid_sequence", tmux_pane=tmux_pane, pipe_instance_id=pipe_instance_id, seq=None, chunk_bytes=0)

    if not isinstance(chunk, str):
        return _drop_pane_output(agent_id, info, reason="invalid_chunk", tmux_pane=tmux_pane, pipe_instance_id=pipe_instance_id, seq=seq, chunk_bytes=0)
    chunk_bytes = len(chunk.encode("utf-8"))

    if chunk_bytes > PANE_OUTPUT_MAX_CHUNK_BYTES:
        return _drop_pane_output(agent_id, info, reason="chunk_too_large", tmux_pane=tmux_pane, pipe_instance_id=pipe_instance_id, seq=seq, chunk_bytes=chunk_bytes)

    last_seq = int(info.get("pipe_last_seq") or 0)
    if seq <= last_seq:
        reason = "duplicate_sequence" if seq == last_seq else "out_of_order_sequence"
        return _drop_pane_output(agent_id, info, reason=reason, tmux_pane=tmux_pane, pipe_instance_id=pipe_instance_id, seq=seq, chunk_bytes=chunk_bytes)
    if seq - last_seq > PANE_OUTPUT_MAX_SEQUENCE_GAP:
        return _drop_pane_output(agent_id, info, reason="sequence_gap_exceeded", tmux_pane=tmux_pane, pipe_instance_id=pipe_instance_id, seq=seq, chunk_bytes=chunk_bytes)

    now = time.time()
    rate_limited, window_started, window_chunks = _pane_output_rate_limited(info, now)
    if rate_limited:
        return _drop_pane_output(agent_id, info, reason="rate_limited", tmux_pane=tmux_pane, pipe_instance_id=pipe_instance_id, seq=seq, chunk_bytes=chunk_bytes)

    state.update_agent(
        agent_id,
        pipe_last_seq=seq,
        pipe_chunks_accepted=int(info.get("pipe_chunks_accepted") or 0) + 1,
        pipe_bytes_accepted=int(info.get("pipe_bytes_accepted") or 0) + chunk_bytes,
        pipe_last_chunk_at=now,
        pipe_last_timestamp=params.get("timestamp"),
        pipe_rate_window_started_at=window_started,
        pipe_rate_window_chunks=window_chunks + 1,
    )
    logging.info(
        "accepted pane_output agent_id=%s pane=%s pipe_instance_id=%s seq=%s chunk_bytes=%s",
        agent_id,
        tmux_pane,
        pipe_instance_id,
        seq,
        chunk_bytes,
    )
    _process_pane_output_parser(agent_id, pipe_instance_id, chunk, now)
    return _pane_output_response(
        agent_id,
        accepted=True,
        reason=None,
        tmux_pane=tmux_pane,
        pipe_instance_id=pipe_instance_id,
        seq=seq,
        chunk_bytes=chunk_bytes,
    )


def _reject_remote_pane_output_control_target(params: dict) -> None:
    target_address = params.get("target_address")
    if not target_address:
        return
    target = str(target_address).strip()
    if "/" in target or target.startswith("registry:") or ":" in target:
        raise ValueError("remote agents cannot be piped locally")


def _resolve_pane_output_control_target(params: dict) -> str:
    _reject_remote_pane_output_control_target(params)
    target = params.get("agent_id") or params.get("agent_name") or params.get("name")
    if not isinstance(target, str) or not target:
        raise ValueError("agent_id or agent_name is required")
    return target


def handle_enable_pane_output(params: dict) -> dict:
    if not isinstance(params, dict):
        raise ValueError("Invalid params")
    rotate = bool(params.get("rotate", True))
    return pane_output_lifecycle.enable_pane_output(_resolve_pane_output_control_target(params), rotate=rotate)


def handle_disable_pane_output(params: dict) -> dict:
    if not isinstance(params, dict):
        raise ValueError("Invalid params")
    return pane_output_lifecycle.disable_pane_output(_resolve_pane_output_control_target(params))


def handle_pane_output_status(params: dict) -> dict:
    if not isinstance(params, dict):
        raise ValueError("Invalid params")
    return pane_output_lifecycle.pane_output_status(_resolve_pane_output_control_target(params))


def handle_publish_tracker_event(params: dict) -> dict:
    target_tracker_id = params.get("target_tracker_id")
    event_type = params.get("event_type")
    payload = params.get("payload")
    if not target_tracker_id or not event_type or not payload:
        raise ValueError("target_tracker_id, event_type, and payload are required")

    status = registry_client.publish_tracker_event(target_tracker_id, event_type, payload)
    if status in (200, 202):
        return {"success": True}
    raise RuntimeError(f"Failed to publish tracker event: status {status}")


def handle_list_trackers(params: dict) -> list[dict]:
    """Fetches registered trackers and configs from the registry."""
    status, body = registry_client.fetch_trackers()
    if status == 200:
        return body.get("trackers") or []
    raise RuntimeError(f"Failed to list trackers from registry: status {status}")


def handle_memory_propose(params: dict, caller_pid: int = None) -> dict:
    return memory_handlers.handle_memory_propose(params, caller_pid, _identify_agent)

def handle_memory_propose_edit(params: dict, caller_pid: int = None) -> dict:
    return memory_handlers.handle_memory_propose_edit(params, caller_pid, _identify_agent)

def handle_memory_propose_archive(params: dict, caller_pid: int = None) -> dict:
    return memory_handlers.handle_memory_propose_archive(params, caller_pid, _identify_agent)

def handle_memory_approve(params: dict, caller_pid: int = None) -> dict:
    return memory_handlers.handle_memory_approve(params, caller_pid, _identify_agent)

def handle_memory_reject(params: dict, caller_pid: int = None) -> dict:
    return memory_handlers.handle_memory_reject(params, caller_pid, _identify_agent)

def handle_memory_show(params: dict) -> dict:
    return memory_handlers.handle_memory_show(params)

def handle_memory_history(params: dict) -> dict:
    return memory_handlers.handle_memory_history(params)

def handle_memory_list(params: dict) -> list[dict]:
    return memory_handlers.handle_memory_list(params)

def handle_memory_search(params: dict) -> list[dict]:
    return memory_handlers.handle_memory_search(params)

def handle_memory_edit(params: dict, caller_pid: int = None) -> dict:
    return memory_handlers.handle_memory_edit(params, caller_pid, _identify_agent)

def handle_memory_rollback(params: dict, caller_pid: int = None) -> dict:
    return memory_handlers.handle_memory_rollback(params, caller_pid, _identify_agent)

def handle_memory_revoke(params: dict, caller_pid: int = None) -> dict:
    return memory_handlers.handle_memory_revoke(params, caller_pid, _identify_agent)

def handle_memory_budget(params: dict) -> dict:
    return memory_handlers.handle_memory_budget(params)


dispatcher = {
    "register": handle_register,
    "ensure_mailbox": handle_ensure_mailbox,
    "list": handle_list,
    "update_agent": handle_update_agent,
    "heartbeat": handle_heartbeat,
    "rename": handle_rename,
    "spin_agent": handle_spin_agent,
    "send_message": handle_send_message,
    "send_input": handle_send_input,
    "get_inbox": handle_get_inbox,
    "get_unread_counts": handle_get_unread_counts,
    "list_swarms": handle_list_swarms,
    "request_stop": handle_request_stop,
    "restart_agent": handle_restart_agent,
    "assign_live_swarm": handle_assign_live_swarm,
    "assign_swarm": handle_assign_live_swarm,
    "get_group_timeline": handle_get_group_timeline,
    "get_swarm_timeline": handle_get_swarm_timeline,
    "watch_swarm": handle_watch_swarm,
    "update_watchlist": handle_update_watchlist,
    "wait_events": handle_wait_events,
    "tracker_info": handle_tracker_info,
    "whoami": handle_whoami,
    "unregister": handle_unregister,
    "publish_tracker_event": handle_publish_tracker_event,
    "list_trackers": handle_list_trackers,
    "capture_pane": handle_capture_pane,
    "pane_output": handle_pane_output,
    "enable_pane_output": handle_enable_pane_output,
    "disable_pane_output": handle_disable_pane_output,
    "pane_output_status": handle_pane_output_status,
    "memory.propose": handle_memory_propose,
    "memory.propose_edit": handle_memory_propose_edit,
    "memory.propose_archive": handle_memory_propose_archive,
    "memory.approve": handle_memory_approve,
    "memory.reject": handle_memory_reject,
    "memory.show": handle_memory_show,
    "memory.history": handle_memory_history,
    "memory.list": handle_memory_list,
    "memory.search": handle_memory_search,
    "memory.edit": handle_memory_edit,
    "memory.rollback": handle_memory_rollback,
    "memory.revoke": handle_memory_revoke,
    "memory.budget": handle_memory_budget,
}

def handle_client(conn: socket.socket) -> None:
    """Handles a single client connection, reading JSON-RPC request and sending response."""
    logging.info("handle_client: accepted connection")
    try:
        conn.settimeout(2.0)
        
        # Try to get peer credentials (PID)
        caller_pid = None
        try:
            # SO_PEERCRED returns (pid, uid, gid) as 3 integers
            creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i'))
            caller_pid, _, _ = struct.unpack('3i', creds)
            logging.info("handle_client: peer credentials pid=%s", caller_pid)
        except Exception as e:
            logging.debug(f"Failed to get SO_PEERCRED: {e}")
            logging.info("handle_client: peer credentials SO_PEERCRED not available")

        data = b""
        logging.info("handle_client: entering read loop")
        while True:
            chunk = conn.recv(BUFFER_SIZE)
            if not chunk:
                logging.info("handle_client: read loop EOF detected")
                break
            logging.info("handle_client: read chunk len=%d", len(chunk))
            data += chunk
            
        logging.info("handle_client: read loop finished, total bytes read=%d", len(data))
        if not data:
            logging.info("handle_client: no data read, returning")
            return
            
        try:
            req = json.loads(data.decode())
            logging.info("JSON-RPC Request: %s", _sanitize_request_for_logging(req))
        except json.JSONDecodeError as e:
            logging.error("handle_client: JSON decode error: %s", e)
            return
            
        method = req.get("method")
        params = req.get("params", {})
        req_id = req.get("id")
        
        result = None
        error = None
        
        handler = dispatcher.get(method)
        if handler:
            logging.info("handle_client: dispatching method=%s, req_id=%s, caller_pid=%s", method, req_id, caller_pid)
            try:
                # Pass caller_pid to handlers that might need it
                if method in ["get_inbox", "update_agent", "heartbeat", "send_message", "send_input", "wait_events", "whoami", "list", "rename", "unregister", "spin_agent", "capture_pane",
                              "memory.propose", "memory.propose_edit", "memory.propose_archive", "memory.approve", "memory.reject",
                              "memory.edit", "memory.rollback", "memory.revoke"]:
                    result = handler(params, caller_pid=caller_pid)
                else:
                    result = handler(params)
                logging.info("handle_client: method=%s success", method)
            except CursorExpiredError as e:
                error = {"code": -32001, "message": "cursor_expired"}
                logging.warning("handle_client: method=%s cursor expired error: %s", method, e)
            except RPCStructuredError as e:
                error = {"code": e.code, "message": str(e), "data": e.data}
                logging.warning("handle_client: method=%s RPC structured error: %s", method, e)
            except ValueError as e:
                error = {"code": -32602, "message": str(e)}
                logging.warning("handle_client: method=%s value error: %s", method, e)
            except RuntimeError as e:
                error = {"code": -32603, "message": str(e)}
                logging.error("handle_client: method=%s runtime error: %s", method, e)
            except Exception as e:
                error = {"code": -32603, "message": f"Internal error: {e}"}
                logging.error("handle_client: method=%s unexpected error: %s", method, e)
        else:
            logging.warning("handle_client: method=%s not found", method)
            error = {"code": -32601, "message": "Method not found"}
            
        resp = {"jsonrpc": "2.0", "id": req_id}
        if error:
            resp["error"] = error
        else:
            resp["result"] = result
            
        resp_data = json.dumps(resp).encode()
        logging.info("handle_client: sending response, len=%d", len(resp_data))
        conn.sendall(resp_data)
        logging.info("handle_client: response sent successfully")
    except (socket.error, socket.timeout) as e:
        logging.error(f"Socket error handling client: {e}")
    except Exception as e:
        logging.error(f"Unexpected error handling client: {e}")
    finally:
        logging.info("handle_client: closing connection socket")
        conn.close()
