import sys
import os
import json
from pathlib import Path
from contextlib import closing

# Add app/ to sys.path to allow importing learning_kernel
_workspace_root = Path(__file__).resolve().parents[2]
_app_dir = _workspace_root / "app"
if str(_app_dir) not in sys.path:
    sys.path.insert(0, str(_app_dir))

import config
from learning_kernel import LearningKernel

_kernel_instance = None

def _get_app_cache_dir() -> Path:
    configured = config.get("paths", "cache_dir")
    if configured:
        return Path(configured)
    xdg_cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return xdg_cache / "broccoli-comms"

def get_kernel() -> LearningKernel:
    global _kernel_instance
    if _kernel_instance is None:
        db_path = _get_app_cache_dir() / "learning-kernel.sqlite3"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _kernel_instance = LearningKernel(db_path)
    return _kernel_instance

def _map_exception(memory_id: str, e: Exception):
    from rpc_handler import RPCStructuredError
    msg = str(e)
    if "stale" in msg:
        raise RPCStructuredError("stale memory version", {"memory_id": memory_id}, code=-32002)
    if "transition conflict" in msg:
        raise RPCStructuredError("memory transition conflict", {"memory_id": memory_id}, code=-32003)
    if "limit exceeded" in msg:
        raise RPCStructuredError("pending memory limit exceeded", {"memory_id": memory_id}, code=-32004)

    raise e

def handle_memory_propose(params: dict, caller_pid: int = None, identify_agent = None) -> dict:
    k = get_kernel()
    caller_agent = identify_agent(params, caller_pid) if identify_agent and (caller_pid or params) else None
    proposed_by = caller_agent or "user"
    
    kwargs = {
        "type": params.get("type"),
        "title": params.get("title"),
        "body": params.get("body"),
        "scope": params.get("scope"),
        "subject_agent": params.get("subject_agent"),
        "tags": params.get("tags"),
        "metadata": params.get("metadata"),
        "source_task_id": params.get("source_task_id"),
        "trusted_manual": params.get("trusted_manual", False),
        "idempotency_key": params.get("idempotency_key"),
        "proposed_by": proposed_by,
        "proposed_by_instance": params.get("proposed_by_instance")
    }
    
    try:
        res = k.memory_propose(**kwargs)
        return {
            "memory": res["memory"],
            "event": res.get("event"),
            "idempotent": res.get("idempotent", False)
        }
    except Exception as e:
        _map_exception(params.get("memory_id", ""), e)

def handle_memory_propose_edit(params: dict, caller_pid: int = None, identify_agent = None) -> dict:
    k = get_kernel()
    caller_agent = identify_agent(params, caller_pid) if identify_agent and (caller_pid or params) else None
    proposed_by = caller_agent or "user"
    
    memory_id = params.get("memory_id")
    expected_version = params.get("expected_version")
    if not memory_id or expected_version is None:
        raise ValueError("memory_id and expected_version are required")
        
    kwargs = {
        "type": params.get("type"),
        "title": params.get("title"),
        "body": params.get("body"),
        "scope": params.get("scope"),
        "subject_agent": params.get("subject_agent"),
        "tags": params.get("tags"),
        "metadata": params.get("metadata"),
        "source_task_id": params.get("source_task_id"),
        "proposed_by": proposed_by
    }
    kwargs = {k: v for k, v in kwargs.items() if v is not None}
    
    try:
        res = k.memory_propose_edit(memory_id, expected_version=expected_version, **kwargs)
        return {
            "memory": res["memory"],
            "event": res.get("event")
        }
    except Exception as e:
        _map_exception(memory_id, e)

def handle_memory_propose_archive(params: dict, caller_pid: int = None, identify_agent = None) -> dict:
    k = get_kernel()
    caller_agent = identify_agent(params, caller_pid) if identify_agent and (caller_pid or params) else None
    proposed_by = caller_agent or "user"
    
    memory_id = params.get("memory_id")
    expected_version = params.get("expected_version")
    if not memory_id or expected_version is None:
        raise ValueError("memory_id and expected_version are required")
        
    kwargs = {
        "reason": params.get("reason"),
        "source_task_id": params.get("source_task_id"),
        "proposed_by": proposed_by
    }
    
    try:
        res = k.memory_propose_archive(memory_id, expected_version=expected_version, **kwargs)
        return {
            "memory": res["memory"],
            "event": res.get("event")
        }
    except Exception as e:
        _map_exception(memory_id, e)

def handle_memory_approve(params: dict, caller_pid: int = None, identify_agent = None) -> dict:
    k = get_kernel()
    caller_agent = identify_agent(params, caller_pid) if identify_agent and (caller_pid or params) else None
    actor = params.get("actor") or caller_agent or "user"
    
    memory_id = params.get("memory_id")
    version = params.get("version")
    if not memory_id or version is None:
        raise ValueError("memory_id and version are required")
        
    try:
        res = k.memory_approve(memory_id, version, actor=actor)
        if "limit_exceeded" in res:
            from rpc_handler import RPCStructuredError
            raise RPCStructuredError("active memory limit exceeded", res, code=-32005)
        return {
            "memory": res["memory"],
            "event": res.get("event"),
            "idempotent": res.get("idempotent", False)
        }
    except Exception as e:
        _map_exception(memory_id, e)

