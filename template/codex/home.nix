{ config, pkgs, lib, ... }: {
  # Home Manager needs a bit of information about you and the
  # paths it should manage.
  # IMPORTANT: Update these placeholders with your actual username and home directory!
  home.username = lib.mkDefault "change-me";
  home.homeDirectory = lib.mkDefault "/home/change-me";

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
  ];

  # Enable the Broccoli Comms agent tracker service
  services.broccoli-comms = {
    enable = true;
    
    # Define settings for your agent tracker
    config = {
      paths = {
        # Workspace root for your agents
        agentRootDir = "${config.home.homeDirectory}/agents-root";
      };
      
      # Enable remote run to allow central orchestration
      registry = {
        remoteRunEnabled = true;
        authEnabled = false;
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
