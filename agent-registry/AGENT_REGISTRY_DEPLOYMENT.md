# Agent Registry Deployment Guide

> **Note**: This is a registry dev/ops operational document.

This guide shows how to run `agent-registry` as a standalone service, how to configure shared-secret auth, and how to intentionally disable auth for local/dev setups.

## What this slice ships

The registry flake exports:

- `packages.<system>.default`
- `apps.<system>.default`
- `nixosModules.default`
- `homeManagerModules.default`

Files:

- `agent-registry/server.py` — registry server
- `agent-registry/flake.nix` — standalone flake entrypoint
- `agent-registry/module.nix` — minimal NixOS module
- `agent-registry/home-manager-module.nix` — Linux/Home Manager module for non-NixOS or user-scoped deployments

## 1. Standalone registry deployment on NixOS / homelab hosts

### Flake input wiring

In your `flake.nix`:

```nix
{
  inputs.agent-registry.url = "path:/path/to/broccoli-comms/agent-registry";

  outputs = { self, nixpkgs, agent-registry, ... }: {
    nixosConfigurations.my-host = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        agent-registry.nixosModules.default
        ./hosts/my-host.nix
      ];
    };
  };
}
```

Then in a normal NixOS module such as `hosts/my-host.nix`:

```nix
{ ... }: {
  services.agent-registry = {
    enable = true;
    port = 8080;
    auth = true;
    tokenFile = "/run/secrets/agent-registry-token";
    staleSeconds = 60;
    goneSeconds = 180;
  };
}
```

Then rebuild normally:

```bash
sudo nixos-rebuild switch --flake .#my-host
```

This is the preferred path for arbitrary homelab NixOS machines: import the standalone flake's `nixosModules.default` into whichever host should run the registry.

## 2. Linux with Nix package manager + Home Manager (non-NixOS supported)

For Linux machines that are not running NixOS but do use Nix + Home Manager, import the standalone Home Manager module:

```nix
{
  inputs.agent-registry.url = "path:/path/to/broccoli-comms/agent-registry";

  outputs = { self, nixpkgs, home-manager, agent-registry, ... }: {
    homeConfigurations.user = home-manager.lib.homeManagerConfiguration {
      pkgs = import nixpkgs { system = "x86_64-linux"; };
      modules = [
        agent-registry.homeManagerModules.default
        ({ ... }: {
          home.username = "your-user";
          home.homeDirectory = "/home/your-user";
          home.stateVersion = "24.05";

          services.agent-registry = {
            enable = true;
            auth = false;
            port = 8080;
          };
        })
      ];
    };
  };
}
```

Apply it with your usual Home Manager workflow:

```bash
home-manager switch --flake .#user
```

On this path, `agent-registry` runs as a `systemd --user` service and stores state under `~/.local/state/agent-registry/state.json` by default.

Important: this is a user-service deployment model. If you expect the registry and managed agents to keep running across logout/reboot without an active login session, enable persistent user services with `loginctl enable-linger <user>` (or an equivalent persistent user-manager setup) on that machine.

Managed agents are also supported in the Home Manager module; they run as the Home Manager user and support the same `session`, `cwd`, `tmuxSocketPath`, `trackerSocketPath`, and scheduled restart options as the NixOS module.

## 3. Running the app directly

For quick manual runs:

```bash
nix run path:/path/to/broccoli-comms/agent-registry
```

Optional environment:

```bash
export AGENT_REGISTRY_PORT=8080
export TRACKER_STALE_SECONDS=60
export TRACKER_GONE_SECONDS=180
export AGENT_REGISTRY_AUTH=true
export AGENT_REGISTRY_TOKEN="your-shared-token"
export AGENT_REGISTRY_STATE_PATH="$HOME/.local/state/agent-registry/state.json"
nix run path:/path/to/broccoli-comms/agent-registry
```

## 4. Secret setup when auth is enabled

When `services.agent-registry.auth = true`, you must provide `services.agent-registry.tokenFile`.

Example token creation:

```bash
umask 077
openssl rand -base64 32 > /run/secrets/agent-registry-token
```

Recommended properties:

- readable only by root/service manager
- not committed to git
- not stored in the Nix store

The module reads the token at runtime and exports it to the service process as `AGENT_REGISTRY_TOKEN`.

