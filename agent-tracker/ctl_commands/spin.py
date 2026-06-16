import argparse
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
import tempfile
import tomllib
import uuid

from .common import call_rpc, spin_session_name


DEFAULT_AGENT_CONTRACT_TEMPLATE = """# Agent Operating Contract

You are: {agent}
Agent profile: {agent}
Instance: {instance}
Ephemeral cwd: {cwd}
Launch/source cwd: {source_cwd}

For file/project queries not related to agent memory, read/search files under the ephemeral cwd by default. Use the launch/source cwd only when the user explicitly asks for it or the task names it.

Durable state lives in Broccoli Comms. Do not rely on either cwd for memory.

## Required startup
1. Run `broccoli-comms task bootstrap --agent {agent} --json` or `broccoli-comms task next --agent {agent} --include-profile --json`.
2. If a task is returned, run `broccoli-comms state show --task <task_id> --agent {agent} --json`.
3. If no task is ready, stand by and do not invent work.

## Checkpoint and discovery rules
- Update WorkingState when starting, blocking, requesting review, finishing, or discovering reusable facts.
- Use `broccoli-comms state set --task <task_id> --agent {agent} --instance <agent_instance_id> --task-chain-id <chain> --root-task-id <root> --status working --current-activity ... --next-step ... --notes ... --clarification-count N --correction-count N --need-improvements-count N --first-pass-success|--no-first-pass-success --json` for bounded checkpoints.
- Checkpoint important discoveries such as database names, table names, commands/tools used, blockers, assumptions, and the next_step.
- Example: when finding the right database, record the database/table name and why it was correct as a bounded checkpoint so future agents can avoid rediscovery.

## Result and validation workflow
- When completing work, update the task with a bounded result_summary suitable for user validation.
- When a task chain or scoped phase is complete, use task-chain completion submission before review/validation: `broccoli-comms task submit-completion <task_id> --summary ... --task-chain-id <chain> --root-task-id <root> --json`; after it is approved/validated, run `broccoli-comms task summarize-chain <task_chain_id> --json` so future agents can resume from a bounded chain summary.
- When the user says a result is correct, ensure the task is marked `good`/`validated` with `broccoli-comms task mark-result <task_id> --result good --json` so the append-only event log captures: goal -> checkpoints/discoveries -> result summary -> user validation.
- For partial or incorrect results, include a remediation next_step with `task mark-result --result bad|need_improvements --next-step ...`.

## Clarification and correction tracking
- Record each user clarification or correction as bounded task/state/result metadata, not raw chat.
- Use structured `state set` metadata flags for clarification_count, correction_count, need_improvements_count, and first_pass_success so these metrics are derivable from `working_state_set` events.
- Before marking good/validated, ensure the append-only log can show whether success was first-pass or correction-assisted.
- For examples like finding the right database, checkpoint user corrections/clarifications and the final corrected database/table name so future agents know why the answer was correct.

## Parallel instances and task chains
- `{agent}` is the stable agent profile/name; each process has its own runtime agent_instance_id and ephemeral workspace.
- Multiple active instances of the same profile may work in parallel on different task_chain_id/root_task_id values while sharing the ordered append-only log.
- Do not overwrite another instance's same-chain checkpoint; if duplicate same-profile instances target the same active task chain, ask the coordinator to resolve claiming.

## Immutable / non-learning instances
- Phase 1 durable CLI commands are for normal reproducible learning instances.
- If you are explicitly launched as immutable or non-learning, treat work as transient: do not write state checkpoints, task results, or learning events to the persistent append-only log.
- Immutable/non-learning messages or results cannot be used for future validated learning unless a coordinator explicitly records them separately as learning-eligible facts.
- Do not silently claim task chains that require durable validation when operating in immutable/non-learning mode.

## Safety and memory boundaries
- Ask user/coordinator for context if confidence is low.
- Explicit user instructions override task/profile/habits.
- Save durable outputs only through Broccoli Comms commands.
- Never store raw terminal transcripts, full query logs, secrets, tokens, passwords, or large file contents in task/state/result text.
- Keep task/state/result text concise and bounded; store facts and conclusions, not bulky evidence.

## Tasks and Memory Management

### Adding a Task
Only skip task creation for true one-off user queries that have easy answers and require no investigation, file/code inspection, command execution, durable state, or verifiable deliverable. Anything that requires investigation must be created and tracked as a task first.
"""


def _config_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "broccoli-comms" / "config.toml"


def agent_contract_template() -> str | None:
    env_template = os.environ.get("BROCCOLI_COMMS_AGENT_CONTRACT_TEMPLATE")
    if env_template:
        return env_template
    path = _config_path()
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            cfg = tomllib.load(f)
        agents_md = cfg.get("agents_md") or {}
        if agents_md.get("static_sections"):
            return "\n\n---\n\n".join(agents_md["static_sections"])
        learning = cfg.get("learning") or {}
        if learning.get("agent_contract_template"):
            return str(learning["agent_contract_template"])
        if learning.get("agent_contract_template_path"):
            return Path(str(learning["agent_contract_template_path"])).expanduser().read_text()
    except Exception:
        return None
    return None


