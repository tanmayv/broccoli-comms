import argparse
import sys
from .common import call_rpc

def register(subparsers):
    parser = subparsers.add_parser("request-stop", help="Request a local or remote agent to stop")
    parser.add_argument("target", help="Agent name, ID, or host-qualified address (e.g., host-a/alice)")
    parser.add_argument("--timeout", default="60s", help="Timeout warning duration (default: 60s)")
    parser.add_argument("--force", action="store_true", help="Force immediate termination using SIGTERM, bypassing graceful warning")
    parser.set_defaults(handler=handle, skip_ensure=True)

def handle(args):
    params = {"target_address": args.target, "timeout": args.timeout, "force": args.force}
    try:
        success = call_rpc("request_stop", params)
        if success:
            print(f"Stop request sent successfully to target '{args.target}'.")
        else:
            print(f"Failed to send stop request to target '{args.target}'.", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
