# Broccoli Comms setup and multi-device registry guide

This guide covers:

- installing/running Broccoli Comms on a new machine
- required dependencies for Nix and non-Nix installs
- configuring managed agents
- setting up `agent-registry` for multi-device agent discovery and messaging
- validating the registry in the reusable `~/projects/nix/test-vm`

## 1. What Broccoli Comms owns

Broccoli Comms is designed to avoid depending on a user's existing Home Manager or global tracker setup. The app owns:

- a `broccoli-comms-agents` session in the user's default tmux server by default
- a private `agent-tracker` daemon/socket
- managed agent windows launched through `agent-wrapper`
- the `agent-communicator` TUI launched with explicit private socket environment

Default paths:

| Purpose | Default |
| --- | --- |
| Runtime dir | `${XDG_RUNTIME_DIR:-/tmp/$UID}/broccoli-comms` |
| Tracker socket | `${XDG_RUNTIME_DIR:-/tmp/$UID}/broccoli-comms/agent-tracker.sock` |
| Tmux mode | `default` uses the user's normal tmux server; `BROCCOLI_COMMS_TMUX_MODE=private` uses `${XDG_RUNTIME_DIR:-/tmp/$UID}/broccoli-comms/tmux.sock` |
| Config | `$XDG_CONFIG_HOME/broccoli-comms/config.json` |
| Logs/cache | `$XDG_CACHE_HOME/broccoli-comms` |

## 2. Dependencies

### Nix path

With Nix, Broccoli Comms packages include the runtime dependencies they launch, including:

- Python
- tmux
- agent-tracker
- agent-wrapper
- agent-communicator TUI

You still need the configured agent command itself to be usable, for example `pi`, `claude`, `codex`, or `gemini`, unless you configure agents with absolute store paths.

### Manual/non-Nix path

Manual installs currently require these on `PATH`:

- `python3`
- `tmux`
- `go` for building the TUI from source
- configured agent commands, for example `pi`, `claude`, `codex`, or `gemini`

## 3. New-machine single-device setup

From a checkout:

```sh
git clone <broccoli-comms-repo> broccoli-comms
cd broccoli-comms
nix run .#broccoli-comms -- doctor --json
nix run .#broccoli-comms -- run main --cwd "$HOME/project" -- pi
nix run .#broccoli-comms -- agent edit main --cwd "$HOME/project" --command "pi" --autostart
nix run .#broccoli-comms -- start
nix run .#broccoli-comms -- open
```

For persistent installation:

```sh
cd broccoli-comms
nix profile install .#broccoli-comms
broccoli-comms doctor
broccoli-comms run main --cwd "$HOME/project" -- pi
broccoli-comms agent edit main --cwd "$HOME/project" --command "pi" --autostart
broccoli-comms start
broccoli-comms open
```

Useful commands:

```sh
broccoli-comms status --json
broccoli-comms agent list --json
broccoli-comms agent focus main
broccoli-comms agent attach main
broccoli-comms stop
```

## 4. Non-Nix/manual setup

```sh
git clone <broccoli-comms-repo> broccoli-comms
cd broccoli-comms
make build
./bin/broccoli-comms doctor --json
./bin/broccoli-comms run main --cwd "$HOME/project" -- pi
./bin/broccoli-comms agent edit main --cwd "$HOME/project" --command "pi" --autostart
./bin/broccoli-comms start
./bin/broccoli-comms open
```

If `doctor` fails, install the missing runtime dependency or use the Nix package path.

## 5. Provider customization layout

`broccoli-comms run NAME -- PROVIDER` can use provider-specific bootstrap paths from `~/.config/broccoli-comms/config.toml`.

- Legacy/default providers that set `agentsDir` write generated bootstrap files under that directory, preserving existing behavior.
- The `jetski` provider defaults to `context-layout = "jetski"`: each workspace is created under `~/agents-root/<agent-name>` unless explicitly overridden, `AGENTS.md` is written at the workspace root, rules are written to `.agents/rules/{memory,habits,expertise}.md`, and durable skills are written to `.agents/skills/<skill>/SKILL.md`.
- Non-Jetski providers keep the legacy layout unless `context-layout = "jetski"` is set explicitly.

Example:

```toml
[providers.jetski]
cmd = "/google/bin/releases/jetski-devs/tools/cli"
agentsDir = ".agents"
context-layout = "jetski"
```