## 5. Disable auth / secrets intentionally

For local development or trusted test environments, you can disable auth entirely:

```nix
services.agent-registry = {
  enable = true;
  auth = false;
  port = 8080;
};
```

When `auth = false`:

- `tokenFile` is not required
- the registry accepts unauthenticated requests on non-`/healthz` endpoints
- this should only be used for local/dev or otherwise trusted networks

## 6. Tracker-side configuration

Trackers do **not** talk to registries unless `registries` is non-empty.

**Delivery model:** cross-tracker messages are now queued at the registry and pulled by each tracker via authenticated long-polling. That means the registry does **not** need to open inbound HTTP connections back to tracker hosts for normal message delivery. Tracker-side inbound reachability is therefore no longer required for registry messaging.

**Durability/semantics:** the registry persists its pending-delivery queue on disk before returning `202` from `POST /messages`. Delivery is at-least-once across registry restarts. Trackers ack only after successful local inbox delivery, and tracker-side inbox writes de-duplicate by `message_id`.

Home Manager tracker options added by this slice:

```nix
services.agent-tracker = {
  enable = true;
  httpPort = 19876;

  # Enable registry integration
  registries = [
    { name = "default"; url = "http://agent-registry.example:8080"; }
  ];

  # Auth defaults to false so existing local-only setups keep working.
  registryAuth = true;
  registryTokenFile = "/run/secrets/agent-registry-token";

  registryHeartbeatSeconds = 30;
};
```

### Tracker secret setup

Create the same shared token file on tracker machines.

**Important:** `services.agent-tracker` runs as the user, not as root. So `registryTokenFile` must point to a file readable by that user. A root-only `/run/secrets/...` path is usually **not** correct for the tracker unless you separately arrange user-readable secret material.

Example user-readable path:

```bash
mkdir -p ~/.config/agent-tracker
umask 077
printf '%s' 'your-shared-token' > ~/.config/agent-tracker/registry-token
```

Then configure:

```nix
services.agent-tracker.registryTokenFile = "/home/your-user/.config/agent-tracker/registry-token";
```

When `registryAuth = true`, `registryTokenFile` is required.
The tracker daemon reads the token file at runtime and exports `AGENT_REGISTRY_TOKEN` without embedding the secret in the Nix store.

After changing tracker-side Home Manager options, apply them with your normal Home Manager workflow, for example:

```bash
build-and-switch
```

Or equivalently:

```bash
home-manager switch
```

### Disable tracker auth intentionally

For local/dev testing against a registry with auth disabled:

```nix
services.agent-tracker = {
  enable = true;
  registries = [
    { name = "default"; url = "http://127.0.0.1:8080"; }
  ];
  registryAuth = false;
};
```

This leaves local-only agent-tracker usage unaffected and avoids secret setup.

## 7. Minimal end-to-end dev setup

Registry host:

```nix
services.agent-registry = {
  enable = true;
  auth = false;
  port = 8080;
};
```

Tracker host:

```nix
services.agent-tracker = {
  enable = true;
  registries = [
    { name = "default"; url = "http://registry-host:8080"; }
  ];
  registryAuth = false;
};
```

## 8. Managed agents on the registry host

The NixOS module can keep selected agents running locally on the registry host inside tmux.

### 7.1 What you need first

For each managed agent, the target user must already be able to run:

- `tmux`
- the agent command itself, for example `pi` or `claude`
- the wrapper executable referenced by `wrapperPath` (default: `agent-wrapper`)

Those can be provided either through the target user's normal PATH or by using absolute store paths in `command` / `wrapperPath`.

**Important:** `managedAgents` by itself only ensures an agent process exists in tmux. If you also want that managed agent to appear in the global registry (`/agents` on `agents.mundus.in` or another registry), the same machine/user must also be running a local `agent-tracker` with registry integration enabled. In other words:

- `services.agent-registry` + `managedAgents` => starts and keeps the local tmux agent alive
- `services.agent-tracker` + `registries` => discovers that local agent and publishes it to the registry

Without `agent-tracker`, the managed agent is just a local tmux process and will not be reported upstream.

### 7.2 Add a managed agent

Example:

