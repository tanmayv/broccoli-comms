self: { config, lib, pkgs, ... }:
let
  cfg = config.services.broccoli-comms;
  pcfg = config.programs.broccoli-comms;
  packages = self.packages.${pkgs.system};
  configTomlFormat = pkgs.formats.toml {};
  defaultInitialMessage = "Check if any task are assigned to you, if yes, start working on them, else be on standby";

  registrySpecType = lib.types.submodule {
    options = {
      name = lib.mkOption { type = lib.types.str; description = "Registry name."; };
      url = lib.mkOption { type = lib.types.str; description = "Registry base URL."; };
      token-file = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "Optional token file for this registry.";
      };
    };
  };

  providerSpecType = lib.types.submodule ({ name, ... }: {
    options = {
      cmd = lib.mkOption { type = lib.types.str; default = name; description = "Provider executable or absolute command path."; };
      agentsDir = lib.mkOption { type = lib.types.nullOr lib.types.str; default = null; description = "Optional provider-specific agents directory."; };
      contextLayout = lib.mkOption { type = lib.types.nullOr lib.types.str; default = null; description = "Optional provider bootstrap context layout, for example jetski for root AGENTS.md plus .agents/rules and .agents/skills."; };
      agentRootDir = lib.mkOption { type = lib.types.nullOr lib.types.str; default = null; description = "Optional stable root for this provider's agent workspaces."; };
      autoAcceptFlag = lib.mkOption { type = lib.types.str; default = ""; description = "Provider flag that enables auto-accept/auto-approve behavior. Empty disables it."; };
      promptFlagName = lib.mkOption { type = lib.types.str; default = "--"; description = "Provider flag for the initial prompt. Use -- when the prompt is positional."; };
      initialMessage = lib.mkOption { type = lib.types.nullOr lib.types.str; default = defaultInitialMessage; description = "Initial prompt passed to the provider; null disables it. Defaults to checking assigned tasks and standing by when none are ready."; };
      tmuxSubmitKey = lib.mkOption { type = lib.types.nullOr lib.types.str; default = null; description = "Optional tmux key used to submit text for this provider, for example C-M."; };
      extraSettings = lib.mkOption { type = lib.types.attrs; default = {}; description = "Additional raw TOML settings for this provider."; };
    };
  });

  compactAttrs = lib.filterAttrsRecursive (_: value: value != null);
  providerToml = provider: compactAttrs ({
    cmd = provider.cmd;
    agentsDir = provider.agentsDir;
    "context-layout" = provider.contextLayout;
    "agent-root-dir" = provider.agentRootDir;
    "auto-accept-flag" = provider.autoAcceptFlag;
    "prompt-flag-name" = provider.promptFlagName;
    "initial-message" = provider.initialMessage;
    "tmux-submit-key" = provider.tmuxSubmitKey;
  } // provider.extraSettings);

  cacheRoot = config.xdg.cacheHome or "${config.home.homeDirectory}/.cache";
  stateRoot = config.xdg.stateHome or "${config.home.homeDirectory}/.local/state";
  configRoot = config.xdg.configHome or "${config.home.homeDirectory}/.config";

  # Keep the default runtime/socket owned by the Broccoli CLI itself:
  # ${XDG_RUNTIME_DIR:-/tmp/$UID}/broccoli-comms/agent-tracker.sock.
  # Home Manager only pins BROCCOLI_COMMS_RUNTIME_DIR when explicitly configured.
  broccoliRuntimeDir = cfg.runtimeDir;
  broccoliCacheDir = if cfg.cacheDir != null then cfg.cacheDir else "${cacheRoot}/broccoli-comms";
  broccoliConfigDir = if cfg.configDir != null then cfg.configDir else "${configRoot}/broccoli-comms";
  broccoliAgentRootDir = cfg.agentRootDir;
  trackerSocket = if broccoliRuntimeDir != null then "${broccoliRuntimeDir}/agent-tracker.sock" else null;
  trackerStdout = "${broccoliCacheDir}/launchd.stdout.log";
  trackerStderr = "${broccoliCacheDir}/launchd.stderr.log";
  trackerHostSuffixPath = "${stateRoot}/broccoli-comms/agent-tracker/hostname-suffix";

  configTomlAttrs = compactAttrs {
    paths = compactAttrs {
      runtime_dir = broccoliRuntimeDir;
      cache_dir = broccoliCacheDir;
      config_dir = broccoliConfigDir;
      "agent-root-dir" = broccoliAgentRootDir;
    };
    tracker.http_port = cfg.tracker.httpPort;
    registry = {
      heartbeat_seconds = cfg.tracker.registryHeartbeatSeconds;
      auth_enabled = cfg.tracker.registryAuth;
      remote_pane_input_enabled = cfg.tracker.remotePaneInput.enable;
    };
    ui.capture_pane_default_lines = cfg.tracker.capturePaneDefaultLines;
    core.enable_reliable_send_keys = cfg.tracker.enableReliableSendKeys;
    scheduled_jobs = {
      enabled = true;
      agent_task_nudge = {
        enabled = true;
        interval_seconds = cfg.tracker.agentTaskNudgeIntervalSeconds;
        backoff_multiplier = cfg.tracker.agentTaskNudgeBackoffMultiplier;
        max_nudges = cfg.tracker.agentTaskNudgeMaxNudges;
      };
    };
    providers = lib.mapAttrs (_: providerToml) cfg.providers;
  };

  escapedRegistries = builtins.replaceStrings ["\""] ["\\\""] (builtins.toJSON cfg.tracker.registries);

  envList = attrs: lib.mapAttrsToList (name: value: "${name}=\"${builtins.replaceStrings ["\""] ["\\\""] (toString value)}\"") attrs;
  optionalEnv = name: value: lib.optionalAttrs (value != null) { ${name} = value; };

  broccoliEnv = {};

  broccoliSessionEnv = broccoliEnv // optionalEnv "AGENT_TRACKER_SOCKET" trackerSocket;

  trackerEnv = broccoliEnv // {
    PATH = lib.concatStringsSep ":" [
      "${config.home.homeDirectory}/.nix-profile/bin"
      "/etc/profiles/per-user/${config.home.username}/bin"
      "/nix/var/nix/profiles/default/bin"
      "/run/current-system/sw/bin"
      "/usr/local/bin"
      "/opt/homebrew/bin"
      "/usr/bin"
      "/bin"
      "/usr/sbin"
      "/sbin"
      (lib.makeBinPath [ pkgs.tmux pkgs.coreutils pkgs.gnugrep pkgs.procps pkgs.bash ])
    ];
  } // optionalEnv "AGENT_TRACKER_HOSTNAME" cfg.tracker.hostname
    // optionalEnv "AGENT_TRACKER_TMUX_SOCKET" cfg.tracker.tmuxSocketPath
    // optionalEnv "AGENT_REGISTRY_TOKEN" cfg.tracker.registryToken
    // lib.optionalAttrs (cfg.tracker.registries != []) {
      AGENT_REGISTRIES_JSON = builtins.toJSON cfg.tracker.registries;
    }
    // lib.optionalAttrs cfg.tracker.remotePaneInput.enable {
      BROCCOLI_COMMS_REMOTE_PANE_INPUT_ENABLED = "1";
    }
    // cfg.tracker.environment;

  trackerStart = pkgs.writeShellScript "broccoli-comms-agent-tracker-start" ''
    if [ -z "''${AGENT_TRACKER_HOSTNAME:-}" ]; then
      suffix_file=${lib.escapeShellArg trackerHostSuffixPath}
      mkdir -p "$(dirname "$suffix_file")"
      if [ ! -s "$suffix_file" ]; then
        ${pkgs.python3}/bin/python3 - <<'PY' > "$suffix_file"