## 6. Multi-device architecture

`agent-registry` is the rendezvous service for multiple agent-tracker instances.

```text
machine A agent-tracker ─┐
                         ├─ agent-registry ─ discovery + queued messages
machine B agent-tracker ─┘
```

Important properties:

- Trackers only integrate with registries when configured with a non-empty registry list.
- Registry delivery uses durable queue + tracker long-poll/ack.
- The registry does not need to connect back to tracker machines for normal message delivery.
- Cross-device messages are at-least-once; tracker inbox delivery de-duplicates by `message_id`.

## 7. Registry host setup

### Broccoli Comms source/runtime CLI

For local development or small self-managed deployments, Broccoli Comms can run the in-repo registry directly:

```sh
# Loopback dev registry; auth disabled only on localhost/loopback.
broccoli-comms registry start --host 127.0.0.1 --port 8080 --name local --noauth
broccoli-comms registry health
broccoli-comms registry agents --json
broccoli-comms registry status --json
broccoli-comms registry stop

# LAN/public bind requires auth and should use a token file.
umask 077
openssl rand -base64 32 > ~/.config/broccoli-comms/registry-token
broccoli-comms registry start --host 0.0.0.0 --port 8080 --name home --auth --token-file ~/.config/broccoli-comms/registry-token
```

Managed registry process files are stored under Broccoli Comms runtime/cache/config paths (`agent-registry.pid`, `agent-registry.log`, `agent-registry/state.json`, and `registry.json`). Unauthenticated non-loopback binds are refused. Starting this process does not automatically configure a tracker to publish to it; use `broccoli-comms registry add` as described below, explicit `AGENT_REGISTRIES_JSON`, or tracker/module registry settings. Remote direct pane input remains separately gated and is not enabled by `registry start`.

### NixOS module

Add the registry flake/module to the host that should run the rendezvous service:

```nix
{
  inputs.broccoli-comms.url = "github:<owner>/broccoli-comms";

  outputs = { self, nixpkgs, broccoli-comms, ... }: {
    nixosConfigurations.registry-host = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        broccoli-comms.nixosModules.agent-registry # if exposed by your wrapper flake
        # or import the standalone agent-registry flake/module directly
      ];
    };
  };
}
```

If using the standalone registry slice directly:

```nix
{
  inputs.agent-registry.url = "path:/path/to/broccoli-comms/agent-registry";

  outputs = { self, nixpkgs, agent-registry, ... }: {
    nixosConfigurations.registry-host = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        agent-registry.nixosModules.default
        ({ ... }: {
          services.agent-registry = {
            enable = true;
            port = 8080;
            auth = true;
            tokenFile = "/run/secrets/agent-registry-token";
            staleSeconds = 60;
            goneSeconds = 180;
          };
        })
      ];
    };
  };
}
```

Create a shared token on the registry host:

```sh
sudo install -d -m 0700 /run/secrets
umask 077
openssl rand -base64 32 | sudo tee /run/secrets/agent-registry-token >/dev/null
```

For local/dev-only testing, you may disable auth:

```nix
services.agent-registry = {
  enable = true;
  port = 8080;
  auth = false;
};
```

Check the registry:

```sh
curl http://registry-host:8080/healthz
curl -H "Authorization: Bearer $(cat token-file)" http://registry-host:8080/agents
```

## 8. Tracker/client machine setup

Each machine that should publish local agents or receive cross-device messages needs `agent-tracker` registry integration.

For Broccoli Comms' private tracker, save registry URLs with the CLI:

```sh
# Existing authenticated central registry.
broccoli-comms registry add --name home --url https://registry.example.com --auth --token-file ~/.config/broccoli-comms/registry-token

# Local/dev unauthenticated registry.
broccoli-comms registry add --name local --url http://127.0.0.1:8080 --noauth

broccoli-comms registry list
broccoli-comms registry env --json
broccoli-comms stop
broccoli-comms start
broccoli-comms agent-tracker registry-status
```

Saved registry URLs live in `$BROCCOLI_COMMS_CONFIG_DIR/registries.json` (default `~/.config/broccoli-comms/registries.json`) using a versioned object schema. Token values are not stored; only `token-file` paths are saved. `broccoli-comms start` automatically supplies enabled saved entries as `AGENT_REGISTRIES_JSON` to the private tracker unless `AGENT_REGISTRIES_JSON` is already explicitly set. Use `broccoli-comms registry remove NAME`, `enable NAME`, or `disable NAME` to manage entries. Restart Broccoli Comms after changing registry URL config.

