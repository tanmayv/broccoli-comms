import base64
import binascii
import datetime
import json
import logging
import os
import socket
import uuid

import config
import message_journal
import registry_client
import state
import tmux_util
from handlers.inbox_handlers import _locked_inbox

LOCAL_HOSTNAME = config.get("tracker", "hostname", socket.gethostname())


class DeliveryTargetNotFound(ValueError):
    pass


class DeliveryValidationError(ValueError):
    pass


def _utc_now_isoformat() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _publish_message_notified(info: dict, agent_name: str, pending_item):
    sender_name = pending_item.get("sender") if isinstance(pending_item, dict) else pending_item
    message_id = pending_item.get("message_id") if isinstance(pending_item, dict) else None
    delivery_id = pending_item.get("delivery_id") if isinstance(pending_item, dict) else None
    sender_agent_id = pending_item.get("sender_agent_id") if isinstance(pending_item, dict) else None
    sender_tracker_id = pending_item.get("sender_tracker_id") if isinstance(pending_item, dict) else None
    notification = {
        "target_agent_id": info.get("agent_id"),
        "target_agent_name": agent_name,
        "sender": sender_name or "unknown",
        "message_id": message_id,
    }
    if delivery_id:
        notification["delivery_id"] = delivery_id
    state.publish_event("message_notified", notification)
    if sender_tracker_id and sender_tracker_id != registry_client.TRACKER_ID:
        remote_payload = {
            "message_id": message_id,
            "sender_agent_id": sender_agent_id,
            "receiver_agent_id": info.get("agent_id"),
            "receiver_agent_name": agent_name,
        }
        if delivery_id:
            remote_payload["delivery_id"] = delivery_id
        registry_client.publish_tracker_event(sender_tracker_id, "message_notified", remote_payload)


def _resolve_target_agent_name(params: dict) -> str | None:
    """Resolves a target agent by explicit agent_id first, then display name."""
    agent_id = params.get("agent_id")
    if agent_id:
        resolved_name = state.get_agent_name_by_id(agent_id)
        if resolved_name:
            return resolved_name

    agent_name = params.get("agent_name")
    if agent_name and state.get_agent(agent_name):
        return agent_name

    return None


def remote_message_focus_enabled() -> bool:
    """Whether remote-origin inbox delivery should best-effort focus target pane.

    Conservative Broccoli default: disabled unless explicitly enabled.
    """
    val = os.environ.get("BROCCOLI_COMMS_FOCUS_REMOTE_MESSAGES")
    if val is not None:
        return val in ("1", "true", "yes")
    return config.get("ui", "focus_remote_messages", False)


def _target_provider(info: dict) -> str:
    return state.normalize_model_type(info.get("model_type"), info.get("agent_type"), info.get("agent_cmd"))


def _provider_submit_key(info: dict) -> str:
    provider = _target_provider(info)
    provider_cfg = config.get_provider(provider)
    raw = None
    for key in ("tmux-submit-key", "tmux_submit_key", "submit-key", "submit_key"):
        if key in provider_cfg:
            raw = provider_cfg.get(key)
            break
    if raw in (None, ""):
        return "Enter"
    return tmux_util.normalize_key_token(str(raw))


def _maybe_focus_remote_delivery(info: dict, current_name: str, msg_obj: dict) -> None:
    sender_tracker_id = msg_obj.get("sender_tracker_id")
    if not sender_tracker_id or sender_tracker_id == registry_client.TRACKER_ID:
        return
    if not remote_message_focus_enabled():
        return
    tmux_pane = info.get("tmux_pane")
    tmux_socket = info.get("tmux_socket")
    if not tmux_pane or not tmux_socket:
        logging.warning("Skipping remote message focus for %s: missing registered pane or tmux socket", current_name)
        return
    try:
        tmux_util.focus_pane(tmux_pane, session=info.get("session"), socket_path=tmux_socket)
    except Exception as e:
        logging.warning("Best-effort remote message focus failed for %s pane %s: %s", current_name, tmux_pane, e)


