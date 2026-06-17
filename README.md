# Broccoli Comms

Broccoli Comms is a standalone agent workspace runtime. It runs terminal-based coding agents, tracks their panes/inboxes, and provides a terminal UI for messaging and control without depending on a user's Home Manager setup, shell hooks, or global agent-tracker service.

## What it is

`broccoli-comms` owns an isolated runtime containing:

- managed agent panes in your default tmux server/session by default
- a private `agent-tracker` daemon/socket for registration, inboxes, pane capture, and local direct input
- managed agent windows launched through `agent-wrapper`
- the `agent-communicator` TUI as the primary interface
- optional `agent-registry` connectivity for multi-device discovery and queued cross-machine messages

Typical local workflow:

```sh
broccoli-comms start       # start tracker and reconcile autostart agents
broccoli-comms ui          # open the TUI in the current shell; alias: open
broccoli-comms status      # inspect runtime state
broccoli-comms stop        # stop Broccoli-owned windows/session state and private tracker
```

`broccoli-comms ui` connects to an already-running tracker and fails clearly if the tracker is not running.

Default paths/mode:

- runtime: the configured `[paths].runtime_dir` from `$XDG_CONFIG_HOME/broccoli-comms/config.toml` when set; otherwise the CLI falls back to `$XDG_RUNTIME_DIR` (or `/tmp/$UID/broccoli-comms` if `XDG_RUNTIME_DIR` is unset)
- tracker socket: `<runtime_dir>/agent-tracker.sock`
- tmux mode: `default` (uses your normal tmux server and a `broccoli-comms-agents` session)
- private tmux compatibility: set `BROCCOLI_COMMS_TMUX_MODE=private` to use `<runtime_dir>/tmux.sock`
- agent config: `$XDG_CONFIG_HOME/broccoli-comms/config.json`
- logs/cache: `$XDG_CACHE_HOME/broccoli-comms`

To avoid the UI and service disagreeing about the tracker socket, pin the runtime explicitly in `$XDG_CONFIG_HOME/broccoli-comms/config.toml`. Use real absolute paths; TOML values are not shell-expanded.

Example for the normal XDG runtime directory:

```toml
[paths]
runtime_dir = "/run/user/1000/broccoli-comms"
cache_dir = "/home/alice/.cache/broccoli-comms"
config_dir = "/home/alice/.config/broccoli-comms"
```

With that config, the tracker socket is `/run/user/1000/broccoli-comms/agent-tracker.sock`.

Example for a Home Manager-managed runtime pinned under the Broccoli cache directory:

```toml
[paths]
runtime_dir = "/home/alice/.cache/broccoli-comms/runtime"
cache_dir = "/home/alice/.cache/broccoli-comms"
config_dir = "/home/alice/.config/broccoli-comms"
```

With that config, the tracker socket is `/home/alice/.cache/broccoli-comms/runtime/agent-tracker.sock`.

### Example `config.toml`

`config.toml` controls runtime paths, tracker settings, scheduled jobs, and provider-level launch defaults. Managed agent definitions still live in `config.json`, but provider entries in `config.toml` can rewrite provider commands and add provider-specific launch flags.

This example intentionally includes only common public providers (`pi`, `codex`, and `claude`) and omits any private/internal providers:

```toml
[paths]
runtime_dir = "/home/alice/.cache/broccoli-comms/runtime"
cache_dir = "/home/alice/.cache/broccoli-comms"
config_dir = "/home/alice/.config/broccoli-comms"
# Optional: when set, `broccoli-comms run NAME ...` creates the agent workspace
# under this stable root as <agent-root-dir>/<agent-name>/ instead of a temp dir.
agent-root-dir = "/home/alice/.cache/broccoli-comms/agents"

[tracker]
http_port = 19876

[registry]
heartbeat_seconds = 30
auth_enabled = true
# Home Manager enables remote direct pane input by default; set
# services.broccoli-comms.tracker.remotePaneInput.enable = false to opt out.
remote_pane_input_enabled = true

[ui]
capture_pane_default_lines = 20

[core]
enable_reliable_send_keys = true

[scheduled_jobs]
enabled = true

[scheduled_jobs.agent_task_nudge]
enabled = true
interval_seconds = 600
backoff_multiplier = 2
max_nudges = 5

[providers.pi]
cmd = "pi"
auto-accept-flag = ""
prompt-flag-name = "--"
initial-message = "Check if any task are assigned to you, if yes, start working on them, else be on standby"

[providers.codex]
cmd = "codex"
auto-accept-flag = "--dangerously-bypass-approvals-and-sandbox"
prompt-flag-name = "--"
initial-message = "Check if any task are assigned to you, if yes, start working on them, else be on standby"

[providers.claude]
cmd = "claude"
agent-root-dir = "~/.agents-root"
auto-accept-flag = "--dangerously-skip-permissions"
prompt-flag-name = "--"
initial-message = "Check if any task are assigned to you, if yes, start working on them, else be on standby"
```