Home Manager-style options from the tracker module:

```nix
services.agent-tracker = {
  enable = true;
  httpPort = 19876;

  registries = [
    { name = "home"; url = "https://registry.example.com"; }
  ];

  registryAuth = true;
  registryTokenFile = "/home/your-user/.config/agent-tracker/registry-token";
  registryHeartbeatSeconds = 30;
};
```

The tracker usually runs as the user, so `registryTokenFile` must be readable by that user:

```sh
mkdir -p ~/.config/agent-tracker
umask 077
printf '%s' '<same-shared-token>' > ~/.config/agent-tracker/registry-token
```

For local/dev testing against an unauthenticated registry:

```nix
services.agent-tracker = {
  enable = true;
  registries = [
    { name = "dev"; url = "http://127.0.0.1:8080"; }
  ];
  registryAuth = false;
};
```

Verify from a Broccoli Comms tracker machine:

```sh
broccoli-comms agent-tracker registry-status
broccoli-comms agent-tracker list
broccoli-comms agent-tracker send-message other-host/agent-name "hello from this host"
```

## 9. Managed agents on a registry host

The registry NixOS/Home Manager modules can also keep local agents running in tmux. This is optional and separate from registry discovery.

Example NixOS managed agent:

```nix
services.agent-registry = {
  enable = true;
  auth = false;

  managedAgents.reviewer = {
    user = "tanmay";
    session = "broccoli-review";
    cwd = "/home/tanmay/project";
    command = "pi";
    wrapperPath = "agent-wrapper";
    reconcileIntervalSeconds = 30;

    restart = {
      enable = true;
      intervalSeconds = 86400;
      warningLeadTimeSeconds = 300;
      warningMessage = "Restarting in 5 minutes";
    };
  };
};
```

Important: `managedAgents` only starts/reconciles local tmux agents. To publish those agents to a multi-device registry, the same machine/user must also run `agent-tracker` with registry integration enabled.

By default, managed agents use the target user's normal tmux socket under `/run/user/<uid>/tmux-<uid>/default`. The helper defensively ignores invalid inherited runtime dirs such as bare `/run/user`; set `tmuxSocketPath` explicitly if you need a different socket.

## 10. Testing with `~/projects/nix/test-vm`

The reusable test VM provides SSH on `127.0.0.1:2222` with user/password `dev`/`dev`.

Start the VM:

```sh
cd ~/projects/nix/test-vm
nohup nix run .#devvm >/tmp/test-vm-devvm.log 2>&1 &
tail -f /tmp/test-vm-devvm.log
```

Wait for SSH:

```sh
ss -ltn '( sport = :2222 )'
```

Create a temporary test flake that imports the registry module and the test-vm base module:

```sh
mkdir -p /tmp/broccoli-registry-test
cat > /tmp/broccoli-registry-test/flake.nix <<'EOF'
{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  inputs.test-vm.url = "path:/home/tanmay/projects/nix/test-vm";
  inputs.agent-registry.url = "path:/home/tanmay/projects/nix/broccoli-comms/agent-registry";

  outputs = { nixpkgs, test-vm, agent-registry, ... }: {
    nixosConfigurations.registry-test = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        "${test-vm}/devvm.nix"
        agent-registry.nixosModules.default
        ({ pkgs, ... }: {
          services.agent-registry = {
            enable = true;
            auth = false;
            port = 8080;
            managedAgents.smoke = {
              user = "dev";
              session = "registry-smoke";
              cwd = "/home/dev";
              command = "sleep 300";
              wrapperPath = "env";
              reconcileIntervalSeconds = 30;
            };
          };
          environment.systemPackages = [ agent-registry.packages.x86_64-linux.default pkgs.curl pkgs.jq pkgs.tmux pkgs.coreutils ];
        })
      ];
    };
  };
}
EOF
```

Build and push the config to the VM:

```sh
out=$(nix build path:/tmp/broccoli-registry-test#nixosConfigurations.registry-test.config.system.build.toplevel --no-link --print-out-paths)
NIX_SSHOPTS='-p 2222 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null' \
  nix copy --no-check-sigs --to ssh-ng://dev@127.0.0.1 "$out"
ssh -p 2222 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null dev@127.0.0.1 \
  sudo "$out/bin/switch-to-configuration" test
```