def deliver_local_message(target_name_or_id: str, msg_obj: dict, notify_sender: str | None = None, verify: bool = False) -> str:
    """Writes a message to a local agent inbox and triggers/queues notification."""
    msg_id = msg_obj.get("message_id") or str(uuid.uuid4())
    msg_obj["message_id"] = msg_id
    delivery_id = msg_obj.get("delivery_id") or msg_id

    info = state.get_agent(target_name_or_id)
    if not info:
        raise DeliveryTargetNotFound("Target agent not found")

    current_name = state.get_agent_name_by_id(info["agent_id"]) or target_name_or_id
    uuid_str = info.get("uuid") or current_name
    inbox_file = os.path.join(state.INBOX_DIR, f"{uuid_str}.inbox")
    notify_sender = notify_sender or msg_obj.get("sender", "unknown")
    attach_dir = None

    try:
        with _locked_inbox(inbox_file):
            if msg_id and os.path.exists(inbox_file):
                with open(inbox_file, "r") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            existing = json.loads(line)
                            existing_delivery_id = existing.get("delivery_id") or existing.get("message_id")
                            if existing_delivery_id == delivery_id:
                                logging.info(f"Skipping duplicate delivery {delivery_id} for {current_name}")
                                return current_name
                        except json.JSONDecodeError:
                            continue

            attachments = []
            if msg_obj.get("attachments"):
                msg_id = msg_id or str(uuid.uuid4())
                attach_dir = os.path.join(state.INBOX_DIR, "attachments", uuid_str, msg_id)
                os.makedirs(attach_dir, exist_ok=True)
                seen_names = set()
                for att in msg_obj["attachments"]:
                    raw_name = att.get("name")
                    safe_name = os.path.basename(raw_name or "")
                    if not safe_name or "content_b64" not in att:
                        raise DeliveryValidationError("invalid attachments")
                    if safe_name in seen_names:
                        raise DeliveryValidationError("duplicate attachment name")
                    seen_names.add(safe_name)
                    try:
                        content = base64.b64decode(att["content_b64"], validate=True)
                    except (binascii.Error, ValueError) as e:
                        raise DeliveryValidationError(f"invalid attachment payload: {e}")
                    path = os.path.join(attach_dir, safe_name)
                    with open(path, "wb") as af:
                        af.write(content)
                    attachments.append({
                        "name": safe_name,
                        "path": path,
                        "content_type": att.get("content_type", "application/octet-stream"),
                        "size": os.path.getsize(path),
                    })
                msg_obj = {**msg_obj, "message_id": msg_id, "attachments": attachments}

            with open(inbox_file, "a") as f:
                f.write(json.dumps(msg_obj) + "\n")

        try:
            state.record_to_matching_group_timelines(notify_sender, current_name, msg_obj)
        except Exception as ge:
            logging.warning("Failed to record observed message to group timelines: %s", ge)

        try:
            inbound_registry_delivery = bool(msg_obj.get("sender_tracker_id") and msg_obj.get("sender_tracker_id") != registry_client.TRACKER_ID)
            journal_event = message_journal.record_local_message(
                notify_sender,
                current_name,
                msg_obj,
                sender_info=state.get_agent(notify_sender) or {},
                recipient_info=info,
                swarm_context=msg_obj.get("swarm_context") or msg_obj.get("swarm"),
                direction="inbound" if inbound_registry_delivery else "local",
                source="registry_delivery" if inbound_registry_delivery else "deliver_local_message",
            )
            if journal_event:
                try:
                    registry_client.publish_message_event(message_journal.to_registry_event(journal_event))
                except Exception as pe:
                    logging.warning("Best-effort registry message-event publish failed: %s", pe)
        except Exception as je:
            logging.warning("Failed to record observed message to durable journal: %s", je)

        notification = {
            "target_agent_id": info.get("agent_id"),
            "target_agent_name": current_name,
            "sender": notify_sender,
            "message_id": msg_obj.get("message_id"),
            "has_attachments": bool(msg_obj.get("attachments")),
            "content_type": msg_obj.get("content_type"),
            "kind": msg_obj.get("kind"),
        }
        if msg_obj.get("delivery_id"):
            notification["delivery_id"] = msg_obj.get("delivery_id")
        state.publish_event("message_delivered", notification)
        if msg_obj.get("sender_tracker_id") and msg_obj.get("sender_tracker_id") != registry_client.TRACKER_ID:
            remote_payload = {
                "message_id": msg_obj.get("message_id"),
                "sender_agent_id": msg_obj.get("sender_agent_id"),
                "receiver_agent_id": info.get("agent_id"),
                "receiver_agent_name": current_name,
            }
            if msg_obj.get("delivery_id"):
                remote_payload["delivery_id"] = msg_obj.get("delivery_id")
            registry_client.publish_tracker_event(msg_obj.get("sender_tracker_id"), "message_delivered", remote_payload)

        _maybe_focus_remote_delivery(info, current_name, msg_obj)

        pending_item = {
            "sender": notify_sender,
            "message_id": msg_obj.get("message_id"),
            "delivery_id": msg_obj.get("delivery_id"),
            "sender_agent_id": msg_obj.get("sender_agent_id"),
            "sender_tracker_id": msg_obj.get("sender_tracker_id"),
        }
        if info.get("no_notify_with_send_keys", False):
            logging.info(f"Skipping tmux send-keys notification for {current_name} from {notify_sender}")
        else:
            notify_msg = f"New message in inbox from {notify_sender}"
            enable_reliable = config.get("core", "enable_reliable_send_keys", True)
            delivered = False
            if enable_reliable or verify:
                try:
                    logging.info(f"Attempting reliable notification delivery for {current_name} to pane {info['tmux_pane']} (verify={verify})")
                    submit_key = _provider_submit_key(info)
                    kwargs = {"timeout": 5} if submit_key == "Enter" else {"timeout": 5, "submit_key": submit_key}
                    delivered = tmux_util.send_keys_reliable(info["tmux_pane"], notify_msg, info["tmux_socket"], **kwargs)
                    if delivered:
                        logging.info(f"Reliable notification successfully delivered to {current_name} in pane {info['tmux_pane']}")
                    else:
                        if verify:
                            raise RuntimeError("Notification delivery timed out")
                        logging.warning(f"Reliable notification delivery timed out/failed for {current_name} in pane {info['tmux_pane']}. Falling back to legacy send_keys.")
                except Exception as e:
                    if verify:
                        raise RuntimeError(f"Reliable notification delivery failed: {e}")
                    logging.warning(f"Error during reliable notification delivery: {e}. Falling back to legacy send_keys.")

            if not delivered:
                submit_key = _provider_submit_key(info)
                if submit_key == "Enter":
                    tmux_util.send_keys(info["tmux_pane"], notify_msg, info["tmux_socket"])
                else:
                    tmux_util.send_keys(info["tmux_pane"], notify_msg, info["tmux_socket"], submit_key=submit_key)

            _publish_message_notified(info, current_name, pending_item)
        return current_name
    except DeliveryValidationError:
        if attach_dir and os.path.isdir(attach_dir):
            for root, _, files in os.walk(attach_dir, topdown=False):
                for name in files:
                    os.remove(os.path.join(root, name))
                os.rmdir(root)
        raise
    except OSError as e:
        if attach_dir and os.path.isdir(attach_dir):
            for root, _, files in os.walk(attach_dir, topdown=False):
                for name in files:
                    os.remove(os.path.join(root, name))
                os.rmdir(root)
        logging.error(f"Failed to write to inbox file for {target_name_or_id}: {e}")
        raise RuntimeError(f"Failed to send message: {e}")