import random
import string
print("".join(random.choice(string.ascii_lowercase) for _ in range(3)))
PY
      fi
      suffix="$(tr -cd 'a-z' < "$suffix_file" | cut -c1-3)"
      if [ "''${#suffix}" -ne 3 ]; then
        suffix="$(${pkgs.python3}/bin/python3 - <<'PY'
import random
import string
print("".join(random.choice(string.ascii_lowercase) for _ in range(3)))
PY
)"
        printf '%s\n' "$suffix" > "$suffix_file"
      fi
      base="$(hostname -s 2>/dev/null || hostname 2>/dev/null || printf '%s' ${lib.escapeShellArg config.home.username})"
      base="$(printf '%s' "$base" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9._-' '-' | sed 's/^-//; s/-$//')"
      export AGENT_TRACKER_HOSTNAME="''${base:-${config.home.username}}-$suffix"
    fi
    ${lib.optionalString (cfg.tracker.registryAuth && cfg.tracker.registryTokenFile != null) ''export AGENT_REGISTRY_TOKEN="$(cat ${lib.escapeShellArg (toString cfg.tracker.registryTokenFile)})"''}
    exec ${pcfg.package}/bin/broccoli-comms agent-tracker daemon
  '';

  registryStatePath = if cfg.registry.statePath != null then cfg.registry.statePath else "${stateRoot}/broccoli-comms/agent-registry/state.json";
  registryCacheDir = if cfg.registry.cacheDir != null then cfg.registry.cacheDir else "${broccoliCacheDir}/agent-registry";
  registryEnv = broccoliEnv // {
    PATH = trackerEnv.PATH;
  } // lib.optionalAttrs cfg.tracker.remotePaneInput.enable {
    BROCCOLI_COMMS_REMOTE_PANE_INPUT_REGISTRY_ENABLED = "1";
    AGENT_REGISTRY_REMOTE_PANE_INPUT_ENABLED = "1";
  } // cfg.registry.environment;
  registryStart = pkgs.writeShellScript "broccoli-comms-agent-registry-start" ''
    exec ${pcfg.package}/bin/broccoli-comms registry start --foreground --force \
      --host ${lib.escapeShellArg cfg.registry.host} \
      --port ${toString cfg.registry.port} \
      --name ${lib.escapeShellArg cfg.registry.name} \
      --state-path ${lib.escapeShellArg registryStatePath} \
      --stale-seconds ${toString cfg.registry.staleSeconds} \
      --gone-seconds ${toString cfg.registry.goneSeconds} \
      ${if cfg.registry.auth then "--auth --token-file ${lib.escapeShellArg (toString cfg.registry.tokenFile)}" else "--noauth"}
  '';

  broccoliCommsCli = pkgs.writeShellApplication {
    name = "broccoli-comms";
    runtimeInputs = [ pcfg.package ];
    text = ''
      ${lib.optionalString (cfg.tracker.registries != []) ''
        if [ -z "''${AGENT_REGISTRIES_JSON:-}" ]; then
          export AGENT_REGISTRIES_JSON="${escapedRegistries}"
        fi
      ''}
      exec ${pcfg.package}/bin/broccoli-comms "$@"
    '';
  };

  installedPackages =
    lib.optionals pcfg.enable ([ broccoliCommsCli ]
      ++ lib.optional pcfg.install.tracker packages.agentTracker
      ++ lib.optional pcfg.install.trackerCtl packages.agentTrackerCtl
      ++ lib.optional pcfg.install.wrapper packages.agentWrapper
      ++ lib.optional pcfg.install.registry packages.agentRegistry
      ++ lib.optional pcfg.install.managedAgent packages.managedAgent
      ++ lib.optional pcfg.install.tui packages.agentCommunicator);
