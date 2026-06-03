# Broccoli Comms Electron Communicator

Electron + Vite + React + TypeScript desktop communicator prototype.

This integration worktree adds an initial **agent-tracker-only Simple View** path while preserving the mock/dev fallback from the approved Electron mock.

## Current scope

Implemented in this branch:

- Electron renderer remains isolated from Node and sockets.
- Main process owns tracker JSON-RPC over a Unix socket via preload/IPC.
- Tracker integration is **local-only** and **Simple View only**:
  - local tracker `list` agents
  - selected local agent one-to-one timeline assembled from the Electron inbox identity plus session-local sent messages
  - normal message send via tracker `send_message`
  - receiving replies sent to a configured registered local inbox identity
- Stable local identity uses `local:<agent_id>` when tracker `agent_id` is available, then `local:<uuid>`, then legacy name fallback.
- Mock/dev mode remains available when no explicit tracker runtime is provided.

Not implemented in this slice:

- no registry APIs
- no remote protocol or host-qualified target delivery
- no advanced view
- no direct pane-control integration in tracker mode
- no Nix packaging
- no renderer socket/Node access

## Private runtime and socket rules

Tracker mode is opt-in. The Electron main process may use only:

1. `AGENT_TRACKER_SOCKET`
2. `BROCCOLI_COMMS_RUNTIME_DIR/agent-tracker.sock`

If neither is set, the app stays in deterministic mock/dev mode.

The Electron app intentionally does **not** fall back to `~/.cache/agent-tracker`, inherited `TMUX`, inherited `TMUX_PANE`, default tmux sockets, registry config, or remote protocol state. This preserves Broccoli private runtime rules for native frontends.

## Install and run

```sh
cd /path/to/broccoli-comms/agent-communicator-electron
npm install
npm run dev
```

To run against a local Broccoli runtime/tracker, launch with an explicit tracker runtime, for example:

```sh
AGENT_TRACKER_SOCKET=/run/user/1000/broccoli-comms/agent-tracker.sock npm run dev
# or
BROCCOLI_COMMS_RUNTIME_DIR=/run/user/1000/broccoli-comms npm run dev
```

By default, tracker mode uses the shared communicator identity `agent-communicator` and ensures a local no-pane mailbox for that identity before listing/sending/receiving:

```sh
BROCCOLI_COMMS_RUNTIME_DIR=/run/user/1000/broccoli-comms npm run dev
```

Agents will see messages as coming from `agent-communicator`. When they reply to `agent-communicator`, the app polls that shared communicator inbox and shows matching messages in the selected local conversation. To use a different local UI identity, set `BROCCOLI_COMMS_ELECTRON_AGENT_NAME`; `AGENT_COMMUNICATOR_ELECTRON_AGENT_NAME` is accepted as a legacy fallback. The app intentionally ignores inherited `AGENT_NAME` from the launching shell/pane so it does not impersonate the coding agent that started it.

Without explicit tracker runtime env vars, the app shows local fixture data from mock/dev mode.

## Validation

```sh
npm run test
npm run typecheck
npm run build
```

`npm test` uses Vitest for focused main-process integration tests. Current tests cover:

- explicit tracker socket resolution
- no cache/default tmux fallback
- stable local identity
- tracker send-message target parameter mapping
- local-only/remote rejection guardrails
- local tracker agent/message mapping
- configured Electron inbox identity and receive-side conversation filtering
- chronological merge of locally sent messages with received inbox messages

## npm scripts

- `npm run dev` — launch the Electron app with Vite dev tooling.
- `npm run test` — run focused Vitest coverage.
- `npm run typecheck` — run TypeScript validation with `tsc --noEmit`.
- `npm run build` — run typecheck, then build Electron main/preload/renderer bundles.

## Mock/dev fixture data

Mock runtime data lives in `src/test/fixtures.ts` and remains useful for UI development without a running tracker:

- `mockRuntimeStatus` — simulated runtime/tracker/registry/tmux health chips and notes.
- `mockAgents` — local and remote fixture agents, statuses, cwd/project metadata, unread counts, target addresses, tags, and direct-control eligibility.
- `mockMessages` — deterministic conversation timelines.

## Tracker Simple View details

The main-process tracker client is in `src/main/trackerClient.ts`.

- `resolveTrackerSocket()` only accepts explicit Broccoli runtime env.
- `ensure_mailbox` creates/refreshes the shared local communicator mailbox identity without a pane, registry advertisement, or tmux notifications.
- `listAgents()` calls tracker `list`, maps local rows only, filters remote/host-qualified rows, and excludes the configured Electron inbox identity from targets.
- `listMessages()` polls tracker `get_inbox` for the configured Electron identity with `mark_read: false` and a selected-agent sender filter, then merges matching received messages with session-local sent messages without marking unrelated inbox entries read.
- `sendMessage()` rejects non-local or host-qualified targets at the main-process boundary, then calls tracker `send_message` with `agent_id` when stable local identity is available and `sender_name` set to the configured Electron identity.
- Direct pane-control APIs return not-implemented in tracker mode for this slice.

## Future integration notes

Future chunks can replace more mock behavior with real local runtime APIs while keeping the renderer behind `CommunicatorRuntimeClient` and preload IPC. Remote/registry functionality, advanced view, direct pane control, durable message state, and packaging should remain separate explicit tasks with their own guardrails and review.
