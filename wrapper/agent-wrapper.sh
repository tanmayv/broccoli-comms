#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: agent-wrapper <command> [args...]" >&2
  exit 2
fi

cmd="$1"
shift

obs_enabled=false
no_notify_with_send_keys=false
no_registry=false
args=()
for arg in "$@"; do
  case "$arg" in
    --obs) obs_enabled=true ;;
    --no-notify-with-send-keys) no_notify_with_send_keys=true ;;
    --no-registry) no_registry=true ;;
    *) args+=("$arg") ;;
  esac
done
set -- "${args[@]}"
# Keep --obs accepted for parity with the Home Manager wrapper. The standalone
# wrapper does not open observer panes yet.
if [[ "$obs_enabled" == "true" ]]; then
  :
fi

if [[ "${BROCCOLI_COMMS_TRACK_ACTIVE:-}" == "1" || "${AGENT_WRAPPER_DEPTH:-0}" != "0" ]]; then
  exec "$cmd" "$@"
fi

if [[ -z "${TMUX:-}" ]]; then
  exec "$cmd" "$@"
fi

pane_id="${TMUX_PANE:-}"
if [[ -z "$pane_id" ]]; then
  exec "$cmd" "$@"
fi

tmux_socket="${AGENT_TRACKER_TMUX_SOCKET:-${BROCCOLI_COMMS_TMUX_SOCKET:-}}"
if [[ -z "$tmux_socket" ]]; then
  tmux_socket="${TMUX%%,*}"
fi
tmux_cmd=(tmux)
if [[ -n "$tmux_socket" ]]; then
  tmux_cmd=(tmux -S "$tmux_socket")
fi
export AGENT_TRACKER_TMUX_SOCKET="${AGENT_TRACKER_TMUX_SOCKET:-$tmux_socket}"
export BROCCOLI_COMMS_TMUX_SOCKET="${BROCCOLI_COMMS_TMUX_SOCKET:-$tmux_socket}"

session_name=$("${tmux_cmd[@]}" display-message -p -t "$pane_id" '#S' 2>/dev/null || echo broccoli-comms)
wrapper_pid="$$"
suggested_name="${SUGGESTED_AGENT_NAME:-}"
agent_type="${AGENT_TYPE:-$(basename "$cmd")}"
agent_cmd="${AGENT_CMD:-$(basename "$cmd")}"
model_type="${AGENT_MODEL_TYPE:-${MODEL_TYPE:-${agent_type}}}"
agent_id="${AGENT_ID:-$(python3 - <<'PY'
import uuid
print(uuid.uuid4())
PY
)}"
export AGENT_ID="$agent_id"
swarms_json="${AGENT_SWARMS_JSON:-[]}"
current_cwd=$("${tmux_cmd[@]}" display-message -p -t "$pane_id" '#{pane_current_path}' 2>/dev/null || pwd)

rpc_register() {
  python3 - "$session_name" "$pane_id" "$wrapper_pid" "$tmux_socket" "$suggested_name" "$agent_type" "$agent_cmd" "$model_type" "$agent_id" "$no_notify_with_send_keys" "$no_registry" "$current_cwd" "$swarms_json" <<'PY'
import json, os, socket, sys
session, pane, wrapper_pid, tmux_socket, name, agent_type, agent_cmd, model_type, agent_id, no_notify, no_registry, cwd, swarms_json = sys.argv[1:]
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(3)

def get_socket():
    import os
    sock = os.environ.get("AGENT_TRACKER_SOCKET")
    if sock:
        return sock
    config_path = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "broccoli-comms/config.toml")
    runtime_dir = None
    try:
        with open(config_path, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("agent_tracker_socket"):
                    sock = line.split("=")[1].strip().strip('"').strip("'")
                elif line.startswith("runtime_dir"):
                    runtime_dir = line.split("=")[1].strip().strip('"').strip("'")
    except Exception:
        pass
    if sock:
        return sock
    if runtime_dir:
        return os.path.join(runtime_dir, "agent-tracker.sock")
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime:
        return os.path.join(xdg_runtime, "broccoli-comms/agent-tracker.sock")
    try:
        uid = os.getuid()
        return f"/tmp/{uid}/broccoli-comms/agent-tracker.sock"
    except Exception:
        import tempfile
        return os.path.join(tempfile.gettempdir(), "broccoli-comms/agent-tracker.sock")

s.connect(get_socket())
try:
    swarms = json.loads(swarms_json or "[]")
except Exception:
    swarms = []
req = {
  "jsonrpc": "2.0",
  "method": "register",
  "params": {
    "session": session,
    "tmux_pane": pane,
    "wrapper_pid": int(wrapper_pid),
    "tmux_socket": tmux_socket,
    "name": name,
    "agent_type": agent_type,
    "agent_cmd": agent_cmd,
    "model_type": model_type,
    "agent_id": agent_id,
    "no_notify_with_send_keys": no_notify.lower() == "true",
    "no_registry": no_registry.lower() == "true",
    "cwd": cwd,
    "swarms": swarms,
  },
  "id": 1,
}
s.sendall(json.dumps(req).encode())
s.shutdown(socket.SHUT_WR)
data = json.loads(s.recv(4096).decode())
if data.get("error"):
  raise SystemExit(data["error"].get("message", "register failed"))
print(data.get("result", ""))
PY
}

