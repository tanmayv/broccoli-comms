package tracker

import "time"

type DetectionStatus struct {
	Enabled              bool     `json:"enabled"`
	Configured           bool     `json:"configured"`
	Provider             string   `json:"provider"`
	CaptureLines         int      `json:"capture_lines"`
	ScanIntervalSeconds  float64  `json:"scan_interval_seconds"`
	LastScanAt           float64  `json:"last_scan_at"`
	NextScanAt           float64  `json:"next_scan_at"`
	SecondsUntilNextScan int      `json:"seconds_until_next_scan"`
	LastResult           string   `json:"last_result"`
	LastDetectedAt       float64  `json:"last_detected_at"`
	LastNotifiedAt       float64  `json:"last_notified_at"`
	MatchedKeywords      []string `json:"matched_keywords"`
	PaneTitle            string   `json:"pane_title"`
	Error                string   `json:"error"`
}

type ListOptions struct {
	IncludeRemote bool
	AgentID       string
	AgentName     string
}

type SwarmMembership struct {
	Name string `json:"name"`
	Role string `json:"role"`
}

type Agent struct {
	Name                string            `json:"name"`
	AgentID             string            `json:"agent_id"`
	UUID                string            `json:"uuid"`
	Session             string            `json:"session"`
	TmuxPane            string            `json:"tmux_pane"`
	Status              string            `json:"status"`
	WaitingApproval     bool              `json:"waiting_approval"`
	AgentType           string            `json:"agent_type"`
	AgentCmd            string            `json:"agent_cmd"`
	ModelType           string            `json:"model_type"`
	CurrentTask         string            `json:"current_task,omitempty"`
	CurrentTaskID       string            `json:"current_task_id,omitempty"`
	CurrentTaskStatus   string            `json:"current_task_status,omitempty"`
	CurrentTaskNextStep string            `json:"current_task_next_step,omitempty"`
	Aliases             []string          `json:"aliases"`
	IsThisMe            bool              `json:"is_this_me"`
	CWD                 string            `json:"cwd,omitempty"`
	Scope               string            `json:"scope,omitempty"`
	Hostname            string            `json:"hostname,omitempty"`
	TargetAddress       string            `json:"target_address,omitempty"`
	TrackerID           string            `json:"tracker_id,omitempty"`
	RegistryName        string            `json:"registry_name,omitempty"`
	Detection           DetectionStatus   `json:"detection,omitempty"`
	Swarms              []SwarmMembership `json:"swarms,omitempty"`
}

type Attachment struct {
	Name        string `json:"name"`
	ContentB64  string `json:"content_b64,omitempty"`
	ContentType string `json:"content_type,omitempty"`
	Path        string `json:"path,omitempty"`
	Size        int64  `json:"size,omitempty"`
}

type SwarmMember struct {
	Name          string `json:"name"`
	AgentName     string `json:"agent_name,omitempty"`
	Role          string `json:"role,omitempty"`
	AgentID       string `json:"agent_id,omitempty"`
	TargetAddress string `json:"target_address,omitempty"`
	Configured    *bool  `json:"configured,omitempty"`
	Running       *bool  `json:"running,omitempty"`
	Launchable    *bool  `json:"launchable,omitempty"`
	Scope         string `json:"scope,omitempty"`
	Status        string `json:"status,omitempty"`
	Hostname      string `json:"hostname,omitempty"`
	TrackerID     string `json:"tracker_id,omitempty"`
	RegistryName  string `json:"registry_name,omitempty"`
	ModelType     string `json:"model_type,omitempty"`
	AgentType     string `json:"agent_type,omitempty"`
	AgentCmd      string `json:"agent_cmd,omitempty"`
}

type Swarm struct {
	Name     string        `json:"name"`
	Main     SwarmMember   `json:"main"`
	Members  []SwarmMember `json:"members"`
	Warnings []string      `json:"warnings,omitempty"`
}

type ListSwarmsResult struct {
	Swarms []Swarm `json:"swarms"`
}

type SwarmTimelineMessage struct {
	MessageID   string `json:"message_id,omitempty"`
	Sender      string `json:"sender"`
	Recipient   string `json:"recipient,omitempty"`
	Timestamp   string `json:"timestamp"`
	Message     string `json:"message"`
	Body        string `json:"body,omitempty"`
	ContentType string `json:"content_type,omitempty"`
}

type SwarmTimelineResult struct {
	Messages []SwarmTimelineMessage `json:"messages"`
}

type AssignSwarmResult struct {
	OK      bool   `json:"ok"`
	Swarm   string `json:"swarm"`
	Members []struct {
		Agent string `json:"agent"`
		Role  string `json:"role"`
	} `json:"members"`
	Swarms []Swarm `json:"swarms"`
}

