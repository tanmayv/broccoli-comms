{ config, pkgs, ... }: {
  programs.tmux = {
    enable = true;
    shortcut = "a"; # Use Ctrl-a as prefix, which is very common and agent-friendly
    keyMode = "vi";
    customPaneNavigationAndResize = true;

    extraConfig = ''
      # Split panes using | and - (more intuitive)
      bind | split-window -h -c "#{pane_current_path}"
      bind - split-window -v -c "#{pane_current_path}"
      unbind '"'
      unbind %

      # Enable mouse mode for scrolling and pane resizing
      set -g mouse on

      # Improve colors and terminal overrides
      set -g default-terminal "screen-256color"

      # Don't rename windows automatically (prevent clutter)
      set-option -g allow-rename off

      # Increase scrollback history limit
      set -g history-limit 10000

      # Shift-arrow to switch windows
      bind -n S-Left  previous-window
      bind -n S-Right next-window
    '';
  };
}
