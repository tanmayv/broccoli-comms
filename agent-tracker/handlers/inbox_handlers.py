import os
import fcntl
import json
import logging
from contextlib import contextmanager

@contextmanager
def _locked_inbox(inbox_file: str):
    os.makedirs(os.path.dirname(inbox_file), exist_ok=True)
    lock_path = inbox_file + ".lock"
    with open(lock_path, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)

def _atomic_write_inbox(inbox_file: str, messages: list[dict]) -> None:
    tmp = inbox_file + ".tmp"
    with open(tmp, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg) + "\n")
    os.replace(tmp, inbox_file)

def _read_and_update_inbox_file(
    inbox_file: str,
    clear: bool,
    last_n: int = None,
    agent_name: str | None = None,
    agent_info: dict | None = None,
    mark_read: bool = True,
    sender_name: str | None = None,
    sender_agent_id: str | None = None,
    sender_tracker_id: str | None = None,
    state=None,
    registry_client=None,
) -> dict:
    """Reads inbox history and optionally marks returned messages read under a file lock."""
    if not os.path.exists(inbox_file):
        return {"mode": "history", "messages": []}

    try:
        with _locked_inbox(inbox_file):
            all_messages = []
            with open(inbox_file, "r") as f:
                for line in f:
                    if line.strip():
                        try:
                            all_messages.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass

            def matches_filters(msg):
                # Task updates are system-level UI notifications, not a normal
                # peer conversation.  Include them even when the TUI requests a
                # focused sender conversation so task cards remain visible in
                # simple chat while ordinary messages stay sender-filtered.
                if _is_task_update_message(msg):
                    return True
                if sender_agent_id and msg.get("sender_agent_id") != sender_agent_id:
                    return False
                if sender_tracker_id:
                    msg_tracker_id = msg.get("sender_tracker_id")
                    if msg_tracker_id != sender_tracker_id:
                        if not (sender_tracker_id == registry_client.TRACKER_ID and not msg_tracker_id):
                            return False
                if sender_name and msg.get("sender") != sender_name:
                    return False
                return True

            candidate_messages = [m for m in all_messages if matches_filters(m)]

            mode = "unread"
            newly_read = []
            if last_n is not None:
                mode = "last_n"
                result_messages = candidate_messages[-last_n:] if last_n > 0 else []
            else:
                result_messages = [m for m in candidate_messages if not m.get("read", False)]
                if not result_messages:
                    mode = "history"
                    result_messages = candidate_messages[-5:]

            if mark_read and mode != "history":
                for msg in result_messages:
                    if not msg.get("read", False):
                        newly_read.append(msg)
                    msg["read"] = True

            if clear:
                remaining = all_messages[-25:] if len(all_messages) > 25 else all_messages
                _atomic_write_inbox(inbox_file, remaining)
            else:
                _atomic_write_inbox(inbox_file, all_messages)

        if agent_name and agent_info:
            for msg in newly_read:
                logging.info("publishing message_read target=%s sender=%s message_id=%s sender_agent_id=%s sender_tracker_id=%s", agent_name, msg.get("sender", "unknown"), msg.get("message_id"), msg.get("sender_agent_id"), msg.get("sender_tracker_id"))
                read_payload = {
                    "target_agent_id": agent_info.get("agent_id"),
                    "target_agent_name": agent_name,
                    "sender": msg.get("sender", "unknown"),
                    "message_id": msg.get("message_id"),
                }
                if msg.get("delivery_id"):
                    read_payload["delivery_id"] = msg.get("delivery_id")
                state.publish_event("message_read", read_payload)
                if msg.get("sender_tracker_id") and msg.get("sender_tracker_id") != registry_client.TRACKER_ID:
                    logging.info("relaying remote message_read back to sender_tracker_id=%s message_id=%s reader=%s", msg.get("sender_tracker_id"), msg.get("message_id"), agent_name)
                    remote_payload = {
                        "message_id": msg.get("message_id"),
                        "sender_agent_id": msg.get("sender_agent_id"),
                        "reader_agent_id": agent_info.get("agent_id"),
                        "reader_agent_name": agent_name,
                    }
                    if msg.get("delivery_id"):
                        remote_payload["delivery_id"] = msg.get("delivery_id")
                    registry_client.publish_tracker_event(msg.get("sender_tracker_id"), "message_read", remote_payload)
        return {"mode": mode, "messages": result_messages}
    except IOError as e:
        raise RuntimeError(f"Failed to access inbox file: {e}")