Provider fields:

When installed through the Broccoli Comms Home Manager module, prefer Nix options such as `services.broccoli-comms.providers.<name>.cmd`, `autoAcceptFlag`, `promptFlagName`, `initialMessage`, `tmuxSubmitKey`, `agentRootDir`, `agentsDir`, `contextLayout`, and `extraSettings`; the module renders `~/.config/broccoli-comms/config.toml` from those options.

- `cmd`: executable or absolute path to launch for the provider alias used in `broccoli-comms run NAME -- PROVIDER ...`.
- `defaultArgs`: optional string or TOML array appended after `cmd` for every launch of that provider.
- `auto-accept-flag`: optional provider-specific flag that enables auto-accept/auto-approve mode. Leave empty to disable.
- `prompt-flag-name` plus `initial-message`: when both are non-empty, `broccoli-comms run` passes them as the provider's initial prompt/message flag and value. Use `--` for providers whose initial prompt is a positional argument. The Home Manager default initial message is `Check if any task are assigned to you, if yes, start working on them, else be on standby`; explicit provider overrides still win. The message is passed as the configured provider argument; it is not converted into an inbox notification.
- provider-level `agent-root-dir`: optional stable root for only that provider. It overrides `[paths].agent-root-dir` for runs launched through the provider alias.
- `agentsDir`: optional provider customization subdirectory. The legacy layout writes bootstrap files under that subdirectory. The `jetski` provider uses `context-layout = "jetski"` by default: each workspace is created under `~/agents-root/<agent-name>` unless explicitly overridden, `AGENTS.md` stays at the workspace root, rules are written to `<agentsDir>/rules/{memory,habits,expertise}.md`, and skills are written to `<agentsDir>/skills/<skill>/SKILL.md`. Other providers keep the legacy layout unless `context-layout` is explicitly set.

Path fields:

- `[paths].agent-root-dir`: optional stable root for agent workspaces. When unset, `broccoli-comms run` preserves the existing temp workspace behavior. When set, a run named `NAME` uses `<agent-root-dir>/<NAME>/` as the launch/context root, with unsafe characters in `NAME` sanitized the same way as temp workspaces.

## Install and quick start

### Nix machine

Required on the host:

- Nix with flakes enabled
- `git` if you want to clone a checkout locally
- the agent commands you want to run on `PATH`, for example `pi`, `claude`, or `codex`

The Nix package supplies Broccoli Comms' own runtime tools, including Python, tmux, the Go-built TUI, `agent-tracker`, `agent-wrapper`, and `agent-registry`.

Run directly from a checkout:

```sh
git clone https://github.com/tanmayv/broccoli-comms.git
cd broccoli-comms
nix run .#broccoli-comms -- doctor
nix run .#broccoli-comms -- start
nix run .#broccoli-comms -- ui
```

Run directly from GitHub:

```sh
nix run github:tanmayv/broccoli-comms#broccoli-comms -- doctor
nix run github:tanmayv/broccoli-comms#broccoli-comms -- start
nix run github:tanmayv/broccoli-comms#broccoli-comms -- ui
```

Install persistently with Nix:

```sh
nix profile install github:tanmayv/broccoli-comms#broccoli-comms
broccoli-comms doctor
broccoli-comms start
broccoli-comms ui
```

### Non-Nix machine

A non-Nix install builds the TUI from source and uses system dependencies.

Required on the host:

- `git` to clone this repository
- `make` to run the build/install targets
- `go` to build `agent-communicator`
- `python3` to run `broccoli-comms`, `agent-tracker`, and `agent-registry`
- `tmux` for managed agent panes
- a POSIX shell; `bash` is needed for the smoke-test scripts
- the agent commands you want to run on `PATH`, for example `pi`, `claude`, or `codex`

No third-party Python packages are required for the core launcher/tracker/registry path; they use the Python standard library.

Build and run:

```sh
git clone https://github.com/tanmayv/broccoli-comms.git
cd broccoli-comms
make build
./bin/broccoli-comms doctor
./bin/broccoli-comms start
./bin/broccoli-comms ui
```

Optional: put the checkout's `./bin` directory on `PATH`, or symlink `bin/broccoli-comms` into a directory on `PATH`. Prefer a symlink over copying so the launcher can still find the rest of the source checkout.

