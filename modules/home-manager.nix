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
  broccoliRuntimeDir = cfg.config.paths.runtimeDir;
  broccoliCacheDir = if cfg.config.paths.cacheDir != null then cfg.config.paths.cacheDir else "${cacheRoot}/broccoli-comms";
  broccoliConfigDir = if cfg.config.paths.configDir != null then cfg.config.paths.configDir else "${configRoot}/broccoli-comms";
  broccoliAgentRootDir = cfg.config.paths.agentRootDir;
  trackerSocket = if broccoliRuntimeDir != null then "${broccoliRuntimeDir}/agent-tracker.sock" else null;
  trackerStdout = "${broccoliCacheDir}/launchd.stdout.log";
  trackerStderr = "${broccoliCacheDir}/launchd.stderr.log";
  trackerHostSuffixPath = "${stateRoot}/broccoli-comms/agent-tracker/hostname-suffix";
  cleanedTrackerRegistries = builtins.map
    (registry: compactAttrs {
      name = registry.name;
      url = registry.url;
      "token-file" = registry."token-file";
    })
    cfg.config.tracker.registries;

  configTomlAttrs = compactAttrs {
    paths = compactAttrs {
      runtime_dir = broccoliRuntimeDir;
      cache_dir = broccoliCacheDir;
      config_dir = broccoliConfigDir;
      "agent-root-dir" = broccoliAgentRootDir;
      tmux_socket = cfg.config.paths.tmuxSocket;
      agent_tracker_socket = cfg.config.paths.trackerSocket;
      permission_detection_config = cfg.config.paths.permissionDetectionConfig;
    };
    tracker = compactAttrs {
      poll_interval = cfg.config.tracker.pollInterval;
      heartbeat_stale_seconds = cfg.config.tracker.heartbeatStaleSeconds;
      heartbeat_grace_seconds = cfg.config.tracker.heartbeatGraceSeconds;
      max_delivery_bytes = cfg.config.tracker.maxDeliveryBytes;
      http_port = cfg.config.tracker.httpPort;
      hostname = cfg.config.tracker.hostname;
      broad_watch_enabled = cfg.config.tracker.broadWatchEnabled;
      tracker_id = cfg.config.tracker.trackerId;
      address = cfg.config.tracker.address;
    };
    registry = compactAttrs {
      auth_enabled = cfg.config.registry.authEnabled;
      token = cfg.config.registry.token;
      heartbeat_seconds = cfg.config.registry.heartbeatSeconds;
      delivery_wait_seconds = cfg.config.registry.deliveryWaitSeconds;
      delivery_target_grace_seconds = cfg.config.registry.deliveryTargetGraceSeconds;
      remote_pane_input_max_text_bytes = cfg.config.registry.remotePaneInputMaxTextBytes;
      remote_pane_input_max_keys = cfg.config.registry.remotePaneInputMaxKeys;
      pane_output_events_enabled = cfg.config.registry.paneOutputEventsEnabled;
      pane_output_event_ttl_seconds = cfg.config.registry.paneOutputEventTtlSeconds;
      remote_run_enabled = cfg.config.registry.remoteRunEnabled;
      endpoints = cleanedTrackerRegistries;
    };
    ui = compactAttrs {
      unseen_inbox_reminder_seconds = cfg.config.ui.unseenInboxReminderSeconds;
      capture_pane_default_lines = cfg.config.ui.capturePaneDefaultLines;
      default_mailbox_name = cfg.config.ui.defaultMailboxName;
      focus_remote_messages = cfg.config.ui.focusRemoteMessages;
      remote_pane_input_enabled = cfg.config.ui.remotePaneInputEnabled;
      debug_log = cfg.config.ui.debugLog;
    };
    core = compactAttrs {
      enable_reliable_send_keys = cfg.config.core.enableReliableSendKeys;
      tmux_mode = cfg.config.core.tmuxMode;
    };
    scheduled_jobs = compactAttrs {
      enabled = cfg.config.scheduledJobs.enable;
      agent_task_nudge = compactAttrs {
        enabled = cfg.config.scheduledJobs.agentTaskNudge.enable;
        interval_seconds = cfg.config.scheduledJobs.agentTaskNudge.intervalSeconds;
        max_nudges = cfg.config.scheduledJobs.agentTaskNudge.maxNudges;
        backoff_multiplier = cfg.config.scheduledJobs.agentTaskNudge.backoffMultiplier;
        state_path = cfg.config.scheduledJobs.agentTaskNudge.statePath;
      };
    };
    pane_output = compactAttrs {
      enabled = cfg.config.paneOutput.enable;
      agent_types = cfg.config.paneOutput.agentTypes;
    };
    agents_md = compactAttrs {
      static_sections = cfg.config.agentsMd.staticSections;
      summarize_memories = cfg.config.agentsMd.summarizeMemories;
      full_expertise = cfg.config.agentsMd.fullExpertise;
    };
    runner = compactAttrs {
      tmux_session = cfg.config.runner.tmuxSession;
    };
    providers = lib.mapAttrs (_: providerToml) cfg.config.providers;
  };

  escapedRegistries = builtins.replaceStrings ["\""] ["\\\""] (builtins.toJSON cleanedTrackerRegistries);

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
  } // optionalEnv "AGENT_TRACKER_HOSTNAME" cfg.config.tracker.hostname
    // optionalEnv "AGENT_TRACKER_TMUX_SOCKET" cfg.tracker.tmuxSocketPath
    // optionalEnv "AGENT_REGISTRY_TOKEN" (if cfg.tracker.registryToken != null then cfg.tracker.registryToken else cfg.config.registry.token)
    // lib.optionalAttrs (cfg.config.tracker.registries != []) {
      AGENT_REGISTRIES_JSON = builtins.toJSON cleanedTrackerRegistries;
    }
    // lib.optionalAttrs cfg.config.ui.remotePaneInputEnabled {
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
    ${lib.optionalString (cfg.config.registry.authEnabled && cfg.tracker.registryTokenFile != null) ''export AGENT_REGISTRY_TOKEN="$(cat ${lib.escapeShellArg (toString cfg.tracker.registryTokenFile)})"''}
    exec ${pcfg.package}/bin/broccoli-comms agent-tracker daemon
  '';

  registryStatePath = if cfg.registry.statePath != null then cfg.registry.statePath else "${stateRoot}/broccoli-comms/agent-registry/state.json";
  registryCacheDir = if cfg.registry.cacheDir != null then cfg.registry.cacheDir else "${broccoliCacheDir}/agent-registry";
  registryEnv = broccoliEnv // {
    PATH = trackerEnv.PATH;
  } // lib.optionalAttrs cfg.config.ui.remotePaneInputEnabled {
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
      ${lib.optionalString (cfg.config.tracker.registries != []) ''
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

    tracker = {
      enable = mkOption { type = types.bool; default = cfg.enable; description = "Enable agent-tracker as a systemd user service or launchd agent. Defaults to services.broccoli-comms.enable."; };
      package = mkOption { type = types.package; default = packages.agentTracker; defaultText = "self.packages.<system>.agentTracker"; description = "Tracker daemon package to execute. Defaults to agentTracker package."; };
      hostname = mkOption {
        type = types.nullOr types.str;
        default = null;
        description = "Optional AGENT_TRACKER_HOSTNAME override. When null, the service generates a stable <machine-hostname>-<three-letter-suffix> identity and stores the suffix under XDG state. Defaults to null.";
      };
      socketPath = mkOption { type = types.nullOr types.str; default = null; description = "Deprecated custom tracker socket path override. Defaults to null."; };
      cacheDir = mkOption { type = types.nullOr types.str; default = null; description = "Deprecated custom tracker cache directory path override. Defaults to null."; };
      tmuxSocketPath = mkOption { type = types.nullOr types.str; default = null; description = "Optional AGENT_TRACKER_TMUX_SOCKET environment variable override. Defaults to null."; };
      registryToken = mkOption { type = types.nullOr types.str; default = null; description = "Inline registry token override. Defaults to null."; };
      registryTokenFile = mkOption { type = types.nullOr types.path; default = null; description = "Registry token file path override. Defaults to null."; };
      environment = mkOption { type = types.attrsOf types.str; default = {}; description = "Extra environment variables for the tracker service. Defaults to empty set."; };
    };

    registry = {
      enable = mkEnableOption "Broccoli Comms agent-registry service";
      package = mkOption { type = types.package; default = packages.agentRegistry; defaultText = "self.packages.<system>.agentRegistry"; description = "Registry daemon package to execute. Defaults to agentRegistry package."; };
      host = mkOption { type = types.str; default = "127.0.0.1"; description = "Bind host for agent-registry. Defaults to 127.0.0.1."; };
      port = mkOption { type = types.port; default = 18000; description = "Bind port for agent-registry. Defaults to 18000."; };
      name = mkOption { type = types.str; default = "local"; description = "Logical registry name used by `broccoli-comms registry start`. Defaults to local."; };
      auth = mkOption { type = types.bool; default = false; description = "Enable authentication for the registry service daemon. Defaults to false."; };
      tokenFile = mkOption { type = types.nullOr types.path; default = null; description = "Registry token file path containing valid client credentials. Defaults to null."; };
      staleSeconds = mkOption { type = types.int; default = 60; description = "Seconds of inactivity before peer heartbeats are marked stale. Defaults to 60."; };
      goneSeconds = mkOption { type = types.int; default = 180; description = "Seconds of inactivity before peer registration is fully pruned. Defaults to 180."; };
      statePath = mkOption { type = types.nullOr types.str; default = null; description = "Path to the registry state JSON file. Defaults to state database path."; };
      cacheDir = mkOption { type = types.nullOr types.str; default = null; description = "Path to the registry cache directory. Defaults to cache directory path."; };
      environment = mkOption { type = types.attrsOf types.str; default = {}; description = "Extra environment variables for the registry service. Defaults to empty set."; };
    };

    config = {
      paths = {
        runtimeDir = mkOption { type = types.nullOr types.str; default = null; description = "Optional custom runtime directory. If null, the CLI defaults to \${XDG_RUNTIME_DIR:-/tmp/\$UID}/broccoli-comms. Defaults to null."; };
        cacheDir = mkOption { type = types.nullOr types.str; default = null; description = "Optional custom cache/log directory. If null, defaults to ~/.cache/broccoli-comms. Defaults to null."; };
        configDir = mkOption { type = types.nullOr types.str; default = null; description = "Optional custom config directory. If null, defaults to ~/.config/broccoli-comms. Defaults to null."; };
        agentRootDir = mkOption { type = types.nullOr types.str; default = null; description = "Stable root for agent workspaces. When null, temporary workspaces are generated. Defaults to null."; };
        tmuxSocket = mkOption { type = types.nullOr types.str; default = null; description = "Fallback path to the private tmux socket used when tmuxMode is set to \"private\". Defaults to null."; };
        trackerSocket = mkOption { type = types.nullOr types.str; default = null; description = "Deprecated custom tracker socket path override. Defaults to null."; };
        permissionDetectionConfig = mkOption { type = types.nullOr types.str; default = null; description = "Custom JSON path containing allowed system command prefixes and settings. Defaults to null."; };
      };

      tracker = {
        pollInterval = mkOption { type = types.ints.positive; default = 5; description = "Frequency (in seconds) that the tracker polls agent tmux pane status. Defaults to 5."; };
        heartbeatStaleSeconds = mkOption { type = types.ints.positive; default = 20; description = "Mark an agent's heartbeat as stale after this duration (in seconds). Defaults to 20."; };
        heartbeatGraceSeconds = mkOption { type = types.ints.positive; default = 30; description = "Mark an agent as gone/inactive after this additional grace period (in seconds). Defaults to 30."; };
        maxDeliveryBytes = mkOption { type = types.ints.positive; default = 5242880; description = "Maximum body size limit (in bytes) for incoming registry HTTP requests. Defaults to 5242880."; };
        httpPort = mkOption { type = types.port; default = 19876; description = "The local port the tracker runs its HTTP server on. Defaults to 19876."; };
        hostname = mkOption { type = types.nullOr types.str; default = null; description = "Host name override. If null, a stable <hostname>-<3-letter-random-suffix> is generated. Defaults to null."; };
        broadWatchEnabled = mkOption { type = types.bool; default = false; description = "Enable broad/transitive workspace watching. Defaults to false."; };
        trackerId = mkOption { type = types.nullOr types.str; default = null; description = "Specific UUID override for this tracker. Defaults to a DNS UUID derived from hostname if null. Defaults to null."; };
        address = mkOption { type = types.nullOr types.str; default = null; description = "External IP/hostname address of this tracker. If null, defaults to the hostname. Defaults to null."; };
        registries = mkOption { type = types.listOf registrySpecType; default = []; description = "List of target central registries. Each contains name (string), url (string), and optional token-file (string). Defaults to []."; };
      };

      registry = {
        authEnabled = mkOption { type = types.bool; default = true; description = "Enable central registry authentication validation. Defaults to true."; };
        token = mkOption { type = types.nullOr types.str; default = null; description = "Inline access token string. Prefer registryTokenFile for security. Defaults to null."; };
        heartbeatSeconds = mkOption { type = types.ints.positive; default = 30; description = "Frequency (in seconds) the tracker pushes status updates to the registry. Defaults to 30."; };
        deliveryWaitSeconds = mkOption { type = types.ints.positive; default = 25; description = "Registry long-poll wait time before timeout (in seconds). Defaults to 25."; };
        deliveryTargetGraceSeconds = mkOption { type = types.ints.positive; default = 60; description = "Post-delivery grace period before messages are pruned (in seconds). Defaults to 60."; };
        remotePaneInputMaxTextBytes = mkOption { type = types.ints.positive; default = 4096; description = "Size limit (in bytes) for text payloads received from remote control requests. Defaults to 4096."; };
        remotePaneInputMaxKeys = mkOption { type = types.ints.positive; default = 16; description = "Keypress event limit per remote command packet. Defaults to 16."; };
        paneOutputEventsEnabled = mkOption { type = types.bool; default = true; description = "Log and publish tmux output buffers to the central registry. Defaults to true."; };
        paneOutputEventTtlSeconds = mkOption { type = types.ints.positive; default = 86400; description = "TTL (in seconds) for published output events on the registry. Defaults to 86400 (24 hours)."; };
        remoteRunEnabled = mkOption { type = types.bool; default = true; description = "Allow starting agents remotely via the registry. Defaults to true."; };
      };

      ui = {
        unseenInboxReminderSeconds = mkOption { type = types.ints.positive; default = 900; description = "Reminder interval (in seconds) for unacknowledged inbox items. Defaults to 900 (15 minutes)."; };
        capturePaneDefaultLines = mkOption { type = types.ints.positive; default = 20; description = "Default number of scrollback lines captured when inspecting agent terminals. Defaults to 20."; };
        defaultMailboxName = mkOption { type = types.str; default = "agent-communicator"; description = "Default pane mailbox identity. Defaults to \"agent-communicator\"."; };
        focusRemoteMessages = mkOption { type = types.bool; default = false; description = "Automatically shift focus to incoming messages from remote hosts. Defaults to false."; };
        remotePaneInputEnabled = mkOption { type = types.bool; default = true; description = "Boolean check for the TUI to decide if remote pane inputs are supported. Defaults to true."; };
        debugLog = mkOption { type = types.nullOr types.str; default = null; description = "Output destination file path for TUI logs (e.g. debug logging info). Defaults to null."; };
      };

      core = {
        enableReliableSendKeys = mkOption { type = types.bool; default = true; description = "Utilize rate-limited tmux inputs to guarantee keystroke ordering. Defaults to true."; };
        tmuxMode = mkOption { type = types.enum [ "default" "private" ]; default = "default"; description = "Determines how the CLI resolves tmux socket paths. \"private\" redirects CLI queries to the configured fallback socket under paths.tmuxSocket. Defaults to \"default\"."; };
      };

      scheduledJobs = {
        enable = mkOption { type = types.bool; default = true; description = "Enable central background job runner. Defaults to true."; };
        agentTaskNudge = {
          enable = mkOption { type = types.bool; default = true; description = "Activate periodic nudging of active agents when tasks remain stagnant. Defaults to true."; };
          intervalSeconds = mkOption { type = types.ints.positive; default = 600; description = "Nudge frequency interval in seconds. Defaults to 600 (10 minutes)."; };
          maxNudges = mkOption { type = types.ints.positive; default = 5; description = "Maximum number of reminders sent for a stagnant task. Defaults to 5."; };
          backoffMultiplier = mkOption { type = types.ints.positive; default = 2; description = "Exponential backoff multiplier between consecutive reminders. Defaults to 2."; };
          statePath = mkOption { type = types.nullOr types.str; default = null; description = "Custom JSON path to store sent-nudge counts per task. Defaults to a database under the cache dir. Defaults to null."; };
        };
      };

      paneOutput = {
        enable = mkOption { type = types.bool; default = false; description = "Enable active background capturing of terminal updates. Defaults to false."; };
        agentTypes = mkOption { type = types.listOf types.str; default = []; description = "Capture only terminal feeds belonging to these specific agent types (e.g. [\"pi\", \"claude\"]). Defaults to []."; };
      };

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

      agentsMd = {
        staticSections = mkOption {
          type = types.listOf types.str;
          default = [];
          description = "A list of custom static Markdown sections (guidelines, rules, contracts) to include in AGENTS.md. If empty, the system falls back to the standard default agent operating contract sections. Defaults to [].";
        };
        summarizeMemories = mkOption {
          type = types.bool;
          default = true;
          description = "Whether to summarize non-expertise memories (facts, habits, episodes) in AGENTS.md by listing only their titles, IDs, and the exact command to view them in full, rather than printing their entire bodies. Defaults to true.";
        };
        fullExpertise = mkOption {
          type = types.bool;
          default = true;
          description = "Whether to include the full content of expertise memories in AGENTS.md to preserve the agent's persona. Defaults to true.";
        };
      };
      runner = {
        tmuxSession = mkOption {
          type = types.str;
          default = "broccoli-comms-agents";
          description = "The name of the tmux session where Broccoli Comms starts and manages agents. Defaults to \"broccoli-comms-agents\".";
        };
      };
    };
  };

  config = lib.mkMerge [
    {
      warnings = lib.optionals (cfg.config.paths.trackerSocket != null) [
        "services.broccoli-comms.config.paths.trackerSocket is deprecated; set services.broccoli-comms.config.paths.runtimeDir instead."
      ] ++ lib.optionals (cfg.tracker.socketPath != null) [
        "services.broccoli-comms.tracker.socketPath is deprecated; set services.broccoli-comms.config.paths.runtimeDir instead."
      ] ++ lib.optionals (cfg.tracker.cacheDir != null) [
        "services.broccoli-comms.tracker.cacheDir is deprecated; set services.broccoli-comms.config.paths.cacheDir instead."
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
      assertions = [{ assertion = !cfg.config.registry.authEnabled || cfg.tracker.registryTokenFile != null || cfg.tracker.registryToken != null || cfg.config.registry.token != null; message = "services.broccoli-comms.tracker.registryTokenFile, registryToken, or config.registry.token is required when authEnabled is enabled."; }];
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
        if ! /bin/launchctl print "$domain" >/dev/null 2>&1; then
          domain="user/$(id -u)"
        fi
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
        if ! /bin/launchctl print "$domain" >/dev/null 2>&1; then
          domain="user/$(id -u)"
        fi
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