def _resolve_local_target_address(params: dict, allow_remote: bool) -> dict:
    """Resolve local target_address forms while rejecting remote direct-input targets."""
    target_address = params.get("target_address")
    if not target_address or "/" not in target_address:
        return params

    hostname, target = target_address.split("/", 1)
    if ":" in hostname:
        _, hostname = hostname.split(":", 1)
    if hostname not in {"local", LOCAL_HOSTNAME}:
        if allow_remote:
            return params
        raise RuntimeError("remote direct pane input is disabled")
    return {**params, **({"agent_id": target} if _is_uuid(target) else {"agent_name": target})}


def _validate_send_input_payload(params: dict) -> tuple[str, dict]:
    mode = (params.get("input_type") or params.get("mode") or "").lower()
    if mode not in {"text", "keys"}:
        raise ValueError("Invalid params")
    if mode == "text":
        text = params.get("text")
        if not isinstance(text, str) or text == "":
            raise ValueError("text must be a non-empty string")
        max_bytes = config.get("registry", "remote_pane_input_max_text_bytes", 4096)
        if len(text.encode("utf-8")) > max_bytes:
            raise ValueError(f"text exceeds max bytes ({max_bytes})")
        submit = params.get("submit", True)
        if not isinstance(submit, bool):
            raise ValueError("submit must be a boolean")
        return mode, {"text": text, "submit": submit}
    keys = params.get("keys")
    if keys is None and params.get("key") is not None:
        keys = [params.get("key")]
    max_keys = config.get("registry", "remote_pane_input_max_keys", 16)
    if isinstance(keys, (list, tuple)) and len(keys) > max_keys:
        raise ValueError(f"keys exceed max count ({max_keys})")
    normalized = tmux_util.normalize_key_tokens(keys)
    return mode, {"keys": normalized}