def _is_task_update_message(msg: dict) -> bool:
    return msg.get("content_type") == "application/vnd.broccoli.task-update+json" or msg.get("kind") in {"task_update", "task_status_changed"}


def _unread_count_key(msg: dict, registry_client=None) -> str | None:
    sender_agent_id = msg.get("sender_agent_id")
    sender_tracker_id = msg.get("sender_tracker_id")
    if sender_agent_id:
        if sender_tracker_id and registry_client and sender_tracker_id != registry_client.TRACKER_ID:
            return f"remote:{sender_tracker_id}:{sender_agent_id}"
        return f"local:{sender_agent_id}"
    sender = str(msg.get("sender") or "").strip()
    if sender:
        return f"sender:{sender}"
    return None

def handle_get_unread_counts(
    params: dict,
    caller_pid: int = None,
    identify_agent=None,
    state=None,
    registry_client=None,
    RPCError=None
) -> dict:
    """Counts unread messages in an agent inbox by stable sender conversation keys."""
    agent_name = identify_agent(params, caller_pid)
    if not agent_name:
        raise RPCError("Agent not identified. Provide agent_name or run from an agent pane.", {
            "error_code": "agent_not_identified",
            "operation": "get_unread_counts",
            "retryable": True,
        })

    info = state.get_agent(agent_name)
    if not info:
        raise RPCError(f"Agent '{agent_name}' not found", {
            "error_code": "agent_not_found",
            "agent": agent_name,
            "hostname": registry_client.HOSTNAME,
            "operation": "get_unread_counts",
            "retryable": True,
        }, code=-32004)

    uuid_str = info.get("uuid") or agent_name
    inbox_file = os.path.join(state.INBOX_DIR, f"{uuid_str}.inbox")
    counts = {}
    total = 0
    if not os.path.exists(inbox_file):
        return {"counts": counts, "total": total}
    try:
        with _locked_inbox(inbox_file):
            with open(inbox_file, "r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if msg.get("read", False) or _is_task_update_message(msg):
                        continue
                    key = _unread_count_key(msg, registry_client)
                    if not key:
                        continue
                    counts[key] = counts.get(key, 0) + 1
                    total += 1
        return {"counts": counts, "total": total}
    except IOError as e:
        raise RuntimeError(f"Failed to access inbox file: {e}")

def handle_get_inbox(
    params: dict,
    caller_pid: int = None,
    identify_agent=None,
    state=None,
    registry_client=None,
    RPCError=None
) -> dict:
    """Handles get_inbox RPC call by reading directly from the inbox file."""
    clear = params.get("clear", False)
    mark_read = params.get("mark_read", True)
    sender_name = params.get("sender_name")
    sender_agent_id = params.get("sender_agent_id")
    sender_tracker_id = params.get("sender_tracker_id")
    last_n = params.get("last_n")

    if not isinstance(mark_read, bool):
        raise ValueError("mark_read must be a boolean")
    if sender_name is not None and not isinstance(sender_name, str):
        raise ValueError("sender_name must be a string")
    if sender_agent_id is not None and not isinstance(sender_agent_id, str):
        raise ValueError("sender_agent_id must be a string")
    if sender_tracker_id is not None and not isinstance(sender_tracker_id, str):
        raise ValueError("sender_tracker_id must be a string")

    if last_n is not None:
        try:
            last_n = int(last_n)
        except ValueError:
            raise ValueError("last_n must be an integer")

    agent_name = identify_agent(params, caller_pid)
    if not agent_name:
        raise RPCError("Agent not identified. Provide agent_name or run from an agent pane.", {
            "error_code": "agent_not_identified",
            "operation": "get_inbox",
            "retryable": True,
        })

    info = state.get_agent(agent_name)
    if not info:
        raise RPCError(f"Agent '{agent_name}' not found", {
            "error_code": "agent_not_found",
            "agent": agent_name,
            "hostname": registry_client.HOSTNAME,
            "operation": "get_inbox",
            "retryable": True,
        }, code=-32004)

    uuid_str = info.get("uuid") or agent_name
    inbox_file = os.path.join(state.INBOX_DIR, f"{uuid_str}.inbox")

    return _read_and_update_inbox_file(
        inbox_file,
        clear,
        last_n,
        agent_name,
        info,
        mark_read=mark_read,
        sender_name=sender_name,
        sender_agent_id=sender_agent_id,
        sender_tracker_id=sender_tracker_id,
        state=state,
        registry_client=registry_client
    )
