package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/tanmayvijay/home-manager-core/agent-communicator-tui/internal/tracker"
)

type approvalReviewResult struct {
	ApprovalID string
	Result     string
	Err        error
}

type approvalRecord struct {
	ApprovalID              string `json:"approval_id"`
	TaskID                  string `json:"task_id"`
	TaskChainID             string `json:"task_chain_id"`
	RootTaskID              string `json:"root_task_id"`
	TaskVersionAtSubmission int    `json:"task_version_at_submission"`
	CreatedEventSeq         int64  `json:"created_event_seq"`
	EventSeqAtSubmission    int64  `json:"event_seq_at_submission"`
	Status                  string `json:"status"`
}

var runApprovalCLI = runApprovalCLICommand

func broccoliCommsCommandContext(ctx context.Context, args ...string) *exec.Cmd {
	cli := os.Getenv("BROCCOLI_COMMS_CLI")
	if cli == "" {
		cli = "broccoli-comms"
	}
	cmd := exec.CommandContext(ctx, cli, args...)
	cmd.Env = envWithoutAgentIdentity(os.Environ())
	return cmd
}

func envWithoutAgentIdentity(env []string) []string {
	filtered := make([]string, 0, len(env))
	for _, entry := range env {
		key, _, _ := strings.Cut(entry, "=")
		switch key {
		case "AGENT_NAME", "AGENT_ID", "AGENT_UUID":
			continue
		default:
			filtered = append(filtered, entry)
		}
	}
	return filtered
}

func runApprovalCLICommand(ctx context.Context, args ...string) ([]byte, error) {
	cmd := broccoliCommsCommandContext(ctx, args...)
	return cmd.CombinedOutput()
}

func selectedApprovalMessage(m model) (tracker.Message, bool) {
	messages := m.displayOrderedMessages()
	if m.messageSelected < 0 || m.messageSelected >= len(messages) {
		return tracker.Message{}, false
	}
	msg := messages[m.messageSelected]
	return msg, approvalMessageHasContext(msg)
}

func approvalMessageHasContext(msg tracker.Message) bool {
	return msg.ApprovalID != "" && (isApprovalRequestMessage(msg) || isTaskUpdateMessage(msg))
}

func approvalMessageForReview(approvalID string, selected tracker.Message) tracker.Message {
	msg := selected
	msg.ApprovalID = approvalID
	return msg
}

func approvalReviewCmd(msg tracker.Message, result string) tea.Cmd {
	return func() tea.Msg {
		if msg.ApprovalID == "" {
			return approvalReviewResult{ApprovalID: msg.ApprovalID, Result: result, Err: errors.New("approval id is required")}
		}
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		shown, err := loadApprovalForMessage(ctx, msg)
		if err != nil {
			return approvalReviewResult{ApprovalID: msg.ApprovalID, Result: result, Err: err}
		}
		if err := validateApprovalRecordForMessage(shown, msg); err != nil {
			return approvalReviewResult{ApprovalID: msg.ApprovalID, Result: result, Err: err}
		}
		reviewVersion := msg.TaskVersionAtSubmission
		if reviewVersion == 0 {
			reviewVersion = shown.TaskVersionAtSubmission
		}
		args := []string{"task", "approval", "review", msg.ApprovalID, "--result", result, "--task-version-at-submission", strconv.Itoa(reviewVersion), "--json"}
		if msg.RecipientAgent != "" {
			args = append(args, "--actor", msg.RecipientAgent)
		}
		if result != "good" {
			args = append(args, "--next-step", "Please address reviewer feedback from the approval card.")
		}
		out, err := runApprovalCLI(ctx, args...)
		if err != nil {
			return approvalReviewResult{ApprovalID: msg.ApprovalID, Result: result, Err: fmt.Errorf("approval review failed: %w: %s", err, string(out))}
		}
		return approvalReviewResult{ApprovalID: msg.ApprovalID, Result: result, Err: nil}
	}
}

func loadApprovalForMessage(ctx context.Context, msg tracker.Message) (approvalRecord, error) {
	out, err := runApprovalCLI(ctx, "task", "approval", "show", msg.ApprovalID, "--json")
	if err != nil {
		return approvalRecord{}, fmt.Errorf("approval lookup failed: %w: %s", err, string(out))
	}
	var rec approvalRecord
	if err := json.Unmarshal(out, &rec); err != nil {
		return approvalRecord{}, fmt.Errorf("approval lookup returned invalid JSON: %w", err)
	}
	return rec, nil
}

func isLocallyTrustedApprovalMessage(msg tracker.Message) bool {
	// Inbox metadata is attacker-controlled hinting: local RPC callers can spoof
	// sender/source fields. Trust is established only after loading the durable
	// approval record in approvalReviewCmd; static inbox cards are never treated
	// as trusted/actionable by themselves.
	return false
}

func validateApprovalRecordForMessage(rec approvalRecord, msg tracker.Message) error {
	if rec.ApprovalID != msg.ApprovalID {
		return errors.New("approval card is stale or does not match durable task DB")
	}
	if msg.TaskID != "" && rec.TaskID != msg.TaskID {
		return errors.New("approval card is stale or does not match durable task DB")
	}
	if msg.TaskVersionAtSubmission != 0 && rec.TaskVersionAtSubmission != msg.TaskVersionAtSubmission {
		return errors.New("approval card is stale or does not match durable task DB")
	}
	if msg.TaskChainID != "" && rec.TaskChainID != msg.TaskChainID {
		return errors.New("approval card task chain mismatch")
	}
	if msg.RootTaskID != "" && rec.RootTaskID != msg.RootTaskID {
		return errors.New("approval card root task mismatch")
	}
	if msg.CreatedEventSeq != 0 && rec.CreatedEventSeq != msg.CreatedEventSeq {
		return errors.New("approval card created event mismatch")
	}
	if msg.EventSeqAtSubmission != 0 && rec.EventSeqAtSubmission != msg.EventSeqAtSubmission {
		return errors.New("approval card submission event mismatch")
	}
	if rec.TaskChainID == "" || rec.RootTaskID == "" {
		return errors.New("approval must target a task chain")
	}
	if rec.TaskChainID == rec.TaskID && rec.RootTaskID == rec.TaskID {
		return errors.New("single-task approval requests are not supported")
	}
	if rec.Status != "pending" {
		return errors.New("approval is no longer pending")
	}
	return nil
}