## Build from source

From a source checkout you can build either with Nix or with the repository `Makefile`.

Nix build:

```sh
git clone https://github.com/tanmayv/broccoli-comms.git
cd broccoli-comms
nix build .#broccoli-comms
./result/bin/broccoli-comms doctor
```

Non-Nix build:

```sh
git clone https://github.com/tanmayv/broccoli-comms.git
cd broccoli-comms
make build
./bin/broccoli-comms doctor
```

The non-Nix build is source-checkout based: keep the checkout in place and run `./bin/broccoli-comms`, add the checkout's `bin/` directory to `PATH`, or symlink `bin/broccoli-comms` into a directory on `PATH`. Do not copy only the launcher by itself; it needs the repository's `agent-tracker/`, `agent-registry/`, and wrapper files.

The non-Nix build creates these local executables:

- `bin/broccoli-comms`
- `bin/agent-communicator`
- `bin/agent-wrapper`
- `bin/agent-tracker`
- `bin/agent-tracker-ctl` (deprecated shim)

The tracker control implementation remains in the source tree, but users should invoke it through `broccoli-comms agent-tracker ...` so commands use the Broccoli runtime environment.

Useful source checks:

```sh
make check
nix flake check
```

## Common commands

```sh
broccoli-comms doctor
broccoli-comms start
broccoli-comms ui             # alias: open
broccoli-comms status --json
broccoli-comms attach
broccoli-comms agent list --json
broccoli-comms agent focus main
broccoli-comms agent-tracker list
broccoli-comms registry start --host 127.0.0.1 --port 8080 --name local --noauth
broccoli-comms registry status
broccoli-comms stop
```

Exposed Nix packages:

- `broccoliComms` / `default`
- `agent-tracker`
- `agent-tracker-ctl` (deprecated shim; use `broccoli-comms agent-tracker ...`)
- `agent-wrapper`
- `agent-communicator`
- `agent-registry`
- `agent-registry-managed-agent`

The standalone `agent-tracker-ctl` command is deprecated as a user-facing entrypoint; use `broccoli-comms agent-tracker ...` instead.

## Usage modes

### 1. Local-only, no registry

This is the simplest mode. Agents on the same machine communicate through the private local tracker only. No central registry is needed.

```sh
broccoli-comms start
broccoli-comms ui
```

`start` starts the private tracker, reconciles autostart agents, and creates/reuses the `broccoli-comms-agents` tmux session. `ui` then connects to that running tracker and opens the TUI in the current shell.

### 2. Use an existing central registry

A central `agent-registry` is required for multi-device communication. Each machine runs its own local `broccoli-comms`/`agent-tracker`, and all trackers publish to and poll the same registry for discovery and queued messages.

Configure an existing registry URL before opening the UI so the private tracker receives it:

```sh
broccoli-comms registry add --name home --url https://registry.example.com --auth --token-file ~/.config/broccoli-comms/registry-token
broccoli-comms registry list
broccoli-comms registry env
broccoli-comms start
broccoli-comms ui
broccoli-comms agent-tracker registry-status
```

For an unauthenticated local/dev registry:

```sh
broccoli-comms registry add --name local --url http://127.0.0.1:8080 --noauth
broccoli-comms start
broccoli-comms ui
```

Saved registry URLs live in `$BROCCOLI_COMMS_CONFIG_DIR/registries.json` (default `~/.config/broccoli-comms/registries.json`). Token contents are not stored; use `token-file`. If `AGENT_REGISTRIES_JSON` is explicitly set in the environment, it overrides saved registry URLs for that invocation. If Broccoli Comms is already running when you add or change registry URLs, stop and restart it so the tracker starts with the new config:

```sh
broccoli-comms stop
broccoli-comms start
broccoli-comms ui
```

### 3. Run a standalone registry with Broccoli Comms

One machine can host the central registry service, while every participating machine points its tracker at that registry URL.

```sh
# On the registry host:
broccoli-comms registry start --host 0.0.0.0 --port 8080 --name home --auth --token-file ~/.config/broccoli-comms/registry-token
broccoli-comms registry status

# On each client machine before opening the UI:
broccoli-comms registry add --name home --url http://REGISTRY_HOST:8080 --auth --token-file ~/.config/broccoli-comms/registry-token
broccoli-comms start
broccoli-comms ui
broccoli-comms agent-tracker registry-status
```

For loopback-only local testing, the registry can be started without auth:

```sh
broccoli-comms registry start --host 127.0.0.1 --port 8080 --name local --noauth
```