```nix
services.agent-registry = {
  enable = true;
  auth = false;

  managedAgents.nixos-expert = {
    user = "tanmay";
    session = "nix-homelab-config";
    cwd = "~";
    command = "pi";

    # Defaults shown explicitly:
    wrapperPath = "agent-wrapper";
    reconcileIntervalSeconds = 30;
    # By default this now uses the user's normal tmux socket.
    # Set tmuxSocketPath explicitly if you want a dedicated tmux server instead.
    # tmuxSocketPath = "/home/tanmay/.cache/agent-registry/tmux.sock";

    restart = {
      enable = true;
      intervalSeconds = 86400;
      warningLeadTimeSeconds = 300;
      warningMessage = "Restarting in 5 minutes";
    };
  };
};
```

This creates:

- a reconcile service/timer:
  - `agent-registry-managed-nixos-expert.service`
  - `agent-registry-managed-nixos-expert.timer`
- and, if `restart.enable = true`, a restart service/timer:
  - `agent-registry-restart-nixos-expert.service`
  - `agent-registry-restart-nixos-expert.timer`

Apply it normally:

```bash
sudo nixos-rebuild switch --flake .#your-host
```

### 7.3 Important options

| Option | Meaning |
| --- | --- |
| `user` | User that owns the tmux session and runs the agent. |
| `session` | tmux session name to create/reuse. |
| `cwd` | Working directory for the agent process. `"~"` resolves to the target user's home. |
| `command` | Agent command to run, for example `pi`, `claude`, or an absolute path. |
| `wrapperPath` | Wrapper executable used to launch the agent. Default: `agent-wrapper`. |
| `trackerSocketPath` | Optional override for the target user's tracker socket. |
| `tmuxSocketPath` | Optional override for the tmux socket. By default managed agents use the user's normal tmux socket under `XDG_RUNTIME_DIR` (for example `/run/user/<uid>/tmux-<uid>/default`). |
| `reconcileIntervalSeconds` | How often the registry host re-checks that the agent exists. |
| `restart.enable` | Enables scheduled restarts. |
| `restart.onCalendar` | systemd `OnCalendar` restart schedule. |
| `restart.intervalSeconds` | systemd `OnUnitActiveSec` restart schedule. |
| `restart.warningLeadTimeSeconds` | How long to wait after sending the warning before restarting. Default: `300`. |
| `restart.warningMessage` | Optional custom warning message sent into the pane before restart. |

### 7.4 Behavior notes

- Reconcile and restart units run as the target user, not root.
- The module sets explicit `HOME`, `PATH`, `XDG_RUNTIME_DIR`, tracker socket, and tmux socket context.
- **Important:** by default managed agents now use the user's normal tmux socket so they appear in the same tmux server the user interacts with over SSH. If you want isolated/persistent managed-agent tmux state instead, override `tmuxSocketPath` explicitly to a dedicated socket such as `~/.cache/agent-registry/tmux.sock`.
- Managed agents use tmux pane metadata (`@agent_name`) as the idempotency source of truth.
  Manual pane or agent renames can therefore break reconciliation and may cause a duplicate pane to be spawned.
- If tmux is not already running for that socket, the reconcile unit starts it automatically.
- If a scheduled restart finds no live pane for the managed agent, it skips the warning and just starts/reconciles the agent immediately.

### 7.5 Verifying it

Useful checks:

```bash
systemctl status agent-registry-managed-nixos-expert.service
systemctl status agent-registry-managed-nixos-expert.timer
systemctl status agent-registry-restart-nixos-expert.timer
```

If using the default normal tmux socket:

```bash
sudo -u tanmay tmux ls
sudo -u tanmay tmux list-panes -a -F '#S #{pane_id} #{@agent_name}'
```

If you override to a dedicated tmux socket instead:

```bash
sudo -u tanmay tmux -S /home/tanmay/.cache/agent-registry/tmux.sock ls
sudo -u tanmay tmux -S /home/tanmay/.cache/agent-registry/tmux.sock list-panes -a -F '#S #{pane_id} #{@agent_name}'
```

## 8. Notes / current slice scope

This deployment guide reflects the current vertical slice.
Deferred follow-up items include:

- persistence/state file handling
- `DELETE /trackers`
- cross-tracker `POST /messages`
- broader Home Manager integration for the registry service itself
