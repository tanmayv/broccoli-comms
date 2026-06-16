#!/usr/bin/env python3
"""Standalone launcher for the Broccoli Comms agent runtime.

This launcher owns a private agent-tracker socket and a managed tmux session.
By default the tmux session lives in the user's default tmux server; private tmux
compatibility mode is available with BROCCOLI_COMMS_TMUX_MODE=private.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import re
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
import learning_kernel as learning_kernel_module
from learning_kernel import LearningKernel, agent_contract, parse_csv, parse_duration_seconds

def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11 fallback for the test/runtime CLI.
    tomllib = None

def get_config_path() -> Path:
    xdg_config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return xdg_config / "broccoli-comms" / "config.toml"

def _parse_simple_toml(text: str) -> dict:
    data: dict = {}
    section: dict = data
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = data.setdefault(line[1:-1].strip(), {})
            continue
        if "=" not in line:
            continue
        key, value = [part.strip() for part in line.split("=", 1)]
        if "#" in value:
            value = value.split("#", 1)[0].strip()
        if value.startswith('"') and value.endswith('"'):
            parsed = value[1:-1]
        elif value.startswith("'") and value.endswith("'"):
            parsed = value[1:-1]
        elif value.lower() in {"true", "false"}:
            parsed = value.lower() == "true"
        elif value.startswith("[") and value.endswith("]"):
            parsed = []
            inner = value[1:-1].strip()
            if inner:
                for item in inner.split(","):
                    item = item.strip()
                    if (item.startswith('"') and item.endswith('"')) or (item.startswith("'") and item.endswith("'")):
                        parsed.append(item[1:-1])
                    else:
                        parsed.append(item)
        else:
            parsed = value
        section[key] = parsed
    return data


def load_toml_config() -> dict:
    path = get_config_path()
    if not path.exists():
        return {}
    try:
        if tomllib is not None:
            with open(path, "rb") as f:
                return tomllib.load(f)
        return _parse_simple_toml(path.read_text())
    except Exception:
        return {}

def get_toml_config(section: str, key: str, default=None):
    cfg = load_toml_config()
    return cfg.get(section, {}).get(key, default)


def get_toml_config_any(section: str, keys: list[str], default=None):
    cfg = load_toml_config().get(section, {})
    for key in keys:
        if key in cfg:
            return cfg[key]
    return default


APP = "broccoli-comms"
VERSION = os.environ.get("BROCCOLI_COMMS_VERSION", "0.1.0")
REVISION = os.environ.get("BROCCOLI_COMMS_REVISION", "unknown")


def build_info() -> dict:
    return {"version": VERSION, "revision": REVISION, "display": f"{VERSION}+{REVISION}" if REVISION and REVISION != "unknown" else VERSION}
SESSION = "broccoli-comms-agents"
MANAGED_AGENT_OPTION = "@broccoli_managed_agent"
SHELL_WINDOW_OPTION = "@broccoli_shell_window"
UI_WINDOW_OPTION = "@broccoli_ui_window"
UI_WINDOW_NAME = "ui"
UI_AGENT_NAME = "agent-communicator"
AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
VALID_SWARM_ROLES = {"main", "subagent"}


def xdg_runtime() -> Path:
    return Path(get_toml_config("paths", "runtime_dir", os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/{os.getuid()}/broccoli-comms"))

def xdg_cache() -> Path:
    return Path(get_toml_config("paths", "cache_dir", Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / APP))

def xdg_config() -> Path:
    return Path(get_toml_config("paths", "config_dir", Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP))


def configured_agent_root_dir() -> Path | None:
    value = get_toml_config_any("paths", ["agent-root-dir", "agent_root_dir"], None)
    if not value:
        return None
    return Path(str(value)).expanduser()

def get_active_tracker_socket() -> Path:
    candidates = []
    env_sock = os.environ.get("AGENT_TRACKER_SOCKET")
    if env_sock:
        candidates.append(Path(env_sock))
    configured_sock = get_toml_config("paths", "agent_tracker_socket", None)
    if configured_sock:
        candidates.append(Path(configured_sock))
    candidates.append(xdg_runtime() / "agent-tracker.sock")
    candidates.append(xdg_cache() / "agent-tracker" / "agent-tracker.sock")
    candidates.append(xdg_cache() / "agent-tracker.sock")
    legacy_cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    candidates.append(legacy_cache / "agent-tracker" / "agent-tracker.sock")
    
    for sock in candidates:
        if sock.exists():
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(0.1)
                s.connect(str(sock))
                s.close()
                return sock
            except OSError:
                pass
    return candidates[0]

def paths() -> dict[str, Path]:
    runtime = xdg_runtime()
    cache = xdg_cache()
    config = xdg_config()
    tracker_socket = get_active_tracker_socket()
    return {
        "runtime": runtime,
        "cache": cache,
        "config": config,
        "tmux_socket": runtime / "tmux.sock",
        "tracker_socket": tracker_socket,
        "tracker_pid": tracker_socket.with_name("agent-tracker.pid"),
        "tracker_log": cache / "agent-tracker.log",
        "registry_pid": runtime / "agent-registry.pid",
        "registry_log": cache / "agent-registry.log",
        "registry_state": cache / "agent-registry" / "state.json",
        "registry_config": config / "registry.json",
        "registries_json": config / "registries.json",
        "tmux_conf": config / "tmux.conf",
        "config_json": config / "config.json",
        "learning_db": cache / "learning-kernel.sqlite3",
    }


def learning_kernel() -> LearningKernel:
    return LearningKernel(paths()["learning_db"])


def agent_contract_template() -> str | None:
    static_sections = get_toml_config("agents_md", "static_sections", [])
    if static_sections:
        return "\n\n---\n\n".join(static_sections)
    template = get_toml_config("learning", "agent_contract_template", None)
    if template:
        return str(template)
    template_path = get_toml_config("learning", "agent_contract_template_path", None)
    if template_path:
        return Path(str(template_path)).expanduser().read_text()
    return None


def _print_payload(payload: object, as_json: bool = False) -> None:
    if as_json or isinstance(payload, (dict, list)):
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(str(payload))


def ensure_dirs() -> None:
    for key in ("runtime", "cache", "config"):
        paths()[key].mkdir(parents=True, exist_ok=True)


def tmux_mode() -> str:
    mode = get_toml_config("core", "tmux_mode", "default").lower()
    if mode not in {"default", "private"}:
        raise SystemExit("core.tmux_mode must be 'default' or 'private'")
    return mode


def use_private_tmux() -> bool:
    return tmux_mode() == "private"


def tmux_socket_label() -> str | None:
    return str(paths()["tmux_socket"]) if use_private_tmux() else None


def base_env(preserve_agent_identity: bool = False) -> dict[str, str]:
    p = paths()
    env = os.environ.copy()
    strip_keys = ["TMUX", "TMUX_PANE", "SUGGESTED_AGENT_NAME", "AGENT_SWARMS_JSON", "BROCCOLI_COMMS_CLI"]
    if not preserve_agent_identity:
        strip_keys.extend(["AGENT_ID", "AGENT_NAME", "AGENT_UUID"])
    for key in strip_keys:
        env.pop(key, None)
    env.update({
        "BROCCOLI_COMMS_APP_RUNTIME": "1",
        "XDG_CACHE_HOME": str(p["cache"]),
        "AGENT_TRACKER_SOCKET": str(p["tracker_socket"]),
    })
    # NOTE: The daemon/CLI components now natively read config.toml, so we don't strictly need to pass
    # AGENT_TRACKER_SOCKET, BROCCOLI_COMMS_CACHE_DIR, etc., unless for legacy backwards compatibility.
    if use_private_tmux():
        env["BROCCOLI_COMMS_TMUX_SOCKET"] = str(p["tmux_socket"])
        env["AGENT_TRACKER_TMUX_SOCKET"] = str(p["tmux_socket"])
    else:
        env.pop("BROCCOLI_COMMS_TMUX_SOCKET", None)
        env.pop("AGENT_TRACKER_TMUX_SOCKET", None)
    apply_configured_registries(env)
    launcher = broccoli_comms_launcher_argv()
    if len(launcher) == 1:
        env["BROCCOLI_COMMS_CLI"] = launcher[0]
    bin_dir = repo_root() / "bin"
    env["PATH"] = f"{bin_dir}:{repo_root() / 'wrapper'}:{env.get('PATH', '')}"
    return env


def can_connect(sock: Path) -> bool:
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(0.3)
        s.connect(str(sock))
        s.close()
        return True
    except OSError:
        return False


def tracker_rpc(method: str, params: dict | None = None, timeout: float = 2.0) -> object | None:
    sock_path = paths()["tracker_socket"]
    s = None
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(str(sock_path))
        s.sendall(json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}, "id": 1}).encode())
        s.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
        data = json.loads(b"".join(chunks).decode())
        if data.get("error"):
            raise RuntimeError(data["error"].get("message", "tracker RPC error"))
        return data.get("result")
    except OSError:
        return None
    finally:
        if s is not None:
            try:
                s.close()
            except Exception:
                pass


def memory_rpc(method: str, params: dict | None = None) -> object:
    """Sends a JSON-RPC request to the tracker daemon specifically for memory operations.
    
    Raises ValueError on RPC errors or connection failures so they are caught
    by the existing CLI handlers and printed cleanly.
    """
    sock_path = paths()["tracker_socket"]
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5.0)
        s.connect(str(sock_path))
        
        req = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": 1
        }
        s.sendall(json.dumps(req).encode("utf-8"))
        s.shutdown(socket.SHUT_WR)
        
        resp_bytes = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            resp_bytes += chunk
            
        resp = json.loads(resp_bytes.decode("utf-8"))
        if "error" in resp:
            err = resp["error"]
            raise ValueError(err.get("message") or "Unknown RPC error")
        return resp.get("result")
    except OSError as e:
        raise ValueError(f"Failed to connect to agent-tracker daemon at {sock_path}. Is the daemon running? (Error: {e})")


def tracker_script() -> str:
    return os.environ.get("BROCCOLI_COMMS_AGENT_TRACKER") or str(repo_root() / "agent-tracker" / "agent-tracker.py")

def tracker_ctl_script() -> str:
    return os.environ.get("BROCCOLI_COMMS_AGENT_TRACKER_CTL") or str(repo_root() / "agent-tracker" / "agent-tracker-ctl.py")

def wrapper_path() -> str:
    return os.environ.get("BROCCOLI_COMMS_AGENT_WRAPPER") or str(repo_root() / "wrapper" / "agent-wrapper.sh")

def registry_script() -> str:
    return os.environ.get("BROCCOLI_COMMS_AGENT_REGISTRY") or str(repo_root() / "agent-registry" / "server.py")

def tui_path() -> str:
    return os.environ.get("BROCCOLI_COMMS_AGENT_COMMUNICATOR_TUI") or "agent-communicator"


def ensure_tracker() -> None:
    ensure_dirs()
    p = paths()
    if can_connect(p["tracker_socket"]):
        return
    if p["tracker_socket"].exists():
        p["tracker_socket"].unlink()
    log = open(p["tracker_log"], "ab", buffering=0)
    tracker = tracker_script()
    cmd = [sys.executable, tracker] if tracker.endswith(".py") else [tracker]
    proc = subprocess.Popen(cmd, env=base_env(), stdout=log, stderr=log, start_new_session=True)
    p["tracker_pid"].write_text(str(proc.pid))
    for _ in range(50):
        if can_connect(p["tracker_socket"]):
            return
        if proc.poll() is not None:
            raise SystemExit(f"agent-tracker exited early; see {p['tracker_log']}")
        time.sleep(0.1)
    raise SystemExit(f"agent-tracker did not become ready; see {p['tracker_log']}")


def write_tmux_conf() -> None:
    p = paths()
    if p["tmux_conf"].exists():
        return
    p["tmux_conf"].write_text(
        "\n".join([
            "set -g mouse on",
            "set -g status off",
            "set -g pane-border-status bottom",
            'set -g pane-border-format "#[fg=green]#{?@agent_name,#{@agent_name},pane} #[fg=colour8]#T"',
            "set -g history-limit 10000",
            "set-window-option -g allow-rename off",
            "set-option -g set-titles off",
            "",
        ])
    )


def tmux_command(*args: str) -> list[str]:
    if use_private_tmux():
        return ["tmux", "-S", str(paths()["tmux_socket"]), *args]
    return ["tmux", *args]


def tmux(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(tmux_command(*args), env=base_env(), check=check, text=True, capture_output=True)


def in_tmux_client() -> bool:
    return bool(os.environ.get("TMUX"))


def exec_tmux_interactive(target: str) -> None:
    if in_tmux_client():
        if use_private_tmux():
            raise SystemExit(
                "Broccoli Comms is using a private tmux socket. Run this command outside tmux, or attach manually:\n"
                f"  tmux -S {paths()['tmux_socket']} attach -t {target}"
            )
        result = subprocess.run(["tmux", "switch-client", "-t", target], env=os.environ.copy(), text=True, capture_output=True)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            suffix = f": {detail}" if detail else ""
            raise SystemExit(f"failed to switch current tmux client to {target}{suffix}")
        return
    os.execvpe("tmux", tmux_command("attach", "-t", target), base_env())


def ensure_tmux() -> None:
    ensure_dirs()
    if tmux("has-session", "-t", SESSION, check=False).returncode == 0:
        return
    if use_private_tmux():
        write_tmux_conf()
        result = tmux("-f", str(paths()["tmux_conf"]), "new-session", "-d", "-P", "-F", "#{window_id}", "-s", SESSION, "-c", str(Path.home()), "bash")
    else:
        result = tmux("new-session", "-d", "-P", "-F", "#{window_id}", "-s", SESSION, "-c", str(Path.home()), "bash")
    window_id = result.stdout.strip()
    if window_id:
        tmux("set-option", "-w", "-t", window_id, SHELL_WINDOW_OPTION, "1", check=False)


def default_config() -> dict:
    return {"agents": {}}


def normalize_config(cfg: dict) -> dict:
    if not isinstance(cfg, dict):
        raise ValueError("config must be a JSON object")
    agents = cfg.get("agents") or {}
    if not isinstance(agents, dict):
        raise ValueError("config agents must be an object")
    swarms = normalize_swarm_config(cfg.get("swarms") or {})
    normalized = {**cfg, "agents": agents}
    if swarms:
        normalized["swarms"] = swarms
    for name, spec in agents.items():
        validate_agent_name(name)
        if not isinstance(spec, dict):
            raise ValueError(f"agent {name!r} config must be an object")
        if "autostart" in spec and not isinstance(spec.get("autostart"), bool):
            raise ValueError(f"agent {name!r} autostart must be a boolean")
        if "swarms" in spec or "swarm" in spec or "role" in spec:
            if "swarms" in spec:
                spec["swarms"] = normalize_swarms(spec.get("swarms"))
            elif "swarm" in spec:
                spec["swarms"] = normalize_swarms([{"name": spec.get("swarm"), "role": spec.get("role")}])
                spec.pop("swarm", None)
                spec.pop("role", None)
            else:
                raise ValueError(f"agent {name!r} role requires swarm")
    return normalized


def load_config() -> dict:
    p = paths()["config_json"]
    if not p.exists():
        save_config(default_config())
    try:
        return normalize_config(json.loads(p.read_text()))
    except json.JSONDecodeError as e:
        raise SystemExit(f"failed to parse config {p}: {e}")
    except ValueError as e:
        raise SystemExit(f"invalid config {p}: {e}")


def save_config(cfg: dict) -> None:
    ensure_dirs()
    cfg = normalize_config(cfg)
    p = paths()["config_json"]
    with tempfile.NamedTemporaryFile("w", dir=p.parent, delete=False) as f:
        json.dump(cfg, f, indent=2, sort_keys=True)
        f.write("\n")
        tmp = Path(f.name)
    tmp.replace(p)


def validate_agent_name(name: str) -> None:
    if not name or not AGENT_NAME_RE.match(name):
        raise ValueError("agent name must contain only letters, numbers, dot, underscore, and dash")


def validate_swarm_name(name: str) -> None:
    if not isinstance(name, str) or not name or not AGENT_NAME_RE.match(name):
        raise ValueError("swarm name must contain only letters, numbers, dot, underscore, and dash")


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
        validate_swarm_name(name)
        if role not in VALID_SWARM_ROLES:
            raise ValueError("swarm role must be 'main' or 'subagent'")
        normalized.append({"name": name, "role": role})
    return normalized


def normalize_swarm_config(swarms: object) -> dict[str, dict]:
    if swarms is None:
        return {}
    if not isinstance(swarms, dict):
        raise ValueError("config swarms must be an object")
    normalized = {}
    for swarm_name, spec in swarms.items():
        validate_swarm_name(swarm_name)
        if not isinstance(spec, dict):
            raise ValueError(f"swarm {swarm_name!r} config must be an object")
        members = spec.get("members") or []
        if not isinstance(members, list):
            raise ValueError(f"swarm {swarm_name!r} members must be a list")
        normalized_members = []
        for member in members:
            if not isinstance(member, dict):
                raise ValueError(f"swarm {swarm_name!r} member must be an object")
            agent = member.get("agent")
            role = member.get("role")
            validate_agent_name(agent)
            if role not in VALID_SWARM_ROLES:
                raise ValueError("swarm role must be 'main' or 'subagent'")
            normalized_members.append({"agent": agent, "role": role})
        normalized[swarm_name] = {**spec, "name": spec.get("name") or swarm_name, "members": normalized_members}
    return normalized


def parse_swarm_args(args: argparse.Namespace) -> list[dict[str, str]]:
    swarm_values = list(getattr(args, "swarm", None) or [])
    role_values = list(getattr(args, "role", None) or [])
    if role_values and not swarm_values:
        raise SystemExit("--role requires --swarm")
    if swarm_values and not role_values:
        raise SystemExit("--swarm requires --role")
    if len(swarm_values) != len(role_values):
        raise SystemExit("each --swarm requires a paired --role")
    try:
        return normalize_swarms([{"name": swarm, "role": role} for swarm, role in zip(swarm_values, role_values)])
    except ValueError as e:
        raise SystemExit(str(e))


def agent_spec(cfg: dict, name: str) -> dict:
    agents = cfg.get("agents") or {}
    if name not in agents:
        raise SystemExit(f"agent {name!r} is not configured")
    return agents[name]


def agent_autostart(spec: dict) -> bool:
    return bool(spec.get("autostart", False))


def tmux_up() -> bool:
    return tmux("has-session", "-t", SESSION, check=False).returncode == 0


def managed_windows(name: str | None = None) -> list[dict[str, str]]:
    if not tmux_up():
        return []
    fmt = f"#{{window_id}}\t#{{window_name}}\t#{{{MANAGED_AGENT_OPTION}}}\t#{{pane_id}}\t#{{pane_current_path}}"
    result = tmux("list-windows", "-t", SESSION, "-F", fmt, check=False)
    if result.returncode != 0:
        return []
    windows = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 4)
        if len(parts) != 5:
            continue
        window_id, window_name, managed_agent, pane_id, pane_current_path = parts
        if not managed_agent:
            continue
        if name is not None and managed_agent != name:
            continue
        windows.append({
            "window_id": window_id,
            "window_name": window_name,
            "managed_agent": managed_agent,
            "pane_id": pane_id,
            "cwd": pane_current_path or None,
        })
    return windows


def window_exists(name: str) -> bool:
    return bool(managed_windows(name))


def agent_window_pane(name: str) -> str | None:
    windows = managed_windows(name)
    if not windows:
        return None
    return windows[0].get("pane_id") or None


def unregister_agent_pane(pane_id: str | None) -> None:
    if pane_id and can_connect(paths()["tracker_socket"]):
        try:
            tracker_rpc("unregister", {"tmux_pane": pane_id})
        except Exception:
            pass


def kill_agent_window(name: str) -> bool:
    killed_any = False
    for window in managed_windows(name):
        window_id = window["window_id"]
        pane_id = window.get("pane_id")
        killed = tmux("kill-window", "-t", window_id, check=False).returncode == 0
        if killed:
            killed_any = True
            unregister_agent_pane(pane_id)
    return killed_any


def session_windows() -> list[dict[str, str]]:
    if not tmux_up():
        return []
    fmt = f"#{{window_id}}\t#{{window_name}}\t#{{pane_id}}\t#{{{MANAGED_AGENT_OPTION}}}\t#{{{UI_WINDOW_OPTION}}}\t#{{{SHELL_WINDOW_OPTION}}}"
    result = tmux("list-windows", "-t", SESSION, "-F", fmt, check=False)
    if result.returncode != 0:
        return []
    windows = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 5)
        if len(parts) != 6:
            continue
        window_id, window_name, pane_id, managed_agent, ui_marker, shell_marker = parts
        windows.append({
            "window_id": window_id,
            "window_name": window_name,
            "pane_id": pane_id,
            "managed_agent": managed_agent,
            "ui_marker": ui_marker,
            "shell_marker": shell_marker,
        })
    return windows


def ui_tracker_pane() -> str | None:
    if not can_connect(paths()["tracker_socket"]):
        return None
    ui_info = tracker_agents().get(UI_AGENT_NAME) or {}
    return ui_info.get("tmux_pane")


def is_broccoli_tmux_window(window: dict[str, str], ui_pane: str | None = None) -> bool:
    pane_id = window.get("pane_id")
    return (
        bool(window.get("managed_agent"))
        or window.get("ui_marker") == "1"
        or bool(ui_pane and pane_id == ui_pane and window.get("window_name") == UI_WINDOW_NAME)
        or window.get("shell_marker") == "1"
    )


def has_broccoli_tmux_windows() -> bool:
    ui_pane = ui_tracker_pane()
    return any(is_broccoli_tmux_window(window, ui_pane) for window in session_windows())


def kill_broccoli_tmux_windows() -> None:
    ui_pane = ui_tracker_pane()
    for window in session_windows():
        if not is_broccoli_tmux_window(window, ui_pane):
            continue
        pane_id = window.get("pane_id")
        if tmux("kill-window", "-t", window["window_id"], check=False).returncode == 0:
            unregister_agent_pane(pane_id)


def tmux_env_assignments_for_pane() -> list[str]:
    if not use_private_tmux():
        return []
    p = paths()
    return [
        f"AGENT_TRACKER_TMUX_SOCKET={shlex.quote(str(p['tmux_socket']))}",
        f"BROCCOLI_COMMS_TMUX_SOCKET={shlex.quote(str(p['tmux_socket']))}",
    ]


def shell_env_assignment(key: str, value: str) -> str:
    return f"{key}={shlex.quote(str(value))}"


def broccoli_comms_launcher_argv() -> list[str]:
    candidate = sys.argv[0] or ""
    if candidate:
        if os.path.isabs(candidate) or os.sep in candidate:
            path = Path(candidate).expanduser().resolve()
            if path.exists():
                if path.suffix == ".py":
                    return [sys.executable, str(path)]
                if os.access(path, os.X_OK):
                    return [str(path)]
                return [sys.executable, str(path)]
        resolved = shutil.which(candidate, path=f"{repo_root() / 'bin'}:{os.environ.get('PATH', '')}")
        if resolved:
            return [resolved]
    # Prefer the current Python app over the checked-in bin/ compatibility
    # wrapper. During local development bin/broccoli-comms can lag behind the
    # app and miss newer subcommands such as task/bootstrap, which makes
    # nested `broccoli-comms run` bootstrap panes exit immediately.
    current_script = Path(__file__).resolve()
    if current_script.exists():
        return [sys.executable, str(current_script)]
    local_launcher = repo_root() / "bin" / "broccoli-comms"
    if local_launcher.exists():
        if os.access(local_launcher, os.X_OK):
            return [str(local_launcher)]
        return [sys.executable, str(local_launcher)]
    return [sys.executable, str(current_script)]


def managed_track_env_assignments() -> list[str]:
    env = base_env()
    if use_private_tmux():
        env["BROCCOLI_COMMS_TMUX_MODE"] = "private"
    elif "BROCCOLI_COMMS_TMUX_MODE" in os.environ:
        env["BROCCOLI_COMMS_TMUX_MODE"] = tmux_mode()
    # `broccoli-comms run` is often invoked from inside an already tracked
    # agent pane.  The new tmux pane must start as a fresh wrapper context;
    # otherwise agent-wrapper sees BROCCOLI_COMMS_TRACK_ACTIVE=1 or
    # AGENT_WRAPPER_DEPTH=1 and bypasses registration entirely.
    env.update({
        "BROCCOLI_COMMS_TRACK_ACTIVE": "",
        "AGENT_WRAPPER_DEPTH": "0",
        "AGENT_NAME": "",
        "AGENT_ID": "",
        "AGENT_UUID": "",
    })
    keys = [
        "PATH",
        "BROCCOLI_COMMS_APP_RUNTIME",
        "BROCCOLI_COMMS_RUNTIME_DIR",
        "BROCCOLI_COMMS_CACHE_DIR",
        "BROCCOLI_COMMS_CONFIG_DIR",
        "BROCCOLI_COMMS_TMUX_MODE",
        "BROCCOLI_COMMS_AGENT_TRACKER",
        "BROCCOLI_COMMS_AGENT_TRACKER_CTL",
        "BROCCOLI_COMMS_AGENT_WRAPPER",
        "BROCCOLI_COMMS_AGENT_REGISTRY",
        "BROCCOLI_COMMS_AGENT_COMMUNICATOR_TUI",
        "AGENT_TRACKER_SOCKET",
        "XDG_CACHE_HOME",
        "AGENT_TRACKER_HOSTNAME",
        "AGENT_TRACKER_HTTP_PORT",
        "AGENT_REGISTRIES_JSON",
        "AGENT_REGISTRY_TOKEN",
        "AGENT_REGISTRY_AUTH",
        "AGENT_REGISTRY_HEARTBEAT_SECONDS",
        "BROCCOLI_COMMS_REMOTE_PANE_INPUT_ENABLED",
        "BROCCOLI_COMMS_REMOTE_PANE_INPUT_SEND_ENABLED",
        "BROCCOLI_COMMS_REMOTE_PANE_INPUT_RECEIVE_ENABLED",
        "BROCCOLI_COMMS_REMOTE_PANE_INPUT_REGISTRY_ENABLED",
        "BROCCOLI_COMMS_TRACK_ACTIVE",
        "AGENT_WRAPPER_DEPTH",
        "AGENT_NAME",
        "AGENT_ID",
        "AGENT_UUID",
    ]
    return [shell_env_assignment(key, env[key]) for key in keys if key in env and (env.get(key) or key in {"BROCCOLI_COMMS_TRACK_ACTIVE", "AGENT_WRAPPER_DEPTH", "AGENT_NAME", "AGENT_ID", "AGENT_UUID"})]


def _provider_context_layout(provider_alias: str | None, provider_cfg: dict | None) -> str:
    cfg = provider_cfg if isinstance(provider_cfg, dict) else {}
    explicit = cfg.get("context-layout", cfg.get("contextLayout", cfg.get("context_layout")))
    if explicit:
        return str(explicit)
    if provider_alias == "jetski":
        return "jetski"
    return "legacy"


def _provider_launch_settings(command: list[str], *, apply_command_overrides: bool = True) -> tuple[list[str], str | None, str | None, str]:
    agents_dir = None
    provider_agent_root_dir = None
    context_layout = "legacy"
    if not command:
        return command, agents_dir, provider_agent_root_dir, context_layout
    provider_alias = command[0]
    provider_cfg = get_toml_config("providers", provider_alias, {})
    context_layout = _provider_context_layout(provider_alias, provider_cfg)
    if provider_cfg:
        if apply_command_overrides:
            cmd_override = provider_cfg.get("cmd", provider_alias)
            default_args = _provider_arg_list(provider_cfg.get("defaultArgs", []))
            auto_accept_args = _provider_arg_list(provider_cfg.get("auto-accept-flag", provider_cfg.get("autoAcceptFlag", provider_cfg.get("auto_accept_flag", ""))))
            initial_message_args = _provider_initial_message_args(provider_cfg)
            command = [cmd_override] + default_args + auto_accept_args + initial_message_args + command[1:]
        if "agentsDir" in provider_cfg:
            agents_dir = provider_cfg["agentsDir"]
        provider_agent_root_dir = provider_cfg.get("agent-root-dir", provider_cfg.get("agentRootDir", provider_cfg.get("agent_root_dir")))
    if provider_alias == "jetski" and not provider_agent_root_dir:
        provider_agent_root_dir = str(Path.home() / "agents-root")
    return command, agents_dir, provider_agent_root_dir, context_layout


def ephemeral_agent_workspace(name: str, agents_dir: str | None = None, source_cwd: str | Path | None = None, agent_root_dir: str | Path | None = None, context_layout: str = "legacy") -> str:
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "-", name).strip("-._") or "agent"
    agent_root = Path(str(agent_root_dir)).expanduser() if agent_root_dir else configured_agent_root_dir()
    if agent_root is not None:
        tmp_root = agent_root / safe_name
        exist_ok = True
    else:
        base = Path(tempfile.gettempdir()) / "broccoli-agents" / safe_name
        tmp_root = base / uuid.uuid4().hex[:12]
        exist_ok = False
    workspace = tmp_root if context_layout == "jetski" else (tmp_root / agents_dir if agents_dir else tmp_root)
    workspace.mkdir(parents=True, exist_ok=exist_ok)
    (workspace / "AGENTS.md").write_text(agent_contract(name, f"{name}@pending", workspace, agent_contract_template(), source_cwd=source_cwd))
    return str(tmp_root)


def _parse_command_for_bootstrap(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError as e:
        raise SystemExit(f"invalid command: {e}")


def _is_agent_wrapper_command(command: str) -> bool:
    wrapper = wrapper_path()
    if not command:
        return False
    if os.path.basename(command) == "agent-wrapper":
        return True
    try:
        return os.path.isfile(command) and os.path.samefile(command, wrapper)
    except OSError:
        return False


def managed_agent_launch_command(
    name: str,
    cwd: str,
    command: str,
    swarms: list[dict[str, str]] | None = None,
    launch_cwd: str | None = None,
    scope: str | None = None,
    immutable: bool = False,
    agents_dir: str | None = None,
    context_layout: str = "legacy",
) -> str:
    launch_cwd = launch_cwd or cwd
    command_args = _parse_command_for_bootstrap(command)
    actual_agent_cmd = os.path.basename(command_args[0]) if command_args else "agent"
    if "_broccoli_agent_bootstrap" in command_args:
        marker_index = command_args.index("_broccoli_agent_bootstrap")
        if marker_index + 1 < len(command_args):
            actual_agent_cmd = os.path.basename(command_args[marker_index + 1])
    normalized_swarms = normalize_swarms(swarms or [])
    if scope:
        bootstrap_context = str(Path(launch_cwd))
        command_args = _build_bootstrap_track_command(name, cwd, scope, command_args, bootstrap_context)
    launcher = [wrapper_path()]
    if command_args and _is_agent_wrapper_command(command_args[0]):
        launcher = []
    launch_parts: list[str] = [
        *managed_track_env_assignments(),
        f"BROCCOLI_COMMS_SOURCE_CWD={shlex.quote(str(cwd))}",
        f"BROCCOLI_COMMS_EPHEMERAL_CWD={shlex.quote(str(launch_cwd))}",
        f"SUGGESTED_AGENT_NAME={shlex.quote(name)}",
        f"AGENT_TYPE={shlex.quote(actual_agent_cmd)}",
        f"AGENT_CMD={shlex.quote(actual_agent_cmd)}",
        f"AGENT_MODEL_TYPE={shlex.quote(actual_agent_cmd)}",
    ]
    if agents_dir:
        launch_parts.append(f"BROCCOLI_AGENTS_DIR={shlex.quote(agents_dir)}")
    if context_layout and context_layout != "legacy":
        launch_parts.append(f"BROCCOLI_COMMS_CONTEXT_LAYOUT={shlex.quote(context_layout)}")
    if immutable:
        launch_parts.append("BROCCOLI_COMMS_IMMUTABLE_INSTANCE=1")
        launch_parts.append("BROCCOLI_COMMS_NON_LEARNING=1")
    if normalized_swarms:
        launch_parts.append(f"AGENT_SWARMS_JSON={shlex.quote(json.dumps(normalized_swarms, separators=(',', ':')))}")
    launch_parts.extend(shlex.quote(part) for part in launcher)
    launch_parts.extend(shlex.quote(part) for part in command_args)
    return " ".join(launch_parts)


def _build_bootstrap_track_command(name: str, source_cwd: str | None, scope: str | None, command: list[str], bootstrap_context: str) -> list[str]:
    bootstrap_invocation = [*broccoli_comms_launcher_argv(), "task", "bootstrap", "--agent", name, "--write-context-dir", bootstrap_context]
    if source_cwd:
        bootstrap_invocation.extend(["--cwd", source_cwd])
    if scope:
        bootstrap_invocation.extend(["--scope", scope])
    bootstrap_script = " ".join(shlex.quote(part) for part in bootstrap_invocation)
    script = (
        "set -euo pipefail; "
        f"{bootstrap_script} >/dev/null; "
        "exec \"$@\""
    )
    return ["bash", "-lc", script, "_broccoli_agent_bootstrap", *command]


def reconcile_agents(names: set[str] | None = None, *, autostart_only: bool = False) -> list[str]:
    cfg = load_config()
    agents = cfg.get("agents") or {}
    launched = []
    for name, spec in agents.items():
        if names is not None and name not in names:
            continue
        if autostart_only and not agent_autostart(spec):
            continue
        if window_exists(name):
            continue
        cwd = os.path.abspath(os.path.expanduser(spec.get("cwd") or str(Path.home())))
        if not os.path.isdir(cwd):
            raise SystemExit(f"configured cwd for agent {name!r} does not exist: {cwd}")
        command = spec.get("command") or "bash"
        command_args, agents_dir, provider_agent_root_dir, context_layout = _provider_launch_settings(_parse_command_for_bootstrap(str(command)), apply_command_overrides=False)
        launch_command = shlex.join(command_args) if command_args else str(command)
        launch_cwd = ephemeral_agent_workspace(name, agents_dir=agents_dir, source_cwd=cwd, agent_root_dir=provider_agent_root_dir, context_layout=context_layout)
        launch = managed_agent_launch_command(
            name,
            cwd,
            launch_command,
            spec.get("swarms") or [],
            launch_cwd=launch_cwd,
            scope=spec.get("scope"),
            immutable=bool(spec.get("immutable") or spec.get("non_learning")),
            agents_dir=agents_dir,
            context_layout=context_layout,
        )
        result = tmux("new-window", "-d", "-P", "-F", "#{window_id}", "-t", SESSION, "-n", name, "-c", launch_cwd, launch)
        window_id = result.stdout.strip()
        if window_id:
            tmux("set-option", "-w", "-t", window_id, MANAGED_AGENT_OPTION, name)
        launched.append(name)
    return launched


def start(_args: argparse.Namespace) -> None:
    ensure_tracker()
    ensure_tmux()
    reconcile_agents(autostart_only=True)
    print(f"{APP} runtime started")
    print(f"tracker socket: {paths()['tracker_socket']}")
    print(f"tmux mode:      {tmux_mode()}")
    print(f"tmux socket:    {tmux_socket_label() or 'default'}")


def ui_window() -> dict[str, str] | None:
    if not tmux_up():
        return None
    fmt = f"#{{window_id}}\t#{{window_name}}\t#{{pane_id}}\t#{{{UI_WINDOW_OPTION}}}"
    result = tmux("list-windows", "-t", SESSION, "-F", fmt, check=False)
    if result.returncode != 0:
        return None
    named_candidates = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 3)
        if len(parts) != 4:
            continue
        window_id, window_name, pane_id, ui_marker = parts
        window = {"window_id": window_id, "window_name": window_name, "pane_id": pane_id}
        if ui_marker == "1":
            return window
        if window_name == UI_WINDOW_NAME:
            named_candidates.append(window)
    for window in named_candidates:
        if ui_window_registered(window):
            return window
    return None


def ui_window_registered(window: dict[str, str] | None) -> bool:
    if not window:
        return False
    info = tracker_agents().get(UI_AGENT_NAME) or {}
    if info.get("tmux_pane") != window.get("pane_id"):
        return False
    if use_private_tmux():
        return info.get("tmux_socket") == str(paths()["tmux_socket"])
    return True


def ensure_ui_mailbox() -> dict:
    result = tracker_rpc("ensure_mailbox", {"agent_name": UI_AGENT_NAME, "preserve_pane": True})
    return result if isinstance(result, dict) else {}


def ui_launch_command() -> str:
    p = paths()
    mailbox = ensure_ui_mailbox()
    ui_agent_id = mailbox.get("agent_id") or mailbox.get("uuid")
    assignments = []
    if ui_agent_id:
        assignments.append(f"AGENT_ID={shlex.quote(str(ui_agent_id))}")
    return " ".join([
        *assignments,
        f"SUGGESTED_AGENT_NAME={shlex.quote(UI_AGENT_NAME)}",
        f"AGENT_TRACKER_SOCKET={shlex.quote(str(p['tracker_socket']))}",
        f"BROCCOLI_COMMS_APP_RUNTIME=1",
        f"BROCCOLI_COMMS_RUNTIME_DIR={shlex.quote(str(p['runtime']))}",
        *tmux_env_assignments_for_pane(),
        shlex.quote(wrapper_path()),
        shlex.quote(tui_path()),
        "--no-notify-with-send-keys",
    ])


def ensure_ui_window() -> dict[str, str]:
    window = ui_window()
    if window and ui_window_registered(window):
        return window
    if window:
        tmux("kill-window", "-t", window["window_id"], check=False)
        unregister_agent_pane(window.get("pane_id"))
    result = tmux(
        "new-window",
        "-d",
        "-P",
        "-F",
        "#{window_id}\t#{pane_id}",
        "-t",
        SESSION,
        "-n",
        UI_WINDOW_NAME,
        "-c",
        str(Path.cwd()),
        ui_launch_command(),
    )
    window_id, pane_id = result.stdout.strip().split("\t", 1)
    if window_id:
        tmux("set-option", "-w", "-t", window_id, UI_WINDOW_OPTION, "1", check=False)
    return {"window_id": window_id, "window_name": UI_WINDOW_NAME, "pane_id": pane_id}


def ui_env_for_current_shell() -> dict[str, str]:
    env = base_env()
    for key in ("TMUX", "TMUX_PANE"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def ui(_args: argparse.Namespace) -> None:
    if not can_connect(paths()["tracker_socket"]):
        raise SystemExit(f"broccoli-comms ui requires a running tracker at {paths()['tracker_socket']}. Run `broccoli-comms start` first.")
    tui = tui_path()
    os.execvpe(tui, [tui], ui_env_for_current_shell())


def attach(_args: argparse.Namespace) -> None:
    ensure_tracker()
    ensure_tmux()
    exec_tmux_interactive(SESSION)


def tracker_agents() -> dict:
    if not can_connect(paths()["tracker_socket"]):
        return {}
    try:
        agents = tracker_rpc("list", {})
        return agents if isinstance(agents, dict) else {}
    except Exception:
        return {}


def _tracker_agents_with_remote(include_remote: bool = False) -> dict:
    if not can_connect(paths()["tracker_socket"]):
        return {}
    try:
        agents = tracker_rpc("list", {"include_remote": bool(include_remote)})
        return agents if isinstance(agents, dict) else {}
    except Exception:
        return {}


def _configured_agent_view(name: str, spec: dict) -> dict:
    return {
        "cwd": spec.get("cwd"),
        "command": spec.get("command"),
        "scope": spec.get("scope"),
        "autostart": agent_autostart(spec),
        "swarms": spec.get("swarms", []),
        "immutable": bool(spec.get("immutable") or spec.get("non_learning")),
        "non_learning": bool(spec.get("non_learning") or spec.get("immutable")),
    }


def _remote_registry_agents() -> dict:
    """Best-effort remote registry listing without starting a local launch path."""
    remote: dict[str, dict] = {}
    for entry in load_registry_urls_config().get("registries", []):
        if not entry.get("enabled", True):
            continue
        token = _read_token_file(entry.get("token-file")) if entry.get("token-file") else None
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        url = entry["url"].rstrip("/") + "/agents"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                body = json.loads(resp.read().decode() or "{}")
        except Exception:
            continue
        registry_name = entry.get("name") or "default"
        for agent in (body or {}).get("agents") or []:
            hostname = agent.get("hostname")
            agent_name = agent.get("name")
            if not hostname or not agent_name:
                continue
            base_key = f"{hostname}/{agent_name}"
            key = base_key if base_key not in remote else f"{registry_name}:{base_key}"
            remote[key] = {
                **agent,
                "name": key,
                "scope": "remote",
                "target_address": key,
                "registry_name": registry_name,
            }
    return remote


ACTIVE_DURABLE_STATE_STATUSES = {"working", "blocked", "waiting", "review"}
INACTIVE_DURABLE_TASK_STATUSES = {"done", "validated", "archived"}


def _durable_current_tasks_by_agent() -> dict[str, dict]:
    """Best-effort active task metadata keyed by durable agent/profile names."""
    try:
        kernel = learning_kernel()
        states = kernel.tasks.state_list()
    except Exception:
        return {}
    task_cache: dict[str, dict | None] = {}
    current_by_agent: dict[str, dict] = {}
    for state_row in states:
        if state_row.get("status") not in ACTIVE_DURABLE_STATE_STATUSES:
            continue
        task_id = state_row.get("task_id")
        if not task_id:
            continue
        if task_id not in task_cache:
            try:
                task_cache[task_id] = kernel.tasks.show(task_id)
            except Exception:
                task_cache[task_id] = None
        task = task_cache.get(task_id) or {}
        if task.get("status") in INACTIVE_DURABLE_TASK_STATUSES:
            continue
        current_task = task.get("title") or state_row.get("current_activity") or task_id
        metadata = {
            "current_task": current_task,
            "current_task_id": task_id,
            "current_task_status": state_row.get("status") or task.get("status") or "",
            "current_task_next_step": state_row.get("next_step") or task.get("next_step") or "",
        }
        for key in (state_row.get("agent"), state_row.get("instance_id")):
            if key and key not in current_by_agent:
                current_by_agent[key] = metadata
    return current_by_agent


def _durable_current_task_for_row(name: str, durable_tasks: dict[str, dict]) -> dict:
    candidates = [name]
    if "@" in name:
        candidates.append(name.split("@", 1)[0])
    for candidate in candidates:
        if candidate in durable_tasks:
            return durable_tasks[candidate]
    return {}


def _tracker_current_task_fields(tracker: dict | None) -> dict:
    fields = {key: "" for key in ("current_task", "current_task_id", "current_task_status", "current_task_next_step")}
    if not isinstance(tracker, dict):
        return fields
    for key in fields:
        if key in tracker:
            fields[key] = tracker.get(key) or ""
    return fields


def merged_agent_rows(*, include_remote: bool = False) -> dict[str, dict]:
    cfg = load_config()
    configured_agents = cfg.get("agents") or {}
    windows_by_name: dict[str, list[dict[str, str]]] = {}
    for window in managed_windows():
        windows_by_name.setdefault(window["managed_agent"], []).append(window)
    tracker_by_name = _tracker_agents_with_remote(include_remote=include_remote)
    if include_remote:
        for name, info in _remote_registry_agents().items():
            tracker_by_name.setdefault(name, info)
    durable_current_tasks = _durable_current_tasks_by_agent()

    names = set(configured_agents) | set(windows_by_name) | set(tracker_by_name)
    rows: dict[str, dict] = {}
    for name in sorted(names):
        spec = configured_agents.get(name)
        tracker = tracker_by_name.get(name) if isinstance(tracker_by_name.get(name), dict) else None
        remote = bool((tracker or {}).get("scope") == "remote")
        configured_view = _configured_agent_view(name, spec) if spec is not None else None
        durable_current_task = _durable_current_task_for_row(name, durable_current_tasks) if not remote else {}
        current_task_fields = {**_tracker_current_task_fields(tracker), **durable_current_task}
        row = {
            "name": name,
            "configured": configured_view,
            "is_configured": spec is not None,
            "remote": remote,
            "scope_kind": "remote" if remote else "local",
            "source": "+".join(part for part, present in (("configured", spec is not None), ("running", bool(windows_by_name.get(name)) or bool(tracker and not remote)), ("remote", remote)) if present) or "unknown",
            "running": bool(windows_by_name.get(name)) or bool(tracker and not remote),
            "window_exists": bool(windows_by_name.get(name)),
            "managed_windows": windows_by_name.get(name, []),
            "tracker": tracker,
            "target_address": (tracker or {}).get("target_address") or name,
            "hostname": (tracker or {}).get("hostname"),
            "agent_id": (tracker or {}).get("agent_id") or (tracker or {}).get("uuid"),
            "tracker_id": (tracker or {}).get("tracker_id"),
            "registry_name": (tracker or {}).get("registry_name"),
            "status": (tracker or {}).get("status") or ("configured" if spec is not None else "unknown"),
            "launchable": bool(spec and spec.get("command")),
            "copyable": bool(spec and spec.get("command")) or bool((tracker or {}).get("command") or (tracker or {}).get("agent_command") or (tracker or {}).get("agent_cmd")),
            **current_task_fields,
        }
        if spec is not None:
            row.update({
                # Backward-compatible direct fields for simple JSON consumers.
                "cwd": spec.get("cwd"),
                "command": spec.get("command"),
                "scope": spec.get("scope"),
                "autostart": agent_autostart(spec),
                "swarms": spec.get("swarms", []),
                "immutable": bool(spec.get("immutable") or spec.get("non_learning")),
                "non_learning": bool(spec.get("non_learning") or spec.get("immutable")),
            })
        elif tracker:
            row.update({
                "cwd": tracker.get("cwd"),
                "command": tracker.get("command") or tracker.get("agent_command"),
                "scope": tracker.get("scope"),
                "swarms": tracker.get("swarms", []),
                "agent_cmd": tracker.get("agent_cmd"),
                "agent_type": tracker.get("agent_type"),
                "model_type": tracker.get("model_type"),
            })
        rows[name] = row
    return rows


def filtered_agent_rows(args: argparse.Namespace) -> dict[str, dict]:
    include_remote = bool(getattr(args, "include_remote", False) or getattr(args, "remote_only", False))
    rows = merged_agent_rows(include_remote=include_remote)
    if getattr(args, "configured_only", False):
        rows = {name: row for name, row in rows.items() if row.get("is_configured")}
    if getattr(args, "running_only", False):
        rows = {name: row for name, row in rows.items() if row.get("running")}
    if getattr(args, "remote_only", False):
        rows = {name: row for name, row in rows.items() if row.get("remote")}
    return rows


def agent_list_payload(args: argparse.Namespace | None = None) -> dict:
    args = args or argparse.Namespace(include_remote=False, configured_only=False, running_only=False, remote_only=False)
    runtime_up = tmux_up()
    tracker_up = can_connect(paths()["tracker_socket"])
    return {
        "app": APP,
        "version": VERSION,
        "build": build_info(),
        "config": str(paths()["config_json"]),
        "runtime": {
            "tracker_up": tracker_up,
            "tmux_up": runtime_up,
            "tmux_session": SESSION,
            "tmux_mode": tmux_mode(),
            "tmux_socket": tmux_socket_label(),
        },
        "agents": filtered_agent_rows(args),
    }


def status_payload() -> dict:
    p = paths()
    cfg = load_config()
    configured_agents = cfg.get("agents") or {}
    tracker_up = can_connect(p["tracker_socket"])
    session_up = tmux_up()
    managed = managed_windows() if session_up else []
    return {
        "app": APP,
        "version": VERSION,
        "build": build_info(),
        "paths": {
            "runtime_dir": str(p["runtime"]),
            "cache_dir": str(p["cache"]),
            "config_dir": str(p["config"]),
        },
        "tracker": {
            "socket": str(p["tracker_socket"]),
            "up": tracker_up,
        },
        "tmux": {
            "mode": tmux_mode(),
            "socket": tmux_socket_label(),
            "up": session_up,
            "session": SESSION,
        },
        "config": {
            "path": str(p["config_json"]),
        },
        "agents": {
            "configured_count": len(configured_agents),
            "autostart_count": sum(1 for spec in configured_agents.values() if agent_autostart(spec)),
            "managed_running_count": len(managed),
            "managed_windows": managed,
        },
        # Backward-compatible aliases used by existing smoke checks.
        "tracker_socket": str(p["tracker_socket"]),
        "tracker_up": tracker_up,
        "tmux_socket": tmux_socket_label(),
        "tmux_up": session_up,
    }


def status(args: argparse.Namespace) -> None:
    print(json.dumps(status_payload(), indent=2, sort_keys=bool(getattr(args, "json", False))))


def agent_list(args: argparse.Namespace) -> None:
    payload = agent_list_payload(args)
    print(json.dumps(payload if args.json else payload["agents"], indent=2, sort_keys=True))


def _find_agent_row(name: str, *, include_remote: bool = True) -> tuple[str, dict]:
    rows = merged_agent_rows(include_remote=include_remote)
    if name in rows:
        return name, rows[name]
    for key, row in rows.items():
        aliases = {str(row.get("target_address") or ""), str((row.get("tracker") or {}).get("name") or "")}
        if name in aliases:
            return key, row
    raise SystemExit(f"agent {name!r} not found")


def agent_status(args: argparse.Namespace) -> None:
    key, row = _find_agent_row(args.name, include_remote=True)
    payload = {"name": key, "agent": row}
    print(json.dumps(payload if args.json else row, indent=2, sort_keys=True))


def _row_copy_spec(source_name: str, row: dict, *, immutable: bool) -> dict:
    configured = row.get("configured") if isinstance(row.get("configured"), dict) else {}
    tracker = row.get("tracker") if isinstance(row.get("tracker"), dict) else {}
    command = configured.get("command") or row.get("command") or tracker.get("command") or tracker.get("agent_command") or tracker.get("agent_cmd")
    cwd = configured.get("cwd") or row.get("cwd") or tracker.get("cwd") or os.getcwd()
    if not command:
        raise SystemExit(f"agent {source_name!r} does not expose a copyable command")
    spec = {
        "cwd": str(cwd),
        "command": str(command),
        "scope": configured.get("scope") or row.get("scope") or tracker.get("bootstrap_scope"),
        "swarms": configured.get("swarms") or row.get("swarms") or tracker.get("swarms") or [],
        "autostart": False,
        "immutable": bool(immutable),
        "non_learning": bool(immutable),
        "source": {
            "name": source_name,
            "target_address": row.get("target_address"),
            "remote": bool(row.get("remote")),
            "registry_name": row.get("registry_name"),
        },
    }
    return {k: v for k, v in spec.items() if v not in (None, [], {}) or k in {"swarms", "autostart", "immutable", "non_learning"}}


def agent_copy(args: argparse.Namespace) -> None:
    try:
        validate_agent_name(args.new_name)
    except ValueError as e:
        raise SystemExit(str(e))
    cfg = load_config()
    agents = cfg.setdefault("agents", {})
    if args.new_name in agents and not getattr(args, "replace", False):
        raise SystemExit(f"agent {args.new_name!r} is already configured; use --replace")
    source_key, row = _find_agent_row(args.source, include_remote=True)
    spec = _row_copy_spec(source_key, row, immutable=bool(args.immutable))
    agents[args.new_name] = spec
    save_config(cfg)
    payload = {"copied": source_key, "name": args.new_name, "agent": spec}
    print(json.dumps(payload if args.json else payload, indent=2, sort_keys=True))


def agent_remove(args: argparse.Namespace) -> None:
    try:
        validate_agent_name(args.name)
    except ValueError as e:
        raise SystemExit(str(e))
    cfg = load_config()
    agents = cfg.get("agents") or {}
    if args.name not in agents:
        raise SystemExit(f"agent {args.name!r} is not configured")
    removed = agents.pop(args.name)
    save_config(cfg)
    window_killed = kill_agent_window(args.name)
    print(json.dumps({"removed": args.name, "window_killed": window_killed, "agent": removed}, indent=2, sort_keys=True))


def agent_assign_swarm(args: argparse.Namespace) -> None:
    try:
        swarm = _validate_swarm_name_value(args.swarm)
    except ValueError as e:
        raise SystemExit(str(e))
    subagents = list(args.subagent or [])
    ensure_tracker()
    result = tracker_rpc("assign_live_swarm", {"swarm": swarm, "main": args.main, "subagents": subagents})
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        members = ", ".join(f"{m.get('agent')}:{m.get('role')}" for m in result.get("members", []))
        print(f"Assigned swarm {result.get('swarm')}: {members}")


def _validate_swarm_name_value(name: str) -> str:
    validate_swarm_name(name)
    return name


def agent_start_swarm(args: argparse.Namespace) -> None:
    try:
        swarm_name = _validate_swarm_name_value(args.swarm)
    except ValueError as e:
        raise SystemExit(str(e))
    cfg = load_config()
    swarms = cfg.get("swarms") if isinstance(cfg.get("swarms"), dict) else {}
    swarm = swarms.get(swarm_name)
    if not isinstance(swarm, dict):
        legacy_members = [name for name, spec in (cfg.get("agents") or {}).items() if any(m.get("name") == swarm_name for m in normalize_swarms((spec or {}).get("swarms", [])))]
        if legacy_members:
            raise SystemExit(f"swarm {swarm_name!r} only exists as legacy per-agent membership; configure top-level swarms.{swarm_name}.members to start it")
        raise SystemExit(f"configured swarm {swarm_name!r} not found in config swarms")
    members = swarm.get("members") or []
    if not members:
        raise SystemExit(f"configured swarm {swarm_name!r} has no members")
    agents = cfg.get("agents") or {}
    names = []
    for member in members:
        agent = member.get("agent") if isinstance(member, dict) else None
        try:
            validate_agent_name(agent)
        except ValueError as e:
            raise SystemExit(str(e))
        if agent not in agents:
            raise SystemExit(f"configured swarm {swarm_name!r} references missing agent {agent!r}")
        names.append(agent)
    ensure_tracker()
    ensure_tmux()
    launched = reconcile_agents(set(names))
    payload = {"swarm": swarm_name, "members": names, "launched": launched, "already_running": [name for name in names if name not in launched]}
    print(json.dumps(payload if args.json else payload, indent=2, sort_keys=True))


def agent_edit(args: argparse.Namespace) -> None:
    try:
        validate_agent_name(args.name)
    except ValueError as e:
        raise SystemExit(str(e))

    if not window_exists(args.name):
        raise SystemExit(f"agent {args.name!r} is not running; agent edit only works on live agents")

    cfg = load_config()
    agents = cfg.setdefault("agents", {})
    existing = agents.get(args.name)

    if args.rename:
        try:
            validate_agent_name(args.rename)
        except ValueError as e:
            raise SystemExit(str(e))
        if args.rename in agents:
            raise SystemExit(f"agent target name {args.rename!r} already exists")

    raw_command = _consume_post_name_launch_options(args, list(getattr(args, "command", None) or []), allow_command_flag=True, allow_edit_flags=True)
    if raw_command and raw_command[0] == "--":
        raw_command = raw_command[1:]
    command_string = getattr(args, "command_string", None)
    command = shlex.join(raw_command) if raw_command else (command_string.strip() if command_string else None)
    if command is not None and not command.strip():
        raise SystemExit("--command must not be empty")

    source_cwd = None
    if args.cwd is not None:
        source_cwd = os.path.abspath(os.path.expanduser(args.cwd))
        if not os.path.isdir(source_cwd):
            raise SystemExit(f"cwd does not exist: {source_cwd}")

    swarms = parse_swarm_args(args) if (args.swarm or args.role) else None
    target = args.rename or args.name

    spec = dict(existing or {})
    if command is not None:
        spec["command"] = command
    if source_cwd is not None:
        spec["cwd"] = source_cwd
    if swarms is not None:
        spec["swarms"] = swarms
    if args.scope is not None:
        spec["scope"] = args.scope
    if args.autostart is not None:
        spec["autostart"] = args.autostart

    if not spec.get("command"):
        raise SystemExit("agent edit requires --command when the agent is not already configured with a command")
    if not spec.get("cwd"):
        raise SystemExit("agent edit requires --cwd when the agent is not already configured with a source cwd")
    if target in agents and target != args.name:
        raise SystemExit(f"agent {target!r} already exists")

    if existing is not None and target != args.name:
        del agents[args.name]
    elif target != args.name and args.name in agents:
        del agents[args.name]

    agents[target] = spec
    save_config(cfg)

    ensure_tracker()
    ensure_tmux()
    window_killed = kill_agent_window(args.name)
    launched = reconcile_agents({target})
    print(json.dumps({
        "edited": args.name,
        "name": target,
        "window_killed": window_killed,
        "launched": target in launched,
        "agent": spec,
    }, indent=2, sort_keys=True))


def agent_restart(args: argparse.Namespace) -> None:
    try:
        validate_agent_name(args.name)
    except ValueError as e:
        raise SystemExit(str(e))
    cfg = load_config()
    agent_spec(cfg, args.name)
    ensure_tracker()
    ensure_tmux()
    window_killed = kill_agent_window(args.name)
    launched = reconcile_agents({args.name})
    print(json.dumps({"restarted": args.name, "window_killed": window_killed, "launched": args.name in launched}, indent=2, sort_keys=True))


def managed_window_for_agent(name: str) -> dict[str, str]:
    cfg = load_config()
    agent_spec(cfg, name)
    ensure_tracker()
    ensure_tmux()
    windows = managed_windows(name)
    if not windows:
        raise SystemExit(f"agent {name!r} is configured but has no running managed window; run `broccoli-comms start` or `broccoli-comms agent restart {name}`")
    return windows[0]


def focus_managed_window(name: str) -> dict[str, str]:
    window = managed_window_for_agent(name)
    window_id = window["window_id"]
    tmux("select-window", "-t", window_id)
    if in_tmux_client() and not use_private_tmux():
        subprocess.run(["tmux", "switch-client", "-t", window_id], env=os.environ.copy(), check=False, text=True, capture_output=True)
    else:
        tmux("switch-client", "-t", window_id, check=False)
    return window


def agent_focus(args: argparse.Namespace) -> None:
    try:
        validate_agent_name(args.name)
    except ValueError as e:
        raise SystemExit(str(e))
    window = focus_managed_window(args.name)
    print(json.dumps({"focused": args.name, "window": window}, indent=2, sort_keys=True))


def agent_attach(args: argparse.Namespace) -> None:
    try:
        validate_agent_name(args.name)
    except ValueError as e:
        raise SystemExit(str(e))
    window = focus_managed_window(args.name)
    exec_tmux_interactive(window["window_id"])


def _consume_post_name_launch_options(args: argparse.Namespace, command: list[str], *, allow_command_flag: bool = False, allow_edit_flags: bool = False, allow_default_agent_command: bool = False) -> list[str]:
    """Support `run NAME --cwd DIR -- cmd` despite argparse REMAINDER.

    argparse stops option parsing once a REMAINDER positional is reached.  The
    public launch UX intentionally reads as `run NAME [options] -- COMMAND`, so
    normalize any leading command-tail options back onto the namespace before
    dispatching.
    """
    remaining = list(command or [])
    swarms = list(getattr(args, "swarm", None) or [])
    roles = list(getattr(args, "role", None) or [])
    idx = 0
    while idx < len(remaining):
        token = remaining[idx]
        if token == "--":
            idx += 1
            break
        if token == "--json":
            setattr(args, "json", True)
            idx += 1
            continue
        if allow_edit_flags and token in {"--autostart", "--no-autostart"}:
            args.autostart = (token == "--autostart")
            idx += 1
            continue
        if token in ({"--cwd", "--scope", "--swarm", "--role", "--host", "--timeout"} | ({"--command"} if allow_command_flag else set()) | ({"--rename"} if allow_edit_flags else set()) | ({"--default-agent-command"} if allow_default_agent_command else set())):
            if idx + 1 >= len(remaining):
                raise SystemExit(f"{token} requires a value")
            value = remaining[idx + 1]
            if token == "--cwd":
                args.cwd = value
            elif token == "--scope":
                args.scope = value
            elif token == "--swarm":
                swarms.append(value)
            elif token == "--role":
                roles.append(value)
            elif token == "--host":
                args.host = value
            elif token == "--timeout":
                try:
                    args.timeout = float(value)
                except ValueError:
                    raise SystemExit("--timeout requires a number")
            elif token == "--command":
                args.command_string = value
            elif token == "--default-agent-command":
                args.default_agent_command = value
            elif token == "--rename":
                args.rename = value
            idx += 2
            continue
        break
    args.swarm = swarms or None
    args.role = roles or None
    return remaining[idx:]


def _resolve_remote_run_tracker(host: str) -> dict:
    trackers = tracker_rpc("list_trackers", {}, timeout=10.0)
    if not isinstance(trackers, list):
        raise SystemExit("remote run requires registry tracker discovery")
    matches = [t for t in trackers if host in {str(t.get("hostname") or ""), str(t.get("tracker_id") or "")}]
    if not matches:
        raise SystemExit(f"remote run host not found: {host}")
    if len(matches) > 1:
        raise SystemExit(f"remote run host is ambiguous: {host}")
    tracker_id = matches[0].get("tracker_id")
    if not tracker_id:
        raise SystemExit(f"remote run host has no tracker_id: {host}")
    return matches[0]


def _remote_run_wait_result(request_id: str, timeout_seconds: float) -> dict:
    initial = tracker_rpc("wait_events", {"since": 0, "timeout": 0})
    cursor = int((initial or {}).get("last_seq") or 0) if isinstance(initial, dict) else 0
    deadline = time.time() + max(0.0, min(float(timeout_seconds), 120.0))
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            raise SystemExit(f"remote run timed out waiting for result request_id={request_id}")
        wait_timeout = min(remaining, 5.0)
        result = tracker_rpc("wait_events", {"since": cursor, "timeout": wait_timeout}, timeout=wait_timeout + 2.0)
        if not isinstance(result, dict):
            continue
        cursor = int(result.get("last_seq") or cursor)
        for event in result.get("events") or []:
            if event.get("type") == "remote_run_result" and event.get("request_id") == request_id:
                return event


def _provider_arg_list(value) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return shlex.split(value)
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return [str(value)]


def _provider_str(value) -> str:
    return value if isinstance(value, str) else ""


def _provider_initial_message_args(provider_cfg: dict) -> list[str]:
    prompt_flag = _provider_str(provider_cfg.get("prompt-flag-name", ""))
    initial_message = _provider_str(provider_cfg.get("initial-message", ""))
    if not prompt_flag or not initial_message:
        return []
    if prompt_flag == "--":
        return [initial_message]
    return [prompt_flag, initial_message]


def run_remote(args: argparse.Namespace, command: list[str]) -> None:
    ensure_tracker()
    tracker = _resolve_remote_run_tracker(args.host)
    info = tracker_rpc("tracker_info", {})
    source_tracker_id = (info or {}).get("tracker_id") if isinstance(info, dict) else None
    request_id = f"remote-run-{uuid.uuid4().hex[:12]}"
    payload = {"request_id": request_id, "agent": args.name}
    if args.cwd:
        payload["cwd"] = os.path.abspath(os.path.expanduser(args.cwd))
    if args.scope:
        payload["scope"] = args.scope
    if command:
        payload["command"] = command
    if source_tracker_id:
        payload["source_tracker_id"] = source_tracker_id
        payload["reply_to_tracker_id"] = source_tracker_id
    publish = tracker_rpc("publish_tracker_event", {"target_tracker_id": tracker["tracker_id"], "event_type": "remote_run_request", "payload": payload}, timeout=10.0)
    if not isinstance(publish, dict) or not publish.get("success"):
        raise SystemExit(f"failed to publish remote_run_request to {args.host}")
    result = _remote_run_wait_result(request_id, getattr(args, "timeout", 30.0))
    print(json.dumps({"remote_run": result, "target_tracker": tracker}, indent=2, sort_keys=True))


def run(args: argparse.Namespace) -> None:
    command = _consume_post_name_launch_options(args, list(getattr(args, "command", None) or []), allow_default_agent_command=True)
    if command and command[0] == "--":
        command = command[1:]

    try:
        validate_agent_name(args.name)
    except ValueError as e:
        raise SystemExit(str(e))

    if getattr(args, "host", None):
        run_remote(args, command)
        return

    requested_command = shlex.join(command) if command else None
    cfg = load_config()
    spec = (cfg.get("agents") or {}).get(args.name)
    using_saved_config = False
    using_default_agent_command = False
    if not command:
        if spec is not None:
            saved_command = spec.get("command")
            if not saved_command:
                raise SystemExit(f"saved agent {args.name!r} has no command")
            command = _parse_command_for_bootstrap(str(saved_command))
            using_saved_config = True
        else:
            default_command = str(getattr(args, "default_agent_command", "") or "").strip()
            if not default_command:
                raise SystemExit(f"run requires a command after --, a saved agent definition for {args.name!r}, or --default-agent-command")
            command = _parse_command_for_bootstrap(default_command)
            requested_command = shlex.join(command)
            using_default_agent_command = True

    source_cwd_value = args.cwd or ((spec or {}).get("cwd") if using_saved_config else None)
    source_cwd = os.path.abspath(os.path.expanduser(source_cwd_value)) if source_cwd_value else os.getcwd()
    if not os.path.isdir(source_cwd):
        raise SystemExit(f"run source-cwd does not exist or is not a directory: {source_cwd}")

    command, agents_dir, provider_agent_root_dir, context_layout = _provider_launch_settings(command)

    ensure_tracker()
    ensure_tmux()
    if window_exists(args.name):
        raise SystemExit(f"agent {args.name!r} is already running; stop or edit it first")

    launch_cwd = ephemeral_agent_workspace(args.name, agents_dir=agents_dir, source_cwd=source_cwd, agent_root_dir=provider_agent_root_dir, context_layout=context_layout)
    context_path = str(Path(launch_cwd))

    if using_saved_config and not (getattr(args, "swarm", None) or getattr(args, "role", None)):
        swarms = normalize_swarms((spec or {}).get("swarms") or [])
    else:
        swarms = parse_swarm_args(args)
    scope = args.scope if args.scope is not None else ((spec or {}).get("scope") if using_saved_config else None)
    immutable = bool(using_saved_config and spec and (spec.get("immutable") or spec.get("non_learning")))
    if requested_command is not None:
        agents = cfg.setdefault("agents", {})
        agents[args.name] = {
            **(agents.get(args.name) or {}),
            "cwd": source_cwd,
            "command": requested_command,
            "scope": scope,
            "swarms": swarms,
            "autostart": False,
        }
        save_config(cfg)
    bootstrap_cwd = source_cwd if (args.cwd or using_saved_config) else None
    wrapped = _build_bootstrap_track_command(args.name, bootstrap_cwd, scope, command, context_path)
    wrapped_cmd = shlex.join(wrapped)
    launch = managed_agent_launch_command(
        args.name,
        source_cwd,
        wrapped_cmd,
        swarms,
        launch_cwd=launch_cwd,
        immutable=immutable,
        agents_dir=agents_dir,
        context_layout=context_layout,
    )

    result = tmux(
        "new-window", "-d", "-P", "-F", "#{window_id}\t#{pane_id}",
        "-t", SESSION,
        "-n", args.name,
        "-c", launch_cwd,
        launch,
    )
    window_and_pane = result.stdout.strip().split("\t", 1)
    if not window_and_pane:
        raise SystemExit("failed to create run window")
    window_id = window_and_pane[0]
    pane_id = window_and_pane[1] if len(window_and_pane) > 1 else ""
    tmux("set-option", "-w", "-t", window_id, MANAGED_AGENT_OPTION, args.name)
    print(json.dumps({"started": args.name, "window_id": window_id, "pane_id": pane_id, "ephemeral_cwd": launch_cwd, "bootstrap_context": context_path, "bootstrap_context_dir": context_path, "configured": using_saved_config, "default_agent_command": using_default_agent_command, "immutable": immutable}, indent=2, sort_keys=True))


def stop(_args: argparse.Namespace) -> None:
    p = paths()
    kill_broccoli_tmux_windows()
    for _ in range(50):
        if tmux("has-session", "-t", SESSION, check=False).returncode != 0 or not has_broccoli_tmux_windows():
            break
        time.sleep(0.1)
    if use_private_tmux() and p["tmux_socket"].exists() and tmux("list-sessions", check=False).returncode != 0:
        p["tmux_socket"].unlink(missing_ok=True)

    pid_file = p["tracker_pid"]
    if pid_file.exists():
        try:
            os.kill(int(pid_file.read_text().strip()), signal.SIGTERM)
        except Exception:
            pass
        for _ in range(50):
            if not can_connect(p["tracker_socket"]):
                break
            time.sleep(0.1)
        if can_connect(p["tracker_socket"]):
            try:
                os.kill(int(pid_file.read_text().strip()), signal.SIGKILL)
            except Exception:
                pass
            for _ in range(20):
                if not can_connect(p["tracker_socket"]):
                    break
                time.sleep(0.1)
        pid_file.unlink(missing_ok=True)
    if p["tracker_socket"].exists() and not can_connect(p["tracker_socket"]):
        p["tracker_socket"].unlink(missing_ok=True)
    print(f"{APP} stopped")


def _resolve_executable(command: str) -> str | None:
    if not command:
        return None
    if os.path.isabs(command) or os.sep in command:
        return command if os.path.exists(command) and os.access(command, os.X_OK) else None
    return shutil.which(command, path=base_env().get("PATH"))


def _command_version(command: list[str]) -> str | None:
    try:
        result = subprocess.run(command, check=False, text=True, capture_output=True, timeout=3, env=base_env())
    except Exception:
        return None
    output = (result.stdout or result.stderr).strip().splitlines()
    return output[0] if output else None


def _doctor_check(checks: list[dict], name: str, status: str, message: str, **extra) -> None:
    checks.append({"name": name, "status": status, "message": message, **{k: v for k, v in extra.items() if v is not None}})


def _check_writable_dir(path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=path, delete=True) as f:
            f.write("ok")
        return True, "writable"
    except Exception as e:
        return False, str(e)


SHELL_BUILTINS = {
    "alias", "bg", "cd", "command", "echo", "eval", "exec", "exit", "export", "fg", "hash", "jobs", "pwd", "read", "set", "shift", "test", "trap", "type", "ulimit", "umask", "unalias", "unset", "wait",
}
SHELL_COMPLEX_TOKENS = ("|", "&", ";", "<", ">", "(", ")", "$", "`", "\\", "\n")


def _env_enabled(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes", "on"}


def remote_pane_input_doctor_checks() -> list[dict]:
    checks = []
    send_enabled = _env_enabled("BROCCOLI_COMMS_REMOTE_PANE_INPUT_ENABLED") or _env_enabled("BROCCOLI_COMMS_REMOTE_PANE_INPUT_SEND_ENABLED") or _env_enabled("AGENT_TRACKER_REMOTE_PANE_INPUT_SEND_ENABLED")
    receive_enabled = _env_enabled("BROCCOLI_COMMS_REMOTE_PANE_INPUT_ENABLED") or _env_enabled("BROCCOLI_COMMS_REMOTE_PANE_INPUT_RECEIVE_ENABLED") or _env_enabled("AGENT_TRACKER_REMOTE_PANE_INPUT_RECEIVE_ENABLED")
    registry_enabled = _env_enabled("BROCCOLI_COMMS_REMOTE_PANE_INPUT_REGISTRY_ENABLED") or _env_enabled("AGENT_REGISTRY_REMOTE_PANE_INPUT_ENABLED")
    any_enabled = send_enabled or receive_enabled or registry_enabled
    if not any_enabled:
        _doctor_check(checks, "remote pane input", "ok", "remote direct pane input is disabled by default")
        return checks

    enabled_roles = ",".join(role for role, enabled in (("send", send_enabled), ("receive", receive_enabled), ("registry", registry_enabled)) if enabled)
    _doctor_check(checks, "remote pane input", "warning", "remote direct pane input is enabled; this bypasses inboxes and controls panes directly", enabled_roles=enabled_roles)

    registry_auth_disabled = not get_toml_config("registry", "auth_enabled", True)
    registry_token = get_toml_config("registry", "token", "")
    if registry_auth_disabled:
        _doctor_check(checks, "remote pane input auth", "warning", "remote direct pane input is enabled while registry auth is disabled")
    elif not registry_token:
        _doctor_check(checks, "remote pane input auth", "warning", "remote direct pane input is enabled but AGENT_REGISTRY_TOKEN is not set in this environment")
    else:
        _doctor_check(checks, "remote pane input auth", "ok", "registry token is present for remote direct pane input")
    return checks


def configured_agent_command_checks() -> list[dict]:
    checks = []
    cfg = load_config()
    for name, spec in sorted((cfg.get("agents") or {}).items()):
        command = str(spec.get("command") or "").strip()
        check_name = f"agent command:{name}"
        if not command:
            _doctor_check(checks, check_name, "error", "configured command is empty")
            continue
        if any(token in command for token in SHELL_COMPLEX_TOKENS):
            _doctor_check(checks, check_name, "warning", "command is shell-complex; skipping executable lookup", command=command)
            continue
        try:
            parts = shlex.split(command)
        except ValueError as e:
            _doctor_check(checks, check_name, "warning", f"could not parse command: {e}", command=command)
            continue
        while parts and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", parts[0]):
            parts = parts[1:]
        executable = parts[0] if parts else ""
        if not executable:
            _doctor_check(checks, check_name, "warning", "command has no executable after env assignments", command=command)
        elif executable in SHELL_BUILTINS:
            _doctor_check(checks, check_name, "warning", "command starts with a shell builtin; skipping executable lookup", command=command, executable=executable)
        else:
            resolved = _resolve_executable(executable)
            if resolved:
                _doctor_check(checks, check_name, "ok", "configured command executable found", command=command, executable=executable, path=resolved)
            else:
                _doctor_check(checks, check_name, "error", "configured command executable not found on PATH", command=command, executable=executable)
    if not checks:
        _doctor_check(checks, "agent commands", "ok", "no configured agents")
    return checks


def doctor_payload() -> dict:
    p = paths()
    checks: list[dict] = []

    tmux_path = _resolve_executable("tmux")
    _doctor_check(checks, "tmux", "ok" if tmux_path else "error", "tmux executable found" if tmux_path else "tmux executable not found; Nix packages include tmux, manual installs must provide system tmux", path=tmux_path, version=_command_version([tmux_path, "-V"]) if tmux_path else None)

    python_path = sys.executable if os.path.exists(sys.executable) else _resolve_executable("python3")
    _doctor_check(checks, "python", "ok" if python_path else "error", "Python executable found" if python_path else "python3 executable not found", path=python_path, version=_command_version([python_path, "--version"]) if python_path else None)

    tracker = tracker_script()
    _doctor_check(checks, "tracker script", "ok" if os.path.exists(tracker) else "error", "tracker script found" if os.path.exists(tracker) else "tracker script missing", path=tracker)

    wrapper = wrapper_path()
    wrapper_ok = os.path.exists(wrapper) and os.access(wrapper, os.X_OK)
    _doctor_check(checks, "agent-wrapper", "ok" if wrapper_ok else "error", "agent-wrapper executable found" if wrapper_ok else "agent-wrapper missing or not executable", path=wrapper)

    tui = tui_path()
    tui_resolved = _resolve_executable(tui)
    _doctor_check(checks, "agent-communicator", "ok" if tui_resolved else "error", "agent-communicator executable found" if tui_resolved else "agent-communicator executable not found", path=tui_resolved or tui)

    for label, path in (("runtime dir", p["runtime"]), ("cache dir", p["cache"]), ("config dir", p["config"])):
        ok, message = _check_writable_dir(path)
        _doctor_check(checks, label, "ok" if ok else "error", message, path=str(path))

    checks.extend(configured_agent_command_checks())
    checks.extend(remote_pane_input_doctor_checks())

    tracker_socket_exists = p["tracker_socket"].exists()
    tracker_reachable = can_connect(p["tracker_socket"])
    if tracker_reachable:
        _doctor_check(checks, "tracker socket", "ok", "private tracker socket is reachable", path=str(p["tracker_socket"]))
    elif tracker_socket_exists:
        _doctor_check(checks, "tracker socket", "warning", "tracker socket exists but is not reachable", path=str(p["tracker_socket"]))
    else:
        _doctor_check(checks, "tracker socket", "ok", "runtime is not running; tracker socket not present", path=str(p["tracker_socket"]))

    tmux_reachable = bool(tmux_path) and tmux_up()
    if use_private_tmux():
        tmux_socket_exists = p["tmux_socket"].exists()
        if tmux_reachable:
            _doctor_check(checks, "tmux session", "ok", "private tmux session is reachable", mode=tmux_mode(), path=str(p["tmux_socket"]), session=SESSION)
        elif tmux_socket_exists:
            _doctor_check(checks, "tmux session", "warning", "tmux socket exists but private session is not reachable", mode=tmux_mode(), path=str(p["tmux_socket"]), session=SESSION)
        else:
            _doctor_check(checks, "tmux session", "ok", "runtime is not running; private tmux socket not present", mode=tmux_mode(), path=str(p["tmux_socket"]), session=SESSION)
    else:
        if tmux_reachable:
            _doctor_check(checks, "tmux session", "ok", "default tmux server has Broccoli Comms session", mode=tmux_mode(), session=SESSION)
        else:
            _doctor_check(checks, "tmux session", "ok", "runtime is not running; default tmux session not present", mode=tmux_mode(), session=SESSION)

    return {
        "app": APP,
        "version": VERSION,
        "build": build_info(),
        "ok": not any(check["status"] == "error" for check in checks),
        "paths": {"runtime_dir": str(p["runtime"]), "cache_dir": str(p["cache"]), "config_dir": str(p["config"])},
        "runtime": {"tracker_up": tracker_reachable, "tmux_up": tmux_reachable, "tmux_session": SESSION, "tmux_mode": tmux_mode(), "tmux_socket": tmux_socket_label()},
        "checks": checks,
    }


def _default_registries_config() -> dict:
    return {"version": 1, "registries": []}


def _normalize_registry_urls_config(data: object) -> dict:
    if data is None:
        return _default_registries_config()
    if not isinstance(data, dict):
        raise ValueError("registries config must be a JSON object")
    version = data.get("version", 1)
    if version != 1:
        raise ValueError(f"unsupported registries config version: {version}")
    registries = data.get("registries", [])
    if not isinstance(registries, list):
        raise ValueError("registries config field 'registries' must be a list")
    normalized = []
    for entry in registries:
        if not isinstance(entry, dict):
            raise ValueError("registry entries must be objects")
        name = entry.get("name")
        url = entry.get("url")
        if not isinstance(name, str) or not AGENT_NAME_RE.match(name):
            raise ValueError(f"invalid registry name in config: {name!r}")
        if not isinstance(url, str):
            raise ValueError(f"registry {name} has invalid url")
        item = {"name": name, "url": _normalize_registry_url(url), "enabled": bool(entry.get("enabled", True))}
        if "auth" in entry:
            item["auth"] = bool(entry.get("auth"))
        token_file = entry.get("token-file")
        if token_file:
            if not isinstance(token_file, str):
                raise ValueError(f"registry {name} has invalid token-file")
            item["token-file"] = str(Path(token_file).expanduser())
        normalized.append(item)
    return {"version": 1, "registries": normalized}


def load_registry_urls_config() -> dict:
    path = paths()["registries_json"]
    if not path.exists():
        return _default_registries_config()
    try:
        return _normalize_registry_urls_config(json.loads(path.read_text()))
    except json.JSONDecodeError as e:
        raise SystemExit(f"failed to parse {path}: {e}")
    except ValueError as e:
        raise SystemExit(f"invalid {path}: {e}")


def save_registry_urls_config(config: dict) -> None:
    normalized = _normalize_registry_urls_config(config)
    path = paths()["registries_json"]
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(normalized, f, indent=2, sort_keys=True)
            f.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _normalize_registry_url(url: str) -> str:
    if not isinstance(url, str):
        raise ValueError("registry URL must be a string")
    url = url.strip()
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("registry URL must start with http:// or https:// and include a host")
    path = parsed.path.rstrip("/")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))


def _registry_entry_for_tracker(entry: dict) -> dict[str, str]:
    result = {"name": entry["name"], "url": entry["url"]}
    token_file = entry.get("token-file")
    if token_file:
        result["token-file"] = token_file
    return result


def configured_registries_for_tracker() -> list[dict[str, str]]:
    config = load_registry_urls_config()
    return [_registry_entry_for_tracker(entry) for entry in config.get("registries", []) if entry.get("enabled", True)]


def apply_configured_registries(env: dict[str, str]) -> dict[str, str]:
    if "AGENT_REGISTRIES_JSON" in env:
        return env
    if os.environ.get("BROCCOLI_COMMS_DISABLE_CONFIG_REGISTRIES", "").lower() in {"1", "true", "yes"}:
        return env
    registries = configured_registries_for_tracker()
    if registries:
        env["AGENT_REGISTRIES_JSON"] = json.dumps(registries, separators=(",", ":"))
    return env


def _redact_registry_entry(entry: dict) -> dict:
    redacted = {k: v for k, v in entry.items() if k != "token"}
    if "token" in entry:
        redacted["token"] = "<redacted>"
    return redacted


def _redacted_registry_urls_config(config: dict) -> dict:
    normalized = _normalize_registry_urls_config(config)
    return {"version": 1, "registries": [_redact_registry_entry(entry) for entry in normalized.get("registries", [])]}


def registry_add(args: argparse.Namespace) -> None:
    if not AGENT_NAME_RE.match(args.name):
        raise SystemExit("registry add: NAME may contain only letters, digits, underscore, dot, or dash")
    if args.auth and not args.token_file:
        raise SystemExit("registry add: --auth requires --token-file")
    if args.noauth and args.token_file:
        raise SystemExit("registry add: --noauth cannot be combined with --token-file")
    url = _normalize_registry_url(args.url)
    token_file = str(Path(args.token_file).expanduser()) if args.token_file else None
    if token_file and not Path(token_file).exists():
        raise SystemExit(f"registry add: token file does not exist: {token_file}")
    config = load_registry_urls_config()
    registries = config.get("registries", [])
    existing = next((i for i, entry in enumerate(registries) if entry.get("name") == args.name), None)
    if existing is not None and not args.replace:
        raise SystemExit(f"registry add: registry {args.name!r} already exists; use --replace")
    entry = {"name": args.name, "url": url, "enabled": True}
    if args.noauth:
        entry["auth"] = False
    elif args.auth or token_file:
        entry["auth"] = True
    if token_file:
        entry["token-file"] = token_file
    if existing is None:
        registries.append(entry)
    else:
        registries[existing] = entry
    config["registries"] = registries
    save_registry_urls_config(config)
    print(f"registry {args.name} configured. Restart Broccoli Comms for changes to affect the running tracker.")


def registry_list(args: argparse.Namespace) -> None:
    config = load_registry_urls_config()
    redacted = _redacted_registry_urls_config(config)
    if args.json:
        print(json.dumps(redacted, indent=2, sort_keys=True))
        return
    registries = redacted.get("registries", [])
    if not registries:
        print("no registries configured")
        return
    rows = [("NAME", "URL", "AUTH", "ENABLED")]
    for entry in registries:
        if entry.get("token-file"):
            auth = "token-file"
        elif entry.get("auth") is False:
            auth = "noauth"
        else:
            auth = "none"
        rows.append((entry["name"], entry["url"], auth, "yes" if entry.get("enabled", True) else "no"))
    widths = [max(len(row[i]) for row in rows) for i in range(4)]
    for row in rows:
        print("  ".join(value.ljust(widths[i]) for i, value in enumerate(row)))


def registry_remove(args: argparse.Namespace) -> None:
    config = load_registry_urls_config()
    registries = config.get("registries", [])
    kept = [entry for entry in registries if entry.get("name") != args.name]
    if len(kept) == len(registries) and not args.missing_ok:
        raise SystemExit(f"registry remove: registry {args.name!r} not found")
    config["registries"] = kept
    save_registry_urls_config(config)
    print(f"registry {args.name} removed. Restart Broccoli Comms for changes to affect the running tracker.")


def _set_registry_enabled(args: argparse.Namespace, enabled: bool) -> None:
    config = load_registry_urls_config()
    for entry in config.get("registries", []):
        if entry.get("name") == args.name:
            entry["enabled"] = enabled
            save_registry_urls_config(config)
            state = "enabled" if enabled else "disabled"
            print(f"registry {args.name} {state}. Restart Broccoli Comms for changes to affect the running tracker.")
            return
    raise SystemExit(f"registry {args.name!r} not found")


def registry_enable(args: argparse.Namespace) -> None:
    _set_registry_enabled(args, True)


def registry_disable(args: argparse.Namespace) -> None:
    _set_registry_enabled(args, False)


def registry_env(args: argparse.Namespace) -> None:
    registries = configured_registries_for_tracker()
    value = json.dumps(registries, separators=(",", ":"))
    if args.json:
        print(json.dumps({"AGENT_REGISTRIES_JSON": value, "registries": registries}, indent=2, sort_keys=True))
    else:
        print(f"AGENT_REGISTRIES_JSON={shlex.quote(value)}")


def _is_loopback_host(host: str) -> bool:
    return host in {"127.0.0.1", "localhost", "::1"}


def _pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except Exception:
        return None


def _read_token_file(path: str | None) -> str | None:
    if not path:
        return None
    try:
        return Path(path).expanduser().read_text().strip()
    except OSError as e:
        raise SystemExit(f"failed to read token file {path}: {e}")


def _registry_auth_enabled(args: argparse.Namespace) -> bool:
    if args.auth and args.noauth:
        raise SystemExit("registry start: choose only one of --auth or --noauth")
    if args.auth:
        return True
    if args.noauth:
        if not _is_loopback_host(args.host):
            raise SystemExit("registry start: refusing unauthenticated non-loopback bind; use --auth --token-file for public/LAN hosts")
        return False
    if _is_loopback_host(args.host):
        return False
    raise SystemExit("registry start: non-loopback binds require explicit --auth (recommended) or --noauth is refused for safety")


def _registry_state_path(args: argparse.Namespace) -> Path:
    if getattr(args, "state_path", None):
        return Path(args.state_path).expanduser()
    return paths()["registry_state"]


def _registry_url(host: str, port: int) -> str:
    url_host = "127.0.0.1" if host == "0.0.0.0" else host
    if ":" in url_host and not url_host.startswith("["):
        url_host = f"[{url_host}]"
    return f"http://{url_host}:{port}"


def _registry_config_from_args(args: argparse.Namespace, auth_enabled: bool, state_path: Path) -> dict:
    return {
        "name": args.name,
        "host": args.host,
        "port": int(args.port),
        "auth": auth_enabled,
        "token_file": str(Path(args.token_file).expanduser()) if getattr(args, "token_file", None) else None,
        "state_path": str(state_path),
        "url": _registry_url(args.host, int(args.port)),
    }


def _load_registry_config() -> dict:
    path = paths()["registry_config"]
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {
        "name": "local",
        "host": "127.0.0.1",
        "port": 8080,
        "auth": False,
        "token_file": None,
        "state_path": str(paths()["registry_state"]),
        "url": "http://127.0.0.1:8080",
    }


def _save_registry_config(config: dict) -> None:
    path = paths()["registry_config"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")


def _registry_env(args: argparse.Namespace, auth_enabled: bool, state_path: Path) -> dict[str, str]:
    token = getattr(args, "token", None) or _read_token_file(getattr(args, "token_file", None))
    if auth_enabled and not token:
        raise SystemExit("registry start: --auth requires --token-file or --token")
    env = base_env()
    env.update({
        "AGENT_REGISTRY_HOST": args.host,
        "AGENT_REGISTRY_PORT": str(args.port),
        "AGENT_REGISTRY_AUTH": "true" if auth_enabled else "false",
        "AGENT_REGISTRY_STATE_PATH": str(state_path),
        "TRACKER_STALE_SECONDS": str(args.stale_seconds),
        "TRACKER_GONE_SECONDS": str(args.gone_seconds),
    })
    if token:
        env["AGENT_REGISTRY_TOKEN"] = token
    return env


def _registry_auth_header(config: dict) -> dict[str, str]:
    if not config.get("auth"):
        return {}
    token = _read_token_file(config.get("token_file"))
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _registry_request(path: str, config: dict | None = None, timeout: float = 3.0) -> tuple[int | None, dict | None, str | None]:
    config = config or _load_registry_config()
    url = (config.get("url") or _registry_url(config.get("host", "127.0.0.1"), int(config.get("port", 8080)))).rstrip("/") + path
    req = urllib.request.Request(url, headers={"Accept": "application/json", **_registry_auth_header(config)})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
            return resp.status, json.loads(body) if body else {}, None
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
        except Exception:
            body = None
        return e.code, body, None
    except Exception as e:
        return None, None, str(e)


def _wait_registry_ready(config: dict, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, _, _ = _registry_request("/healthz", config, timeout=0.5)
        if status == 200:
            return True
        time.sleep(0.1)
    return False


def registry_start(args: argparse.Namespace) -> None:
    ensure_dirs()
    auth_enabled = _registry_auth_enabled(args)
    state_path = _registry_state_path(args)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    config = _registry_config_from_args(args, auth_enabled, state_path)
    env = _registry_env(args, auth_enabled, state_path)
    _save_registry_config(config)
    pid_file = paths()["registry_pid"]
    existing_pid = _read_pid(pid_file)
    if existing_pid and _pid_running(existing_pid):
        if not args.force:
            print(f"registry already running pid={existing_pid} url={config['url']}")
            return
        try:
            os.kill(existing_pid, signal.SIGTERM)
        except OSError:
            pass
        for _ in range(50):
            if not _pid_running(existing_pid):
                break
            time.sleep(0.1)

    if args.foreground:
        pid_file.write_text(str(os.getpid()))
        registry = registry_script()
        cmd = [sys.executable, registry] if registry.endswith(".py") else [registry]
        os.execvpe(cmd[0], cmd, env)

    log = open(paths()["registry_log"], "ab", buffering=0)
    registry = registry_script()
    cmd = [sys.executable, registry] if registry.endswith(".py") else [registry]
    proc = subprocess.Popen(cmd, env=env, stdout=log, stderr=log, start_new_session=True)
    pid_file.write_text(str(proc.pid))
    if not _wait_registry_ready(config):
        if proc.poll() is not None:
            raise SystemExit(f"agent-registry exited early; see {paths()['registry_log']}")
        raise SystemExit(f"agent-registry did not become healthy; see {paths()['registry_log']}")
    print(f"registry started name={config['name']} url={config['url']} pid={proc.pid} auth={'on' if auth_enabled else 'off'}")


def registry_stop(args: argparse.Namespace) -> None:
    pid_file = paths()["registry_pid"]
    pid = _read_pid(pid_file)
    if not pid:
        print("registry not running")
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pid_file.unlink(missing_ok=True)
        print("registry not running")
        return
    for _ in range(50):
        if not _pid_running(pid):
            break
        time.sleep(0.1)
    if _pid_running(pid) and args.force:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        for _ in range(20):
            if not _pid_running(pid):
                break
            time.sleep(0.1)
    pid_file.unlink(missing_ok=True)
    print("registry stopped")


def _registry_status_payload() -> dict:
    config = _load_registry_config()
    pid = _read_pid(paths()["registry_pid"])
    running = bool(pid and _pid_running(pid))
    health_status, health_body, health_error = _registry_request("/healthz", config)
    agents_status, agents_body, _ = _registry_request("/agents", config)
    return {
        "name": config.get("name"),
        "url": config.get("url"),
        "host": config.get("host"),
        "port": config.get("port"),
        "auth": bool(config.get("auth")),
        "pid": pid,
        "running": running,
        "state_path": config.get("state_path"),
        "log_path": str(paths()["registry_log"]),
        "health": {"status": health_status, "body": health_body, "error": health_error},
        "agent_count": len((agents_body or {}).get("agents") or []) if agents_status == 200 else None,
    }


def registry_status(args: argparse.Namespace) -> None:
    payload = _registry_status_payload()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    health = payload["health"]
    health_text = "ok" if health.get("status") == 200 else (health.get("error") or f"http {health.get('status')}")
    print(f"registry {payload['name']} {payload['url']} pid={payload['pid']} running={payload['running']} auth={'on' if payload['auth'] else 'off'} health={health_text} agents={payload['agent_count']}")


def registry_health(_args: argparse.Namespace) -> None:
    status, body, error = _registry_request("/healthz")
    if status == 200:
        print(json.dumps(body or {"ok": True}, indent=2, sort_keys=True))
        return
    raise SystemExit(error or json.dumps(body or {"status": status}))


def registry_agents(args: argparse.Namespace) -> None:
    query = {k: v for k, v in {
        "name": getattr(args, "name", None),
        "hostname": getattr(args, "hostname", None),
        "status": getattr(args, "status", None),
        "logical_identity": getattr(args, "logical_identity", None),
        "service_kind": getattr(args, "service_kind", None),
    }.items() if v}
    path = "/agents" + (("?" + urllib.parse.urlencode(query)) if query else "")
    status, body, error = _registry_request(path)
    if status != 200:
        raise SystemExit(error or json.dumps(body or {"status": status}))
    agents = (body or {}).get("agents") or []
    if args.json:
        print(json.dumps(body, indent=2, sort_keys=True))
    else:
        for agent in agents:
            service = ""
            if agent.get("logical_identity") or agent.get("service_kind"):
                service = f" {agent.get('logical_identity', '')}/{agent.get('service_kind', '')}".rstrip("/")
            print(f"{agent.get('hostname', '?')}/{agent.get('name', '?')} {agent.get('status', 'unknown')} {agent.get('agent_id', '')}{service}")
        if not agents:
            print("no agents")


def registry_trackers(args: argparse.Namespace) -> None:
    status, body, error = _registry_request("/trackers")
    if status != 200:
        raise SystemExit(error or json.dumps(body or {"status": status}))
    if args.json:
        print(json.dumps(body, indent=2, sort_keys=True))
    else:
        trackers = (body or {}).get("trackers") or []
        for tracker in trackers:
            print(f"{tracker.get('hostname', '?')} {tracker.get('status', 'unknown')} {tracker.get('tracker_id', '')}")
        if not trackers:
            print("no trackers")


def agent_tracker(args: argparse.Namespace) -> None:
    """Run the in-repo agent-tracker-ctl against Broccoli Comms private sockets."""
    tracker_args = list(getattr(args, "tracker_args", None) or ["--help"])
    
    read_only_cmds = {"status-bar", "list", "registry-status", "whoami", "read-inbox"}
    if not (tracker_args and tracker_args[0] in read_only_cmds):
        ensure_tracker()
        ensure_tmux()
    ctl = tracker_ctl_script()
    tracker_args = list(getattr(args, "tracker_args", None) or ["--help"])
    env = base_env(preserve_agent_identity=True)
    cmd = [sys.executable, ctl] if ctl.endswith(".py") else [ctl]
    cmd.extend(tracker_args)
    os.execvpe(cmd[0], cmd, env)


def _default_task_participants(args: argparse.Namespace) -> list[dict]:
    participants: list[dict] = []
    for role, attr in (("reviewer", "reviewer"), ("verifier", "verifier"), ("coordinator", "coordinator")):
        for agent in getattr(args, attr, None) or []:
            participants.append({"agent": agent, "role": role})
    for item in getattr(args, "participant", None) or []:
        if ":" not in item:
            raise ValueError("--participant must use role:agent")
        role, agent = item.split(":", 1)
        participants.append({"agent": agent, "role": role})
    return participants


def task_create(args: argparse.Namespace) -> None:
    try:
        task = learning_kernel().tasks.create(
            title=args.title,
            description=args.description or "",
            assigned_agent=args.agent,
            scope=args.scope,
            next_step=args.next_step,
            acceptance_criteria=list(args.acceptance or []),
            depends_on=parse_csv(args.depends_on),
            priority=args.priority,
            participants=_default_task_participants(args),
            task_chain_id=args.task_chain_id,
            root_task_id=args.root_task_id,
            actor=os.environ.get("AGENT_NAME") or "user",
        )
    except ValueError as e:
        raise SystemExit(str(e))
    _print_payload(task, args.json)


def task_show(args: argparse.Namespace) -> None:
    try:
        _print_payload(learning_kernel().tasks.show(args.task_id, include_participants=getattr(args, "include_participants", False)), args.json)
    except KeyError:
        raise SystemExit(f"task not found: {args.task_id}")


def task_list(args: argparse.Namespace) -> None:
    statuses = parse_csv(args.status)
    roles = parse_csv(getattr(args, "participant_role", None))
    try:
        payload = learning_kernel().tasks.list(agent=args.agent, statuses=statuses or None, include_archived=args.include_archived, scope=args.scope, include_participants=getattr(args, "include_participants", False), participant_roles=roles or None)
    except ValueError as e:
        raise SystemExit(str(e))
    _print_payload(payload, args.json)


def task_chain_default_participant_set(args: argparse.Namespace) -> None:
    try:
        payload = learning_kernel().tasks.chain_default_participant_set(args.task_chain_id, args.agent, args.role, root_task_id=args.root_task_id, status=args.status, actor=os.environ.get("AGENT_NAME") or "user")
    except ValueError as e:
        raise SystemExit(str(e))
    _print_payload(payload, args.json)


def task_chain_default_participant_list(args: argparse.Namespace) -> None:
    try:
        payload = learning_kernel().tasks.chain_default_participant_list(args.task_chain_id)
    except ValueError as e:
        raise SystemExit(str(e))
    _print_payload(payload, args.json)


def task_participant_list(args: argparse.Namespace) -> None:
    try:
        _print_payload(learning_kernel().tasks.participant_list(args.task_id), args.json)
    except KeyError:
        raise SystemExit(f"task not found: {args.task_id}")


def task_participant_add(args: argparse.Namespace) -> None:
    try:
        payload = learning_kernel().tasks.participant_add(args.task_id, args.agent, args.role, actor=os.environ.get("AGENT_NAME") or "user", task_chain_id=args.task_chain_id, root_task_id=args.root_task_id, instance_id=args.instance, status=args.status)
    except KeyError:
        raise SystemExit(f"task not found: {args.task_id}")
    except ValueError as e:
        raise SystemExit(str(e))
    _print_payload(payload, args.json)


def task_participant_update(args: argparse.Namespace) -> None:
    try:
        payload = learning_kernel().tasks.participant_update(args.participant_id, status=args.status, actor=os.environ.get("AGENT_NAME") or "user")
    except KeyError:
        raise SystemExit(f"participant not found: {args.participant_id}")
    except ValueError as e:
        raise SystemExit(str(e))
    _print_payload(payload, args.json)


def task_participant_remove(args: argparse.Namespace) -> None:
    try:
        payload = learning_kernel().tasks.participant_remove(args.participant_id, actor=os.environ.get("AGENT_NAME") or "user")
    except KeyError:
        raise SystemExit(f"participant not found: {args.participant_id}")
    _print_payload(payload, args.json)


def duplicate_profile_instances(agent: str | None) -> list[dict]:
    if not agent:
        return []
    matches = []
    for name, info in tracker_agents().items():
        if name == agent or str(name).startswith(f"{agent}@"):
            item = {"name": name}
            if isinstance(info, dict):
                item.update({k: info.get(k) for k in ("agent_id", "uuid", "tmux_pane", "status") if info.get(k)})
            matches.append(item)
    return matches if len(matches) > 1 else []


def task_next(args: argparse.Namespace) -> None:
    agent = args.agent or os.environ.get("AGENT_NAME")
    roles = parse_csv(getattr(args, "participant_role", None))
    try:
        payload = learning_kernel().tasks.next(agent=agent, scope=args.scope, include_profile=args.include_profile, participant_roles=roles or None)
    except ValueError as e:
        raise SystemExit(str(e))
    conflicts = duplicate_profile_instances(agent)
    if conflicts:
        payload["profile_conflict"] = {"agent": agent, "instances": conflicts, "message": "duplicate same-profile instances detected; parallel different task chains are allowed, but same-chain auto-claiming should be coordinator-resolved"}
    _print_payload(payload, args.json)


def _agent_identity_key(agent: str | None) -> str:
    value = str(agent or "").strip().lower()
    if not value:
        return ""
    return value.rsplit("/", 1)[-1]


def _agent_alias_keys(agent: str | None) -> set[str]:
    if not agent:
        return set()
    value = str(agent).strip()
    keys = {_agent_identity_key(value)}
    if "/" in value:
        keys.add(value.lower())
    return {k for k in keys if k}


def _recipient_matches(agent: str | None, other: str | None) -> bool:
    if not agent or not other:
        return False
    return bool(_agent_alias_keys(agent) & _agent_alias_keys(other))


def _append_task_recipient(recipients: list[dict], agent: str | None, actor: str, role: str, reason: str) -> None:
    if not agent or agent == UI_AGENT_NAME or _recipient_matches(agent, UI_AGENT_NAME) or _recipient_matches(agent, actor):
        return
    keys = _agent_alias_keys(agent)
    for recipient in recipients:
        if keys & set(recipient.get("identity_keys") or []):
            if role not in recipient["roles"]:
                recipient["roles"].append(role)
            if reason not in recipient["reasons"]:
                recipient["reasons"].append(reason)
            return
    recipients.append({"agent": agent, "roles": [role], "reasons": [reason], "identity_keys": sorted(keys)})


def _task_has_active_role(task: dict, role: str) -> bool:
    if role == "assignee" and task.get("assigned_agent"):
        return True
    return any(p.get("status") == "active" and p.get("role") == role and p.get("agent") for p in task.get("participants") or [])


def _append_task_role_recipients(recipients: list[dict], task: dict, roles: set[str], actor: str, reason: str) -> None:
    if "requester" in roles and task.get("created_by") not in {None, "", "user"}:
        _append_task_recipient(recipients, task.get("created_by"), actor, "requester", reason)
    if "assignee" in roles:
        _append_task_recipient(recipients, task.get("assigned_agent"), actor, "assignee", reason)
    for participant in task.get("participants") or []:
        if participant.get("status") == "active" and participant.get("role") in roles:
            _append_task_recipient(recipients, participant.get("agent"), actor, participant.get("role") or "participant", reason)


def _task_update_notification_recipients(task: dict, actor: str, updates: dict) -> list[dict]:
    status = updates.get("status")
    result_status = updates.get("result_status")
    recipients: list[dict] = []
    if result_status in {"bad", "need_improvements"}:
        _append_task_role_recipients(recipients, task, {"assignee", "coordinator", "requester"}, actor, f"result:{result_status}")
    elif result_status == "good":
        if _task_has_active_role(task, "verifier"):
            _append_task_role_recipients(recipients, task, {"verifier"}, actor, "result:good")
        else:
            _append_task_role_recipients(recipients, task, {"coordinator", "requester"}, actor, "result:good")
        if status == "validated":
            _append_task_role_recipients(recipients, task, {"assignee", "coordinator", "requester"}, actor, "status:validated")
    elif status == "validated":
        _append_task_role_recipients(recipients, task, {"assignee", "coordinator", "requester"}, actor, "status:validated")
    elif status in {"done", "review"}:
        roles = {"reviewer", "verifier"} if (_task_has_active_role(task, "reviewer") or _task_has_active_role(task, "verifier")) else {"coordinator", "requester"}
        _append_task_role_recipients(recipients, task, roles, actor, f"status:{status}")
    elif status == "ready":
        _append_task_role_recipients(recipients, task, {"assignee", "coordinator", "requester"}, actor, "status:ready")
    elif status == "working":
        _append_task_role_recipients(recipients, task, {"coordinator", "requester"}, actor, "status:working")
    elif status == "blocked":
        _append_task_role_recipients(recipients, task, {"assignee", "coordinator", "requester"}, actor, "status:blocked")
    elif status == "archived":
        _append_task_role_recipients(recipients, task, {"assignee", "coordinator", "requester"}, actor, "status:archived")
    if result_status == "good" or status == "validated":
        for dependent in task.get("ready_dependents") or []:
            _append_task_role_recipients(recipients, dependent, {"assignee", "coordinator", "requester"}, actor, "dependent:ready")
    return recipients


def _single_line_message_part(value: object, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


def _task_update_attention_message(task: dict, actor: str, updates: dict) -> str:
    task_id = _single_line_message_part(task.get("task_id") or "unknown", 80)
    status = _single_line_message_part(updates.get("status") or task.get("status") or "unknown", 80)
    actor = _single_line_message_part(actor or "unknown", 80)
    summary = _single_line_message_part(task.get("result_summary") or "", 240)
    message = f"Task {task_id} moved to {status} by {actor}. Read inbox."
    result_status = updates.get("result_status")
    if result_status:
        message += f" Result: {_single_line_message_part(result_status, 80)}."
    if summary:
        message += f" Summary: {summary}"
    return _single_line_message_part(message, 1000)


SHARED_SERVICE_IDENTITIES = {UI_AGENT_NAME: {"kind": "shared_service", "default_delivery": "fanout_active_trackers"}}


def _task_notification_delivery(agent: str, message: str, metadata: dict, sender_name: str) -> dict:
    if "/" in str(agent or ""):
        result = tracker_rpc("send_message", {"agent_name": agent, "message": message, "metadata": {**metadata, "preferred_delivery": "send_input", "delivery_fallback_reason": "remote_or_qualified_target"}, "sender_name": sender_name})
        return {"agent": agent, "sent": bool(result), "delivery": "send_message", "result": result}
    try:
        result = tracker_rpc("send_input", {"agent_name": agent, "mode": "text", "text": message, "submit": True, "sender_name": sender_name})
        if not result:
            raise RuntimeError("tracker RPC send_input failed")
        return {"agent": agent, "sent": True, "delivery": "send_input", "result": result}
    except Exception as e:
        fallback_metadata = {**metadata, "delivery_fallback_reason": str(e), "preferred_delivery": "send_input"}
        result = tracker_rpc("send_message", {"agent_name": agent, "message": message, "metadata": fallback_metadata, "sender_name": sender_name})
        return {"agent": agent, "sent": bool(result), "delivery": "send_message_fallback", "error": str(e), "result": result}


def _remote_shared_service_targets(identity: str) -> list[str]:
    """Return explicit remote target addresses for active shared-service instances.

    Phase 3 uses registry-backed agent discovery via tracker `list` with
    include_remote instead of guessing one service per tracker hostname.  Older
    trackers/registries without service metadata fall back to the previous
    tracker-hostname enumeration for compatibility.
    """
    targets: list[str] = []
    seen: set[str] = set()
    try:
        agents = tracker_rpc("list", {"include_remote": True}, timeout=10.0)
    except Exception:
        agents = None
    if isinstance(agents, dict):
        for name, agent in agents.items():
            if not isinstance(agent, dict):
                continue
            if agent.get("scope") != "remote":
                continue
            if agent.get("status") not in {None, "", "active", "online", "idle", "working"}:
                continue
            if agent.get("logical_identity") != identity or agent.get("service_kind") != "shared_service":
                continue
            target = str(agent.get("target_address") or name or "").strip()
            if not target or "/" not in target or target in seen:
                continue
            seen.add(target)
            targets.append(target)
        if targets:
            return targets

    try:
        info = tracker_rpc("tracker_info", {}, timeout=5.0)
        local_tracker_id = (info or {}).get("tracker_id") if isinstance(info, dict) else None
        trackers = tracker_rpc("list_trackers", {}, timeout=10.0)
    except Exception:
        return []
    if not isinstance(trackers, list):
        return []
    for tracker in trackers:
        if not isinstance(tracker, dict):
            continue
        if tracker.get("tracker_id") and tracker.get("tracker_id") == local_tracker_id:
            continue
        if tracker.get("status") not in {None, "", "active", "online"}:
            continue
        hostname = str(tracker.get("hostname") or "").strip()
        if not hostname:
            continue
        target = f"{hostname}/{identity}"
        if target not in seen:
            seen.add(target)
            targets.append(target)
    return targets


def _routing_metadata(metadata: dict | None, target: str, kind: str, scope: str) -> dict:
    return {
        **(metadata or {}),
        "recipient_agent": target,
        "recipient_kind": kind,
        "delivery_scope": scope,
        "target_logical_identity": target if kind == "shared_service" else None,
    }


def _clean_routing_metadata(metadata: dict) -> dict:
    return {k: v for k, v in metadata.items() if v is not None}


def _delivery_id(message_id: str, target: str, scope: str) -> str:
    digest = hashlib.sha256(f"{message_id}\0{target}\0{scope}".encode("utf-8")).hexdigest()[:24]
    return f"del-{digest}"


def _route_message(target: str, message: str, metadata: dict | None, sender_name: str, *, mode: str = "auto", delivery_scope: str | None = None) -> dict:
    """Route a message using local/direct/fanout/auto semantics.

    This is the phase-1 internal routing layer for system notifications.  It
    preserves bare-name local-only compatibility for ordinary sends while giving
    task-update broadcasts an explicit shared-service fanout path.
    """
    target = str(target or "").strip()
    if not target:
        return {"sent": False, "error": "target required", "mode": mode}
    if mode == "auto":
        mode = "direct" if "/" in target else "local"
    if mode not in {"local", "direct", "fanout"}:
        raise ValueError("unsupported delivery mode")

    if mode == "fanout":
        scope = delivery_scope or "shared_service_broadcast"
        message_id = str((metadata or {}).get("message_id") or uuid.uuid4())
        local_delivery_id = _delivery_id(message_id, f"local/{target}", scope)
        local_metadata = _clean_routing_metadata({**_routing_metadata(metadata, target, "shared_service", scope), "message_id": message_id, "delivery_id": local_delivery_id})
        local_result = None
        local_error = None
        try:
            local_result = tracker_rpc("send_message", {"agent_name": target, "message": message, "metadata": local_metadata, "sender_name": sender_name, "message_id": message_id, "delivery_id": local_delivery_id})
            if not local_result:
                raise RuntimeError("tracker RPC send_message failed")
        except Exception as e:
            local_error = str(e)
        remote_results = []
        for remote_target in _remote_shared_service_targets(target):
            remote_delivery_id = _delivery_id(message_id, remote_target, scope)
            remote_metadata = {**local_metadata, "delivery_id": remote_delivery_id}
            try:
                result = tracker_rpc("send_message", {"target_address": remote_target, "message": message, "metadata": remote_metadata, "sender_name": sender_name, "message_id": message_id, "delivery_id": remote_delivery_id})
                remote_results.append({"target": remote_target, "sent": bool(result), "delivery": "direct", "delivery_id": remote_delivery_id, "result": result})
            except Exception as e:
                remote_results.append({"target": remote_target, "sent": False, "delivery": "direct", "delivery_id": remote_delivery_id, "error": str(e)})
        payload = {"sent": bool(local_result) or any(item.get("sent") for item in remote_results), "mode": "fanout", "message_id": message_id, "delivery_scope": scope, "local": {"target": target, "sent": bool(local_result), "delivery": "local", "delivery_id": local_delivery_id, "result": local_result}, "remote": remote_results}
        if local_error:
            payload["local"]["error"] = local_error
        return payload

    scope = delivery_scope or ("direct" if mode == "direct" else "local")
    route_metadata = _clean_routing_metadata(_routing_metadata(metadata, target, "direct" if mode == "direct" else "local", scope))
    params = {"message": message, "metadata": route_metadata, "sender_name": sender_name}
    if mode == "direct" and "/" in target and not target.startswith("local/"):
        params["target_address"] = target
    else:
        params["agent_name"] = target[6:] if target.startswith("local/") else target
    if (metadata or {}).get("message_id"):
        params["message_id"] = (metadata or {}).get("message_id")
    if (metadata or {}).get("delivery_id"):
        params["delivery_id"] = (metadata or {}).get("delivery_id")
    result = tracker_rpc("send_message", params)
    return {"sent": bool(result), "mode": mode, "delivery_scope": scope, "target": target, "result": result}


def _notify_shared_service_identity(identity: str, message: str, metadata: dict, sender_name: str) -> dict:
    policy = SHARED_SERVICE_IDENTITIES.get(identity, {})
    scope = "shared_service_broadcast" if policy.get("kind") == "shared_service" else "fanout"
    return _route_message(identity, message, metadata, sender_name, mode="fanout", delivery_scope=scope)


def notify_task_update(task: dict, actor: str, updates: dict) -> dict:
    if not task or not updates:
        return {"sent": False, "skipped": True}
    status = task.get("status") or "unknown"
    title = task.get("title") or "Untitled task"
    task_id = task.get("task_id") or "unknown"
    metadata = {
        "content_type": "application/vnd.broccoli.task-update+json",
        "kind": "task_update",
        "task_id": task_id,
        "task_title": title,
        "task_status": status,
        "task_assigned_agent": task.get("assigned_agent") or "",
        "task_next_step": task.get("next_step") or "",
        "result_summary": task.get("result_summary") or "",
        "source": "system/task-kernel",
        "sender_source": "system",
    }
    message = _task_update_attention_message(task, actor, updates)
    sender_name = actor or "task-kernel"
    ui_result = _notify_shared_service_identity(UI_AGENT_NAME, message, metadata, sender_name)
    participant_results = []
    if "status" in updates or "result_status" in updates:
        for recipient in _task_update_notification_recipients(task, actor, updates):
            agent = recipient["agent"]
            participant_metadata = {**metadata, "recipient_agent": agent, "recipient_kind": "task_participant", "recipient_roles": recipient.get("roles") or [], "recipient_reasons": recipient.get("reasons") or []}
            try:
                delivery = _task_notification_delivery(agent, message, participant_metadata, sender_name)
                participant_results.append({**delivery, "roles": recipient.get("roles") or [], "reasons": recipient.get("reasons") or []})
            except Exception as participant_error:
                participant_results.append({"agent": agent, "roles": recipient.get("roles") or [], "reasons": recipient.get("reasons") or [], "sent": False, "delivery": "failed", "error": str(participant_error)})
    payload = {"sent": bool(ui_result.get("sent")), "result": ui_result.get("local", {}).get("result"), "ui_broadcast": ui_result, "participant_notifications": participant_results}
    if ui_result.get("local", {}).get("error"):
        payload["error"] = ui_result["local"]["error"]
    return payload


def task_update(args: argparse.Namespace) -> None:
    actor = os.environ.get("AGENT_NAME") or "user"
    updates = {k: v for k, v in {
        "status": args.status,
        "next_step": args.next_step,
        "blocked_reason": args.blocked_reason,
        "result_summary": args.result_summary,
        "assigned_agent": args.assign_agent,
    }.items() if v is not None}
    try:
        payload = learning_kernel().tasks.update(
            args.task_id,
            status=args.status,
            next_step=args.next_step,
            blocked_reason=args.blocked_reason,
            result_summary=args.result_summary,
            assigned_agent=args.assign_agent,
            actor=actor,
        )
    except KeyError:
        raise SystemExit(f"task not found: {args.task_id}")
    except ValueError as e:
        raise SystemExit(str(e))
    if updates:
        kernel = learning_kernel()
        notify_payload = kernel.tasks.show(args.task_id, include_participants=True)
        if updates.get("status") == "validated":
            notify_payload["ready_dependents"] = kernel.tasks.ready_dependents(args.task_id, include_participants=True)
        payload["notification"] = notify_task_update(notify_payload, actor, updates)
    _print_payload(payload, args.json)


def task_mark_result(args: argparse.Namespace) -> None:
    actor = os.environ.get("AGENT_NAME") or "user"
    kernel = learning_kernel()
    try:
        payload = kernel.tasks.mark_result(args.task_id, args.result, args.notes, actor=actor, next_step=args.next_step, status=args.status)
    except KeyError:
        raise SystemExit(f"task not found: {args.task_id}")
    except ValueError as e:
        raise SystemExit(str(e))
    notify_payload = kernel.tasks.show(args.task_id, include_participants=True)
    notify_payload["ready_dependents"] = kernel.tasks.ready_dependents(args.task_id, include_participants=True)
    payload["notification"] = notify_task_update(notify_payload, actor, {"status": payload.get("status"), "result_status": args.result})
    _print_payload(payload, args.json)


def notify_chain_summary(kernel: LearningKernel, summary: dict, actor: str) -> dict:
    chain_id = summary.get("task_chain_id") or "unknown"
    root_task_id = summary.get("root_task_id") or chain_id
    message = _single_line_message_part(f"Task chain {chain_id} summarized by {actor}. Read inbox. Summary: {summary.get('summary') or ''}", 1000)
    metadata = {
        "content_type": "application/vnd.broccoli.task-chain-summary+json",
        "kind": "task_chain_summary",
        "summary_id": summary.get("summary_id"),
        "task_chain_id": chain_id,
        "root_task_id": root_task_id,
        "event_seq_start": summary.get("event_seq_start"),
        "event_seq_end": summary.get("event_seq_end"),
        "source": "system/task-kernel",
        "sender_source": "system",
    }
    ui_sent = False
    ui_result = None
    ui_error = None
    try:
        ui_result = tracker_rpc("send_message", {"agent_name": UI_AGENT_NAME, "message": message, "metadata": metadata, "sender_name": actor or "task-kernel"})
        if not ui_result:
            raise RuntimeError("tracker RPC send_message failed")
        ui_sent = True
    except Exception as e:
        ui_error = str(e)
    participant_results = []
    recipients: list[dict] = []
    try:
        root_task = kernel.tasks.show(root_task_id, include_participants=True)
        _append_task_role_recipients(recipients, root_task, {"assignee", "reviewer", "verifier", "coordinator", "requester"}, actor, "chain_summary")
    except Exception as e:
        participant_results.append({"agent": None, "sent": False, "error": str(e)})
    for recipient in recipients:
        agent = recipient["agent"]
        try:
            participant_result = tracker_rpc("send_message", {"agent_name": agent, "message": message, "metadata": {**metadata, "recipient_agent": agent, "recipient_kind": "task_participant", "recipient_roles": recipient.get("roles") or [], "recipient_reasons": recipient.get("reasons") or []}, "sender_name": actor or "task-kernel"})
            if not participant_result:
                raise RuntimeError("tracker RPC send_message failed")
            participant_results.append({"agent": agent, "roles": recipient.get("roles") or [], "reasons": recipient.get("reasons") or [], "sent": True, "result": participant_result})
        except Exception as participant_error:
            participant_results.append({"agent": agent, "roles": recipient.get("roles") or [], "reasons": recipient.get("reasons") or [], "sent": False, "error": str(participant_error)})
    payload = {"sent": ui_sent, "result": ui_result, "participant_notifications": participant_results}
    if ui_error:
        payload["error"] = ui_error
    return payload


def task_summarize_chain(args: argparse.Namespace) -> None:
    actor = os.environ.get("AGENT_NAME") or "user"
    kernel = learning_kernel()
    try:
        payload = kernel.tasks.summarize_chain(args.task_chain_id, root_task_id=args.root_task_id, next_task_chain_id=args.next_task_chain_id, actor=actor)
    except KeyError:
        raise SystemExit(f"task chain not found: {args.task_chain_id}")
    except ValueError as e:
        raise SystemExit(str(e))
    payload["notification"] = notify_chain_summary(kernel, payload, actor)
    _print_payload(payload, args.json)


def safe_context_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "-", name).strip("-._") or "agent"


def _markdown_memory_list(title: str, records: list[dict]) -> str:
    lines = [f"# {title}", ""]
    if not records:
        lines.append("No active records.")
        return "\n".join(lines).rstrip() + "\n"
    for mem in records:
        lines.extend([
            f"## {mem.get('title') or mem.get('memory_id')}",
            f"- id: `{mem.get('memory_id')}`",
            f"- type: `{mem.get('type')}`",
            f"- scope: `{mem.get('scope')}`",
        ])
        if mem.get("subject_agent"):
            lines.append(f"- subject_agent: `{mem.get('subject_agent')}`")
        if mem.get("source_task_id"):
            lines.append(f"- source_task: `{mem.get('source_task_id')}`")
        lines.extend(["", str(mem.get("body") or "").strip(), ""])
    return "\n".join(lines).rstrip() + "\n"


def _bootstrap_agents_md(base: str, path: Path, by_type: dict[str, list[dict]], rules_dir: Path | None = None, skills_dir: Path | None = None) -> str:
    rules_root = rules_dir or path
    skills_root = skills_dir or (path / "skills")
    memory_path = rules_root / "memory.md"
    expertise_path = rules_root / "expertise.md"
    base = base.replace(
        "1. If present in the working directory, read generated `memory.md`, `habits.md`, and `expertise.md`; bootstrap-generated `AGENTS.md` may provide absolute paths for these files and a concise list of available skills.",
        "1. If present in the working directory, read generated `memory.md` and `expertise.md`; retained habits are embedded directly in this AGENTS.md, and bootstrap-generated `AGENTS.md` may provide absolute paths for context files and a concise list of available skills.",
    ).replace(
        "2. Treat active records in `habits.md` as retained operating instructions for this and future turns. Re-check them before task completion/review transitions; do not drop them after the first response.",
        "2. Treat embedded retained habits in this AGENTS.md as mandatory operating instructions for this and future turns. Re-check them before task completion/review transitions; do not drop them after the first response.",
    )
    lines = [
        base.rstrip(),
        "",
        "## Generated bootstrap context",
        "- At session start, you must read the generated context files for this workspace:",
        f"  - Memory: `{memory_path}`",
        f"  - Expertise: `{expertise_path}`",
        "- Use `broccoli-comms memory ...` commands to update durable skills/memory; do not edit generated SKILL.md, memory.md, or expertise.md files as the source of truth.",
        "- **Retained habits are mandatory operating instructions.** The active habits are embedded below; follow them across the whole session, especially at task completion, review handoff, validation, and queue-continuation transitions.",
        "- Before reporting a task complete or validated, re-check the embedded retained habits and perform any follow-on action they require, such as notifying a reviewer or starting the next ready task.",
        "- Task/status/result changes auto-notify role-relevant participants: review/done -> reviewer/verifier when present otherwise coordinator/requester; bad/need_improvements -> assignee plus coordinator/requester with remediation next_step; good -> verifier when present otherwise coordinator/requester; validated -> assignee plus coordinator/requester and newly-ready dependents; ready -> assignee plus coordinator/requester; working -> coordinator/requester; blocked/archived -> assignee plus coordinator/requester. Suppress self-notifications, dedupe local/remote aliases for the same agent, and combine all applicable roles/reasons into one notification per recipient.",
        "- Ordinary single-task completion uses task/status/result flow, not `task submit-completion`: set a bounded result summary and move it to `review` when reviewer/verifier participants are active (or `done` when no review role is configured), then reviewers/users validate with `task mark-result`. Reserve `task submit-completion` for task-chain or scoped-phase completion only. Before submitting chain/scoped completion, refresh the bounded summary with `broccoli-comms task summarize-chain <task_chain_id> --json`, then submit the root with explicit `--task-chain-id` and `--root-task-id`; after approval/validation, run `summarize-chain` again so future agents resume from a bounded post-validation summary.",
    ]

    summarize = get_toml_config("agents_md", "summarize_memories", True)
    full_expertise = get_toml_config("agents_md", "full_expertise", True)

    facts = (by_type.get("fact") or []) + (by_type.get("episode") or [])
    if facts:
        lines.extend(["", "## Embedded facts and episodes from durable memory"])
        if summarize:
            for mem in facts:
                lines.append(f"- **[{str(mem.get('type') or 'fact').capitalize()}] {mem.get('title') or mem.get('memory_id')}** (ID: `{mem.get('memory_id')}`) - To view in full, run: `broccoli-comms memory show {mem.get('memory_id')}`")
        else:
            for mem in facts:
                lines.extend([
                    "",
                    f"### {mem.get('title') or mem.get('memory_id')}",
                    f"- id: `{mem.get('memory_id')}`",
                    f"- type: `{mem.get('type')}`",
                    f"- scope: `{mem.get('scope')}`",
                ])
                if mem.get("source_task_id"):
                    lines.append(f"- source_task: `{mem.get('source_task_id')}`")
                lines.extend(["", str(mem.get("body") or "").strip()])

    expertises = by_type.get("expertise") or []
    if expertises:
        lines.extend(["", "## Embedded expertise from durable memory"])
        if full_expertise:
            for mem in expertises:
                lines.extend([
                    "",
                    f"### {mem.get('title') or mem.get('memory_id')}",
                    f"- id: `{mem.get('memory_id')}`",
                    f"- type: `{mem.get('type')}`",
                    f"- scope: `{mem.get('scope')}`",
                ])
                if mem.get("source_task_id"):
                    lines.append(f"- source_task: `{mem.get('source_task_id')}`")
                lines.extend(["", str(mem.get("body") or "").strip()])
        else:
            for mem in expertises:
                lines.append(f"- **[Expertise] {mem.get('title') or mem.get('memory_id')}** (ID: `{mem.get('memory_id')}`) - To view in full, run: `broccoli-comms memory show {mem.get('memory_id')}`")

    habits = by_type.get("habit") or []
    if habits:
        lines.extend(["", "## Embedded retained habits from durable memory", "These retained habits are mandatory operating instructions. Follow them across turns and before completion, validation, review handoff, or queue-continuation transitions."])
        for mem in habits:
            lines.extend([
                "",
                f"### {mem.get('title') or mem.get('memory_id')}",
                f"- id: `{mem.get('memory_id')}`",
                f"- type: `{mem.get('type')}`",
                f"- scope: `{mem.get('scope')}`",
            ])
            if mem.get("source_task_id"):
                lines.append(f"- source_task: `{mem.get('source_task_id')}`")
            lines.extend(["", str(mem.get("body") or "").strip()])

    skills = by_type.get("skill") or []
    if skills:
        lines.extend(["", "## Available skills from durable memory"])
        for mem in skills:
            skill_name = safe_context_name(str(mem.get("title") or mem.get("memory_id") or "skill"))
            skill_path = skills_root / skill_name / "SKILL.md"
            desc = (mem.get("metadata") or {}).get("description") or ""
            lines.extend([
                f"- **{mem.get('title') or mem.get('memory_id')}**",
                f"  - description: {desc}",
                f"  - fetch: `broccoli-comms memory show {mem.get('memory_id')} --json`",
                f"  - local path: `{skill_path}`",
            ])
    else:
        lines.extend(["", "## Available skills from durable memory", "No active skill memories were included in this bootstrap."])
    return "\n".join(lines).rstrip() + "\n"


def write_bootstrap_context_files(payload: dict, context_dir: str | Path) -> dict:
    root_path = Path(context_dir)
    agents_override = os.environ.get("BROCCOLI_AGENTS_DIR")
    context_layout = os.environ.get("BROCCOLI_COMMS_CONTEXT_LAYOUT") or "legacy"
    if agents_override and context_layout != "jetski":
        path = root_path / agents_override
        rules_dir = path
        skills_dir = path / "skills"
    else:
        path = root_path
        rules_dir = (root_path / agents_override / "rules") if (agents_override and context_layout == "jetski") else root_path
        skills_dir = (root_path / agents_override / "skills") if (agents_override and context_layout == "jetski") else (root_path / "skills")
    path.mkdir(parents=True, exist_ok=True)
    rules_dir.mkdir(parents=True, exist_ok=True)
    skills_dir.mkdir(parents=True, exist_ok=True)
    memories = payload.get("memory") or []
    by_type = {typ: [m for m in memories if m.get("type") == typ] for typ in ["fact", "episode", "habit", "expertise", "skill"]}
    task = payload.get("task") or {}
    state = payload.get("state") or {}
    memory_intro = ["# Memory", "", "Durable active memory returned by Broccoli Comms bootstrap.", ""]
    if task:
        memory_intro.extend(["## Current task", "", f"- id: `{task.get('task_id')}`", f"- title: {task.get('title') or ''}", f"- status: `{task.get('status')}`", ""])
        if task.get("description"):
            memory_intro.extend([textwrap.dedent(str(task.get("description"))).strip(), ""])
    if state:
        memory_intro.extend(["## Current working state", "", f"- status: `{state.get('status')}`", f"- activity: {state.get('current_activity') or ''}", f"- next: {state.get('next_step') or ''}", ""])
    chain_summary = payload.get("chain_summary") or {}
    if chain_summary:
        memory_intro.extend(["## Latest task-chain summary", "", f"- id: `{chain_summary.get('summary_id')}`", f"- task_chain_id: `{chain_summary.get('task_chain_id')}`", f"- root_task_id: `{chain_summary.get('root_task_id')}`", "", str(chain_summary.get("summary") or "").strip(), ""])
    memory_intro.append(_markdown_memory_list("Facts and episodes", by_type["fact"] + by_type["episode"]))
    files = []
    for name, content in {
        "memory.md": "\n".join(memory_intro),
        "habits.md": _markdown_memory_list("Habits", by_type["habit"]),
        "expertise.md": _markdown_memory_list("Expertise", by_type["expertise"]),
    }.items():
        target = rules_dir / name
        target.write_text(content, encoding="utf-8")
        files.append(str(target))
    for mem in by_type["skill"]:
        skill_name = safe_context_name(str(mem.get("title") or mem.get("memory_id") or "skill"))
        target = skills_dir / skill_name / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        desc = (mem.get("metadata") or {}).get("description") or ""
        body = str(mem.get("body") or "").strip()
        target.write_text(f"---\nname: {skill_name}\ndescription: {desc}\n---\n\n{body}\n", encoding="utf-8")
        files.append(str(target))
    agents_target = path / "AGENTS.md"
    agents_target.write_text(_bootstrap_agents_md(str(payload.get("agents_md") or ""), path, by_type, rules_dir=rules_dir, skills_dir=skills_dir), encoding="utf-8")
    files.append(str(agents_target))
    return {"context_dir": str(path), "files": files}


def task_bootstrap(args: argparse.Namespace) -> None:
    agent = args.agent or os.environ.get("AGENT_NAME") or "agent"
    source_cwd = Path(args.cwd or os.environ.get("BROCCOLI_COMMS_SOURCE_CWD") or os.getcwd())
    ephemeral_cwd = Path(getattr(args, "write_context_dir", None) or os.environ.get("BROCCOLI_COMMS_EPHEMERAL_CWD") or os.getcwd())
    payload = learning_kernel().tasks.next(agent=agent, scope=args.scope, include_profile=True)
    task = payload.get("task") if isinstance(payload, dict) else None
    state = learning_kernel().tasks.state_show(task["task_id"], agent) if task else None
    mem = learning_kernel().memory.for_bootstrap(agent=agent, scope=(task or {}).get("scope") or args.scope)
    root_for_summary = (state or {}).get("root_task_id") if isinstance(state, dict) else (task or {}).get("task_id")
    chain_summary = learning_kernel().tasks.latest_chain_summary(root_for_summary) if root_for_summary else None
    payload.update({"state": state, "chain_summary": chain_summary, "memory": mem["records"], "memory_meta": {"truncated": mem["truncated"], "omitted_count": mem["omitted_count"]}, "agents_md": agent_contract(agent, args.instance, ephemeral_cwd, agent_contract_template(), source_cwd=source_cwd)})
    conflicts = duplicate_profile_instances(agent)
    if conflicts:
        payload["profile_conflict"] = {"agent": agent, "instances": conflicts, "message": "duplicate same-profile instances detected; parallel different task chains are allowed, but same-chain queue claiming should be coordinator-resolved"}
    if getattr(args, "write_context_dir", None):
        payload["bootstrap_context"] = write_bootstrap_context_files(payload, args.write_context_dir)
    _print_payload(payload, args.json)


def state_set(args: argparse.Namespace) -> None:
    agent = args.agent or os.environ.get("AGENT_NAME")
    if not agent:
        raise SystemExit("state set requires --agent when AGENT_NAME is unavailable")
    try:
        payload = learning_kernel().tasks.state_set(
            args.task_id,
            agent,
            status=args.status,
            current_activity=args.current_activity,
            next_step=args.next_step,
            blockers=parse_csv(args.blockers),
            notes=args.notes,
            instance_id=args.instance,
            task_chain_id=args.task_chain_id,
            root_task_id=args.root_task_id,
            clarification_count=args.clarification_count,
            correction_count=args.correction_count,
            need_improvements_count=args.need_improvements_count,
            first_pass_success=args.first_pass_success,
            stale_after_seconds=parse_duration_seconds(args.stale_after) if args.stale_after else None,
        )
    except KeyError:
        raise SystemExit(f"task not found: {args.task_id}")
    except ValueError as e:
        raise SystemExit(str(e))
    _print_payload(payload, args.json)


def state_show(args: argparse.Namespace) -> None:
    payload = learning_kernel().tasks.state_show(args.task_id, args.agent or os.environ.get("AGENT_NAME"))
    _print_payload(payload, args.json)


def state_list(args: argparse.Namespace) -> None:
    try:
        stale_after = parse_duration_seconds(args.stale_after) if args.stale_after else None
    except ValueError as e:
        raise SystemExit(str(e))
    _print_payload(learning_kernel().tasks.state_list(agent=args.agent, task_id=args.task_id, stale_after=stale_after), args.json)


def state_clear(args: argparse.Namespace) -> None:
    _print_payload(learning_kernel().tasks.state_clear(args.task_id, args.agent or os.environ.get("AGENT_NAME"), actor=os.environ.get("AGENT_NAME") or "user"), args.json)


def user_profile_show(args: argparse.Namespace) -> None:
    profile = learning_kernel().user_profile()
    if args.format == "markdown" and not args.json:
        print(profile["body"])
        print(f"\n> Warning: {profile['warning']}")
    else:
        _print_payload(profile, True)


def verified_memory_runtime_identity() -> dict:
    env_identity_present = any(os.environ.get(k) for k in ("AGENT_NAME", "AGENT_ID", "AGENT_UUID"))
    try:
        who = tracker_rpc("whoami", {})
    except RuntimeError as e:
        if "not identified" not in str(e).lower():
            raise SystemExit("verified memory actor required")
        who = None
        tracker_reachable_not_identified = True
    else:
        tracker_reachable_not_identified = False
    if who is None and not tracker_reachable_not_identified:
        # tracker_rpc returns None for socket/connectivity errors. Never treat an
        # unreachable tracker as proof that the caller is a local human.
        raise SystemExit("verified memory actor required")
    if isinstance(who, dict) and who.get("name"):
        return {"registered": True, "name": str(who["name"]), "instance": who.get("agent_id") or who.get("uuid")}
    if env_identity_present:
        raise SystemExit("verified memory actor required")
    return {"registered": False, "name": "user", "instance": None}


def trusted_memory_actor_from_runtime() -> str:
    ident = verified_memory_runtime_identity()
    return ident["name"]


def memory_proposal_fallback_markdown(mem: dict) -> str:
    lines = [
        "# Memory proposal",
        f"Memory: `{mem['memory_id']}`",
        f"Type: `{mem.get('type')}`",
        f"Scope: `{mem.get('scope')}`",
        f"Version: `{mem.get('version')}`",
        "",
        f"## {mem.get('title') or 'Untitled'}",
        "",
        str(mem.get("body") or ""),
        "",
        "Use the Agent Communicator command palette action `Memory Approvals` to approve, edit, reject/delete, or roll back this memory proposal.",
    ]
    return "\n".join(lines)


def notify_memory_proposal(mem: dict) -> dict:
    message = memory_proposal_fallback_markdown(mem)
    metadata = {
        "content_type": "application/vnd.broccoli.memory-proposal+json",
        "kind": "memory_proposal",
        "memory_id": mem.get("memory_id"),
        "memory_type": mem.get("type"),
        "memory_title": mem.get("title"),
        "memory_scope": mem.get("scope"),
        "memory_status": mem.get("status"),
        "memory_version": mem.get("version"),
        "source_task_id": mem.get("source_task_id"),
        "source": "system/memory-kernel",
        "sender_source": "system",
    }
    try:
        result = tracker_rpc("send_message", {"agent_name": UI_AGENT_NAME, "message": message, "metadata": metadata, "sender_name": "memory-kernel"})
        if not result:
            raise RuntimeError("tracker RPC send_message failed")
        return {"sent": True, "result": result}
    except Exception as e:
        return {"sent": False, "error": str(e)}


def unverified_memory_proposer(args: argparse.Namespace) -> tuple[str, str | None]:
    agent = args.agent or os.environ.get("AGENT_NAME") or "user"
    instance = args.instance or os.environ.get("AGENT_ID") or os.environ.get("AGENT_UUID")
    return agent, instance


def memory_propose(args: argparse.Namespace) -> None:
    try:
        agent, instance = unverified_memory_proposer(args)
        memory_id = getattr(args, "memory_id", None)
        metadata = json.loads(args.metadata_json) if args.metadata_json else {}
        if memory_id and getattr(args, "archive", False):
            payload = memory_rpc("memory.propose_archive", {
                "memory_id": memory_id,
                "expected_version": args.expected_version,
                "reason": getattr(args, "reason", None),
                "source_task_id": args.source_task,
                "proposed_by": agent,
                "proposed_by_instance": instance,
                "non_learning": immutable_learning_instance(agent, instance),
            })
        elif memory_id:
            payload = memory_rpc("memory.propose_edit", {
                "memory_id": memory_id,
                "expected_version": args.expected_version,
                "proposed_by": agent,
                "proposed_by_instance": instance,
                "type": args.type,
                "scope": args.scope,
                "subject_agent": args.subject_agent,
                "title": args.title,
                "description": getattr(args, "description", None),
                "body": args.body,
                "source_task_id": args.source_task,
                "tags": args.tag,
                "metadata": metadata,
                "non_learning": immutable_learning_instance(agent, instance),
            })
        else:
            trusted_actor = trusted_memory_actor_from_runtime() if args.trusted_manual else None
            if args.trusted_manual:
                ident = verified_memory_runtime_identity()
                agent = trusted_actor
                instance = ident["instance"] if ident["registered"] else None
            payload = memory_rpc("memory.propose", {
                "type": args.type, "scope": args.scope, "subject_agent": args.subject_agent, "title": args.title,
                "description": getattr(args, "description", None), "body": args.body,
                "source_task_id": args.source_task, "trusted_manual": args.trusted_manual, "tags": args.tag, "metadata": metadata,
                "idempotency_key": args.idempotency_key, "proposed_by": agent, "proposed_by_instance": instance,
                "trusted_actor": trusted_actor, "non_learning": immutable_learning_instance(agent, instance),
            })
        if not payload.get("idempotent"):
            payload["notification"] = notify_memory_proposal(payload["memory"])
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        raise SystemExit(str(e))
    _print_payload(payload, args.json)


def memory_propose_edit(args: argparse.Namespace) -> None:
    try:
        agent, instance = unverified_memory_proposer(args)
        metadata = json.loads(args.metadata_json) if args.metadata_json else {}
        payload = memory_rpc("memory.propose_edit", {
            "memory_id": args.memory_id,
            "expected_version": args.expected_version,
            "proposed_by": agent,
            "proposed_by_instance": instance,
            "type": args.type,
            "scope": args.scope,
            "subject_agent": args.subject_agent,
            "title": args.title,
            "description": getattr(args, "description", None),
            "body": args.body,
            "source_task_id": args.source_task,
            "tags": args.tag,
            "metadata": metadata,
            "non_learning": immutable_learning_instance(agent, instance),
        })
        if not payload.get("idempotent"):
            payload["notification"] = notify_memory_proposal(payload["memory"])
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        raise SystemExit(str(e))
    _print_payload(payload, args.json)


def memory_approve(args: argparse.Namespace) -> None:
    try:
        payload = memory_rpc("memory.approve", {
            "memory_id": args.memory_id,
            "version": args.version,
            "actor": trusted_memory_actor_from_runtime()
        })
    except (KeyError, ValueError) as e:
        raise SystemExit(str(e))
    _print_payload(payload, args.json)


def memory_decide(args: argparse.Namespace) -> None:
    try:
        actor = trusted_memory_actor_from_runtime()
        if args.decision == "approve":
            payload = memory_rpc("memory.approve", {
                "memory_id": args.memory_id,
                "version": args.version,
                "actor": actor
            })
        elif args.decision == "reject":
            payload = memory_rpc("memory.reject", {
                "memory_id": args.memory_id,
                "version": args.version,
                "reason": args.reason,
                "actor": actor
            })
        else:
            raise ValueError("decision must be approve or reject")
    except (KeyError, ValueError) as e:
        raise SystemExit(str(e))
    _print_payload(payload, args.json)


def memory_edit(args: argparse.Namespace) -> None:
    try:
        metadata = json.loads(args.metadata_json) if args.metadata_json else None
        payload = memory_rpc("memory.edit", {
            "memory_id": args.memory_id,
            "expected_version": args.expected_version,
            "actor": trusted_memory_actor_from_runtime(),
            "type": args.type,
            "scope": args.scope,
            "subject_agent": args.subject_agent,
            "title": args.title,
            "description": getattr(args, "description", None),
            "body": args.body,
            "source_task_id": args.source_task,
            "trusted_manual": args.trusted_manual,
            "tags": args.tag,
            "metadata": metadata,
        })
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        raise SystemExit(str(e))
    _print_payload(payload, args.json)


def memory_rollback(args: argparse.Namespace) -> None:
    try:
        payload = memory_rpc("memory.rollback", {
            "memory_id": args.memory_id,
            "target_version": args.to_version,
            "expected_version": args.expected_version,
            "actor": trusted_memory_actor_from_runtime()
        })
    except (KeyError, ValueError) as e:
        raise SystemExit(str(e))
    _print_payload(payload, args.json)


def memory_reject(args: argparse.Namespace) -> None:
    try:
        payload = memory_rpc("memory.reject", {
            "memory_id": args.memory_id,
            "version": args.version,
            "reason": args.reason,
            "actor": trusted_memory_actor_from_runtime()
        })
    except (KeyError, ValueError) as e:
        raise SystemExit(str(e))
    _print_payload(payload, args.json)


def memory_revoke(args: argparse.Namespace) -> None:
    try:
        payload = memory_rpc("memory.revoke", {
            "memory_id": args.memory_id,
            "reason": args.reason,
            "expected_version": args.expected_version,
            "actor": trusted_memory_actor_from_runtime()
        })
    except (KeyError, ValueError) as e:
        raise SystemExit(str(e))
    _print_payload(payload, args.json)


def memory_list(args: argparse.Namespace) -> None:
    try:
        payload = memory_rpc("memory.list", {
            "scope": args.scope,
            "type": args.type,
            "status": args.status,
            "agent": args.agent
        })
        _print_payload(payload, True)
    except (KeyError, ValueError) as e:
        raise SystemExit(str(e))


def memory_approvals(args: argparse.Namespace) -> None:
    try:
        pending = memory_rpc("memory.list", {
            "scope": args.scope,
            "type": args.type,
            "status": "pending",
            "agent": args.agent
        })
        approved = memory_rpc("memory.list", {
            "scope": args.scope,
            "type": args.type,
            "status": "active",
            "agent": args.agent
        })
        _print_payload({"pending": pending, "approved": approved}, True)
    except (KeyError, ValueError) as e:
        raise SystemExit(str(e))


def memory_search(args: argparse.Namespace) -> None:
    try:
        payload = memory_rpc("memory.search", {
            "query": args.query,
            "scope": args.scope
        })
        _print_payload(payload, True)
    except (KeyError, ValueError) as e:
        raise SystemExit(str(e))


def memory_show(args: argparse.Namespace) -> None:
    try:
        payload = memory_rpc("memory.show", {
            "memory_id": args.memory_id,
            "version": args.version
        })
    except ValueError as e:
        raise SystemExit(str(e))
    _print_payload(payload, True)


def memory_history(args: argparse.Namespace) -> None:
    try:
        payload = memory_rpc("memory.history", {
            "memory_id": args.memory_id
        })
    except ValueError as e:
        raise SystemExit(str(e))
    _print_payload(payload, True)


def memory_budget(args: argparse.Namespace) -> None:
    try:
        agent = args.agent or os.environ.get("AGENT_NAME") or "user"
        payload = memory_rpc("memory.budget", {
            "agent": agent,
            "scope": args.scope
        })
        _print_payload(payload, True)
    except (KeyError, ValueError) as e:
        raise SystemExit(str(e))


def _parse_discoveries(values: list[str] | None) -> list[dict[str, str]]:
    discoveries = []
    for value in values or []:
        if "=" not in value:
            raise SystemExit("--discovery must be label=value or label=value:reason")
        label, rest = value.split("=", 1)
        val, sep, reason = rest.partition(":")
        discoveries.append({"label": label, "value": val, "reason": reason if sep else ""})
    return discoveries


def immutable_learning_instance(agent: str | None, instance: str | None) -> bool:
    if os.environ.get("BROCCOLI_COMMS_IMMUTABLE_INSTANCE", "").lower() in {"1", "true", "yes", "on"}:
        return True
    if os.environ.get("BROCCOLI_COMMS_NON_LEARNING", "").lower() in {"1", "true", "yes", "on"}:
        return True
    immutable = get_toml_config("learning", "immutable_instances", []) or []
    names = {str(item) for item in immutable if isinstance(item, str)} if isinstance(immutable, list) else set()
    if (agent and agent in names) or (instance and instance in names):
        return True
    if agent:
        try:
            spec = (load_config().get("agents") or {}).get(agent)
            if isinstance(spec, dict) and (spec.get("immutable") or spec.get("non_learning")):
                return True
        except SystemExit:
            raise
        except Exception:
            pass
    return False


def approval_fallback_markdown(approval: dict) -> str:
    approval_id = _single_line_message_part(approval.get("approval_id"), 80)
    task_id = _single_line_message_part(approval.get("task_id"), 80)
    agent = _single_line_message_part(approval.get("submitter_profile"), 80)
    instance = _single_line_message_part(approval.get("submitter_instance_id") or "unknown", 80)
    chain = _single_line_message_part(approval.get("task_chain_id") or approval.get("root_task_id") or approval.get("task_id"), 80)
    summary = _single_line_message_part(approval.get("result_summary") or "", 500)
    command = f"broccoli-comms task approval review {approval_id} --result good|bad|need_improvements"
    message = f"Approval required: Task {task_id} needs your attention; approval={approval_id}; agent={agent}; instance={instance}; task_chain={chain}; summary={summary}; action={command}"
    return _single_line_message_part(message, 4000)


def notify_approval_request(kernel: LearningKernel, approval: dict) -> dict:
    message = approval_fallback_markdown(approval)
    metadata = {
        "content_type": "application/vnd.broccoli.task-approval+json",
        "kind": "task_completion_approval_request",
        "approval_id": approval["approval_id"],
        "task_id": approval["task_id"],
        "task_chain_id": approval.get("task_chain_id"),
        "root_task_id": approval.get("root_task_id"),
        "task_version_at_submission": approval.get("task_version_at_submission"),
        "created_event_seq": approval.get("created_event_seq"),
        "event_seq_at_submission": approval.get("event_seq_at_submission"),
        "agent_profile": approval.get("submitter_profile"),
        "agent_instance_id": approval.get("submitter_instance_id"),
        "result_summary": approval.get("result_summary"),
        "acceptance_summary": approval.get("acceptance_summary"),
        "reusable_discoveries": approval.get("reusable_discoveries") or [],
        "clarification_count": approval.get("clarification_count"),
        "correction_count": approval.get("correction_count"),
        "need_improvements_count": approval.get("need_improvements_count"),
        "first_pass_success": approval.get("first_pass_success"),
        "created_at": approval.get("created_at"),
        "source": "system/task-kernel",
        "sender_source": "system",
    }
    ui_sent = False
    ui_result = None
    ui_error = None
    sender_name = approval.get("submitter_profile") or "task-kernel"
    try:
        ui_result = tracker_rpc("send_message", {"agent_name": UI_AGENT_NAME, "message": message, "metadata": metadata, "sender_name": sender_name})
        if not ui_result:
            raise RuntimeError("tracker RPC send_message failed")
        kernel.tasks.record_approval_notification(approval["approval_id"], True, "agent-communicator")
        ui_sent = True
    except Exception as e:
        ui_error = str(e)
        kernel.tasks.record_approval_notification(approval["approval_id"], False, str(e))
    participant_results = []
    try:
        task = kernel.tasks.show(approval["task_id"], include_participants=True)
        recipients = []
        def append_approval_recipient(agent: str | None) -> None:
            if agent and agent != UI_AGENT_NAME and agent not in recipients:
                recipients.append(agent)
        append_approval_recipient(approval.get("submitter_profile"))
        append_approval_recipient(task.get("assigned_agent"))
        for participant in task.get("participants") or []:
            if participant.get("status") == "active" and participant.get("role") in {"assignee", "reviewer", "verifier", "coordinator"}:
                append_approval_recipient(participant.get("agent"))
        for recipient in recipients:
            participant_metadata = {**metadata, "recipient_agent": recipient, "recipient_kind": "task_participant"}
            try:
                participant_result = tracker_rpc("send_message", {"agent_name": recipient, "message": message, "metadata": participant_metadata, "sender_name": sender_name})
                if not participant_result:
                    raise RuntimeError("tracker RPC send_message failed")
                event = kernel.tasks.record_approval_notification(approval["approval_id"], True, f"participant:{recipient}")
                participant_results.append({"agent": recipient, "sent": True, "result": participant_result, "event": event})
            except Exception as participant_error:
                event = kernel.tasks.record_approval_notification(approval["approval_id"], False, f"participant:{recipient}: {participant_error}")
                participant_results.append({"agent": recipient, "sent": False, "error": str(participant_error), "event": event})
    except Exception as e:
        participant_results.append({"agent": None, "sent": False, "error": str(e)})
    payload = {"sent": ui_sent, "result": ui_result, "participant_notifications": participant_results}
    if ui_error:
        payload["error"] = ui_error
    return payload


def _require_chain_summary_before_submit(kernel: LearningKernel, task_chain_id: str | None, root_task_id: str | None) -> None:
    if not task_chain_id or not root_task_id:
        raise ValueError("submit-completion is only for task-chain/scoped-phase completion; pass --task-chain-id and --root-task-id after running task summarize-chain")
    summary = kernel.tasks.latest_chain_summary(root_task_id)
    if not summary or summary.get("task_chain_id") != task_chain_id:
        raise ValueError("run `broccoli-comms task summarize-chain <task_chain_id> --root-task-id <root_task_id>` before submit-completion")


def task_submit_completion(args: argparse.Namespace) -> None:
    kernel = learning_kernel()
    agent = args.agent or os.environ.get("AGENT_NAME") or "agent"
    instance = args.instance or os.environ.get("AGENT_ID") or os.environ.get("AGENT_UUID")
    try:
        _require_chain_summary_before_submit(kernel, args.task_chain_id, args.root_task_id)
        payload = kernel.tasks.submit_completion(
            args.task_id,
            agent=agent,
            agent_instance_id=instance,
            task_chain_id=args.task_chain_id,
            root_task_id=args.root_task_id,
            result_summary=args.summary,
            acceptance_summary=args.acceptance_summary,
            reusable_discoveries=_parse_discoveries(args.discovery),
            clarification_count=args.clarification_count,
            correction_count=args.correction_count,
            need_improvements_count=args.need_improvements_count,
            first_pass_success=args.first_pass_success,
            idempotency_key=args.idempotency_key,
            non_learning=immutable_learning_instance(agent, instance),
        )
        if not payload.get("idempotent"):
            payload["notification"] = notify_approval_request(kernel, payload["approval"])
    except (KeyError, ValueError) as e:
        raise SystemExit(str(e))
    _print_payload(payload, args.json)


def task_approval_list(args: argparse.Namespace) -> None:
    _print_payload(learning_kernel().tasks.list_approvals(status=args.status), True)


def task_approval_show(args: argparse.Namespace) -> None:
    try:
        _print_payload(learning_kernel().tasks.show_approval(args.approval_id), True)
    except KeyError:
        raise SystemExit(f"approval not found: {args.approval_id}")


def task_approval_review(args: argparse.Namespace) -> None:
    actor = getattr(args, "actor", None) or os.environ.get("AGENT_NAME") or "user"
    kernel = learning_kernel()
    try:
        payload = kernel.tasks.review_completion(args.approval_id, args.result, next_step=args.next_step, notes=args.notes, status=args.status, task_version_at_submission=args.task_version_at_submission, actor=actor)
    except (KeyError, ValueError) as e:
        raise SystemExit(str(e))
    task_id = payload.get("task", {}).get("task_id")
    if task_id and not payload.get("idempotent"):
        notify_payload = kernel.tasks.show(task_id, include_participants=True)
        notify_payload["ready_dependents"] = kernel.tasks.ready_dependents(task_id, include_participants=True)
        payload["notification"] = notify_task_update(notify_payload, actor, {"status": payload.get("task", {}).get("status"), "result_status": args.result})
    _print_payload(payload, args.json)


def events_list(args: argparse.Namespace) -> None:
    _print_payload(learning_kernel().events(task_id=args.task_id, subject_id=args.subject_id, limit=args.limit), True)


def doctor(args: argparse.Namespace) -> None:
    payload = doctor_payload()
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for check in payload["checks"]:
            marker = {"ok": "OK", "warning": "WARN", "error": "FAIL"}.get(check["status"], check["status"].upper())
            detail = check.get("path") or check.get("command") or ""
            suffix = f" ({detail})" if detail else ""
            version = f" [{check['version']}]" if check.get("version") else ""
            print(f"{marker}: {check['name']}: {check['message']}{suffix}{version}")
        if not payload["ok"]:
            print("doctor failed: fix FAIL checks. Nix packages include runtime dependencies such as tmux; manual/non-Nix installs must provide required executables on PATH.", file=sys.stderr)
    if not payload["ok"]:
        raise SystemExit(1)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "agent-tracker":
        agent_tracker(argparse.Namespace(tracker_args=sys.argv[2:]))
        return
    parser = argparse.ArgumentParser(description="Standalone Broccoli Comms runtime")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("start").set_defaults(func=start)
    sub.add_parser("ui").set_defaults(func=ui)
    sub.add_parser("open").set_defaults(func=ui)
    sub.add_parser("attach").set_defaults(func=attach)
    status_parser = sub.add_parser("status")
    status_parser.add_argument("--json", action="store_true", help="Emit stable JSON runtime status")
    status_parser.set_defaults(func=status)
    sub.add_parser("stop").set_defaults(func=stop)
    doctor_parser = sub.add_parser("doctor", help="Check new-machine/runtime readiness")
    doctor_parser.add_argument("--json", action="store_true", help="Emit JSON doctor results")
    doctor_parser.set_defaults(func=doctor)

    run_parser = sub.add_parser("run", help="Run locally, or with --host publish remote_run_request and wait for remote_run_result")
    run_parser.add_argument("name", help="Agent profile name")
    run_parser.add_argument("--cwd", help="Source working directory (defaults to current directory)")
    run_parser.add_argument("--scope", help="Optional task scope filter for bootstrap")
    run_parser.add_argument("--host", help="Remote registry host/tracker id; publish remote_run_request instead of launching locally")
    run_parser.add_argument("--timeout", type=float, default=30.0, help="Seconds to wait for remote_run_result with --host")
    run_parser.add_argument("--swarm", action="append", help="Swarm membership name; repeat with --role for multiple swarms")
    run_parser.add_argument("--role", action="append", choices=sorted(VALID_SWARM_ROLES), help="Swarm role for the preceding --swarm")
    run_parser.add_argument("--json", action="store_true", help="Emit JSON start result")
    run_parser.add_argument("--default-agent-command", help="Command to use when NAME is not configured and no command is passed after --")
    run_parser.add_argument("command", nargs=argparse.REMAINDER, help="Command to run after --")
    run_parser.set_defaults(func=run)

    agent_tracker_parser = sub.add_parser("agent-tracker", help="Run agent-tracker-ctl against the Broccoli Comms private runtime", add_help=False)
    agent_tracker_parser.add_argument("tracker_args", nargs=argparse.REMAINDER)
    agent_tracker_parser.set_defaults(func=agent_tracker)

    task = sub.add_parser("task", help="Manage durable local tasks")
    task_sub = task.add_subparsers(dest="task_command", required=True)
    task_create_parser = task_sub.add_parser("create")
    task_create_parser.add_argument("--title", required=True)
    task_create_parser.add_argument("--description")
    task_create_parser.add_argument("--agent")
    task_create_parser.add_argument("--scope")
    task_create_parser.add_argument("--next-step")
    task_create_parser.add_argument("--acceptance", action="append")
    task_create_parser.add_argument("--reviewer", action="append", help="Default reviewer participant agent; repeatable")
    task_create_parser.add_argument("--verifier", action="append", help="Default verifier participant agent; repeatable")
    task_create_parser.add_argument("--coordinator", action="append", help="Default coordinator participant agent; repeatable")
    task_create_parser.add_argument("--participant", action="append", help="Default participant as role:agent; repeatable")
    task_create_parser.add_argument("--depends-on", help="Comma-separated dependency task IDs")
    task_create_parser.add_argument("--task-chain-id", help="Apply chain-level default participants while creating this task")
    task_create_parser.add_argument("--root-task-id", help="Root task id for inherited/default participants")
    task_create_parser.add_argument("--priority", default="normal")
    task_create_parser.add_argument("--json", action="store_true")
    task_create_parser.set_defaults(func=task_create)
    task_show_parser = task_sub.add_parser("show")
    task_show_parser.add_argument("task_id")
    task_show_parser.add_argument("--include-participants", action="store_true")
    task_show_parser.add_argument("--json", action="store_true")
    task_show_parser.set_defaults(func=task_show)
    task_list_parser = task_sub.add_parser("list")
    task_list_parser.add_argument("--agent")
    task_list_parser.add_argument("--scope")
    task_list_parser.add_argument("--status")
    task_list_parser.add_argument("--participant-role", help="Comma-separated participant roles to match for --agent, e.g. reviewer,verifier")
    task_list_parser.add_argument("--include-archived", action="store_true")
    task_list_parser.add_argument("--include-participants", action="store_true")
    task_list_parser.add_argument("--json", action="store_true")
    task_list_parser.set_defaults(func=task_list)
    chain_defaults_parser = task_sub.add_parser("chain-defaults")
    chain_defaults_sub = chain_defaults_parser.add_subparsers(dest="chain_defaults_command", required=True)
    chain_defaults_set_parser = chain_defaults_sub.add_parser("set")
    chain_defaults_set_parser.add_argument("task_chain_id")
    chain_defaults_set_parser.add_argument("--agent", required=True)
    chain_defaults_set_parser.add_argument("--role", required=True, choices=["assignee", "reviewer", "verifier", "coordinator", "observer", "specialist"])
    chain_defaults_set_parser.add_argument("--root-task-id")
    chain_defaults_set_parser.add_argument("--status", choices=["active", "inactive"])
    chain_defaults_set_parser.add_argument("--json", action="store_true")
    chain_defaults_set_parser.set_defaults(func=task_chain_default_participant_set)
    chain_defaults_list_parser = chain_defaults_sub.add_parser("list")
    chain_defaults_list_parser.add_argument("task_chain_id")
    chain_defaults_list_parser.add_argument("--json", action="store_true")
    chain_defaults_list_parser.set_defaults(func=task_chain_default_participant_list)
    task_participant_parser = task_sub.add_parser("participant")
    task_participant_sub = task_participant_parser.add_subparsers(dest="participant_command", required=True)
    participant_list_parser = task_participant_sub.add_parser("list")
    participant_list_parser.add_argument("task_id")
    participant_list_parser.add_argument("--json", action="store_true")
    participant_list_parser.set_defaults(func=task_participant_list)
    participant_add_parser = task_participant_sub.add_parser("add")
    participant_add_parser.add_argument("task_id")
    participant_add_parser.add_argument("--agent", required=True)
    participant_add_parser.add_argument("--role", required=True, choices=["assignee", "reviewer", "verifier", "coordinator", "observer", "specialist"])
    participant_add_parser.add_argument("--instance")
    participant_add_parser.add_argument("--task-chain-id")
    participant_add_parser.add_argument("--root-task-id")
    participant_add_parser.add_argument("--status", choices=["active", "inactive"])
    participant_add_parser.add_argument("--json", action="store_true")
    participant_add_parser.set_defaults(func=task_participant_add)
    participant_update_parser = task_participant_sub.add_parser("update")
    participant_update_parser.add_argument("participant_id")
    participant_update_parser.add_argument("--status", choices=["active", "inactive"])
    participant_update_parser.add_argument("--json", action="store_true")
    participant_update_parser.set_defaults(func=task_participant_update)
    participant_remove_parser = task_participant_sub.add_parser("remove")
    participant_remove_parser.add_argument("participant_id")
    participant_remove_parser.add_argument("--json", action="store_true")
    participant_remove_parser.set_defaults(func=task_participant_remove)
    task_next_parser = task_sub.add_parser("next")
    task_next_parser.add_argument("--agent")
    task_next_parser.add_argument("--scope")
    task_next_parser.add_argument("--participant-role", help="Comma-separated participant roles to match for --agent; default preserves legacy assignee behavior")
    task_next_parser.add_argument("--include-profile", action="store_true")
    task_next_parser.add_argument("--json", action="store_true")
    task_next_parser.set_defaults(func=task_next)
    task_update_parser = task_sub.add_parser("update")
    task_update_parser.add_argument("task_id")
    task_update_parser.add_argument("--status", choices=sorted({"planning", "queued", "ready", "working", "blocked", "review", "done", "validated", "archived"}))
    task_update_parser.add_argument("--next-step")
    task_update_parser.add_argument("--blocked-reason")
    task_update_parser.add_argument("--result-summary")
    task_update_parser.add_argument("--assign-agent")
    task_update_parser.add_argument("--json", action="store_true")
    task_update_parser.set_defaults(func=task_update)
    task_result_parser = task_sub.add_parser("mark-result")
    task_result_parser.add_argument("task_id")
    task_result_parser.add_argument("--result", required=True, choices=["good", "bad", "need_improvements"])
    task_result_parser.add_argument("--notes")
    task_result_parser.add_argument("--next-step", help="Required remediation/follow-up for bad or need_improvements")
    task_result_parser.add_argument("--status", choices=["ready", "working", "blocked", "validated"], help="Optional resulting task status; non-good results allow ready/working/blocked")
    task_result_parser.add_argument("--json", action="store_true")
    task_result_parser.set_defaults(func=task_mark_result)
    task_summary_parser = task_sub.add_parser("summarize-chain")
    task_summary_parser.add_argument("task_chain_id")
    task_summary_parser.add_argument("--root-task-id", help="Override/preserve root task lineage for the summarized chain")
    task_summary_parser.add_argument("--next-task-chain-id", help="Optional next chain that should resume from this summary")
    task_summary_parser.add_argument("--json", action="store_true")
    task_summary_parser.set_defaults(func=task_summarize_chain)
    task_bootstrap_parser = task_sub.add_parser("bootstrap")
    task_bootstrap_parser.add_argument("--agent")
    task_bootstrap_parser.add_argument("--scope")
    task_bootstrap_parser.add_argument("--cwd")
    task_bootstrap_parser.add_argument("--instance")
    task_bootstrap_parser.add_argument("--write-context-dir", help="Write bootstrap context as memory.md/habits.md/expertise.md plus skills/<skill-name>/SKILL.md files in this directory")
    task_bootstrap_parser.add_argument("--json", action="store_true")
    task_bootstrap_parser.set_defaults(func=task_bootstrap)
    task_submit_parser = task_sub.add_parser("submit-completion")
    task_submit_parser.add_argument("task_id")
    task_submit_parser.add_argument("--summary", required=True)
    task_submit_parser.add_argument("--acceptance-summary")
    task_submit_parser.add_argument("--discovery", action="append", help="Reusable discovery as label=value or label=value:reason")
    task_submit_parser.add_argument("--agent")
    task_submit_parser.add_argument("--instance")
    task_submit_parser.add_argument("--task-chain-id")
    task_submit_parser.add_argument("--root-task-id")
    task_submit_parser.add_argument("--clarification-count", type=int)
    task_submit_parser.add_argument("--correction-count", type=int)
    task_submit_parser.add_argument("--need-improvements-count", type=int)
    task_submit_parser.add_argument("--first-pass-success", action=argparse.BooleanOptionalAction, default=None)
    task_submit_parser.add_argument("--idempotency-key")
    task_submit_parser.add_argument("--json", action="store_true")
    task_submit_parser.set_defaults(func=task_submit_completion)
    task_approval_parser = task_sub.add_parser("approval")
    task_approval_sub = task_approval_parser.add_subparsers(dest="approval_command", required=True)
    approval_list_parser = task_approval_sub.add_parser("list")
    approval_list_parser.add_argument("--status", choices=["pending", "decided", "superseded"])
    approval_list_parser.add_argument("--json", action="store_true")
    approval_list_parser.set_defaults(func=task_approval_list)
    approval_show_parser = task_approval_sub.add_parser("show")
    approval_show_parser.add_argument("approval_id")
    approval_show_parser.add_argument("--json", action="store_true")
    approval_show_parser.set_defaults(func=task_approval_show)
    approval_review_parser = task_approval_sub.add_parser("review")
    approval_review_parser.add_argument("approval_id")
    approval_review_parser.add_argument("--result", required=True, choices=["good", "bad", "need_improvements"])
    approval_review_parser.add_argument("--next-step")
    approval_review_parser.add_argument("--notes")
    approval_review_parser.add_argument("--status", choices=["ready", "working", "blocked", "validated"])
    approval_review_parser.add_argument("--task-version-at-submission", type=int)
    approval_review_parser.add_argument("--actor", help="Reviewer/verifier agent name to record when AGENT_NAME is unavailable (used by trusted TUI approval cards)")
    approval_review_parser.add_argument("--json", action="store_true")
    approval_review_parser.set_defaults(func=task_approval_review)

    state_cmd = sub.add_parser("state", help="Manage durable working state checkpoints")
    state_sub = state_cmd.add_subparsers(dest="state_command", required=True)
    state_set_parser = state_sub.add_parser("set")
    state_set_parser.add_argument("--task", dest="task_id", required=True)
    state_set_parser.add_argument("--agent")
    state_set_parser.add_argument("--status", default="working", choices=["working", "blocked", "waiting", "review", "done"])
    state_set_parser.add_argument("--current-activity")
    state_set_parser.add_argument("--next-step")
    state_set_parser.add_argument("--blockers")
    state_set_parser.add_argument("--notes")
    state_set_parser.add_argument("--instance")
    state_set_parser.add_argument("--task-chain-id", help="Stable task chain identifier for parallel same-profile work")
    state_set_parser.add_argument("--root-task-id", help="Root task ID for this task chain")
    state_set_parser.add_argument("--clarification-count", type=int, help="Bounded count of user clarifications before validation")
    state_set_parser.add_argument("--correction-count", type=int, help="Bounded count of user corrections before validation")
    state_set_parser.add_argument("--need-improvements-count", type=int, help="Bounded count of need_improvements cycles")
    state_set_parser.add_argument("--first-pass-success", action=argparse.BooleanOptionalAction, default=None, help="Whether no corrections were needed before good validation")
    state_set_parser.add_argument("--stale-after")
    state_set_parser.add_argument("--json", action="store_true")
    state_set_parser.set_defaults(func=state_set)
    state_show_parser = state_sub.add_parser("show")
    state_show_parser.add_argument("--task", dest="task_id", required=True)
    state_show_parser.add_argument("--agent")
    state_show_parser.add_argument("--json", action="store_true")
    state_show_parser.set_defaults(func=state_show)
    state_list_parser = state_sub.add_parser("list")
    state_list_parser.add_argument("--agent")
    state_list_parser.add_argument("--task", dest="task_id")
    state_list_parser.add_argument("--stale-after")
    state_list_parser.add_argument("--json", action="store_true")
    state_list_parser.set_defaults(func=state_list)
    state_clear_parser = state_sub.add_parser("clear")
    state_clear_parser.add_argument("--task", dest="task_id", required=True)
    state_clear_parser.add_argument("--agent")
    state_clear_parser.add_argument("--json", action="store_true")
    state_clear_parser.set_defaults(func=state_clear)

    user_profile = sub.add_parser("user-profile", help="Show read-only local user profile")
    user_profile_sub = user_profile.add_subparsers(dest="user_profile_command", required=True)
    user_profile_show_parser = user_profile_sub.add_parser("show")
    user_profile_show_parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    user_profile_show_parser.add_argument("--json", action="store_true")
    user_profile_show_parser.set_defaults(func=user_profile_show)

    memory = sub.add_parser("memory", help="Manage durable memory proposals, approvals, and active records", description="Manage durable memory proposals, approvals, and active records.")
    memory_sub = memory.add_subparsers(dest="memory_command", required=True)
    memory_propose_parser = memory_sub.add_parser("propose", help="Create a new proposal, edit proposal, or archive proposal")
    memory_propose_parser.add_argument("memory_id", nargs="?", help="Existing memory to edit; combine with --archive to propose removal. Omit to propose a new memory.")
    memory_propose_parser.add_argument("--archive", action="store_true", help="With memory_id, propose archiving/removing the target memory instead of editing it")
    memory_propose_parser.add_argument("--reason", help="Reason for an archive/removal proposal")
    memory_propose_parser.add_argument("--type", choices=["fact", "habit", "episode", "expertise", "skill"], help="Memory type for new proposals, or replacement type for edit proposals")
    memory_propose_parser.add_argument("--scope", default="global", help="Memory scope (default: global)")
    memory_propose_parser.add_argument("--subject-agent", help="Agent the memory applies to")
    memory_propose_parser.add_argument("--title", help="Memory title for new/edit proposals")
    memory_propose_parser.add_argument("--description", help="Description of the memory (used for skills front-matter)")
    memory_propose_parser.add_argument("--body", help="Memory body for new/edit proposals")
    memory_propose_parser.add_argument("--source-task", help="Validated source task for proposal provenance")
    memory_propose_parser.add_argument("--trusted-manual", action="store_true", help="Create a trusted manual proposal as a verified trusted actor")
    memory_propose_parser.add_argument("--agent", help="Override proposing agent identity")
    memory_propose_parser.add_argument("--instance", help="Override proposing agent instance id")
    memory_propose_parser.add_argument("--idempotency-key", help="Deduplicate repeated new-memory proposals from the same proposer")
    memory_propose_parser.add_argument("--tag", action="append", help="Tag to attach; repeat for multiple tags")
    memory_propose_parser.add_argument("--metadata-json", help="JSON object metadata; expertise supports task_family/tools/evidence_task_ids/validation_count/last_validated_at/known_limits")
    memory_propose_parser.add_argument("--expected-version", type=int, help="Expected current target version for edit/archive proposals")
    memory_propose_parser.add_argument("--json", action="store_true")
    memory_propose_parser.set_defaults(func=memory_propose)
    memory_propose_edit_parser = memory_sub.add_parser("propose-edit", help="Legacy alias: create a pending edit proposal for an existing memory")
    memory_propose_edit_parser.add_argument("memory_id", help="Existing memory to edit")
    memory_propose_edit_parser.add_argument("--type", choices=["fact", "habit", "episode", "expertise", "skill"])
    memory_propose_edit_parser.add_argument("--scope")
    memory_propose_edit_parser.add_argument("--subject-agent")
    memory_propose_edit_parser.add_argument("--title")
    memory_propose_edit_parser.add_argument("--description")
    memory_propose_edit_parser.add_argument("--body")
    memory_propose_edit_parser.add_argument("--source-task")
    memory_propose_edit_parser.add_argument("--agent")
    memory_propose_edit_parser.add_argument("--instance")
    memory_propose_edit_parser.add_argument("--tag", action="append")
    memory_propose_edit_parser.add_argument("--metadata-json")
    memory_propose_edit_parser.add_argument("--expected-version", type=int)
    memory_propose_edit_parser.add_argument("--json", action="store_true")
    memory_propose_edit_parser.set_defaults(func=memory_propose_edit)
    memory_approve_parser = memory_sub.add_parser("approve", help="Legacy alias: approve a pending memory proposal as a trusted actor")
    memory_approve_parser.add_argument("memory_id", help="Pending proposal id to approve")
    memory_approve_parser.add_argument("version", type=int, help="Specific version to approve")
    memory_approve_parser.add_argument("--json", action="store_true")
    memory_approve_parser.set_defaults(func=memory_approve)
    memory_decide_parser = memory_sub.add_parser("decide", help="Approve or reject a pending proposal as a trusted actor")
    memory_decide_parser.add_argument("memory_id", help="Pending proposal id to decide")
    memory_decide_parser.add_argument("decision", choices=["approve", "reject"], help="Decision to apply to the pending proposal")
    memory_decide_parser.add_argument("version", type=int, help="Specific version to decide")
    memory_decide_parser.add_argument("--reason", help="Reason for rejecting the proposal")
    memory_decide_parser.add_argument("--json", action="store_true")
    memory_decide_parser.set_defaults(func=memory_decide)
    memory_edit_parser = memory_sub.add_parser("edit", help="Directly edit pending/active memory as a trusted actor; agents should usually use memory propose <id>")
    memory_edit_parser.add_argument("memory_id", help="Pending or active memory to edit directly")
    memory_edit_parser.add_argument("--type", choices=["fact", "habit", "episode", "expertise", "skill"])
    memory_edit_parser.add_argument("--scope")
    memory_edit_parser.add_argument("--subject-agent")
    memory_edit_parser.add_argument("--title")
    memory_edit_parser.add_argument("--description")
    memory_edit_parser.add_argument("--body")
    memory_edit_parser.add_argument("--source-task")
    memory_edit_parser.add_argument("--trusted-manual", action="store_true", default=None)
    memory_edit_parser.add_argument("--tag", action="append")
    memory_edit_parser.add_argument("--metadata-json")
    memory_edit_parser.add_argument("--expected-version", type=int)
    memory_edit_parser.add_argument("--json", action="store_true")
    memory_edit_parser.set_defaults(func=memory_edit)
    memory_rollback_parser = memory_sub.add_parser("rollback", help="Directly roll back pending/active memory to an earlier version as a trusted actor")
    memory_rollback_parser.add_argument("memory_id", help="Pending or active memory to roll back")
    memory_rollback_parser.add_argument("--to-version", type=int, required=True, help="Previous memory version to restore")
    memory_rollback_parser.add_argument("--expected-version", type=int)
    memory_rollback_parser.add_argument("--json", action="store_true")
    memory_rollback_parser.set_defaults(func=memory_rollback)
    memory_reject_parser = memory_sub.add_parser("reject", help="Legacy alias: reject a pending memory proposal as a trusted actor")
    memory_reject_parser.add_argument("memory_id", help="Pending proposal id to reject")
    memory_reject_parser.add_argument("version", type=int, help="Specific version to reject")
    memory_reject_parser.add_argument("--reason", help="Reason for rejection")
    memory_reject_parser.add_argument("--json", action="store_true")
    memory_reject_parser.set_defaults(func=memory_reject)
    memory_revoke_parser = memory_sub.add_parser("revoke", help="Directly revoke an active memory as a trusted actor; agents should usually use memory propose <id> --archive")
    memory_revoke_parser.add_argument("memory_id", help="Active memory id to revoke")
    memory_revoke_parser.add_argument("--reason", help="Reason for revocation")
    memory_revoke_parser.add_argument("--expected-version", type=int)
    memory_revoke_parser.add_argument("--json", action="store_true")
    memory_revoke_parser.set_defaults(func=memory_revoke)
    memory_list_parser = memory_sub.add_parser("list", help="List memory records by status/type/scope/agent")
    memory_list_parser.add_argument("--scope")
    memory_list_parser.add_argument("--type", choices=["fact", "habit", "episode", "expertise", "skill"])
    memory_list_parser.add_argument("--status", choices=["pending", "active", "approved", "rejected", "revoked", "superseded"])
    memory_list_parser.add_argument("--agent")
    memory_list_parser.add_argument("--json", action="store_true")
    memory_list_parser.set_defaults(func=memory_list)
    memory_approvals_parser = memory_sub.add_parser("approvals", help="List pending proposals and active approved memory for review UIs")
    memory_approvals_parser.add_argument("--scope")
    memory_approvals_parser.add_argument("--type", choices=["fact", "habit", "episode", "expertise", "skill"])
    memory_approvals_parser.add_argument("--agent")
    memory_approvals_parser.add_argument("--json", action="store_true")
    memory_approvals_parser.set_defaults(func=memory_approvals)
    memory_search_parser = memory_sub.add_parser("search", help="Search active approved memory")
    memory_search_parser.add_argument("--query", required=True)
    memory_search_parser.add_argument("--scope")
    memory_search_parser.add_argument("--json", action="store_true")
    memory_search_parser.set_defaults(func=memory_search)
    memory_show_parser = memory_sub.add_parser("show", help="Show one memory record")
    memory_show_parser.add_argument("memory_id", help="Memory id to show")
    memory_show_parser.add_argument("--version", type=int, help="Specific version to show")
    memory_show_parser.add_argument("--json", action="store_true")
    memory_show_parser.set_defaults(func=memory_show)
    memory_history_parser = memory_sub.add_parser("history", help="Show memory version/event history")
    memory_history_parser.add_argument("memory_id")
    memory_history_parser.add_argument("--json", action="store_true")
    memory_history_parser.set_defaults(func=memory_history)
    memory_budget_parser = memory_sub.add_parser("budget", help="Show memory budget/limits for an agent")
    memory_budget_parser.add_argument("--agent")
    memory_budget_parser.add_argument("--scope")
    memory_budget_parser.add_argument("--json", action="store_true")
    memory_budget_parser.set_defaults(func=memory_budget)

    events = sub.add_parser("events", help="Query append-only task/state event log")
    events_sub = events.add_subparsers(dest="events_command", required=True)
    events_list_parser = events_sub.add_parser("list")
    events_list_parser.add_argument("--task", dest="task_id")
    events_list_parser.add_argument("--subject-id")
    events_list_parser.add_argument("--limit", type=int, default=100)
    events_list_parser.add_argument("--json", action="store_true", help="Accepted for CLI consistency; events are always JSON")
    events_list_parser.set_defaults(func=events_list)

    registry = sub.add_parser("registry", help="Manage a Broccoli Comms agent-registry process")
    registry_sub = registry.add_subparsers(dest="registry_command", required=True)
    registry_start_parser = registry_sub.add_parser("start", help="Start a managed local agent-registry")
    registry_start_parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    registry_start_parser.add_argument("--port", type=int, default=8080, help="Bind port (default: 8080)")
    registry_start_parser.add_argument("--name", default="local", help="Logical registry name")
    auth_group = registry_start_parser.add_mutually_exclusive_group()
    auth_group.add_argument("--auth", action="store_true", help="Require bearer auth")
    auth_group.add_argument("--noauth", action="store_true", help="Disable auth for loopback local/dev use")
    registry_start_parser.add_argument("--token", help="Bearer token value (prefer --token-file)")
    registry_start_parser.add_argument("--token-file", help="File containing bearer token")
    registry_start_parser.add_argument("--state-path", help="Registry state path")
    registry_start_parser.add_argument("--stale-seconds", type=int, default=60, help="Tracker stale threshold")
    registry_start_parser.add_argument("--gone-seconds", type=int, default=180, help="Tracker gone threshold")
    registry_start_parser.add_argument("--foreground", action="store_true", help="Run in foreground")
    registry_start_parser.add_argument("--force", action="store_true", help="Replace stale/running pid file")
    registry_start_parser.set_defaults(func=registry_start)
    registry_stop_parser = registry_sub.add_parser("stop", help="Stop the managed registry")
    registry_stop_parser.add_argument("--force", action="store_true", help="SIGKILL if graceful stop fails")
    registry_stop_parser.set_defaults(func=registry_stop)
    registry_status_parser = registry_sub.add_parser("status", help="Show managed registry status")
    registry_status_parser.add_argument("--json", action="store_true", help="Emit JSON")
    registry_status_parser.set_defaults(func=registry_status)
    registry_sub.add_parser("health", help="GET /healthz").set_defaults(func=registry_health)
    registry_agents_parser = registry_sub.add_parser("agents", help="GET /agents")
    registry_agents_parser.add_argument("--name", help="Filter by agent name")
    registry_agents_parser.add_argument("--hostname", help="Filter by tracker hostname")
    registry_agents_parser.add_argument("--status", help="Filter by agent/tracker status")
    registry_agents_parser.add_argument("--logical-identity", help="Filter by logical identity, e.g. agent-communicator")
    registry_agents_parser.add_argument("--service-kind", help="Filter by service kind, e.g. shared_service")
    registry_agents_parser.add_argument("--json", action="store_true", help="Emit raw JSON")
    registry_agents_parser.set_defaults(func=registry_agents)
    registry_trackers_parser = registry_sub.add_parser("trackers", help="GET /trackers")
    registry_trackers_parser.add_argument("--json", action="store_true", help="Emit raw JSON")
    registry_trackers_parser.set_defaults(func=registry_trackers)
    registry_add_parser = registry_sub.add_parser("add", help="Configure a registry URL for the private tracker")
    registry_add_parser.add_argument("--name", required=True, help="Registry name")
    registry_add_parser.add_argument("--url", required=True, help="Registry URL")
    add_auth_group = registry_add_parser.add_mutually_exclusive_group()
    add_auth_group.add_argument("--auth", action="store_true", help="Require token-file auth for this URL")
    add_auth_group.add_argument("--noauth", action="store_true", help="Save without auth token metadata")
    registry_add_parser.add_argument("--token-file", help="Token file path; token contents are not stored")
    registry_add_parser.add_argument("--replace", action="store_true", help="Replace existing registry with same name")
    registry_add_parser.set_defaults(func=registry_add)
    registry_list_parser = registry_sub.add_parser("list", help="List configured tracker registry URLs")
    registry_list_parser.add_argument("--json", action="store_true", help="Emit JSON")
    registry_list_parser.set_defaults(func=registry_list)
    registry_remove_parser = registry_sub.add_parser("remove", help="Remove a configured tracker registry URL")
    registry_remove_parser.add_argument("name")
    registry_remove_parser.add_argument("--missing-ok", action="store_true", help="Do not fail if missing")
    registry_remove_parser.set_defaults(func=registry_remove)
    registry_enable_parser = registry_sub.add_parser("enable", help="Enable a configured tracker registry URL")
    registry_enable_parser.add_argument("name")
    registry_enable_parser.set_defaults(func=registry_enable)
    registry_disable_parser = registry_sub.add_parser("disable", help="Disable a configured tracker registry URL")
    registry_disable_parser.add_argument("name")
    registry_disable_parser.set_defaults(func=registry_disable)
    registry_env_parser = registry_sub.add_parser("env", help="Show AGENT_REGISTRIES_JSON generated from saved registry URLs")
    registry_env_parser.add_argument("--json", action="store_true", help="Emit JSON")
    registry_env_parser.set_defaults(func=registry_env)

    agent = sub.add_parser("agent", help="Manage configured agents")
    agent_sub = agent.add_subparsers(dest="agent_command", required=True)
    agent_list_parser = agent_sub.add_parser("list", help="List configured, running, and optionally remote agents")
    agent_list_parser.add_argument("--json", action="store_true", help="Include config/runtime metadata in JSON output")
    agent_list_parser.add_argument("--include-remote", action="store_true", help="Include remote registry agents")
    agent_list_parser.add_argument("--configured-only", action="store_true", help="Show only saved configured agents")
    agent_list_parser.add_argument("--running-only", action="store_true", help="Show only running local agents")
    agent_list_parser.add_argument("--remote-only", action="store_true", help="Show only remote registry agents")
    agent_list_parser.set_defaults(func=agent_list)
    agent_status_parser = agent_sub.add_parser("status", help="Show one merged configured/running/remote agent row")
    agent_status_parser.add_argument("name", help="Agent name or target address")
    agent_status_parser.add_argument("--include-remote", action="store_true", help="Include remote registry agents")
    agent_status_parser.add_argument("--json", action="store_true", help="Emit JSON")
    agent_status_parser.set_defaults(func=agent_status)
    agent_copy_parser = agent_sub.add_parser("copy", help="Copy a configured/local/remote agent definition")
    agent_copy_parser.add_argument("source", help="Source agent name or target address")
    agent_copy_parser.add_argument("new_name", help="New local configured agent name")
    agent_copy_parser.add_argument("--immutable", action="store_true", help="Mark copy immutable/non-learning")
    agent_copy_parser.add_argument("--replace", action="store_true", help="Replace an existing saved definition")
    agent_copy_parser.add_argument("--json", action="store_true", help="Emit JSON")
    agent_copy_parser.set_defaults(func=agent_copy)
    agent_edit_parser = agent_sub.add_parser("edit", help="Update and restart a live managed agent")
    agent_edit_parser.add_argument("name", help="Existing live managed agent name")
    agent_edit_parser.add_argument("--rename", help="Rename the managed agent")
    agent_edit_parser.add_argument("--cwd", help="Source working directory")
    agent_edit_parser.add_argument("--scope", help="Bootstrap scope for subsequent restarts")
    agent_edit_parser.add_argument("--command", dest="command_string", help="Replacement command string")
    agent_edit_parser.add_argument("--swarm", action="append", help="Swarm membership name; repeat with --role for multiple swarms")
    agent_edit_parser.add_argument("--role", action="append", choices=sorted(VALID_SWARM_ROLES), help="Swarm role for the preceding --swarm")
    autostart_group = agent_edit_parser.add_mutually_exclusive_group()
    autostart_group.add_argument("--autostart", dest="autostart", action="store_true", help="Enable launch during broccoli-comms start")
    autostart_group.add_argument("--no-autostart", dest="autostart", action="store_false", help="Disable launch during broccoli-comms start")
    agent_edit_parser.add_argument("command", nargs=argparse.REMAINDER, help="Replacement command to run after --")
    agent_edit_parser.set_defaults(func=agent_edit)
    agent_remove_parser = agent_sub.add_parser("remove", help="Remove a configured agent and stop its managed window if running")
    agent_remove_parser.add_argument("name", help="Agent/window name")
    agent_remove_parser.set_defaults(func=agent_remove)
    agent_assign_swarm_parser = agent_sub.add_parser("assign-swarm", help="Assign existing live local agents into a swarm")
    agent_assign_swarm_parser.add_argument("swarm", help="Swarm name")
    agent_assign_swarm_parser.add_argument("--main", required=True, help="Live local main agent name")
    agent_assign_swarm_parser.add_argument("--subagent", action="append", default=[], help="Live local subagent name; repeatable")
    agent_assign_swarm_parser.add_argument("--json", action="store_true", help="Emit JSON")
    agent_assign_swarm_parser.set_defaults(func=agent_assign_swarm)
    agent_start_swarm_parser = agent_sub.add_parser("start-swarm", help="Start all configured local agents in a top-level configured swarm")
    agent_start_swarm_parser.add_argument("swarm", help="Configured swarm name")
    agent_start_swarm_parser.add_argument("--json", action="store_true", help="Emit JSON")
    agent_start_swarm_parser.set_defaults(func=agent_start_swarm)
    agent_restart_parser = agent_sub.add_parser("restart", help="Restart a configured agent window")
    agent_restart_parser.add_argument("name", help="Agent/window name")
    agent_restart_parser.set_defaults(func=agent_restart)
    agent_focus_parser = agent_sub.add_parser("focus", help="Focus a running managed agent window in the Broccoli tmux session")
    agent_focus_parser.add_argument("name", help="Agent/window name")
    agent_focus_parser.set_defaults(func=agent_focus)
    agent_attach_parser = agent_sub.add_parser("attach", help="Attach/switch to a running managed agent window in the Broccoli tmux session")
    agent_attach_parser.add_argument("name", help="Agent/window name")
    agent_attach_parser.set_defaults(func=agent_attach)
    args = parser.parse_args()
    if not hasattr(args, "func"):
        args.func = ui
    args.func(args)


if __name__ == "__main__":
    main()