`--noauth` is only safe for loopback/local development. Registry startup alone does not enable remote direct pane input; remote direct input has separate security gates. The Home Manager module enables those gates by default for managed Broccoli Comms services and can opt out with `services.broccoli-comms.tracker.remotePaneInput.enable = false`.

## Configured agents

Edit `$XDG_CONFIG_HOME/broccoli-comms/config.json`:

```json
{
  "agents": {
    "main": {
      "cwd": "/home/user/project",
      "command": "pi",
      "autostart": true
    },
    "reviewer": {
      "cwd": "/home/user/project",
      "command": "pi --role reviewer",
      "autostart": true
    }
  }
}
```

Or manipulate config through the CLI (non-launch actions) or config files:

```sh
broccoli-comms agent list --json
# Focus/attach/restart/remove remain available for managed windows:
broccoli-comms agent focus reviewer
broccoli-comms agent attach reviewer
broccoli-comms agent restart reviewer
broccoli-comms agent remove reviewer

# For creating or broadly editing managed agents, edit ~/.config/broccoli-comms/config.json (and restart as needed).
```

Then run:

```sh
broccoli-comms start   # reconcile autostart agents
broccoli-comms ui      # open the TUI against the running tracker
```

Configured agents without `autostart: true` remain configured but are not launched by `start`; use `broccoli-comms agent restart NAME` to launch one explicitly.

`start` reconciles configured agents with `"autostart": true` into the `broccoli-comms-agents` tmux session, avoids duplicate windows on repeated starts, and launches each agent through the tracking wrapper with the private tracker environment. By default this session is created in your normal tmux server, and an already-existing `broccoli-comms-agents` session is reused. `broccoli-comms ui` runs `agent-communicator` in the current shell and requires the tracker to already be running. `broccoli-comms stop` removes only Broccoli-owned windows plus the private tracker, leaving unrelated tmux sessions/windows alone. Set `BROCCOLI_COMMS_TMUX_MODE=private` on `start/stop` to use the Broccoli-owned private tmux socket behavior with the same session name.

### Which agent launch command should I use?

Use the higher-level Broccoli commands for most workflows:

| Use case | Command | Notes |
| --- | --- | --- |
| Start a fresh named agent launch | `broccoli-comms run NAME -- COMMAND [ARGS...]` | Creates a fresh workspace under `/tmp/broccoli-agents/<name>/`, writes bootstrap context such as `AGENTS.md`, and starts the command through the Broccoli tracker wrapper. If `NAME` is already configured, `broccoli-comms run NAME` reuses its configured command; otherwise pass `--default-agent-command CMD` to launch and save a default command for new names. |
| Edit and restart an already-running managed agent | `broccoli-comms agent edit NAME [--rename NEW_NAME] [--cwd DIR] [--] [COMMAND [ARGS...]]` | Edit works only on a live managed agent and applies immediately by restart. |
| Assign live agents to a swarm | `broccoli-comms agent assign-swarm SWARM --main MAIN --subagent AGENT` | Assigns already-running local agents; does not create or restart agents. |
| Start configured swarm members | `broccoli-comms agent start-swarm SWARM` | Starts agents listed under top-level `swarms.<name>.members` in `config.json`. |

The public launch surface is limited to `run` and `agent edit`.

If `run` is used for tracker-level experiments, use `run` with the desired profile name and command. For existing managed-window operations, use `agent edit` to update and restart live agents.

### Run a new/ephemeral agent

- Use `broccoli-comms run` for a brand-new agent launch path that always gets fresh `/tmp` workspace state.
- `run` requires a unique `name` that is not currently running; if the name already has a managed window, stop it first (or use `agent edit` for that running agent).
- Command resolution order is explicit command after `--`, then the configured command for `NAME`, then `--default-agent-command CMD` for a new unconfigured name.
- Examples:
  - `broccoli-comms run planner --cwd ~/projects/my-app -- pi --role planner`
  - `broccoli-comms run planner --cwd ~/projects/my-app` (reuses `planner` from `config.json`)
  - `broccoli-comms run reviewer --cwd ~/projects/my-app --default-agent-command "pi --role reviewer"`
- This writes bootstrap context such as `AGENTS.md` into `/tmp/broccoli-agents/planner/<random>/`.

### Edit a live managed agent

- Use `broccoli-comms agent edit` **only if the agent is already running** (managed window exists).
- Any changes are persisted to config and trigger an immediate managed-window restart.
- Example:
  - `broccoli-comms agent edit planner --rename planner-main --scope repo:my-app --cwd ~/projects/my-app -- pi --role planner`

