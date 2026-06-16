# Broccoli Comms Runtime API

Broccoli Comms keeps runtime/control data UI-agnostic so the terminal TUI and future desktop/native frontends can consume the same JSON contracts.

Current API surface is CLI-based. Future local HTTP/RPC APIs should preserve these shapes where practical.

## Design rules

- Runtime data is JSON-friendly: objects, arrays, strings, numbers, booleans, and nulls.
- Frontends must not infer managed-agent identity from tmux window names. Managed windows are identified by tmux metadata (`@broccoli_managed_agent`) and `window_id`.
- Frontends should treat tmux IDs/pane IDs as opaque strings.
- Runtime paths and sockets are explicit. Do not depend on inherited `TMUX`/`TMUX_PANE` for app runtime operations.
- UI-specific behavior belongs in UI clients, not in tracker or launcher state contracts.
- New fields may be added; clients should ignore unknown fields.

## `broccoli-comms status --json`

Returns stable runtime health and path information.

Example:

```json
{
  "app": "broccoli-comms",
  "version": "0.1.0",
  "build": { "version": "0.1.0", "revision": "abc1234", "display": "0.1.0+abc1234" },
  "paths": {
    "runtime_dir": "/run/user/1000/broccoli-comms",
    "cache_dir": "/home/user/.cache/broccoli-comms",
    "config_dir": "/home/user/.config/broccoli-comms"
  },
  "tracker": {
    "socket": "/run/user/1000/broccoli-comms/agent-tracker.sock",
    "up": true
  },
  "tmux": {
    "mode": "default",
    "socket": null,
    "up": true,
    "session": "broccoli-comms-agents"
  },
  "config": {
    "path": "/home/user/.config/broccoli-comms/config.json"
  },
  "agents": {
    "configured_count": 1,
    "managed_running_count": 1,
    "managed_windows": [
      {
        "window_id": "@1",
        "window_name": "main",
        "managed_agent": "main",
        "pane_id": "%2",
        "cwd": "/home/user/project"
      }
    ]
  }
}
```

Compatibility aliases currently included at top level:

- `tracker_socket`
- `tracker_up`
- `tmux_socket`
- `tmux_up`

Prefer the nested `tracker` and `tmux` objects for new clients. The `build` object is intended for cross-host version-skew diagnosis; packaged builds inject a git revision when available.

## `broccoli-comms agent list --json`

Returns configured agents enriched with runtime metadata and tracker registration info when available.

Example:

```json
{
  "app": "broccoli-comms",
  "version": "0.1.0",
  "build": { "version": "0.1.0", "revision": "abc1234", "display": "0.1.0+abc1234" },
  "config": "/home/user/.config/broccoli-comms/config.json",
  "runtime": {
    "tracker_up": true,
    "tmux_up": true,
    "tmux_session": "broccoli-comms-agents",
    "tmux_mode": "default",
    "tmux_socket": null
  },
  "agents": {
    "main": {
      "name": "main",
      "configured": {
        "cwd": "/home/user/project",
        "command": "pi"
      },
      "cwd": "/home/user/project",
      "command": "pi",
      "running": true,
      "window_exists": true,
      "managed_windows": [
        {
          "window_id": "@1",
          "window_name": "main",
          "managed_agent": "main",
          "pane_id": "%2",
          "cwd": "/home/user/project"
        }
      ],
      "tracker": {
        "name": "main",
        "agent_id": "...",
        "tmux_pane": "%2",
        "tmux_socket": "/tmp/tmux-1000/default",
        "status": "idle"
      }
    }
  }
}
```

Notes:

- `configured` is the config source of truth.
- `managed_windows` is derived from tmux metadata in the active tmux mode.
- `running` is true when at least one managed window is present for the agent.
- `tracker` is best-effort and may be null if the private tracker is down or the wrapper has not registered yet.
- `cwd` and `command` remain as direct fields for simple clients; prefer `configured.cwd` and `configured.command` for new clients.

## `broccoli-comms doctor --json`

Returns readiness checks for bootstrap/install diagnostics. The top-level `ok` is false when any check has `status: "error"`; warnings are advisory.

Check objects include at least:

```json
{
  "name": "tmux",
  "status": "ok",
  "message": "tmux executable found",
  "path": "/nix/store/.../bin/tmux",
  "version": "tmux 3.6a"
}
```

Current checks cover executable availability, writable runtime/cache/config directories, configured agent command lookup where practical, tracker reachability, and the Broccoli tmux session in the active tmux mode.

## Tracker agent/list metadata

Tracker `list` responses are additive and include UI-friendly identity fields when known:

- `agent_id` / `uuid`: stable agent identity.
- `scope`: `local` for local tracker rows, `remote` for registry-discovered rows.
- `hostname`: machine identity for grouping/display.
- `tracker_id`: stable tracker identity.
- `target_address`: address frontends should use for message/direct-input actions.
- `registry_name`: registry route prefix when a remote row is registry-qualified.
- `model_type`: canonical model label normalized from explicit `model_type`, `agent_type`, or command basename (`pi`, `claude`, `codex`, or `unknown`).