Check registry health and managed-agent reconciliation inside the VM:

```sh
ssh -p 2222 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null dev@127.0.0.1 \
  'curl -fsS http://127.0.0.1:8080/healthz && systemctl status agent-registry --no-pager && tmux -S /run/user/1000/tmux-1000/default list-panes -a -F "#S #{pane_id} #{@agent_name}"'
```

Expected results:

- `/healthz` returns `{"ok": true}`
- `agent-registry.service` is active
- tmux has a `registry-smoke` pane tagged with `@agent_name=smoke`

## 11. Direct pane input over the registry

Normal `send-message` delivery remains inbox-based and is the default. Direct pane input (`send-text`, `send-key`, TUI `/text`, TUI `/key`) bypasses inbox history and controls an agent pane directly.

Local direct input examples:

```sh
broccoli-comms agent-tracker send-text alice "hello"
broccoli-comms agent-tracker send-text --no-submit alice "draft prompt"
broccoli-comms agent-tracker send-key alice C-c Enter
```

Provider-specific submit keys can be configured in `~/.config/broccoli-comms/config.toml`. The default is `Enter`; set `tmux-submit-key` when a provider needs a different tmux key such as `C-M`:

```toml
[providers.pi]
cmd = "pi"
tmux-submit-key = "Enter"

[providers.codex]
cmd = "codex"
tmux-submit-key = "C-M"
```

`send-message` inbox notifications and direct `send-text` submission choose the submit key from the target agent provider (`model_type`/`agent_type`/`agent_cmd`). `send-text --no-submit` still types text without pressing any submit key.

Remote direct input is enabled by default by the Home Manager module for managed Broccoli Comms services and should be used only with trusted registries/trackers. Set `services.broccoli-comms.tracker.remotePaneInput.enable = false` to opt out. When not using the Home Manager module defaults, enable all required gates before using host-qualified remote targets:

```sh
# Sender tracker/TUI capability
export BROCCOLI_COMMS_REMOTE_PANE_INPUT_SEND_ENABLED=1
# or umbrella for both send and receive on the same tracker:
export BROCCOLI_COMMS_REMOTE_PANE_INPUT_ENABLED=1

# Registry service
export BROCCOLI_COMMS_REMOTE_PANE_INPUT_REGISTRY_ENABLED=1
# or AGENT_REGISTRY_REMOTE_PANE_INPUT_ENABLED=1

# Receiver tracker
export BROCCOLI_COMMS_REMOTE_PANE_INPUT_RECEIVE_ENABLED=1
# or AGENT_TRACKER_REMOTE_PANE_INPUT_RECEIVE_ENABLED=1
```

Optional limits:

```sh
export AGENT_REMOTE_PANE_INPUT_MAX_TEXT_BYTES=4096
export AGENT_REMOTE_PANE_INPUT_MAX_KEYS=16
```

Remote examples after enablement:

```sh
broccoli-comms agent-tracker send-text host-a/alice "hello remotely"
broccoli-comms agent-tracker send-key registry-a:host-a/alice Escape
```

Guardrails:

- registry endpoint is separate: `POST /pane-inputs`; `/messages` semantics are unchanged
- each request carries string `pane_input_id` and `request_id`
- queued delivery uses `delivery_type=pane_input`
- receiver dedupes request IDs before injection, so retries do not duplicate keystrokes
- registry acks only after successful injection or duplicate recognition
- pane input does not write inbox entries or normal inbox notifications
- logs/audit include request metadata and text length/hash, not full text payloads
- `broccoli-comms doctor` warns when remote pane input is enabled without registry auth/token assumptions in the current environment

## 12. Current future work

These are intentionally not required for the basic setup above:

- decide whether `agent-registry` remains bundled by default or optional in Broccoli Comms
- expose Broccoli Comms top-level NixOS/Home Manager modules for registry setup, instead of using the nested standalone registry flake directly
- add remote pane capture UI/approval flows beyond the current snapshot request helper
- add per-agent or interactive approve/deny policy for remote pane control across Pi/Claude/Codex/Gemini
- keep future native/libghostty frontend work out of the main tree until a reviewed frontend plan is selected
- add deeper `doctor` checks for registry config/secrets and agent command versions