### Swarm Mode setup

Use one of the supported swarm setup flows:

- Live agents: `broccoli-comms agent assign-swarm backend-fix --main planner --subagent coder-a`
- Configured agents: add top-level `swarms.backend-fix.members` in `config.json`, then run `broccoli-comms agent start-swarm backend-fix`

Do not use per-agent `--swarm/--role` launch flags as the configured-swarm startup model.

### Agent-tracker saved agent templates

Broccoli Comms managed agents live in `$XDG_CONFIG_HOME/broccoli-comms/config.json` and are controlled with `broccoli-comms agent ...`. The lower-level agent tracker also supports saved agent templates under:

```text
~/.config/agent-tracker/agents/<template-name>/config.json
```

These templates are useful for tracker/registry workflows that need a named saved agent configuration. Each template contains the working directory, agent command, optional command arguments, and a human-readable description.

Example: create a `broccoli-comms` Pi-agent template for this repository:

```sh
mkdir -p ~/.config/agent-tracker/agents/broccoli-comms
cat > ~/.config/agent-tracker/agents/broccoli-comms/config.json <<'JSON'
{
  "directory": "/home/tanmay/projects/nix/broccoli-comms",
  "agent-command": "pi",
  "agent-args": [],
  "description": "Pi agent for the broccoli-comms repository"
}
JSON
```

Generic template shape:

```json
{
  "directory": "/absolute/path/to/project",
  "agent-command": "pi",
  "agent-args": [],
  "description": "Friendly description shown to humans"
}
```

For command arguments, split them into `agent-args` instead of appending them to `agent-command`:

```json
{
  "directory": "/home/user/project",
  "agent-command": "pi",
  "agent-args": ["--model", "gemini-2.5-pro"],
  "description": "Pi agent for /home/user/project"
}
```

To verify saved templates:

```sh
find ~/.config/agent-tracker/agents -maxdepth 2 -name config.json -print
python3 -m json.tool ~/.config/agent-tracker/agents/broccoli-comms/config.json
```

`open` / `ui` runs `agent-communicator` directly in the current shell with `AGENT_TRACKER_SOCKET` set to the app-owned runtime. It requires the private tracker to already be running; run `broccoli-comms start` first. The communicator creates/uses the stable `agent-communicator` mailbox, so its inbox/status views use the private tracker without depending on the user's global tracker. The TUI shows a Broccoli Comms runtime/tracker status line when launched in this app mode, including RPC health, active target/model/machine, local/remote online counts, registry state, and current time.

Agent Communicator key highlights:

- `F1` `/msg inbox`: normal inbox message mode; `Enter` sends to the selected conversation.
- `F2` `/text pane`: explicit direct text mode; `Enter` sends composer text to the selected pane through the existing direct-input backend.
- `F3` `/key pane`: explicit direct key mode; `Enter` sends whitespace-separated key tokens.
- `F4` `/broadcast`: visible but disabled; pressing `Enter` does not send.
- `n` jumps to the next unread conversation; `Ctrl-N` / `Ctrl-P` keep next/previous agent navigation.
- `Ctrl-O` opens the command palette. Choose `Memory Approvals` to list pending memory approvals and approved memory, then approve, edit, delete/reject/revoke, or roll back a memory version from the TUI.

Legacy slash commands (`/msg`, `/text`, `/text --no-submit`, `/key`) remain supported. The composer context line shows the selected target plus model badge and machine where known. Memory approval is handled through the command palette instead of approval slash shortcuts.

`agent focus <name>` selects a running managed-agent window by tmux metadata/window id, and `agent attach <name>` attaches or switches directly to that managed window.

`broccoli-comms agent-tracker <subcommand> [args...]` is the canonical user-facing tracker CLI. It runs the in-repo tracker control implementation against the Broccoli Comms tracker and active tmux mode, so source checkouts do not need a globally installed `agent-tracker-ctl` and commands stay pinned to the app-owned runtime.

```sh
broccoli-comms agent-tracker --help
broccoli-comms agent-tracker list
broccoli-comms agent-tracker read-inbox --last 10
broccoli-comms agent-tracker registry-status
broccoli-comms agent-tracker capture-pane agent-communicator --last 80
broccoli-comms agent-tracker send-message alice "hello" --interrupt
broccoli-comms agent-tracker request-stop alice --timeout 30s
broccoli-comms agent-tracker restart alice
```

### Lifecycle & messaging command details:

- **`send-message TARGET MESSAGE [--interrupt]`**: Sends a message to a local or remote agent.
  - `--interrupt` (alias `--interupt`): Sends the `Escape` key to the target agent's TMUX pane before printing the notification. This ensures stuck or busy agents are interrupted and receive the notification on a fresh command line.