type Message struct {
	Sender                  string       `json:"sender"`
	SenderAgentID           string       `json:"sender_agent_id,omitempty"`
	SenderTrackerID         string       `json:"sender_tracker_id,omitempty"`
	SenderHostname          string       `json:"sender_hostname,omitempty"`
	SenderModelType         string       `json:"sender_model_type,omitempty"`
	SenderAgentType         string       `json:"sender_agent_type,omitempty"`
	SenderAgentCmd          string       `json:"sender_agent_cmd,omitempty"`
	Kind                    string       `json:"kind,omitempty"`
	ApprovalID              string       `json:"approval_id,omitempty"`
	TaskID                  string       `json:"task_id,omitempty"`
	TaskTitle               string       `json:"task_title,omitempty"`
	TaskStatus              string       `json:"task_status,omitempty"`
	TaskAssignedAgent       string       `json:"task_assigned_agent,omitempty"`
	TaskNextStep            string       `json:"task_next_step,omitempty"`
	ResultSummary           string       `json:"result_summary,omitempty"`
	TaskChainID             string       `json:"task_chain_id,omitempty"`
	RootTaskID              string       `json:"root_task_id,omitempty"`
	TaskVersionAtSubmission int          `json:"task_version_at_submission,omitempty"`
	CreatedEventSeq         int64        `json:"created_event_seq,omitempty"`
	EventSeqAtSubmission    int64        `json:"event_seq_at_submission,omitempty"`
	MemoryID                string       `json:"memory_id,omitempty"`
	MemoryType              string       `json:"memory_type,omitempty"`
	MemoryTitle             string       `json:"memory_title,omitempty"`
	MemoryScope             string       `json:"memory_scope,omitempty"`
	MemoryStatus            string       `json:"memory_status,omitempty"`
	MemoryVersion           int          `json:"memory_version,omitempty"`
	SourceTaskID            string       `json:"source_task_id,omitempty"`
	RecipientAgent          string       `json:"recipient_agent,omitempty"`
	RecipientKind           string       `json:"recipient_kind,omitempty"`
	Source                  string       `json:"source,omitempty"`
	SenderSource            string       `json:"sender_source,omitempty"`
	Timestamp               string       `json:"timestamp"`
	Body                    string       `json:"message"`
	ContentType             string       `json:"content_type,omitempty"`
	Attachments             []Attachment `json:"attachments,omitempty"`
	Delivered               bool         `json:"delivered,omitempty"`
	Notified                bool         `json:"notified,omitempty"`
	Read                    bool         `json:"read"`
	MessageID               string       `json:"message_id,omitempty"`
}

type Event struct {
	Seq             int64   `json:"seq"`
	Type            string  `json:"type"`
	Timestamp       float64 `json:"timestamp"`
	TargetAgentID   string  `json:"target_agent_id,omitempty"`
	TargetAgentName string  `json:"target_agent_name,omitempty"`
	Sender          string  `json:"sender,omitempty"`
	MessageID       string  `json:"message_id,omitempty"`
	ContentType     string  `json:"content_type,omitempty"`
	Kind            string  `json:"kind,omitempty"`
	HasAttachments  bool    `json:"has_attachments,omitempty"`
	Hostname        string  `json:"hostname,omitempty"`
	TrackerID       string  `json:"tracker_id,omitempty"`
	Status          string  `json:"status,omitempty"`
	OldStatus       string  `json:"old_status,omitempty"`
	ModelType       string  `json:"model_type,omitempty"`
	AgentType       string  `json:"agent_type,omitempty"`
	AgentCmd        string  `json:"agent_cmd,omitempty"`
	Message         string  `json:"message,omitempty"`
}

type WaitEventsResult struct {
	Events  []Event `json:"events"`
	LastSeq int64   `json:"last_seq"`
	Reset   bool    `json:"reset"`
	Gap     bool    `json:"gap"`
}

type ReadInboxResult struct {
	Mode     string    `json:"mode"`
	Messages []Message `json:"messages"`
}

type UnreadCountsResult struct {
	Counts map[string]int `json:"counts"`
	Total  int            `json:"total"`
}

type SendResult struct {
	Success bool   `json:"success,omitempty"`
	Warning string `json:"warning,omitempty"`
}

type EnsureMailboxResult struct {
	Name    string `json:"name"`
	AgentID string `json:"agent_id"`
	UUID    string `json:"uuid"`
}

type WaitOptions struct {
	Since           int64
	Timeout         time.Duration
	TargetAgentID   string
	TargetAgentName string
}

type AgentConfig struct {
	Name        string `json:"name"`
	Description string `json:"description"`
}

type BuildInfo struct {
	Version  string `json:"version"`
	Revision string `json:"revision"`
	Display  string `json:"display"`
}

type TrackerInfo struct {
	Hostname                 string           `json:"hostname"`
	TrackerID                string           `json:"tracker_id"`
	HTTPPort                 int              `json:"http_port"`
	Status                   string           `json:"status"`
	Version                  string           `json:"version,omitempty"`
	Revision                 string           `json:"revision,omitempty"`
	Build                    BuildInfo        `json:"build,omitempty"`
	AgentCount               int              `json:"agent_count"`
	OnlineAgentCount         int              `json:"online_agent_count"`
	RegistryConnected        *bool            `json:"registry_connected,omitempty"`
	Registries               []RegistryHealth `json:"registries,omitempty"`
	RemoteTrackerCount       int              `json:"remote_tracker_count,omitempty"`
	OnlineRemoteTrackerCount int              `json:"online_remote_tracker_count,omitempty"`
}

type RegistryHealth struct {
	Name          string  `json:"name"`
	Connected     bool    `json:"connected"`
	RegistryURL   string  `json:"registry_url,omitempty"`
	LastOperation string  `json:"last_operation,omitempty"`
	StatusCode    int     `json:"status_code,omitempty"`
	LastAttempt   float64 `json:"last_attempt,omitempty"`
	LastSuccess   float64 `json:"last_success,omitempty"`
	LastError     string  `json:"last_error,omitempty"`
}

type RemoteTracker struct {
	TrackerID    string        `json:"tracker_id"`
	Hostname     string        `json:"hostname"`
	Address      string        `json:"address"`
	HTTPPort     int           `json:"http_port"`
	Status       string        `json:"status"`
	AgentConfigs []AgentConfig `json:"agent_configs"`
}