def _route_remote_send_input(params: dict, caller_pid: int = None, identify_agent=None) -> dict | None:
    target_address = params.get("target_address")
    if not target_address or "/" not in target_address:
        return None
    registry_name = None
    hostname, target = target_address.split("/", 1)
    if ":" in hostname:
        registry_name, hostname = hostname.split(":", 1)
    if hostname in {"local", LOCAL_HOSTNAME}:
        return None
    if not registry_client.remote_pane_input_send_enabled():
        raise RuntimeError("remote direct pane input is disabled")
    mode, payload = _validate_send_input_payload(params)
    sender_name = (identify_agent(_sender_identification_params(params), caller_pid) if identify_agent else None) or "cli-user"
    sender_info = state.get_agent(params.get("sender_id") or sender_name) or {}
    sender_id = sender_info.get("agent_id") or params.get("sender_id")
    pane_input_id = params.get("pane_input_id") or params.get("request_id") or str(uuid.uuid4())
    request_id = params.get("request_id") or pane_input_id
    if registry_name:
        status, body = registry_client.send_remote_pane_input_to_registry(
            registry_name,
            sender_name,
            sender_id,
            registry_client.TRACKER_ID,
            hostname,
            target,
            mode,
            text=payload.get("text"),
            keys=payload.get("keys"),
            submit=payload.get("submit", True),
            pane_input_id=pane_input_id,
            request_id=request_id,
        )
    else:
        status, body = registry_client.send_remote_pane_input(
            sender_name,
            sender_id,
            registry_client.TRACKER_ID,
            hostname,
            target,
            mode,
            text=payload.get("text"),
            keys=payload.get("keys"),
            submit=payload.get("submit", True),
            pane_input_id=pane_input_id,
            request_id=request_id,
        )
    if status == 202:
        return {"success": True, "queued": True, "mode": mode, "pane_input_id": (body or {}).get("pane_input_id", pane_input_id), "request_id": (body or {}).get("request_id", request_id)}
    raise RuntimeError(f"Remote pane input failed: {(body or {}).get('message', 'unknown error')}")


def _is_mailbox_or_ui_agent(info: dict) -> bool:
    if not info:
        return False
    return bool(info.get("direct_input_disabled")) or bool(info.get("is_mailbox")) or info.get("agent_type") == "agent-communicator-ui"


