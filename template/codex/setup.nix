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

  # Broccoli Comms configuration
  broccoli = {
    enable = true;
    # Workspace root for your agents
    agentRootDir = "${detectedHome}/agents-root";
    # Remote run settings
    remoteRunEnabled = true;
    authEnabled = false;
  };
}