agent_name=""
if agent_name=$(rpc_register 2>/tmp/broccoli-comms-agent-wrapper.log); then
  :
else
  agent_name="${suggested_name:-$agent_cmd}"
fi

if [[ -n "$agent_name" ]]; then
  export AGENT_NAME="$agent_name"
  "${tmux_cmd[@]}" set-option -p -t "$pane_id" @agent_name "$agent_name" 2>/dev/null || true
  "${tmux_cmd[@]}" set-option -p -t "$pane_id" @agent_id "$agent_id" 2>/dev/null || true
  "${tmux_cmd[@]}" set-option -p -t "$pane_id" @agent_uuid "$agent_id" 2>/dev/null || true
  "${tmux_cmd[@]}" set-option -p -t "$pane_id" @agent_type "$agent_type" 2>/dev/null || true
  "${tmux_cmd[@]}" set-option -p -t "$pane_id" @agent_cmd "$agent_cmd" 2>/dev/null || true
  "${tmux_cmd[@]}" select-pane -t "$pane_id" -T "$agent_name" 2>/dev/null || true
fi

heartbeat() {
  while true; do
    current_cwd=$("${tmux_cmd[@]}" display-message -p -t "$pane_id" '#{pane_current_path}' 2>/dev/null || pwd)
    python3 - "$agent_id" "$wrapper_pid" "$current_cwd" <<'PY' >/dev/null 2>>/tmp/broccoli-comms-agent-wrapper.log || true
import json, os, socket, sys
agent_id, wrapper_pid, cwd = sys.argv[1:]
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(2)

def get_socket():
    import os
    sock = os.environ.get("AGENT_TRACKER_SOCKET")
    if sock:
        return sock
    config_path = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "broccoli-comms/config.toml")
    runtime_dir = None
    try:
        with open(config_path, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("agent_tracker_socket"):
                    sock = line.split("=")[1].strip().strip('"').strip("'")
                elif line.startswith("runtime_dir"):
                    runtime_dir = line.split("=")[1].strip().strip('"').strip("'")
    except Exception:
        pass
    if sock:
        return sock
    if runtime_dir:
        return os.path.join(runtime_dir, "agent-tracker.sock")
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime:
        return os.path.join(xdg_runtime, "broccoli-comms/agent-tracker.sock")
    try:
        uid = os.getuid()
        return f"/tmp/{uid}/broccoli-comms/agent-tracker.sock"
    except Exception:
        import tempfile
        return os.path.join(tempfile.gettempdir(), "broccoli-comms/agent-tracker.sock")

s.connect(get_socket())
s.sendall(json.dumps({"jsonrpc":"2.0","method":"heartbeat","params":{"agent_id":agent_id,"wrapper_pid":int(wrapper_pid),"cwd":cwd},"id":1}).encode())
s.shutdown(socket.SHUT_WR)
s.recv(1024)
PY
    sleep 5
  done
}
heartbeat &
heartbeat_pid=$!

