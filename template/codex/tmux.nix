{ config, pkgs, ... }:
let
  palette = {
    background = "#1a1b26";
    foreground = "#c0caf5";
    color0 = "#15161e";
    color1 = "#f7768e";
    color2 = "#9ece6a";
    color3 = "#e0af68";
    color4 = "#7aa2f7";
    color5 = "#bb9af7";
    color6 = "#7dcfff";
    color7 = "#a9b1d6";
    color8 = "#414868";
    color9 = "#f7768e";
    color10 = "#9ece6a";
    color11 = "#e0af68";
    color12 = "#7aa2f7";
    color13 = "#bb9af7";
    color14 = "#7dcfff";
    color15 = "#c0caf5";
  };

  tmux-session-list-formatter = pkgs.writeScriptBin "tmux-session-list-formatter" ''
    #!${pkgs.python3}/bin/python3
    import sys

    if len(sys.argv) < 3:
        sys.exit(1)

    width = int(sys.argv[1])
    current = sys.argv[2]
    available = width - 10 # Margin for right buttons

    lines = sys.stdin.read().splitlines()

    sessions = []
    for line in lines:
        parts = line.split("|")
        if len(parts) == 3:
            sessions.append({"created": int(parts[0]), "name": parts[1], "id": parts[2]})

    # Sort by timestamp (oldest first)
    sessions.sort(key=lambda s: s["created"])

    # Remove sessions until it fits based on visible length (names)
    truncated = False
    while len(' · '.join(s["name"] for s in sessions)) > available and len(sessions) > 1:
        sessions.pop() # Remove from the end (newest)
        truncated = True

    formatted = []
    for s in sessions:
        name = s["name"]
        sid = s["id"]
        if name == current:
            display_name = f"#[fg=${palette.color3},bold]{name}#[fg=${palette.color8},nobold]"
        else:
            display_name = name
        formatted.append(f"#[range=session|{sid}]{display_name}#[norange]")

    output = ' · '.join(formatted)

    if truncated:
        output += " · ..."

    print(output)
  '';

  tmux-status-refresh = pkgs.writeScriptBin "tmux-status-refresh" ''
    #!${pkgs.python3}/bin/python3
    import json
    import os
    import subprocess
    import sys

    def run_cmd(cmd):
        try:
            return subprocess.check_output(cmd, shell=True, text=True).strip()
        except subprocess.CalledProcessError:
            return ""

    def main():
        config_path = os.path.expanduser("~/.config/tmux/status-refresh.json")
        if not os.path.exists(config_path):
            print(f"Config file not found: {config_path}", file=sys.stderr)
            # Fallback to basic status on
            subprocess.run(["tmux", "set", "-g", "status", "on"])
            return

        with open(config_path, "r") as f:
            config = json.load(f)

        palette = config.get("palette", config)
        color4 = palette.get("color4", "#7aa2f7")
        color8 = palette.get("color8", "#414868")

        lines = {}
        line_idx = 1

        # 1. Core row 1 (Active Sessions)
        num_sessions = int(run_cmd("tmux list-sessions 2>/dev/null | wc -l") or "0")
        if num_sessions > 1:
            sessions_part = f"#[align=left,fg={color4},bold] Active Sessions: #[fg={color8},nobold]#(tmux list-sessions -F \"##{{session_created}}|##{{session_name}}|##{{session_id}}\" | tmux-session-list-formatter 150 \"#S\")"
            lines[line_idx] = sessions_part
            line_idx += 1

        # 2. Process extra lines from extensions
        extra_lines = config.get("extraLines", [])
        for line in extra_lines:
            condition = line.get("condition", "true")
            # Evaluate condition by running it in shell
            res = subprocess.run(condition, shell=True, capture_output=True)
            if res.returncode == 0:
                lines[line_idx] = f"#[align=left]{line.get('command')}"
                line_idx += 1

        total_lines = line_idx - 1

        if total_lines == 0:
            subprocess.run(["tmux", "set", "-g", "status", "on"])
        else:
            subprocess.run(["tmux", "set", "-g", "status", str(total_lines + 1)])
            for idx, content in lines.items():
                subprocess.run(["tmux", "set", "-g", f"status-format[{idx}]", content])

    if __name__ == "__main__":
        main()
  '';
