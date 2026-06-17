import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import ctl_commands.common as _common
from ctl_commands.common import *  # re-export helpers for tests/backward compatibility
from ctl_commands import daemon, ensure_running, focus, list as list_cmd, read_inbox, registry_status, rename, save, send_key, send_message, send_text, spin, status_bar, unregister, whoami, capture_pane, send_pane, pane_output, assign_swarm, request_stop, restart

def _sync_common_overrides():
    _common.REGISTRY_STATUS_PATH = REGISTRY_STATUS_PATH
    _common.urllib = urllib


def load_registry_status():
    _sync_common_overrides()
    return _common.load_registry_status()


def registry_connection_states(status=None, now=None):
    _sync_common_overrides()
    return _common.registry_connection_states(status=status, now=now)


def is_registry_connected(now=None):
    _sync_common_overrides()
    return _common.is_registry_connected(now=now)


def fetch_registry_agents(timeout=3.0):
    _sync_common_overrides()
    return _common.fetch_registry_agents(timeout=timeout)


COMMAND_MODULES = [
    list_cmd,
    status_bar,
    registry_status,
    ensure_running,
    daemon,
    send_message,
    send_text,
    send_key,
    focus,
    rename,
    spin,
    read_inbox,
    whoami,
    unregister,
    save,
    capture_pane,
    send_pane,
    pane_output,
    assign_swarm,
    request_stop,
    restart,
]


def build_parser():
    parser = argparse.ArgumentParser(
        description="Agent Tracker Control",
        epilog=(
            "Remote messaging via agent-registry:\n"
            "  send-message alice \"hello\"                 # local-only by bare name\n"
            "  send-message 123e4567-e89b-12d3-a456-426614174000 \"hello\"  # local-only by bare UUID\n"
            "  send-message host-a/alice \"hello\"          # remote by hostname/name\n"
            "  send-message host-a/123e4567-e89b-12d3-a456-426614174000 \"hello\"  # remote by hostname/UUID\n"
            "\n"
            "Direct pane input (local; bypasses inbox messages):\n"
            "  send-text alice \"hello\"                 # type text and press Enter\n"
            "  send-text --no-submit alice \"draft\"    # type text without Enter\n"
            "  send-key alice C-c Enter                  # send symbolic key tokens\n"
            "\n"
            "Bare names/UUIDs stay local-only. Host-qualified targets require registry integration; remote direct input is currently disabled."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Subcommands")
    for module in COMMAND_MODULES:
        module.register(subparsers)
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not args.subcommand:
        parser.print_help()
        return
    if not getattr(args, "skip_ensure", False) and not ensure_tracker_running():
        print("Error: failed to start or connect to agent-tracker.", file=sys.stderr)
        sys.exit(1)
    args.handler(args)


if __name__ == "__main__":
    main()