- **`request-stop TARGET [--timeout DURATION] [--force]`**: Requests a local or remote agent to stop.
  - By default, performs a **graceful cooperative shutdown** by sending an `Escape` key and a warning notification directly into the agent's TMUX pane. This allows the running agent to perform a final memory audit, save state, and self-exit cleanly.
  - `--timeout <duration>` (default `60s`): Sets the duration the tracker waits for the agent to exit. If the agent remains active after this duration, the tracker falls back to a hard process termination (`SIGTERM`).
  - `--force`: Bypasses the graceful warning entirely and immediately terminates the agent using `SIGTERM`.
- **`restart TARGET`**: Restarts a local agent running under `agent-wrapper` by sending `SIGUSR1` to the wrapper process. The wrapper loops and respawns the agent in the same TMUX window, preserving state and registration.

For explicit pane control, `broccoli-comms agent-tracker send-text TARGET TEXT`, `broccoli-comms agent-tracker send-text --no-submit TARGET TEXT`, and `broccoli-comms agent-tracker send-key TARGET KEY [KEY...]` call the tracker `send_input` backend directly. These bypass inbox messages. Local bare names/UUIDs use the registered tmux pane/socket metadata; remote `host/agent` targets are registry-routed only when enabled on sender, registry, and receiver (`BROCCOLI_COMMS_REMOTE_PANE_INPUT_ENABLED=1` or the narrower send/receive/registry env gates). The Home Manager module enables these gates by default for managed services; set `services.broccoli-comms.tracker.remotePaneInput.enable = false` to opt out. Remote direct input should be treated as dangerous pane control.

```sh
broccoli-comms agent-tracker send-text alice "hello"
broccoli-comms agent-tracker send-text --no-submit alice "draft without enter"
broccoli-comms agent-tracker send-key alice C-c Enter
# Remote examples require explicit remote pane-input gates on both trackers and the registry:
broccoli-comms agent-tracker send-text host-a/alice "hello remotely"
broccoli-comms agent-tracker send-key registry-a:host-a/alice Escape
```

Remote-origin inbox delivery can optionally focus the destination pane when `BROCCOLI_COMMS_FOCUS_REMOTE_MESSAGES=1` is set. This is disabled by default; when enabled, the tracker uses only registered tmux pane/socket metadata and treats focus as best-effort so message delivery still succeeds if focus fails.

## Blocked-agent detection

The private tracker can detect common permission/approval prompts in local agent panes and notify `agent-communicator`. Detection is notify-only: it never approves, denies, presses keys, or executes captured pane text. When a block is detected, the notification tells you which pane looks blocked and asks you to inspect/capture it and use `/text` or `/keys` manually if you want to unblock it. In the TUI, a blocked agent is highlighted by a red status dot on the left of the agent name.

Detection is configured by provider, not by individual agent name. The default config path is:

```sh
~/.config/agent-tracker/detection.json
```

You can override it for a tracker process with:

```sh
AGENT_TRACKER_DETECTION_CONFIG=/path/to/detection.json broccoli-comms start
broccoli-comms ui
```

Start from the sample config:

```sh
mkdir -p ~/.config/agent-tracker
cp agent-tracker/detection.sample.json ~/.config/agent-tracker/detection.json
$EDITOR ~/.config/agent-tracker/detection.json
broccoli-comms stop
broccoli-comms start
broccoli-comms ui
```

Minimal example:

```json
{
  "version": 1,
  "enabled": true,
  "notify_target": "agent-communicator",
  "providers": {
    "claude": {
      "enabled": true,
      "scan_interval_seconds": 3,
      "notify_cooldown_seconds": 300,
      "keyword_matches_required": 2,
      "keywords": [
        "bash command",
        "requires approval",
        "do you want to proceed",
        "web search",
        "claude wants to search the web"
      ]
    },
    "codex": {
      "enabled": true,
      "scan_interval_seconds": 3,
      "notify_cooldown_seconds": 300,
      "keyword_matches_required": 2,
      "keywords": [
        "would you like to run the following command",
        "yes, proceed",
        "don't ask again",
        "no, and tell codex"
      ]
    },
    "pi": {
      "enabled": false,
      "keywords": ["permission", "approve", "allow", "blocked"]
    }
  }
}
```

Tuning tips:

