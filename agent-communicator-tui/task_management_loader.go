package main

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	tea "github.com/charmbracelet/bubbletea"
)

type tasksLoaded struct {
	Tasks     []taskRecord
	States    []taskWorkingState
	Approvals []taskApprovalRecord
	Err       error
}

func loadTasksTab(model) tea.Cmd { return loadTasksCmd() }

func loadTasksCmd() tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		tasks, err := loadTaskRecords(ctx)
		if err != nil {
			return tasksLoaded{Err: err}
		}
		states, err := loadTaskStates(ctx)
		if err != nil {
			return tasksLoaded{Err: err}
		}
		approvals, err := loadTaskApprovals(ctx)
		if err != nil {
			return tasksLoaded{Err: err}
		}
		return tasksLoaded{Tasks: tasks, States: states, Approvals: approvals}
	}
}

func loadTaskRecords(ctx context.Context) ([]taskRecord, error) {
	out, err := runApprovalCLI(ctx, "task", "list", "--include-archived", "--include-participants", "--json")
	if err != nil {
		return nil, fmt.Errorf("task list failed: %w: %s", err, string(out))
	}
	var tasks []taskRecord
	if err := json.Unmarshal(out, &tasks); err != nil {
		return nil, fmt.Errorf("task list returned invalid JSON: %w", err)
	}
	return tasks, nil
}

func loadTaskStates(ctx context.Context) ([]taskWorkingState, error) {
	out, err := runApprovalCLI(ctx, "state", "list", "--json")
	if err != nil {
		return nil, fmt.Errorf("state list failed: %w: %s", err, string(out))
	}
	var states []taskWorkingState
	if err := json.Unmarshal(out, &states); err != nil {
		return nil, fmt.Errorf("state list returned invalid JSON: %w", err)
	}
	return states, nil
}

func loadTaskApprovals(ctx context.Context) ([]taskApprovalRecord, error) {
	out, err := runApprovalCLI(ctx, "task", "approval", "list", "--json")
	if err != nil {
		return nil, fmt.Errorf("task approval list failed: %w: %s", err, string(out))
	}
	var approvals []taskApprovalRecord
	if err := json.Unmarshal(out, &approvals); err != nil {
		return nil, fmt.Errorf("task approval list returned invalid JSON: %w", err)
	}
	return approvals, nil
}
