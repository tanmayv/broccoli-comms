---
name: agent-memory-audit
description: Audit a Broccoli Comms agent's tasks/events and approved memory, then propose concise memory additions, edits, or archives with the current simplified memory API. Use when asked to review or update an agent's durable memory from feedback, task chains, or event logs.
allowed-tools: bash read
---

# Agent Memory Audit Skill

Use this skill when the user asks to audit, refresh, improve, prune, or update an agent's durable memory based on Broccoli Comms tasks, validation, feedback, or append-only event logs.

The goal is to produce a conservative memory-maintenance report first. Agents create proposals only; active memory changes require a trusted user/coordinator decision.

## Core principles

- Durable memory is in Broccoli Comms, not generated cwd files.
- The append-only event log, task results, working state, and task-chain summaries are the evidence sources.
- Prefer validated-good evidence. Treat reviewer blockers and user corrections as strong signals, but describe uncertainty.
- Keep memory concise, scoped, reusable, and safe for bootstrap.
- Never store secrets, tokens, passwords, raw transcripts, full command logs, or large file contents.
- Do not directly edit generated `memory.md`, `expertise.md`, or generated `skills/*/SKILL.md`; they are bootstrap outputs, not the source of truth.
- Do not self-approve memory or directly mutate active memory from an untrusted agent runtime.

## Audit workflow

### 1. Identify audit target

Determine:

- `AGENT`: stable agent/profile name to audit.
- Optional `SCOPE`: project/team/global scope.
- Optional window: task ids, task-chain id, event limit, or recent validated tasks.

If unclear, ask the user for the target agent and scope.

### 2. Gather bounded durable state

Use Broccoli Comms CLI:

```bash
broccoli-comms task list --agent AGENT --include-archived --json
broccoli-comms state list --agent AGENT --json
broccoli-comms memory list --agent AGENT --json
broccoli-comms memory budget --agent AGENT --json
```

For relevant tasks/chains:

```bash
broccoli-comms task show TASK_ID --json
broccoli-comms events list --task TASK_ID --limit 200 --json
broccoli-comms task summarize-chain TASK_CHAIN_ID --json  # only when a fresh summary is explicitly needed
```

For important existing memories:

```bash
broccoli-comms memory show MEMORY_ID --json
broccoli-comms memory history MEMORY_ID --json
```

Keep notes bounded: record conclusions and ids, not bulky evidence.

### 3. Classify evidence & Pruning

Review task descriptions, working-state checkpoints, completion submissions, reviewer decisions, `need_improvements` loops, user corrections/clarifications, and current active/pending memory.

Always compare new discoveries against existing active and pending memories. Prioritize pruning, consolidating duplicates, or re-classifying existing memories to the correct category over adding redundant new ones.

Classify memories strictly according to these definitions:
- **Expertise**: Exclusively used to capture the **persona of the agent** (role, traits, responsibilities, boundary rules, and collaboration partners). Do NOT use expertise for technical facts, system designs, or workarounds.
- **Habits**: Used to capture **operational behaviors**, style rules, tool preferences, or recurring behavioral constraints (e.g., "always run nix flake check before submitting").
- **Skills**: Used to capture step-by-step playbooks on **how to use specific tools** or execute complex multi-step procedures (e.g., how to mock environment directories in unit tests).
- **Facts**: Used to remember stable, concrete, and verifiable data (e.g., specific database paths, API endpoints, table names).
- **Episodes**: Used to record a **specific issue and its resolution** (e.g., a specific debugging trajectory, what failed, why it failed, and how it was fixed, or a major pitfall encountered during a task).
- **Stale/Redundant Memory**: Obsolete, duplicate, or incorrectly classified entries. Propose to archive or edit/consolidate them immediately.

### 4. Draft candidate memory changes

For each candidate include:

- action: `propose`, `propose-edit`, `propose-archive`, `decide-approve`, `decide-reject`, or `no-op`
- type, scope, subject_agent, title, concise body
- evidence task ids / event seqs / memory ids
- reason and risk/confidence
- for edits/archives: target `memory_id`, current `version`, and why edit/archive is better than adding a new memory