in {
  home.packages = [
    tmux-session-list-formatter
    tmux-status-refresh
  ];

  xdg.configFile."tmux/status-refresh.json".text = builtins.toJSON {
    palette = {
      inherit (palette) background foreground color4 color8;
    };
    extraLines = [
      {
        name = "agents";
        command = "#(broccoli-comms agent-tracker status-bar '#{pane_id}')";
        condition = "true";
      }
    ];
    row0Right = [];
  };

  programs.tmux = {
    enable = true;
    shortcut = "a";
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
      set -ga terminal-overrides ",*256col*:Tc"

      # Don't rename windows automatically (prevent clutter)
      set-option -g allow-rename off
      set-window-option -g automatic-rename on
      set-option -g set-titles off

      # Increase scrollback history limit
      set -g history-limit 10000

      # Shift-arrow to switch windows
      bind -n S-Left  previous-window
      bind -n S-Right next-window

      # Scroll in alternate screen (TUI apps) using Up/Down arrow keys instead of entering copy-mode
      bind -n WheelUpPane if-shell -F -t= "#{mouse_any_flag}" "send-keys -M" "if -Ft= '#{pane_in_mode}' 'send-keys -M' 'if -Ft= \"#{alternate_on}\" \"send-keys -t= Up Up Up\" \"copy-mode -et=\"'"
      bind -n WheelDownPane if-shell -F -t= "#{mouse_any_flag}" "send-keys -M" "if -Ft= '#{pane_in_mode}' 'send-keys -M' 'if -Ft= \"#{alternate_on}\" \"send-keys -t= Down Down Down\"'"

      # Reload config shortcut
      bind r source-file ~/.config/tmux/tmux.conf \; display-message "Config reloaded!"

      # Pane border and title configuration
      set -g pane-border-status bottom
      set -g pane-border-format "#[bg=${palette.background},fg=${palette.color8}]─(#[bg=${palette.background},fg=${palette.color5}] #D #[bg=${palette.background},fg=${palette.color8}]| #[bg=${palette.background},fg=${palette.color4}]#{?@agent_name,#{@agent_name},no-name} #[bg=${palette.background},fg=${palette.color8}]| #[bg=${palette.background},fg=${palette.color2}]#T #[bg=${palette.background},fg=${palette.color8}])─"
      set -g pane-border-style "bg=${palette.background},fg=${palette.color8}"
      set -g pane-active-border-style "bg=${palette.background},fg=${palette.color4}"

      # Status Bar Position
      set -g status-position bottom

      # tmux-dotbar Tokyo Night theme configuration
      set -g status-justify "absolute-centre"
      set -g status-left-length 60
      set -g status-left "#{?client_prefix,#[bg=${palette.color2} fg=${palette.background} bold],#[fg=${palette.color4} bold]} #S #[default]"
      set -g status-right-length 120
      set -g status-right ""
      set -g window-status-format " #W "
      set -g window-status-current-format "#[bg=${palette.background},fg=${palette.color3},bold] #W #[fg=${palette.color4},bg=${palette.background}]#{?window_zoomed_flag,󰊓,}#[fg=${palette.color8},bg=${palette.background}]"
      set -g window-status-separator " • "
      set -g status-style "bg=${palette.background},fg=${palette.color8}"
      set -g window-status-style "bg=${palette.background},fg=${palette.color8}"
      set -g window-style "bg=${palette.background},fg=${palette.foreground}"
      set -g window-active-style "bg=${palette.background},fg=${palette.foreground}"

      # Set default status bar to 1 line
      set -g status on
      set -g status-interval 5
      set -g detach-on-destroy off

      # Dynamic status line management based on session and agent count
      set-hook -g session-created 'run-shell "tmux-status-refresh"'
      set-hook -g session-closed 'run-shell "tmux-status-refresh"'
      set-hook -g pane-exited 'run-shell "tmux-status-refresh"'

      # Run the check immediately on config load
      run-shell "tmux-status-refresh"

      # Global mouse binding to handle status bar clicks
      bind-key -n MouseDown1Status if-shell -F '#{==:#{mouse_status_range},session}' \
          { switch-client -t = } \
          { if-shell -F '#{m:agent:*,#{mouse_status_range}}' \
              { 
                  # Extract pane_id from agent:pane_id
                  target_id=$(echo '#{mouse_status_range}' | cut -d: -f2); \
                  if tmux list-panes -a -F '##{pane_id}' | grep -q \"^\$target_id\$\"; then \
                      tmux switch-client -t \"\$target_id\"; \
                      tmux select-pane -t \"\$target_id\"; \
                  else \
                      broccoli-comms agent-tracker unregister --pane \"\$target_id\"; \
                      tmux display-message \"Agent pane not found, entry removed\"; \
                  fi
              } \
              { select-window -t = } \
          }

      # Agent navigation shortcuts (from agent-tracker module)
      bind-key N run-shell "broccoli-comms agent-tracker focus --next"
      bind-key P run-shell "broccoli-comms agent-tracker focus --prev"
      bind-key -n MouseDown3Status if-shell -F '#{==:#{mouse_status_range},agent-registries}' \
          { display-popup -w 80% -h 40% -E "broccoli-comms agent-tracker registry-status; echo; printf 'Press Enter to close...'; read _" }
    '';
  };
}