def handle_memory_reject(params: dict, caller_pid: int = None, identify_agent = None) -> dict:
    k = get_kernel()
    caller_agent = identify_agent(params, caller_pid) if identify_agent and (caller_pid or params) else None
    actor = params.get("actor") or caller_agent or "user"
    
    memory_id = params.get("memory_id")
    version = params.get("version")
    if not memory_id or version is None:
        raise ValueError("memory_id and version are required")
        
    reason = params.get("reason")
    
    try:
        res = k.memory_reject(memory_id, version, reason=reason, actor=actor)
        return {
            "memory": res["memory"],
            "event": res.get("event"),
            "idempotent": res.get("idempotent", False)
        }
    except Exception as e:
        _map_exception(memory_id, e)

def handle_memory_show(params: dict) -> dict:
    k = get_kernel()
    memory_id = params.get("memory_id")
    version = params.get("version")
    if not memory_id:
        raise ValueError("memory_id is required")
        
    try:
        return k.memory_show(memory_id, version=version)
    except KeyError:
        raise ValueError(f"Memory {memory_id} not found")

def handle_memory_history(params: dict) -> dict:
    k = get_kernel()
    memory_id = params.get("memory_id")
    if not memory_id:
        raise ValueError("memory_id is required")
        
    with closing(k.connect()) as conn:
        exists = conn.execute("SELECT 1 FROM memory_records WHERE memory_id=? LIMIT 1", (memory_id,)).fetchone()
        if not exists:
            raise ValueError(f"Memory {memory_id} not found")
            
        rows = conn.execute("SELECT * FROM memory_records WHERE memory_id=? ORDER BY version ASC", (memory_id,)).fetchall()
        versions = [k.row_memory(row) for row in rows]
        
    return {
        "memory_id": memory_id,
        "versions": versions
    }

def handle_memory_list(params: dict) -> list[dict]:
    k = get_kernel()
    scope = params.get("scope")
    type_filter = params.get("type")
    status = params.get("status", "active")
    agent = params.get("agent")
    
    return k.memory_list(scope=scope, type=type_filter, status=status, agent=agent)

def handle_memory_search(params: dict) -> list[dict]:
    k = get_kernel()
    query = params.get("query")
    scope = params.get("scope")
    if not query:
        raise ValueError("query is required")
        
    return k.memory_search(query, scope=scope)

def handle_memory_edit(params: dict, caller_pid: int = None, identify_agent = None) -> dict:
    k = get_kernel()
    caller_agent = identify_agent(params, caller_pid) if identify_agent and (caller_pid or params) else None
    actor = params.get("actor") or caller_agent or "user"
    
    memory_id = params.get("memory_id")
    if not memory_id:
        raise ValueError("memory_id is required")
        
    expected_version = params.get("expected_version")
    metadata = params.get("metadata")
    
    kwargs = {
        "type": params.get("type"),
        "scope": params.get("scope"),
        "subject_agent": params.get("subject_agent"),
        "title": params.get("title"),
        "description": params.get("description"),
        "body": params.get("body"),
        "source_task_id": params.get("source_task_id"),
        "trusted_manual": params.get("trusted_manual"),
        "tags": params.get("tags"),
        "metadata": metadata,
    }
    
    try:
        res = k.memory_edit(memory_id, expected_version=expected_version, actor=actor, **kwargs)
        return {
            "memory": res["memory"],
            "event": res.get("event"),
            "idempotent": res.get("idempotent", False)
        }
    except Exception as e:
        _map_exception(memory_id, e)

def handle_memory_rollback(params: dict, caller_pid: int = None, identify_agent = None) -> dict:
    k = get_kernel()
    caller_agent = identify_agent(params, caller_pid) if identify_agent and (caller_pid or params) else None
    actor = params.get("actor") or caller_agent or "user"
    
    memory_id = params.get("memory_id")
    target_version = params.get("target_version")
    if not memory_id or target_version is None:
        raise ValueError("memory_id and target_version are required")
        
    expected_version = params.get("expected_version")
    
    try:
        res = k.memory_rollback(memory_id, target_version=target_version, expected_version=expected_version, actor=actor)
        return {
            "memory": res["memory"],
            "event": res.get("event"),
            "idempotent": res.get("idempotent", False)
        }
    except Exception as e:
        _map_exception(memory_id, e)

def handle_memory_revoke(params: dict, caller_pid: int = None, identify_agent = None) -> dict:
    k = get_kernel()
    caller_agent = identify_agent(params, caller_pid) if identify_agent and (caller_pid or params) else None
    actor = params.get("actor") or caller_agent or "user"
    
    memory_id = params.get("memory_id")
    if not memory_id:
        raise ValueError("memory_id is required")
        
    reason = params.get("reason")
    expected_version = params.get("expected_version")
    
    try:
        res = k.memory_revoke(memory_id, reason=reason, expected_version=expected_version, actor=actor)
        return {
            "memory": res["memory"],
            "event": res.get("event"),
            "idempotent": res.get("idempotent", False)
        }
    except Exception as e:
        _map_exception(memory_id, e)

def handle_memory_budget(params: dict) -> dict:
    k = get_kernel()
    agent = params.get("agent") or "user"
    scope = params.get("scope")
    return k.memory_budget(agent=agent, scope=scope)