def agent_contract(agent: str, instance: str, cwd: str, source_cwd: str | None = None) -> str:
    source_cwd = source_cwd or cwd
    return (agent_contract_template() or DEFAULT_AGENT_CONTRACT_TEMPLATE).format(
        agent=agent,
        instance=instance,
        cwd=cwd,
        ephemeral_cwd=cwd,
        source_cwd=source_cwd,
        launch_cwd=source_cwd,
    )


def ephemeral_workspace(agent: str) -> str:
    safe_agent = re.sub(r"[^A-Za-z0-9_.-]", "-", agent).strip("-._") or "agent"
    base = Path(os.environ.get("BROCCOLI_COMMS_EPHEMERAL_BASE", tempfile.gettempdir())) / "broccoli-agents" / safe_agent
    path = base / uuid.uuid4().hex[:12]
    path.mkdir(parents=True, exist_ok=False)
    return str(path)


def write_agents_contract(directory: str, agent: str, instance: str | None = None, source_cwd: str | None = None) -> Path:
    path = Path(directory) / "AGENTS.md"
    path.write_text(agent_contract(agent, instance or f"{agent}@pending", directory, source_cwd=source_cwd))
    return path


def register(subparsers):
    parser = subparsers.add_parser("spin", help="Spin a new agent in a tmux session for a directory")
    parser.add_argument("--no-fallback", "-n", action="store_true", help="Disable automatic bash shell wrapper and zsh fallback")
    parser.add_argument("directory", help="Working directory; leaf name becomes the tmux session/agent base name")
    parser.add_argument("agent_command", help="Agent command to run")
    parser.add_argument("agent_args", nargs=argparse.REMAINDER, help="Arguments for the agent command")
    parser.set_defaults(handler=handle)


def resolve_agent_wrapper_path() -> str:
    """Find the standalone agent-wrapper from env, PATH, or source checkout."""
    env_val = os.environ.get("BROCCOLI_COMMS_AGENT_WRAPPER")
    if env_val:
        return env_val

    on_path = shutil.which("agent-wrapper")
    if on_path:
        return on_path

    source_tree_wrapper = Path(__file__).resolve().parents[2] / "wrapper" / "agent-wrapper.sh"
    if source_tree_wrapper.exists():
        return str(source_tree_wrapper)

    return "agent-wrapper"


def _same_executable(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    if left == right or os.path.realpath(left) == os.path.realpath(right):
        return True
    try:
        return os.path.exists(left) and os.path.exists(right) and os.path.samefile(left, right)
    except OSError:
        return False


def is_agent_wrapper_command(argv: list[str], wrapper_path: str | None = None) -> bool:
    if not argv:
        return False
    command = argv[0]
    if os.path.basename(command) == "agent-wrapper":
        return True
    wrapper_path = wrapper_path or resolve_agent_wrapper_path()
    return _same_executable(command, wrapper_path) or _same_executable(command, shutil.which("agent-wrapper"))


def build_wrapped_agent_argv(agent_command: str, agent_args: list[str]) -> list[str]:
    argv = [agent_command] + list(agent_args or [])
    wrapper_path = resolve_agent_wrapper_path()
    if is_agent_wrapper_command(argv, wrapper_path):
        return argv
    return [wrapper_path] + argv


def build_spin_command(agent_command: str, agent_args: list[str], no_fallback: bool) -> str:
    inner_command = shlex.join(build_wrapped_agent_argv(agent_command, agent_args))
    if no_fallback:
        return inner_command
    caller_path = os.environ.get("PATH", "")
    return f"bash -c {shlex.quote(f'export PATH={shlex.quote(caller_path)}; {inner_command}; zsh')}"


def handle(args):
    directory = os.path.abspath(os.path.expanduser(args.directory))
    if not os.path.isdir(directory):
        print(f"Error: directory does not exist: {directory}", file=sys.stderr)
        sys.exit(1)
    session = spin_session_name(directory)
    source_directory = directory
    directory = ephemeral_workspace(session)
    write_agents_contract(directory, session, source_cwd=source_directory)
    
    command = build_spin_command(args.agent_command, args.agent_args, args.no_fallback)

    # Do not forward the caller agent's identity to the spun agent.  The
    # tracker/RPC side assigns a fresh placeholder name and passes it as
    # SUGGESTED_AGENT_NAME after resolving conflicts.
    env = {k: v for k, v in os.environ.items() if k not in {"TMUX", "TMUX_PANE", "AGENT_ID", "AGENT_NAME", "AGENT_UUID"}}
    resolved_name = call_rpc("spin_agent", {
        "session": session,
        "directory": directory,
        "command": command,
        "name": session,
        "env": {**env, "BROCCOLI_COMMS_SOURCE_CWD": source_directory, "BROCCOLI_COMMS_EPHEMERAL_CWD": directory},
    })
    if resolved_name:
        print(f"Agent spun successfully as: {resolved_name} in session: {session}")