Clients should prefer `model_type` for badges and fall back to `agent_type` / `agent_cmd` only for legacy trackers.

## Tracker mailbox and message APIs

`ensure_mailbox` creates or refreshes a mailbox-only tracker agent for frontends such as `agent-communicator` without requiring an attached agent pane. It returns:

```json
{
  "name": "agent-communicator",
  "agent_id": "...",
  "uuid": "..."
}
```

Structured JSON-RPC errors may include `error.data` with:

- `error_code`
- `agent`
- `hostname`
- `operation`
- `retryable`

Existing clients may continue to display the raw error message; newer frontends can use `operation` and `retryable` for actionable error bars/retry affordances.

Inbox message objects preserve legacy fields and may include these optional attribution/read-status fields:

```json
{
  "sender": "alice",
  "sender_agent_id": "...",
  "sender_tracker_id": "...",
  "sender_hostname": "workstation-a",
  "sender_model_type": "pi",
  "sender_agent_type": "pi",
  "sender_agent_cmd": "pi --profile reviewer",
  "kind": "text",
  "timestamp": "2026-05-30T10:00:00+00:00",
  "message": "hello",
  "read": false,
  "message_id": "..."
}
```

Local sends enrich sender metadata from tracker state. Registry-routed remote delivery preserves these fields when present. Legacy messages without sender metadata remain valid.

`get_unread_counts` returns durable unread counts derived from inbox `read` flags without marking messages read:

```json
{
  "counts": {
    "local:<agent_id>": 2,
    "remote:<tracker_id>:<agent_id>": 1,
    "sender:<legacy-sender>": 1
  },
  "total": 4
}
```

Stable keys intentionally distinguish local and remote agents even when display names or agent IDs collide. Opening/reading a local conversation matches explicit local `sender_tracker_id` plus legacy local messages missing tracker IDs, but excludes explicit remote tracker IDs.

## Tracker health and events

`tracker_info` returns local identity plus a UI-friendly health snapshot:

```json
{
  "hostname": "workstation-a",
  "tracker_id": "...",
  "http_port": 19876,
  "status": "ok",
  "agent_count": 3,
  "online_agent_count": 2,
  "registry_connected": true,
  "registries": [
    {
      "name": "mundus",
      "connected": true,
      "registry_url": "https://agents.example",
      "last_operation": "heartbeat",
      "status_code": 200,
      "last_attempt": 1780137000.0,
      "last_success": 1780137000.0
    }
  ],
  "remote_tracker_count": 2,
  "online_remote_tracker_count": 1
}
```

Health should degrade gracefully: missing registry status means `registry_connected` may be null/omitted, and clients should treat unknown fields as additive.

`wait_events` remains backward-compatible. Event objects may include lifecycle/system event types and extra fields:

- `agent_registered`
- `agent_unregistered`
- `agent_status_changed`
- `message_delivered`
- `message_read`
- `remote_agent_event`

Common optional fields include `target_agent_id`, `target_agent_name`, `hostname`, `tracker_id`, `status`, `old_status`, `model_type`, `agent_type`, `agent_cmd`, `sender`, `message_id`, and `message`. Existing consumers should ignore unknown event types/fields.

## Agent Communicator TUI key behavior

The TUI composer has persistent input tabs:

- `F1` `/msg inbox`: normal inbox message mode. `Enter` sends a message to the selected conversation.
- `F2` `/text pane`: explicit direct text mode. `Enter` sends composer text to the selected pane through the existing direct-input backend.
- `F3` `/key pane`: explicit direct key mode. `Enter` sends whitespace-separated key tokens.
- `F4` `/broadcast`: visible but disabled unless a future backend is implemented. Pressing `Enter` in this mode surfaces a disabled status and does not send.

Legacy slash commands remain supported from any mode: `/msg`, `/text`, `/text --no-submit`, `/key`, and `/broadcast`. The composer context line shows the selected target plus model badge and machine where known.

Unread navigation: `n` jumps to the next unread conversation, while `Ctrl-N` / `Ctrl-P` keep their existing next/previous agent behavior.

## `broccoli-comms agent-tracker` passthrough

`broccoli-comms agent-tracker <subcommand> [args...]` is the canonical user-facing tracker control CLI. It runs the repository's tracker control implementation against the Broccoli Comms runtime and pins these environment values via `base_env()`:

- `AGENT_TRACKER_SOCKET`
- `BROCCOLI_COMMS_RUNTIME_DIR`
- `BROCCOLI_COMMS_CACHE_DIR`
- `BROCCOLI_COMMS_CONFIG_DIR`

The wrapper does not require standalone `agent-tracker-ctl` to be installed globally or present on the user's shell `PATH`; that command is deprecated for normal use. Pane-sensitive commands such as `send-text`, `send-key`, `focus`, and `capture-pane` use registered tmux pane/socket metadata. Default tmux mode uses the user's normal tmux server and the `broccoli-comms-agents` session; `BROCCOLI_COMMS_TMUX_MODE=private` uses Broccoli's private tmux socket for compatibility.

