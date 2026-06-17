import os
import sys

from .common import call_rpc, parse_target_params


def register(subparsers):
    parser = subparsers.add_parser(
        "send-message",
        help="Send message to a local agent or a remote host-qualified target",
        description=(
            "Send a message to a local or remote agent.\n"
            "Examples:\n"
            "  agent-tracker-ctl send-message alice \"hello\"\n"
            "  agent-tracker-ctl send-message host-a/alice \"hello\"\n"
            "  agent-tracker-ctl send-message host-a/123e4567-e89b-12d3-a456-426614174000 \"hello\"\n"
            "  agent-tracker-ctl send-message --swarm-context backend-fix alice \"hello\"\n"
            "\n"
            "Bare names/UUIDs are always local-only. Use HOST/TARGET for remote delivery via agent-registry."
        ),
    )
    parser.add_argument("target", nargs="?", metavar="TARGET", help="Local agent name/UUID or remote HOST/NAME_OR_UUID")
    parser.add_argument("message", help="Message text")
    parser.add_argument("--id", dest="agent_id", help="Target local agent ID (legacy local-only form)")
    parser.add_argument("--swarm-context", dest="swarm_context", help="Optional swarm name to classify user-targeted message in a swarm timeline")
    parser.add_argument("--verify", action="store_true", help="Wait for delivery confirmation in target pane")
    parser.add_argument("--interrupt", "--interupt", dest="interrupt", action="store_true", help="Interrupt the agent by sending Escape key to its pane first")
    parser.set_defaults(handler=handle)


def handle(args):
    if not args.target and not args.agent_id:
        print("Error: send-message requires <target> or --id <agent_id>", file=sys.stderr)
        sys.exit(1)
    params = {"message": args.message}
    if "AGENT_ID" in os.environ:
        params["sender_id"] = os.environ["AGENT_ID"]
    elif "AGENT_NAME" in os.environ:
        params["sender_name"] = os.environ["AGENT_NAME"]
    if args.agent_id:
        params["agent_id"] = args.agent_id
    else:
        params.update(parse_target_params(args.target))

    if args.swarm_context:
        params["swarm_context"] = args.swarm_context

    if args.verify:
        params["verify"] = True

    if args.interrupt:
        params["interrupt"] = True

    res = call_rpc("send_message", params)
    if isinstance(res, dict) and res.get("warning"):
        print(res["warning"], file=sys.stderr)

    if isinstance(res, dict) and not res.get("success", True):
        print(f"Error: {res.get('error', 'Notification delivery failed')}", file=sys.stderr)
        sys.exit(1)

    print("Message sent.")
