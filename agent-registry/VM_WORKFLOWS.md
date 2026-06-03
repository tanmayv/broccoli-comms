# VM Workflows for `agent-registry`

> **Note**: This is a registry dev/ops operational document.

Use two VM modes:

- **Stateful dev VM** for fast iteration and manual debugging
- **Stateless test VM** for clean end-to-end verification

## 1. Stateful dev VM

The flake exports a reusable development VM:

```bash
nix run path:/path/to/broccoli-comms/agent-registry#devvm
```

This launches the `devvm` NixOS configuration from the flake.

Note: this dev VM target is currently oriented around `x86_64-linux` and typical Linux-host NixOS VM workflows.

### What it includes

- `services.agent-registry.enable = true`
- registry auth disabled for local iteration
- SSH enabled
- user `dev` / password `dev`
- `nix-command` + `flakes` enabled
- `dev` is a Nix trusted user so you can push closures and rebuild remotely
- forwarded ports:
  - host `2222` -> guest `22`
  - host `8080` -> guest `8080`

The VM is intentionally generic: it does **not** hardcode a managed-agent spec. You boot the base VM once, then push your current `agent-registry` closure/config into it from the host while iterating.

### Persistence / statefulness

The generated `run-*-vm` script keeps a qcow2 disk image in the working directory and reuses it on later boots. That makes this VM appropriate for iterative development: user home, tmux state, and service state can persist across restarts.

### Useful commands

Boot the VM:

```bash
nix run .#devvm
```

SSH into it:

```bash
ssh -p 2222 dev@127.0.0.1
```

Check the registry:

```bash
curl http://127.0.0.1:8080/healthz
```

Inspect the managed-agent tmux server inside the VM:

```bash
ssh -p 2222 dev@127.0.0.1 'tmux -S /home/dev/.cache/agent-registry/tmux.sock list-panes -a -F "#{session_name} #{pane_id} #{@agent_name}"'
```

### Iterating on local changes

From the host, deploy updated config into the running VM:

```bash
NIX_SSHOPTS='-p 2222' \
sudo nixos-rebuild test \
  --flake path:$PWD/agent-registry#devvm \
  --target-host dev@127.0.0.1 \
  --sudo
```

If you want the config to persist as the active system profile, use `switch` instead of `test`.

Because `dev` is configured as a trusted Nix user, this VM is suitable for repeated closure pushes from the host during development.

Recommended use cases:

- tmux behavior debugging
- managed-agent reconcile/restart iteration
- timer behavior checks
- manual registry smoke tests
- repeatedly pushing your current host-built closure into a long-lived VM

## 2. Stateless end-to-end VM test

For clean, reproducible validation, use the stateless NixOS test:

```bash
nix build .#checks.x86_64-linux.managed-agent --no-link -L
```

This test:

- builds a fresh NixOS test VM
- boots from clean state
- enables the registry module
- reconciles a managed agent into tmux
- verifies restart behavior

Because the VM is recreated from scratch every run, this catches hidden dependencies on leftover tmux sessions, old cache files, or prior system state.

## 3. Recommended workflow

### During development

1. Boot the stateful dev VM once.
2. Iterate using `nixos-rebuild test --target-host ...`.
3. Run focused manual smoke tests inside the VM.

### Before final review / merge

1. Run the stateless NixOS check:
   ```bash
   nix build .#checks.x86_64-linux.managed-agent --no-link -L
   ```
2. Run Python/unit tests as needed:
   ```bash
   python3 -m unittest agent-registry/test_managed_agent.py
   ```

## 4. Why both modes exist

- **Stateful dev VM** improves development speed and debugging ergonomics.
- **Stateless test VM** provides confidence that the feature works from a clean boot with no hidden state.

Use both. Do not rely on the stateful VM alone for final validation.