- Set `providers.<provider>.enabled` to turn detection on/off for `claude`, `codex`, or `pi` agents.
- Add/remove `keywords` for the exact prompt text your provider shows.
- Increase `keyword_matches_required` to reduce false positives; lower it only if prompts are being missed.
- `scan_interval_seconds` controls how often panes are scanned.
- `notify_cooldown_seconds` suppresses duplicate notifications for the same prompt.
- `capture_lines` is capped at 10 lines so notifications stay small.
- `agents.<agent-name>` can be used for a one-off override, but provider-level config is recommended for normal use.

After editing the config, restart Broccoli Comms so the tracker reloads it:

```sh
broccoli-comms stop
broccoli-comms start
broccoli-comms ui
```

## Scheduled tracker jobs

The tracker has an extensible scheduled job runner. The first job, `agent_task_nudge`, runs for local controllable agents only. On each interval it checks the agent's durable current task; if the task is present and not blocked, it sends `Escape` to the pane and then types the nudge text directly into the pane to tell the agent to continue work or mark the task blocked to avoid future nudges.

Configure the cadence in `~/.config/broccoli-comms/config.toml`:

```toml
[scheduled_jobs]
enabled = true

[scheduled_jobs.agent_task_nudge]
enabled = true
interval_seconds = 600
backoff_multiplier = 2
max_nudges = 5
# Optional override for persisted per-task nudge state:
# state_path = "/Users/me/.cache/broccoli-comms/agent-tracker/scheduled-task-nudges.json"
```

`interval_seconds` is the base per-task nudge interval. After each nudge for a task, the next eligible time backs off by `interval_seconds * backoff_multiplier^nudge_count`; `max_nudges` stops nudging a task after the configured count.

Future scheduled jobs should use their own `[scheduled_jobs.<name>]` table so each job can have a separate frequency and job-specific settings.

## Local registry management

`broccoli-comms registry ...` can run the in-repo `agent-registry` rendezvous service under Broccoli Comms runtime/cache/config paths:

```sh
# Local development registry, loopback only, no auth.
broccoli-comms registry start --host 127.0.0.1 --port 8080 --name local --noauth
broccoli-comms registry health
broccoli-comms registry agents --json
broccoli-comms registry status --json
broccoli-comms registry stop

# Authenticated LAN/public bind. Prefer token files over shell-history tokens.
broccoli-comms registry start --host 0.0.0.0 --port 8080 --name home --auth --token-file ~/.config/broccoli-comms/registry-token
```

Unauthenticated non-loopback binds are refused for safety. Starting a registry does not automatically point a tracker at it; use `broccoli-comms registry add --name local --url http://127.0.0.1:8080 --noauth` (or `--auth --token-file ...`) when you want the private tracker to publish/consume that registry URL after restart. Use `registry list/remove/enable/disable/env` to manage saved URLs. Registry URL configuration does not enable remote direct pane input; the separate remote pane-input gates remain required.

## Start on boot/login

You can start the Broccoli Comms tracker/managed agents and a Broccoli-managed registry automatically with your OS service manager.

Use full paths in service files because boot/login services often have a minimal `PATH`:

```sh
command -v broccoli-comms
```

Also make sure any agent commands in your config, such as `pi`, `claude`, or `codex`, are either on the service `PATH` or written as absolute paths in `~/.config/broccoli-comms/config.json` managed-agent definitions or the matching `[providers.<name>].cmd` entry in `~/.config/broccoli-comms/config.toml`.

### Linux systemd user services

This example uses user services, so it starts when the user session starts. Enable lingering if you want user services to start at boot before interactive login:

```sh
loginctl enable-linger "$USER"
```

Create `~/.config/systemd/user/broccoli-comms.service` for the private tracker and managed agents:

```ini
[Unit]
Description=Broccoli Comms private tracker and managed agents
After=default.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=%h/.nix-profile/bin/broccoli-comms start
ExecStop=%h/.nix-profile/bin/broccoli-comms stop
Environment=PATH=%h/.nix-profile/bin:/run/current-system/sw/bin:/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=default.target
```

Create `~/.config/systemd/user/broccoli-comms-registry.service` if this machine should also host a central registry:

```ini
[Unit]
Description=Broccoli Comms agent registry
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=%h/.nix-profile/bin/broccoli-comms registry start --foreground --host 0.0.0.0 --port 8080 --name home --auth --token-file %h/.config/broccoli-comms/registry-token
Restart=on-failure
Environment=PATH=%h/.nix-profile/bin:/run/current-system/sw/bin:/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=default.target
```

Enable and start:

```sh
systemctl --user daemon-reload
systemctl --user enable --now broccoli-comms.service
systemctl --user enable --now broccoli-comms-registry.service
systemctl --user status broccoli-comms.service
systemctl --user status broccoli-comms-registry.service
```