in {
  options.programs.broccoli-comms = with lib; {
    enable = mkEnableOption "Broccoli Comms command-line tools";
    package = mkOption {
      type = types.package;
      default = packages.broccoliComms;
      defaultText = "self.packages.<system>.broccoliComms";
      description = "Main broccoli-comms CLI package.";
    };
    install = {
      tracker = mkOption { type = types.bool; default = false; description = "Install the low-level agent-tracker binary. Prefer `broccoli-comms agent-tracker ...` for the app-owned runtime."; };
      trackerCtl = mkOption { type = types.bool; default = false; description = "Install the low-level agent-tracker-ctl binary. Prefer `broccoli-comms agent-tracker ...` for the app-owned runtime."; };
      wrapper = mkOption { type = types.bool; default = false; description = "Install the low-level agent-wrapper binary. Prefer `broccoli-comms track -- ...` for the app-owned runtime."; };
      registry = mkOption { type = types.bool; default = false; description = "Install agent-registry."; };
      managedAgent = mkOption { type = types.bool; default = false; description = "Install agent-registry-managed-agent."; };
      tui = mkOption { type = types.bool; default = false; description = "Install the low-level agent-communicator TUI. Prefer `broccoli-comms ui` for the app-owned runtime."; };
      defaultSkills = mkOption { type = types.bool; default = true; description = "Install Broccoli Comms default agent skills into Pi, Claude, Gemini, and shared .agents skill directories."; };
    };
  };

  options.services.broccoli-comms = with lib; {
    enable = mkEnableOption "Broccoli Comms agent-tracker service";
    runtimeDir = mkOption { type = types.nullOr types.str; default = null; description = "Optional BROCCOLI_COMMS_RUNTIME_DIR. When unset, Broccoli Comms uses its canonical CLI default: \${XDG_RUNTIME_DIR:-/tmp/$UID}/broccoli-comms."; };
    cacheDir = mkOption { type = types.nullOr types.str; default = null; description = "Optional BROCCOLI_COMMS_CACHE_DIR. Defaults to ~/.cache/broccoli-comms for Home Manager-managed logs and state."; };
    configDir = mkOption { type = types.nullOr types.str; default = null; description = "Optional BROCCOLI_COMMS_CONFIG_DIR. Defaults to ~/.config/broccoli-comms."; };
    agentRootDir = mkOption { type = types.nullOr types.str; default = null; description = "Optional stable root for `broccoli-comms run` agent workspaces. When unset, run uses temp directories; when set, workspaces are <agentRootDir>/<agent-name>."; };
    providers = mkOption {
      type = types.attrsOf providerSpecType;
      default = {
        jetski = {
          cmd = "/google/bin/releases/jetski-devs/tools/cli";
          agentsDir = ".agents";
          contextLayout = "jetski";
          agentRootDir = "${config.home.homeDirectory}/agents-root";
          autoAcceptFlag = "--dangerously-skip-permissions";
          promptFlagName = "--prompt-interactive";
        };
        pi = {
          cmd = "pi";
          autoAcceptFlag = "";
        };
        codex = {
          cmd = "codex";
          autoAcceptFlag = "--dangerously-bypass-approvals-and-sandbox";
        };
        claude = {
          cmd = "claude";
          agentRootDir = "${config.home.homeDirectory}/.agents-root";
          autoAcceptFlag = "--dangerously-skip-permissions";
        };
      };
      description = "Provider defaults rendered to ~/.config/broccoli-comms/config.toml under [providers.<name>].";
    };

    tracker = {
      enable = mkOption { type = types.bool; default = cfg.enable; description = "Enable agent-tracker as a systemd user service or launchd agent."; };
      package = mkOption { type = types.package; default = packages.agentTracker; defaultText = "self.packages.<system>.agentTracker"; };
      hostname = mkOption {
        type = types.nullOr types.str;
        default = null;
        description = "Optional AGENT_TRACKER_HOSTNAME override. When null, the service generates a stable <machine-hostname>-<three-letter-suffix> identity and stores the suffix under XDG state.";
      };
      socketPath = mkOption { type = types.nullOr types.str; default = null; description = "Deprecated and ignored by the Broccoli Comms app-owned runtime. Set services.broccoli-comms.runtimeDir instead; the socket is <runtimeDir>/agent-tracker.sock."; };
      cacheDir = mkOption { type = types.nullOr types.str; default = null; description = "Deprecated and ignored by the Broccoli Comms app-owned runtime. Set services.broccoli-comms.cacheDir instead."; };
      tmuxSocketPath = mkOption { type = types.nullOr types.str; default = null; description = "Optional AGENT_TRACKER_TMUX_SOCKET."; };
      httpPort = mkOption { type = types.port; default = 19876; };
      registries = mkOption { type = types.listOf registrySpecType; default = []; description = "Registries published to AGENT_REGISTRIES_JSON."; };
      registryAuth = mkOption { type = types.bool; default = false; };
      registryTokenFile = mkOption { type = types.nullOr types.path; default = null; };
      registryToken = mkOption { type = types.nullOr types.str; default = null; description = "Inline registry token. Prefer registryTokenFile for secrets."; };
      registryHeartbeatSeconds = mkOption { type = types.ints.positive; default = 30; };
      enableReliableSendKeys = mkOption { type = types.bool; default = true; };
      capturePaneDefaultLines = mkOption { type = types.ints.positive; default = 20; };
      agentTaskNudgeIntervalSeconds = mkOption { type = types.ints.positive; default = 600; description = "Base frequency, in seconds, for the local-agent scheduled task nudge job."; };
      agentTaskNudgeBackoffMultiplier = mkOption { type = types.ints.positive; default = 2; description = "Per-task exponential backoff multiplier for scheduled task nudges."; };
      agentTaskNudgeMaxNudges = mkOption { type = types.ints.unsigned; default = 5; description = "Maximum number of scheduled nudges to send for a single task."; };
      remotePaneInput.enable = mkOption { type = types.bool; default = true; description = "Enable registry-routed remote direct pane input by default. Set to false to opt out of remote pane control."; };
      environment = mkOption { type = types.attrsOf types.str; default = {}; description = "Extra environment for the tracker service."; };
    };

    registry = {
      enable = mkEnableOption "Broccoli Comms agent-registry service";
      package = mkOption { type = types.package; default = packages.agentRegistry; defaultText = "self.packages.<system>.agentRegistry"; };
      host = mkOption { type = types.str; default = "127.0.0.1"; description = "Bind host for agent-registry."; };
      port = mkOption { type = types.port; default = 18000; };
      name = mkOption { type = types.str; default = "local"; description = "Logical registry name used by `broccoli-comms registry start`."; };
      auth = mkOption { type = types.bool; default = false; };
      tokenFile = mkOption { type = types.nullOr types.path; default = null; };
      staleSeconds = mkOption { type = types.int; default = 60; };
      goneSeconds = mkOption { type = types.int; default = 180; };
      statePath = mkOption { type = types.nullOr types.str; default = null; };
      cacheDir = mkOption { type = types.nullOr types.str; default = null; };
      environment = mkOption { type = types.attrsOf types.str; default = {}; };
    };
  };

  config = lib.mkMerge [
    {
      warnings = lib.optionals (cfg.tracker.socketPath != null) [
        "services.broccoli-comms.tracker.socketPath is deprecated and ignored by the app-owned Broccoli Comms runtime; set services.broccoli-comms.runtimeDir instead."
      ] ++ lib.optionals (cfg.tracker.cacheDir != null) [
        "services.broccoli-comms.tracker.cacheDir is deprecated and ignored by the app-owned Broccoli Comms runtime; set services.broccoli-comms.cacheDir instead."
      ];
    }

    (lib.mkIf (cfg.tracker.enable || cfg.registry.enable) {
      programs.broccoli-comms.enable = lib.mkDefault true;
      home.sessionVariables = broccoliSessionEnv;
    })

    (lib.mkIf pcfg.enable {
      home.packages = installedPackages;
      home.file = lib.mkIf pcfg.install.defaultSkills {
        ".pi/agent/skills/broccoli-comms-cli/SKILL.md".source = "${packages.defaultSkills}/broccoli-comms-cli/SKILL.md";
        ".pi/agent/skills/agent-memory-audit/SKILL.md".source = "${packages.defaultSkills}/agent-memory-audit/SKILL.md";
        ".claude/skills/broccoli-comms-cli/SKILL.md".source = "${packages.defaultSkills}/broccoli-comms-cli/SKILL.md";
        ".claude/skills/agent-memory-audit/SKILL.md".source = "${packages.defaultSkills}/agent-memory-audit/SKILL.md";
        ".gemini/skills/broccoli-comms-cli/SKILL.md".source = "${packages.defaultSkills}/broccoli-comms-cli/SKILL.md";
        ".gemini/skills/agent-memory-audit/SKILL.md".source = "${packages.defaultSkills}/agent-memory-audit/SKILL.md";
        ".agents/skills/broccoli-comms-cli/SKILL.md".source = "${packages.defaultSkills}/broccoli-comms-cli/SKILL.md";
        ".agents/skills/agent-memory-audit/SKILL.md".source = "${packages.defaultSkills}/agent-memory-audit/SKILL.md";
      };
      xdg.configFile."broccoli-comms/config.toml".source = configTomlFormat.generate "broccolicommsconfig.toml" configTomlAttrs;
    })

    (lib.mkIf cfg.tracker.enable {
      assertions = [{ assertion = !cfg.tracker.registryAuth || cfg.tracker.registryTokenFile != null || cfg.tracker.registryToken != null; message = "services.broccoli-comms.tracker.registryTokenFile or registryToken is required when registryAuth is enabled."; }];
      home.activation.ensureBroccoliCommsRuntimeDirs = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
        mkdir -p ${lib.escapeShellArg broccoliCacheDir} ${lib.escapeShellArg broccoliConfigDir}
        ${lib.optionalString (broccoliRuntimeDir != null) "mkdir -p ${lib.escapeShellArg broccoliRuntimeDir}"}
      '';
    })

    (lib.mkIf (cfg.tracker.enable && pkgs.stdenv.isLinux) {
      systemd.user.services.broccoli-comms-agent-tracker = {
        Unit.Description = "Broccoli Comms agent-tracker daemon";
        Service = {
          Type = "simple";
          Restart = "on-failure";
          Environment = envList trackerEnv;
          ExecStart = toString trackerStart;
          ExecStop = "${pcfg.package}/bin/broccoli-comms stop";
        };
        Install.WantedBy = [ "default.target" ];
      };
    })

    (lib.mkIf (cfg.tracker.enable && pkgs.stdenv.isDarwin) {
      home.activation.restartBroccoliCommsTracker = lib.hm.dag.entryAfter [ "setupLaunchAgents" ] ''
        label="org.nix-community.home.broccoli-comms-agent-tracker"
        domain="gui/$(id -u)"
        service="$domain/$label"
        plist="$HOME/Library/LaunchAgents/$label.plist"
        if [ -f "$plist" ]; then
          /bin/launchctl bootout "$service" >/dev/null 2>&1 || true
          for _ in 1 2 3 4 5; do
            if ! /bin/launchctl print "$service" >/dev/null 2>&1; then
              break
            fi
            /bin/sleep 1
          done
          /bin/launchctl bootstrap "$domain" "$plist" >/dev/null 2>&1 || true
          /bin/launchctl kickstart -k "$service" >/dev/null 2>&1 || true
        fi
      '';

      launchd.agents.broccoli-comms-agent-tracker = {
        enable = true;
        config = {
          ProgramArguments = [ (toString trackerStart) ];
          EnvironmentVariables = trackerEnv;
          KeepAlive = false;
          RunAtLoad = true;
          ProcessType = "Background";
          StandardOutPath = trackerStdout;
          StandardErrorPath = trackerStderr;
        };
      };
    })

    (lib.mkIf cfg.registry.enable {
      assertions = [{ assertion = !cfg.registry.auth || cfg.registry.tokenFile != null; message = "services.broccoli-comms.registry.tokenFile is required when auth is enabled."; }];
      home.activation.ensureBroccoliCommsRegistryDirs = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
        mkdir -p ${lib.escapeShellArg (builtins.dirOf registryStatePath)} ${lib.escapeShellArg registryCacheDir}
      '';
    })

    (lib.mkIf (cfg.registry.enable && pkgs.stdenv.isLinux) {
      systemd.user.services.broccoli-comms-agent-registry = {
        Unit.Description = "Broccoli Comms agent-registry";
        Service = {
          Environment = envList registryEnv;
          ExecStart = toString registryStart;
          Restart = "always";
        };
        Install.WantedBy = [ "default.target" ];
      };
    })

    (lib.mkIf (cfg.registry.enable && pkgs.stdenv.isDarwin) {
      home.activation.restartBroccoliCommsRegistry = lib.hm.dag.entryAfter [ "setupLaunchAgents" ] ''
        label="org.nix-community.home.broccoli-comms-agent-registry"
        domain="gui/$(id -u)"
        service="$domain/$label"
        plist="$HOME/Library/LaunchAgents/$label.plist"
        if [ -f "$plist" ]; then
          /bin/launchctl bootout "$service" >/dev/null 2>&1 || true
          for _ in 1 2 3 4 5; do
            if ! /bin/launchctl print "$service" >/dev/null 2>&1; then
              break
            fi
            /bin/sleep 1
          done
          /bin/launchctl bootstrap "$domain" "$plist" >/dev/null 2>&1 || true
          /bin/launchctl kickstart -k "$service" >/dev/null 2>&1 || true
        fi
      '';

      launchd.agents.broccoli-comms-agent-registry = {
        enable = true;
        config = {
          ProgramArguments = [ (toString registryStart) ];
          EnvironmentVariables = registryEnv;
          KeepAlive = true;
          RunAtLoad = true;
          ProcessType = "Background";
          StandardOutPath = "${registryCacheDir}/launchd.stdout.log";
          StandardErrorPath = "${registryCacheDir}/launchd.stderr.log";
        };
      };
    })
  ];
}
