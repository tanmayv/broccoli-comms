import argparse
import sys
from .common import call_rpc

def register(subparsers):
    parser = subparsers.add_parser("restart", help="Restart a local or remote agent")
    parser.add_argument("target", help="Agent name, ID, or host-qualified address (e.g., host-a/alice)")
    parser.set_defaults(handler=handle, skip_ensure=True)

def handle(args):
    params = {"target_address": args.target}
    try:
        success = call_rpc("restart_agent", params)
        if success:
            print(f"Restart request sent successfully to target '{args.target}'.")
        else:
            print(f"Failed to send restart request to target '{args.target}'.", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