If you do not want this machine to host a registry, omit `broccoli-comms-registry.service`. Client machines only need `broccoli-comms registry add ...` plus `broccoli-comms start`.

### macOS LaunchAgent

A LaunchAgent starts when the user logs in. This is usually safer than a system LaunchDaemon because Broccoli Comms is a user-level tmux/tracker runtime and needs access to the user's home directory and agent commands.

First choose a stable runtime directory. macOS does not always provide `XDG_RUNTIME_DIR`, so this example uses `/tmp/broccoli-comms-$USER`.

Create `~/Library/LaunchAgents/in.broccoli.comms.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>in.broccoli.comms</string>

  <key>RunAtLoad</key>
  <true/>

  <key>ProgramArguments</key>
  <array>
    <string>/Users/YOU/.nix-profile/bin/broccoli-comms</string>
    <string>start</string>
  </array>

  <key>EnvironmentVariables</key>
  <dict>
    <key>BROCCOLI_COMMS_RUNTIME_DIR</key>
    <string>/tmp/broccoli-comms-YOU</string>
    <key>PATH</key>
    <string>/Users/YOU/.nix-profile/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>

  <key>StandardOutPath</key>
  <string>/tmp/broccoli-comms.out.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/broccoli-comms.err.log</string>
</dict>
</plist>
```

Create `~/Library/LaunchAgents/in.broccoli.comms.registry.plist` if this Mac should host a registry:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>in.broccoli.comms.registry</string>

  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>

  <key>ProgramArguments</key>
  <array>
    <string>/Users/YOU/.nix-profile/bin/broccoli-comms</string>
    <string>registry</string>
    <string>start</string>
    <string>--foreground</string>
    <string>--host</string>
    <string>0.0.0.0</string>
    <string>--port</string>
    <string>8080</string>
    <string>--name</string>
    <string>home</string>
    <string>--auth</string>
    <string>--token-file</string>
    <string>/Users/YOU/.config/broccoli-comms/registry-token</string>
  </array>

  <key>EnvironmentVariables</key>
  <dict>
    <key>BROCCOLI_COMMS_RUNTIME_DIR</key>
    <string>/tmp/broccoli-comms-YOU</string>
    <key>PATH</key>
    <string>/Users/YOU/.nix-profile/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>

  <key>StandardOutPath</key>
  <string>/tmp/broccoli-comms-registry.out.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/broccoli-comms-registry.err.log</string>
</dict>
</plist>
```

Replace `YOU` and the `broccoli-comms` path with your actual username/path. Then load the services:

```sh
launchctl unload ~/Library/LaunchAgents/in.broccoli.comms.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/in.broccoli.comms.plist
launchctl start in.broccoli.comms

launchctl unload ~/Library/LaunchAgents/in.broccoli.comms.registry.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/in.broccoli.comms.registry.plist
launchctl start in.broccoli.comms.registry
```

Check logs and status:

```sh
tail -f /tmp/broccoli-comms.err.log
broccoli-comms status --json
broccoli-comms registry status --json
```

Runtime/frontend JSON contracts are documented in `docs/RUNTIME_API.md`:

```sh
broccoli-comms status --json
broccoli-comms agent list --json
```

For complete install, dependency, and multi-device registry setup instructions, see `docs/SETUP_AND_MULTI_DEVICE.md`. Delivery routing diagnostics for multi-instance `agent-communicator` fanout are documented in `docs/DELIVERY_ROUTING_DIAGNOSTICS.md`.

## Smoke test

Run the Nix/package checks and private runtime lifecycle smoke tests with isolated temp runtime/cache/config directories:

```sh
nix flake check
bash scripts/smoke-private-runtime.sh
bash scripts/smoke-default-session-reuse.sh
bash scripts/smoke-managed-agents.sh
# or
make smoke-private-runtime
make smoke-default-session-reuse
make smoke-managed-agents
```

The runtime test starts `broccoli-comms`, verifies the private tracker and active tmux mode/session, checks status JSON, stops the runtime, and verifies cleanup. The default-session reuse smoke verifies `broccoli-comms-agents` reuse, `ui` requiring a running tracker, `attach` targeting, and stop safety for unrelated windows. The managed-agent test adds a harmless `sleep 60` configured agent, verifies reconciliation/no duplicates/restart/remove, and cleans up isolated temp state.

## Migration history

Broccoli Comms began as a standalone extraction of tracker, TUI, registry, wrapper, and launcher code. The current repository is the source of truth for those components; historical migration notes live in `docs/MIGRATION_PLAN.md`.