def handle_send_input(params: dict, caller_pid: int = None, identify_agent=None) -> dict:
    """Sends direct pane input to a registered agent pane.

    Local input uses the registered/private tmux socket. Remote target_address
    routing is registry-mediated and remains default-disabled behind sender,
    registry, and receiver gates.
    """
    remote_result = _route_remote_send_input(params, caller_pid, identify_agent)
    if remote_result is not None:
        return remote_result
    params = _resolve_local_target_address(params, allow_remote=False)
    agent_name = _resolve_target_agent_name(params)
    try:
        mode, input_payload = _validate_send_input_payload(params)
    except ValueError:
        raise
    if not agent_name:
        raise ValueError("Invalid params")

    info = state.get_agent(agent_name)
    if not info:
        raise ValueError("Target agent not found")
    if _is_mailbox_or_ui_agent(info):
        raise RuntimeError("Target is a Broccoli Comms UI/mailbox; direct pane input is disabled")
    tmux_pane = info.get("tmux_pane")
    tmux_socket = info.get("tmux_socket")
    if not tmux_pane:
        raise RuntimeError("Target agent has no registered tmux pane")
    if not tmux_socket:
        raise RuntimeError("Target agent has no registered tmux socket; refusing to use default tmux")

    current_name = state.get_agent_name_by_id(info.get("agent_id")) or agent_name
    try:
        if mode == "text":
            text = input_payload["text"]
            submit = input_payload["submit"]
            submit_key = _provider_submit_key(info)
            if submit_key == "Enter":
                tmux_util.send_literal_text(tmux_pane, text, submit=submit, socket_path=tmux_socket)
            else:
                tmux_util.send_literal_text(tmux_pane, text, submit=submit, socket_path=tmux_socket, submit_key=submit_key)
            return {"success": True, "target": current_name, "mode": "text", "submitted": submit}
        normalized = input_payload["keys"]
        tmux_util.send_symbolic_keys(tmux_pane, normalized, socket_path=tmux_socket)
        return {"success": True, "target": current_name, "mode": "keys", "keys": normalized}
    except ValueError:
        raise
    except Exception as e:
        raise RuntimeError(f"Failed to send direct pane input: {e}")


def _sender_identification_params(params: dict) -> dict:
    """Return only explicit sender identity fields safe for sender inference.

    send-message params may also contain target identifiers such as agent_name,
    agent_id, or target_address. Those must never be fed into _identify_agent()
    when resolving the sender.
    """
    result = {}
    if params.get("sender_id"):
        result["sender_id"] = params["sender_id"]
    if params.get("sender_name"):
        result["agent_name"] = params["sender_name"]
    return result


def _sender_metadata(sender_name: str, sender_info: dict, sender_id: str | None) -> dict:
    return {
        "sender_agent_id": sender_id,
        "sender_hostname": registry_client.HOSTNAME,
        "sender_model_type": state.normalize_model_type(sender_info.get("model_type"), sender_info.get("agent_type"), sender_info.get("agent_cmd")),
        "sender_agent_type": sender_info.get("agent_type"),
        "sender_agent_cmd": sender_info.get("agent_cmd"),
        "kind": "text",
    }