`base_env()` also auto-loads enabled saved tracker registries from `$BROCCOLI_COMMS_CONFIG_DIR/registries.json` into `AGENT_REGISTRIES_JSON` unless `AGENT_REGISTRIES_JSON` is already set by the caller or `BROCCOLI_COMMS_DISABLE_CONFIG_REGISTRIES=1` is set. Manage this file with `broccoli-comms registry add/list/remove/enable/disable/env`; token contents are not stored, only `token-file` paths.

Examples:

```sh
broccoli-comms agent-tracker --help
broccoli-comms agent-tracker list
broccoli-comms agent-tracker read-inbox --last 10
broccoli-comms agent-tracker send-message main "hello"
broccoli-comms agent-tracker registry-status
broccoli-comms agent-tracker capture-pane agent-communicator --last 80
```

## Direct pane input capability

Direct pane input is separate from inbox messaging. It controls a registered tmux pane directly and does not create inbox/outbox conversation history.

Tracker CLI/RPC examples:

```sh
broccoli-comms agent-tracker send-text alice "hello"
broccoli-comms agent-tracker send-text --no-submit alice "draft"
broccoli-comms agent-tracker send-key alice C-c Enter
```

Remote direct input uses the same `send_input` backend with a host-qualified `target_address`. The Home Manager module enables the required managed-service gates by default and exposes `services.broccoli-comms.tracker.remotePaneInput.enable = false` as the opt-out. Outside those defaults it requires explicit gates:

- sender tracker: `AGENT_TRACKER_REMOTE_PANE_INPUT_SEND_ENABLED=1`, `BROCCOLI_COMMS_REMOTE_PANE_INPUT_SEND_ENABLED=1`, or umbrella `BROCCOLI_COMMS_REMOTE_PANE_INPUT_ENABLED=1`
- receiver tracker: `AGENT_TRACKER_REMOTE_PANE_INPUT_RECEIVE_ENABLED=1`, `BROCCOLI_COMMS_REMOTE_PANE_INPUT_RECEIVE_ENABLED=1`, or umbrella `BROCCOLI_COMMS_REMOTE_PANE_INPUT_ENABLED=1`
- registry: `AGENT_REGISTRY_REMOTE_PANE_INPUT_ENABLED=1` or `BROCCOLI_COMMS_REMOTE_PANE_INPUT_REGISTRY_ENABLED=1`

Limits are controlled by `AGENT_REMOTE_PANE_INPUT_MAX_TEXT_BYTES` (default `4096`) and `AGENT_REMOTE_PANE_INPUT_MAX_KEYS` (default `16`). Remote pane input is queued through registry `POST /pane-inputs` as `delivery_type=pane_input` with source-generated `pane_input_id` and `request_id`; destination trackers dedupe request IDs before injection and ack only after successful injection or duplicate recognition.

When launched via `broccoli-comms open` / `broccoli-comms ui`, the communicator TUI derives a runtime capability from the sender-side env gates. Remote `/text` and `/key` commands are rejected before dispatch while disabled. When enabled, the TUI sends the exact selected row `TargetAddress` to the tracker client and surfaces success/failure in the footer.

`broccoli-comms doctor --json` includes advisory checks for remote pane input. If remote direct input is enabled while registry auth is disabled or no registry token is present in the environment, doctor reports warnings rather than silently treating it as safe.

## Stable communicator conversation identity

Communicator conversation state is keyed by stable IDs when available:

- local rows use `local:<agent_id>`
- remote rows use `remote:<tracker_id>:<agent_id>`
- legacy rows/messages fall back to the visible target address/name

Outbox records persist target agent/tracker IDs. Inbound messages carry sender agent/tracker IDs where available. This prevents conversation history from splitting on local renames and prevents same-named agents on different remote trackers from sharing a conversation.

## Related commands

Managed agent config/window commands:

```sh
# Configure and persist a live agent from a one-off run session.
# (run creates the process, edit stores config and can set autostart.)
broccoli-comms run <name> --cwd <dir> -- <cmd>
broccoli-comms agent edit <name> --cwd <dir> --command <cmd> [--autostart]
broccoli-comms agent focus <name>
broccoli-comms agent attach <name>
broccoli-comms agent remove <name>
broccoli-comms agent restart <name>
```

`start` only launches configured agents with `autostart: true`; `agent restart <name>` can still launch a configured agent explicitly. `focus` selects a running managed-agent window using tmux metadata/window ids and prints a JSON-friendly result. `attach` attaches or switches the current terminal/client directly to that managed window. Other commands also print JSON-friendly results.

When launched via `broccoli-comms open` / `broccoli-comms ui`, `agent-communicator` runs in the current shell and receives the private `AGENT_TRACKER_SOCKET`. It requires the tracker to already be running; run `broccoli-comms start` first. If launched from inside tmux, default tmux mode preserves pane `TMUX` metadata for tmux operations; in private mode it also receives `AGENT_TRACKER_TMUX_SOCKET` and `BROCCOLI_COMMS_TMUX_SOCKET`.
