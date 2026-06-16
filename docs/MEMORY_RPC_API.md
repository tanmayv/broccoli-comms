# Broccoli Comms Memory JSON-RPC API Specification (Versioned)

This document specifies the JSON-RPC 2.0 API signatures for all durable memory operations in Broccoli Comms. These APIs expose the durable memory lifecycle (propose, approve, reject, list, show, history) for consumption by agents, the Terminal User Interface (TUI), and central registries.

This specification uses a **Natural Versioning** model. Instead of creating separate "proposal" records with different IDs, edits and archives are proposed as the **next version** of an existing memory ID. This maintains a clean ID space where a piece of knowledge has exactly one stable ID throughout its entire lifecycle.

---

## 1. Data Models

### 1.1 Memory Record
A memory record represents a single version of a unit of durable knowledge. The combination of `(memory_id, version)` is unique.

| Field | Type | Description |
| :--- | :--- | :--- |
| `memory_id` | String | Stable identifier (e.g., `mem-a629549218aa`). Remains constant across edits. |
| `version` | Integer | Monotonically increasing version number (starts at `1`). |
| `type` | String | The category of knowledge (see [1.2 Memory Types](#12-memory-types)). |
| `scope` | String | Scope of applicability (e.g., `global`, `local`, `project:<id>`, `team:<id>`). |
| `subject_agent` | String \| `null` | The specific agent this memory applies to (optional). |
| `title` | String | A concise, human-readable summary of this version. |
| `body` | String | The detailed content of this version (markdown supported). |
| `tags` | Array of Strings | Up to 20 tags for categorization and search. |
| `metadata` | Object | Extra structured metadata. |
| `source_task_id` | String \| `null` | The ID of the validated task that generated this version (optional). |
| `trusted_manual` | Boolean | True if proposed directly by a trusted actor without a source task. |
| `status` | String | The lifecycle state of this version (see [1.3 Memory Statuses](#13-memory-statuses)). |
| `proposed_by` | String | The name of the agent or user who proposed this version. |
| `proposed_by_instance` | String \| `null` | The specific agent instance ID that proposed this version. |
| `created_at` | String | ISO 8601 UTC timestamp of when this version was proposed. |
| `validated_by` | String \| `null` | The trusted actor who approved/rejected this version. |
| `validated_at` | String \| `null` | ISO 8601 UTC timestamp of decision. |

### 1.2 Memory Types
*   `fact`: Simple assertions or discoveries (e.g., database ports, server names).
*   `habit`: Mandatory operating instructions or constraints for agents.
*   `episode`: Historical records of specific task executions or events.
*   `expertise`: Bounded capability profiles (e.g., "specializes in Nix modules").
*   `skill`: Executable instructions or specialized agent workflows.

### 1.3 Memory Statuses
A memory ID can have multiple versions with different statuses, but at most **one** version can be `active` at any time.

*   `pending`: A pending proposal for a **new** memory (always `version = 1`).
*   `pending_edit`: A pending proposal for an **edit** to an existing active memory (`version > 1`).
*   `pending_revocation`: A pending proposal to **archive/remove** an existing active memory (`version > 1`).
*   `active`: The currently approved and active version of the memory (queryable by default).
*   `rejected`: A pending proposal (`pending` or `pending_edit`) that was rejected.
*   `revoked`: An active memory that was approved for archiving/removal.
*   `superseded`: An older active version that was replaced by a newer approved edit.

---

## 2. API Methods

### 2.1 `memory.propose`
Proposes a **new** durable memory record. This always creates `version = 1` with `status: "pending"`.

#### Request Parameters
*   `type` (String, Required): One of the valid [Memory Types](#12-memory-types).
*   `title` (String, Required): Concise summary.
*   `body` (String, Required): Detailed content.
*   `scope` (String, Optional, Default: `"global"`): Target scope.
*   `subject_agent` (String, Optional): Target agent.
*   `tags` (Array of Strings, Optional): Custom tags (max 20).
*   `metadata` (Object, Optional): Structured metadata.
*   `source_task_id` (String, Optional): Required if `trusted_manual` is false and type is not `fact` or `habit`.
*   `trusted_manual` (Boolean, Optional, Default: `false`): Propose as trusted manual (requires trusted actor identity).
*   `idempotency_key` (String, Optional): Key to deduplicate repeated proposals.

#### Response Payload
*   `memory` (Object): The created pending [Memory Record](#11-memory-record) (v1, `pending`).
*   `event` (Object): The generated `memory_proposed` event.
*   `idempotent` (Boolean): `true` if this was a deduplicated duplicate request.

#### Example Request
```json
{
  "jsonrpc": "2.0",
  "method": "memory.propose",
  "params": {
    "type": "fact",
    "title": "Database Server Port",
    "body": "The production database server runs on port 5432.",
    "scope": "project:broccoli",
    "tags": ["database", "config"],
    "source_task_id": "task-12345"
  },
  "id": 1
}
```

---

### 2.2 `memory.propose_edit`
Proposes an edit to an existing active memory. This creates the **next version** of the memory with `status: "pending_edit"`. The current active version remains active and queryable while the edit is pending.

#### Request Parameters
*   `memory_id` (String, Required): The ID of the memory to edit.
*   `expected_version` (Integer, Required): The version number we are editing (concurrency check, must match the current active version).
*   Any fields to update (Optional, defaults to current version's values if omitted): `title`, `body`, `tags`, `metadata`, `source_task_id`.
*   `type`, `scope`, `subject_agent` can also be updated if the category or scope of the knowledge has changed.

#### Response Payload
*   `memory` (Object): The newly created pending version of the [Memory Record](#11-memory-record) (v`expected_version + 1`, `pending_edit`).
*   `event` (Object): The generated `memory_edit_proposed` event.

#### Example Request
```json
{
  "jsonrpc": "2.0",
  "method": "memory.propose_edit",
  "params": {
    "memory_id": "mem-a1b2c3d4e5f6",
    "expected_version": 1,
    "body": "The production database server runs on port 5432. Replica runs on 5433."
  },
  "id": 2
}
```

---

### 2.3 `memory.propose_archive`
Proposes archiving (removing) an existing active memory. This creates the **next version** of the memory with `status: "pending_revocation"`. The current version remains active while the archiving proposal is pending.

#### Request Parameters
*   `memory_id` (String, Required): The ID of the memory to archive.
*   `expected_version` (Integer, Required): Concurrency check (must match the current active version).
*   `reason` (String, Optional): Reason for proposing archiving (stored in the new version's `body` or `metadata`).
*   `source_task_id` (String, Optional): Task provenance.

#### Response Payload
*   `memory` (Object): The newly created pending revocation version of the [Memory Record](#11-memory-record) (v`expected_version + 1`, `pending_revocation`).
*   `event` (Object): The generated `memory_archive_proposed` event.

#### Example Request
```json
{
  "jsonrpc": "2.0",
  "method": "memory.propose_archive",
  "params": {
    "memory_id": "mem-a1b2c3d4e5f6",
    "expected_version": 2,
    "reason": "Database server migrated to cloud service, port 5432 no longer relevant."
  },
  "id": 3
}
```

---

### 2.4 `memory.approve`
Approves a pending version (`pending`, `pending_edit`, or `pending_revocation`) of a memory. Can be called by any agent or user.

#### Transition Rules upon Approval:
1.  **If approving `pending` (v1)**:
    *   v1 transitions: `pending` ➔ `active`.
2.  **If approving `pending_edit` (vN)**:
    *   vN transitions: `pending_edit` ➔ `active`.
    *   The previous active version (vN-1) automatically transitions: `active` ➔ `superseded`.
3.  **If approving `pending_revocation` (vN)**:
    *   vN transitions: `pending_revocation` ➔ `revoked`.
    *   The previous active version (vN-1) automatically transitions: `active` ➔ `superseded`.

#### Request Parameters
*   `memory_id` (String, Required): The ID of the memory.
*   `version` (Integer, Required): The specific pending version number to approve.
*   `actor` (String, Optional): The actor approving (defaults to caller identity).

#### Response Payload
*   `memory` (Object): The newly activated or revoked [Memory Record](#11-memory-record) version.
*   `event` (Object): The approval event.

#### Example Request
```json
{
  "jsonrpc": "2.0",
  "method": "memory.approve",
  "params": {
    "memory_id": "mem-a1b2c3d4e5f6",
    "version": 2,
    "actor": "user-lead"
  },
  "id": 4
}
```

---

### 2.5 `memory.reject`
Rejects a pending version (`pending`, `pending_edit`, or `pending_revocation`). Can be called by any agent or user.

The pending version transitions to `rejected`. The previous active version (if any) remains unaffected and stays `active`.

#### Request Parameters
*   `memory_id` (String, Required): The ID of the memory.
*   `version` (Integer, Required): The specific pending version number to reject.
*   `reason` (String, Optional): Reason for rejection.
*   `actor` (String, Optional): The actor rejecting.

#### Response Payload
*   `memory` (Object): The rejected version of the [Memory Record](#11-memory-record) (status `rejected`).
*   `event` (Object): The rejection event.

#### Example Request
```json
{
  "jsonrpc": "2.0",
  "method": "memory.reject",
  "params": {
    "memory_id": "mem-a1b2c3d4e5f6",
    "version": 2,
    "reason": "Replica port is incorrect, it should be 5434."
  },
  "id": 5
}
```

---

### 2.6 `memory.show`
Retrieves a single memory record. By default, it returns the **currently active** version. An optional `version` parameter can be passed to retrieve a specific historical or pending version.

#### Request Parameters
*   `memory_id` (String, Required): The memory ID.
*   `version` (Integer, Optional): Specific version to retrieve. If omitted, returns the latest version with `status: "active"`. If no version is active, returns the latest version overall.

#### Response Payload
*   The requested [Memory Record](#11-memory-record) version object.

---

### 2.7 `memory.history`
Retrieves the complete version history and audit trail for a memory ID.

#### Request Parameters
*   `memory_id` (String, Required): The memory ID.

#### Response Payload
*   `memory_id` (String): The stable memory ID.
*   `versions` (Array of Objects): List of all versions of this memory, sorted chronologically by version number. Each entry contains the full [Memory Record](#11-memory-record) fields for that version.

#### Example Request
```json
{
  "jsonrpc": "2.0",
  "method": "memory.history",
  "params": {
    "memory_id": "mem-a1b2c3d4e5f6"
  },
  "id": 7
}
```

---

### 2.8 `memory.list`
Lists memory records, optionally filtered by scope, type, status, or agent.

*   By default, if `status` is omitted, it only returns **currently active** (`status: "active"`) versions.
*   If `status: "pending"` is requested, it returns all versions that are currently `pending`, `pending_edit`, or `pending_revocation`.

#### Request Parameters
*   `scope` (String, Optional): Filter by scope.
*   `type` (String, Optional): Filter by [Memory Type](#12-memory-types).
*   `status` (String, Optional, Default: `"active"`): Filter by status (`active`, `pending`, `rejected`, `revoked`, `superseded`).
*   `agent` (String, Optional): Filter by proposing or subject agent.

#### Response Payload
*   Array of [Memory Record](#11-memory-record) objects representing the matching versions.

---

### 2.9 `memory.search`
Performs a full-text search across **currently active** (`status: "active"`) memories.

#### Request Parameters
*   `query` (String, Required): Search query string.
*   `scope` (String, Optional): Restrict search to a specific scope.

#### Response Payload
*   Array of [Memory Record](#11-memory-record) objects matching the query, ordered by relevance.

---

## 3. Error Handling

Durable memory operations use standard JSON-RPC 2.0 error formatting.

### 3.1 Standard Error Codes
*   `-32601`: **Method not found** (e.g., if memory service is disabled).
*   `-32602`: **Invalid params** (e.g., missing required fields, invalid memory type).
*   `-32603`: **Internal error** (e.g., database write failure).

### 3.2 Custom/Structured Error Codes
When a memory operation fails validation or business logic, the response will contain a custom error code and descriptive details in `error.data`.

| Error Code | Message | Description |
| :--- | :--- | :--- |
| `-32002` | `stale memory version` | The `expected_version` concurrency check failed (the version has already progressed). |
| `-32003` | `memory transition conflict` | The requested version cannot transition (e.g., trying to approve an already active or rejected version). |
| `-32004` | `pending memory limit exceeded` | The agent has reached the maximum allowed pending proposals. |
| `-32005` | `active memory limit exceeded` | The active memory limit for the agent/scope/type has been reached. |

#### Example Error Response
```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "error": {
    "code": -32002,
    "message": "stale memory version",
    "data": {
      "memory_id": "mem-a1b2c3d4e5f6",
      "current_active_version": 2,
      "expected_version": 1
    }
  }
}
```
