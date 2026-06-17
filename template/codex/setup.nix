let
  # Auto-detect system in impure mode, fallback to x86_64-linux in pure mode
  detectedSystem = let
    current = builtins.currentSystem or "";
  in if current != "" then current else "x86_64-linux";

  # Auto-detect username in impure mode, fallback to placeholder in pure mode
  detectedUser = let
    envUser = builtins.getEnv "USER";
  in if envUser != "" then envUser else "change-me";

  # Auto-detect home directory in impure mode, fallback to placeholder in pure mode
  detectedHome = let
    envHome = builtins.getEnv "HOME";
  in if envHome != "" then envHome else "/home/change-me";
in
{
  # System architecture (e.g. "x86_64-linux", "aarch64-darwin")
  system = detectedSystem;

  # User details
  username = detectedUser;
  homeDirectory = detectedHome;

  # Broccoli Comms configuration (mirrored from ~/.config/home-manager)
  broccoli = {
    enable = true;
    # Workspace root for your agents
    agentRootDir = "${detectedHome}/agents-root";
    # Remote run settings
    remoteRunEnabled = true;
    authEnabled = false;

    # Broccoli feature toggles
    enable-agent-tracker = true;
    enable-agent-communicator = true;
    enable-agent-communicator-electron = false;
    enable-third-party-agents = false;
    enable-pi-agent = true;
    enable-claude-agent = false;
    enable-codex-agent = false;
    enable-gemini-agent = false;

    # Registry configuration for tracker
    agent-tracker = {
      registry-url = null;
      registry-auth = false;
      registries = [
        { name = "local"; url = "http://127.0.0.1:18000"; }
        { name = "mundus"; url = "https://agents.mundus.in"; }
      ];
    };

    # AI/agent feature flags
    ai_features = {
      enable_agent_knowledge = true;
      enable_ai_ssa_creator_skill = true;
      enable_tmux_based_agent_comms = true;
      enable_home_manager_skill = true;
    };
  };
}
