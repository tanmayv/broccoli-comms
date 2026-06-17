package main

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
)

type memoryApprovalsLoaded struct {
	Items []memoryRecord
	Err   error
}

type memoryEditClosed struct {
	MemoryID string
	Err      error
}

func loadMemoryApprovalsCmd(local localClient) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()

		pending, err := local.MemoryList(ctx, map[string]any{"status": "pending"})
		if err != nil {
			return memoryApprovalsLoaded{Err: fmt.Errorf("failed to load pending memories: %w", err)}
		}

		approved, err := local.MemoryList(ctx, map[string]any{"status": "active"})
		if err != nil {
			return memoryApprovalsLoaded{Err: fmt.Errorf("failed to load active memories: %w", err)}
		}

		items := append([]memoryRecord{}, toLocalMemoryRecords(pending)...)
		items = append(items, toLocalMemoryRecords(approved)...)
		return memoryApprovalsLoaded{Items: items}
	}
}

func memoryManagerActionCmd(local localClient, mem memoryRecord, action string) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		if mem.MemoryID == "" {
			return memoryActionResult{Action: action, Err: fmt.Errorf("memory id is required")}
		}

		var errAct error
		switch action {
		case "approve":
			_, errAct = local.MemoryApprove(ctx, mem.MemoryID, mem.Version, "")
		case "reject":
			_, errAct = local.MemoryReject(ctx, mem.MemoryID, mem.Version, "removed from Memory Management tab", "")
		case "revoke":
			_, errAct = local.MemoryRevoke(ctx, mem.MemoryID, "removed from Memory Management tab", mem.Version, "")
		case "rollback":
			if mem.Version <= 1 {
				return memoryActionResult{MemoryID: mem.MemoryID, Action: action, Err: fmt.Errorf("memory has no previous version")}
			}
			_, errAct = local.MemoryRollback(ctx, mem.MemoryID, mem.Version-1, mem.Version, "")
		default:
			errAct = fmt.Errorf("unknown memory action: %s", action)
		}

		if errAct != nil {
			return memoryActionResult{MemoryID: mem.MemoryID, Action: action, Err: fmt.Errorf("memory %s failed: %w", action, errAct)}
		}
		return memoryActionResult{MemoryID: mem.MemoryID, Action: action}
	}
}

func memoryEditorCommandName() string {
	if editor := os.Getenv("EDITOR"); editor != "" {
		return editor
	}
	return "nvim"
}

func editMemoryInEditor(local localClient, mem memoryRecord) tea.Cmd {
	return func() tea.Msg {
		file, err := os.CreateTemp("", "broccoli-memory-*.md")
		if err != nil {
			return memoryEditClosed{MemoryID: mem.MemoryID, Err: err}
		}
		path := file.Name()
		initial := fmt.Sprintf("%s\n--- body ---\n%s\n", mem.Title, mem.Body)
		if _, err := file.WriteString(initial); err != nil {
			file.Close()
			os.Remove(path)
			return memoryEditClosed{MemoryID: mem.MemoryID, Err: err}
		}
		file.Close()
		editor := memoryEditorCommandName()
		return tea.ExecProcess(exec.Command(editor, path), func(err error) tea.Msg {
			defer os.Remove(path)
			if err != nil {
				return memoryEditClosed{MemoryID: mem.MemoryID, Err: err}
			}
			content, err := os.ReadFile(path)
			if err != nil {
				return memoryEditClosed{MemoryID: mem.MemoryID, Err: err}
			}
			parts := strings.SplitN(string(content), "\n--- body ---\n", 2)
			if len(parts) != 2 {
				return memoryEditClosed{MemoryID: mem.MemoryID, Err: fmt.Errorf("memory edit must keep the --- body --- separator")}
			}
			title := strings.TrimSpace(parts[0])
			body := strings.TrimSpace(parts[1])
			ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
			defer cancel()

			params := map[string]any{
				"memory_id":        mem.MemoryID,
				"expected_version": mem.Version,
				"title":            title,
				"body":             body,
			}
			_, errAct := local.MemoryEdit(ctx, params)
			if errAct != nil {
				return memoryEditClosed{MemoryID: mem.MemoryID, Err: fmt.Errorf("memory edit failed: %w", errAct)}
			}
			return memoryEditClosed{MemoryID: mem.MemoryID}
		})()
	}
}

func (m model) selectedMemoryRecord() (memoryRecord, bool) {
	return m.selectedFilteredMemoryRecord()
}

func memoryRecordAgentName(mem memoryRecord) string {
	return firstNonEmpty(mem.SubjectAgent, mem.ProposedBy, "unknown")
}
