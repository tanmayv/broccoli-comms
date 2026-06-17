{ config, pkgs, lib, inputs, setup, ... }: {
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