Use proposal actions for normal agent runtimes:

- New memory: `broccoli-comms memory propose ...`
- Edit existing memory: `broccoli-comms memory propose MEMORY_ID ... --expected-version VERSION ...`
- Archive/remove existing memory: `broccoli-comms memory propose MEMORY_ID --archive --reason '...' --expected-version VERSION ...`
- Trusted decision: `broccoli-comms memory decide PROPOSAL_ID approve|reject --expected-version VERSION ...`

### 5. Present report before mutation

Before running any memory mutation command, present a final report like:

```markdown
# Memory audit report for AGENT

## Evidence reviewed
- Tasks/chains: ...
- Events: ...
- Existing memory: ...

## Proposed additions
1. type=habit scope=... subject_agent=...
   title: ...
   body: ...
   evidence: task-..., events ...
   command: `broccoli-comms memory propose --type habit ...`

## Proposed edit proposals
1. target memory_id=... version=...
   title/body changes: ...
   evidence: ...
   command: `broccoli-comms memory propose MEMORY_ID --body ... --expected-version VERSION ...`

## Proposed archive proposals
1. target memory_id=... version=...
   reason: ...
   evidence: ...
   command: `broccoli-comms memory propose MEMORY_ID --archive --reason ... --expected-version VERSION ...`

## Decisions for trusted user/coordinator
- proposal_id=... command: `broccoli-comms memory decide PROPOSAL_ID approve --expected-version VERSION --json`
- proposal_id=... command: `broccoli-comms memory decide PROPOSAL_ID reject --reason ... --expected-version VERSION --json`

## No-op / keep
- ...
```

Stop and wait for explicit user/coordinator approval.

## Applying approved proposals

Only after approval, run the approved CLI commands.

### Propose a new memory

```bash
broccoli-comms memory propose \
  --type habit \
  --scope SCOPE \
  --subject-agent AGENT \
  --title 'TITLE' \
  --body 'BODY' \
  --source-task TASK_ID \
  --tag memory-audit \
  --json
```

For skills, use `--type skill` and make the body a compact playbook:

```markdown
When to use: ...
Steps:
1. ...
Validation checks:
- ...
Failure modes:
- ...
```

### Propose an edit to existing memory

```bash
broccoli-comms memory propose MEMORY_ID \
  --title 'UPDATED TITLE' \
  --body 'UPDATED BODY' \
  --source-task TASK_ID \
  --tag memory-audit \
  --expected-version VERSION \
  --json
```

Omit unchanged fields. This creates a pending edit proposal and does not require trusted actor identity.

### Propose archive/removal of existing memory

Use this for stale active memory or stale pending proposals instead of direct revoke/reject from an agent runtime:

```bash
broccoli-comms memory propose MEMORY_ID \
  --archive \
  --reason 'Concise reason based on later validated evidence' \
  --source-task TASK_ID \
  --expected-version VERSION \
  --json
```

Approving the archive proposal revokes an active target or rejects a pending target, preserving append-only auditability.

### Trusted decisions

If the user/coordinator asks you to decide a proposal and your runtime is trusted:

```bash
broccoli-comms memory decide PROPOSAL_ID approve --expected-version VERSION --json
broccoli-comms memory decide PROPOSAL_ID reject --reason 'Concise reason' --expected-version VERSION --json
```

If the current runtime is not trusted, leave proposals pending and report the decision command for a trusted user/coordinator. Do not spoof identity. Use a local trusted path with `AGENT_NAME`, `AGENT_ID`, and `AGENT_UUID` unset only when explicitly authorized.

### Direct trusted maintenance commands

`memory edit`, `memory revoke`, `memory reject`, and `memory rollback` still exist for trusted maintenance and compatibility, but memory audits should prefer proposal + `memory decide` unless the user explicitly requests direct trusted application.

## Final response

After applying approved commands, report:

- commands run
- memory ids proposed/decided
- anything left pending for a trusted user/coordinator
- skipped candidates and why

Keep the final summary concise.
