package main

import (
	"context"
	"errors"
	"fmt"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/tanmayvijay/home-manager-core/agent-communicator-tui/internal/tracker"
)

type memoryActionResult struct {
	MemoryID string
	Action   string
	Err      error
}

type memoryRecord struct {
	MemoryID     string   `json:"memory_id"`
	Status       string   `json:"status"`
	Version      int      `json:"version"`
	Type         string   `json:"type"`
	Scope        string   `json:"scope"`
	SubjectAgent string   `json:"subject_agent"`
	ProposedBy   string   `json:"proposed_by"`
	SourceTaskID string   `json:"source_task_id"`
	Title        string   `json:"title"`
	Body         string   `json:"body"`
	Tags         []string `json:"tags"`
}

func toLocalMemoryRecord(r tracker.MemoryRecord) memoryRecord {
	return memoryRecord{
		MemoryID:     r.MemoryID,
		Status:       r.Status,
		Version:      r.Version,
		Type:         r.Type,
		Scope:        r.Scope,
		SubjectAgent: r.SubjectAgent,
		ProposedBy:   r.ProposedBy,
		SourceTaskID: r.SourceTaskID,
		Title:        r.Title,
		Body:         r.Body,
		Tags:         r.Tags,
	}
}

func toLocalMemoryRecords(records []tracker.MemoryRecord) []memoryRecord {
	out := make([]memoryRecord, len(records))
	for i, r := range records {
		out[i] = toLocalMemoryRecord(r)
	}
	return out
}

func selectedMemoryMessage(m model) (tracker.Message, bool) {
	messages := m.displayOrderedMessages()
	if m.messageSelected < 0 || m.messageSelected >= len(messages) {
		return tracker.Message{}, false
	}
	msg := messages[m.messageSelected]
	return msg, isMemoryProposalMessage(msg)
}

func memoryMessageForAction(memoryID string, selected tracker.Message) tracker.Message {
	msg := selected
	msg.MemoryID = memoryID
	return msg
}

func loadMemoryForMessage(ctx context.Context, local localClient, msg tracker.Message) (memoryRecord, error) {
	res, err := local.MemoryShow(ctx, msg.MemoryID, 0)
	if err != nil {
		return memoryRecord{}, fmt.Errorf("memory lookup failed: %w", err)
	}
	return toLocalMemoryRecord(res), nil
}

func memoryActionCmd(local localClient, msg tracker.Message, action string, title string, body string) tea.Cmd {
	return func() tea.Msg {
		if msg.MemoryID == "" {
			return memoryActionResult{MemoryID: msg.MemoryID, Action: action, Err: errors.New("memory id is required")}
		}
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		shown, err := loadMemoryForMessage(ctx, local, msg)
		if err != nil {
			return memoryActionResult{MemoryID: msg.MemoryID, Action: action, Err: err}
		}
		if shown.MemoryID != msg.MemoryID || shown.Status != "pending" {
			return memoryActionResult{MemoryID: msg.MemoryID, Action: action, Err: errors.New("memory proposal is stale or no longer pending")}
		}

		var errAct error
		switch action {
		case "approve":
			_, errAct = local.MemoryApprove(ctx, msg.MemoryID, shown.Version, "")
		case "reject":
			_, errAct = local.MemoryReject(ctx, msg.MemoryID, shown.Version, "removed from inbox", "")
		case "edit":
			if title == "" && body == "" {
				return memoryActionResult{MemoryID: msg.MemoryID, Action: action, Err: errors.New("/memory edit requires title | body")}
			}
			params := map[string]any{
				"memory_id":        msg.MemoryID,
				"expected_version": shown.Version,
			}
			if title != "" {
				params["title"] = title
			}
			if body != "" {
				params["body"] = body
			}
			_, errAct = local.MemoryEdit(ctx, params)
		default:
			errAct = fmt.Errorf("unknown memory action: %s", action)
		}

		if errAct != nil {
			return memoryActionResult{MemoryID: msg.MemoryID, Action: action, Err: fmt.Errorf("memory %s failed: %w", action, errAct)}
		}
		return memoryActionResult{MemoryID: msg.MemoryID, Action: action, Err: nil}
	}
}