# shellcheck disable=SC2329 # invoked by cleanup, which is invoked via EXIT trap
rpc_unregister() {
  python3 - "$pane_id" "$agent_id" <<'PY' >/dev/null 2>>/tmp/broccoli-comms-agent-wrapper.log || true
import json, os, socket, sys
pane_id, agent_id = sys.argv[1:]

def get_socket():
    import os
    sock = os.environ.get("AGENT_TRACKER_SOCKET")
    if sock:
        return sock
    config_path = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "broccoli-comms/config.toml")
    runtime_dir = None
    try:
        with open(config_path, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("agent_tracker_socket"):
                    sock = line.split("=")[1].strip().strip('"').strip("'")
                elif line.startswith("runtime_dir"):
                    runtime_dir = line.split("=")[1].strip().strip('"').strip("'")
    except Exception:
        pass
    if sock:
        return sock
    if runtime_dir:
        return os.path.join(runtime_dir, "agent-tracker.sock")
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime:
        return os.path.join(xdg_runtime, "broccoli-comms/agent-tracker.sock")
    try:
        uid = os.getuid()
        return f"/tmp/{uid}/broccoli-comms/agent-tracker.sock"
    except Exception:
        import tempfile
        return os.path.join(tempfile.gettempdir(), "broccoli-comms/agent-tracker.sock")

for params in ({"tmux_pane": pane_id}, {"agent_id": agent_id}):
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(get_socket())
        s.sendall(json.dumps({"jsonrpc":"2.0","method":"unregister","params":params,"id":1}).encode())
        s.shutdown(socket.SHUT_WR)
        data = json.loads(s.recv(4096).decode() or "{}")
        s.close()
        if not data.get("error"):
            break
    except Exception:
        pass
PY
}

restart_pending=false

# shellcheck disable=SC2329 # invoked via USR1 trap
handle_usr1() {
  echo "Wrapper received USR1 (restart request). Signaling child $child_pid..." >&2
  restart_pending=true
  kill -TERM "$child_pid" 2>/dev/null || true
}

# shellcheck disable=SC2329 # invoked via TERM trap
handle_term() {
  echo "Wrapper received TERM (stop request). Terminating child $child_pid..." >&2
  restart_pending=false
  kill -TERM "$child_pid" 2>/dev/null || true
}

trap handle_usr1 USR1
trap handle_term TERM

# shellcheck disable=SC2317,SC2329 # invoked via EXIT trap
cleanup() {
  if [[ "$restart_pending" == "true" ]]; then
    # Do not clean up or unregister if we are in the middle of a restart loop
    :
  else
    kill "$heartbeat_pid" >/dev/null 2>&1 || true
    rpc_unregister
    "${tmux_cmd[@]}" set-option -p -u -t "$pane_id" @agent_name 2>/dev/null || true
    "${tmux_cmd[@]}" set-option -p -u -t "$pane_id" @agent_id 2>/dev/null || true
    "${tmux_cmd[@]}" select-pane -t "$pane_id" -T "" 2>/dev/null || true
  fi
}
trap cleanup EXIT

export BROCCOLI_COMMS_TRACK_ACTIVE=1
export AGENT_WRAPPER_DEPTH=1

while true; do
  restart_pending=false
  run_status=0
  
  # Run process in background so traps can interrupt 'wait' immediately
  "$cmd" "$@" &
  child_pid=$!
  
  set +e
  wait "$child_pid"
  run_status=$?
  set -e
  
  # Check if a restart was requested (externally via USR1, or internally via exit code 111)
  if [[ "$restart_pending" == "true" || "$run_status" -eq 111 ]]; then
    echo "Respawning agent process..." >&2
    sleep 1
    continue
  fi
  
  break
done

if [[ "${BROCCOLI_COMMS_WAIT:-}" == "1" ]]; then
  echo "" >&2
  echo "====================================================" >&2
  echo "Agent process exited with status $run_status." >&2
  echo "Press Enter to close, or wait 30 seconds..." >&2
  echo "====================================================" >&2
  read -t 30 -r || true
fi

exit "$run_status"
