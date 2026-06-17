{ config, pkgs, lib, inputs, setup, ... }:
let
  stop-trackers = pkgs.writeShellScriptBin "stop-trackers" ''
    echo "Searching for running agent-tracker processes..."
    
    # Get PIDs of processes matching "agent-tracker", excluding our own PID
    pids=$(pgrep -f "agent-tracker" | grep -v "$$" || true)
    # Clean up whitespace/newlines
    pids=$(echo "$pids" | xargs || true)

    if [ -z "$pids" ]; then
        echo "No running agent-tracker processes found."
        exit 0
    fi

    # Convert to comma-separated for ps
    pids_comma=$(echo "$pids" | tr ' ' ',')

    echo "Found the following agent-tracker processes:"
    ps -p "$pids_comma" -o pid,args 2>/dev/null || ps -p "$pids_comma" -o pid,command || echo "$pids"
    echo

    echo "Stopping processes..."
    for pid in $pids; do
        echo "Stopping PID $pid..."
        kill -15 "$pid" 2>/dev/null || true
    done

    # Wait a moment for graceful shutdown
    sleep 1

    # Force kill any that are still running
    for pid in $pids; do
        if kill -0 "$pid" 2>/dev/null; then
            echo "PID $pid still running, sending SIGKILL..."
            kill -9 "$pid" 2>/dev/null || true
        fi
    done

    echo "Done. All matching agent-tracker processes stopped."
  '';
in
{
  # Home Manager needs a bit of information about you and the
  # paths it should manage.
  # IMPORTANT: Update setup.nix with your actual username and home directory if not using impure mode!
  home.username = lib.mkDefault setup.username;
  home.homeDirectory = lib.mkDefault setup.homeDirectory;

  # This value determines the Home Manager release that your
  # configuration is compatible with. This helps avoid breakage
  # when a new Home Manager release introduces backwards
  # incompatible changes.
  home.stateVersion = "24.05";

  # The home.packages option allows you to install Nix packages into your
  # environment.
  home.packages = with pkgs; [
    # The Codex CLI package
    codex
    
    # Core utilities needed by Broccoli Comms agents
    tmux
    git
    curl
    jq
    
    # Utility to stop all running agent trackers
    stop-trackers

    # The Pi coding agent binary from pi.nix flake
    inputs.pi-nix.packages.${pkgs.system}.default
  ];

  # Enable the Broccoli Comms agent tracker service
  services.broccoli-comms = {
    enable = lib.mkDefault setup.broccoli.enable;
    
    # Define settings for your agent tracker
    config = {
      paths = {
        # Workspace root for your agents
        agentRootDir = lib.mkDefault setup.broccoli.agentRootDir;
      };
      
      # Enable remote run to allow central orchestration
      registry = {
        remoteRunEnabled = lib.mkDefault setup.broccoli.remoteRunEnabled;
        authEnabled = lib.mkDefault setup.broccoli.authEnabled;
      };
    };
  };

  # Let Home Manager install and manage itself.
  programs.home-manager.enable = true;

  # Import our modular configurations
  imports = [
    ./neovim.nix
    ./tmux.nix
  ];
}