def handle_send_message(params: dict, caller_pid: int = None, identify_agent=None, deliver_message=None) -> bool:
    """Sends a message locally or routes it remotely via the registry when target_address is hostname-qualified."""
    sender_name = (identify_agent(_sender_identification_params(params), caller_pid) if identify_agent else None) or "cli-user"
    msg = params.get("message")
    attachments = params.get("attachments")
    target_address = params.get("target_address")
    sender_info = state.get_agent(params.get("sender_id") or sender_name) or {}
    sender_id = sender_info.get("agent_id") or params.get("sender_id")
    sender_metadata = _sender_metadata(sender_name, sender_info, sender_id)
    content_metadata = params.get("metadata")
    if content_metadata is not None and not isinstance(content_metadata, dict):
        raise ValueError("metadata must be an object")

    if target_address and "/" in target_address:
        registry_name = None
        hostname, target = target_address.split("/", 1)

        logging.info("handle_send_message sender=%s sender_id=%s target_address=%s message_id=%s attachments=%s", sender_name, sender_id, target_address, params.get("message_id"), bool(attachments))
        if ":" in hostname:
            registry_name, hostname = hostname.split(":", 1)
        if hostname not in {"local", LOCAL_HOSTNAME}:
            message_id = params.get("message_id") or str(uuid.uuid4())
            delivery_id = params.get("delivery_id") or (content_metadata or {}).get("delivery_id")
            remote_recipient = registry_client.find_remote_agent(hostname, target, registry_name=registry_name) or {
                "name": target,
                "hostname": hostname,
                "tracker_id": None,
                "agent_id": target if _is_uuid(target) else None,
                "swarms": [],
            }
            remote_payload = {
                "message_id": message_id,
                "delivery_id": delivery_id,
                "timestamp": _utc_now_isoformat(),
                "message": msg,
                "attachments": attachments,
                "sender_agent_id": sender_id,
                "sender_tracker_id": registry_client.TRACKER_ID,
                "recipient_agent_id": remote_recipient.get("agent_id"),
                "recipient_tracker_id": remote_recipient.get("tracker_id"),
            }
            remote_event = message_journal.build_message_event(
                sender_name,
                remote_recipient.get("name") or target,
                remote_payload,
                sender_info=sender_info,
                recipient_info=remote_recipient,
                swarm_context=params.get("swarm_context") or params.get("swarm"),
                direction="outbound",
                source="send_message",
            )
            delivery_metadata = {**sender_metadata, **(content_metadata or {})}
            if remote_event:
                delivery_metadata = {
                    **delivery_metadata,
                    "swarms": remote_event.get("swarms") or [],
                    "membership_snapshot": remote_event.get("membership_snapshot") or {},
                    "swarm_context": params.get("swarm_context") or params.get("swarm"),
                }
            if registry_name:
                status, body = registry_client.send_remote_message_to_registry(
                    registry_name, sender_name, sender_id, registry_client.TRACKER_ID, hostname, target, msg, attachments, message_id, delivery_metadata
                )
            else:
                status, body = registry_client.send_remote_message(
                    sender_name,
                    sender_id,
                    registry_client.TRACKER_ID,
                    hostname,
                    target,
                    msg,
                    attachments,
                    message_id,
                    delivery_metadata,
                )
            if status == 202:
                if remote_event:
                    try:
                        registry_client.publish_message_event(message_journal.to_registry_event(remote_event), registry_name=registry_name)
                    except Exception as pe:
                        logging.warning("Best-effort registry message-event publish failed: %s", pe)
                return True
            raise RuntimeError(f"Remote delivery failed: {(body or {}).get('message', 'unknown error')}")
        params = {**params, **({"agent_id": target} if _is_uuid(target) else {"agent_name": target})}

    agent_name = _resolve_target_agent_name(params)
    if not agent_name or (not msg and not attachments):
        raise ValueError("Invalid params")

    current_name = state.get_agent_name_by_id(state.get_agent(agent_name)["agent_id"])
    warning_msg = None
    if agent_name != current_name:
        warning_msg = f"Note: Agent '{agent_name}' was renamed to '{current_name}'."
        logging.info(warning_msg)

    message_id = params.get("message_id") or str(uuid.uuid4())
    delivery_id = params.get("delivery_id") or (content_metadata or {}).get("delivery_id")
    payload = {
        "sender": sender_name,
        "timestamp": _utc_now_isoformat(),
        "message": msg,
        "attachments": attachments,
        "read": False,
        "message_id": message_id,
        **({"delivery_id": delivery_id} if delivery_id else {}),
        **sender_metadata,
        "sender_tracker_id": registry_client.TRACKER_ID,
    }
    if content_metadata:
        payload["metadata"] = content_metadata
        for key in (
            "content_type",
            "kind",
            "approval_id",
            "task_id",
            "task_title",
            "task_status",
            "task_assigned_agent",
            "task_next_step",
            "result_summary",
            "task_chain_id",
            "root_task_id",
            "task_version_at_submission",
            "created_event_seq",
            "event_seq_at_submission",
            "source",
            "sender_source",
            "memory_id",
            "memory_type",
            "memory_title",
            "memory_scope",
            "memory_status",
            "memory_version",
            "source_task_id",
            "recipient_agent",
            "recipient_kind",
            "delivery_id",
            "target_logical_identity",
        ):
            if key in content_metadata:
                payload[key] = content_metadata[key]
    if params.get("swarm_context") is not None:
        payload["swarm_context"] = params.get("swarm_context")
    elif params.get("swarm") is not None:
        payload["swarm_context"] = params.get("swarm")
    logging.info("local delivery payload target=%s sender=%s message_id=%s sender_agent_id=%s sender_tracker_id=%s", agent_name, sender_name, payload.get("message_id"), payload.get("sender_agent_id"), payload.get("sender_tracker_id"))
    verify = params.get("verify", False)
    (deliver_message or deliver_local_message)(agent_name, payload, sender_name, verify=verify)
    return {"success": True, "warning": warning_msg} if warning_msg else True
