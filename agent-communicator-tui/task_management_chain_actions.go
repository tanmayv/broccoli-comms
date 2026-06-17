package main

import (
	"context"
	"fmt"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
)

func (m model) confirmOrRunChainArchive() (model, tea.Cmd) {
	data := m.taskData()
	if len(data.Tasks) == 0 {
		m.tasksErr = fmt.Errorf("no active chain to archive")
		return m, nil
	}
	chainID := firstNonEmpty(data.ActiveChainID, data.RootTaskID, "active-chain")
	if m.tasksConfirm.Action != "archive_chain" || m.tasksConfirm.TaskID != chainID {
		m.tasksConfirm = taskActionConfirmation{Action: "archive_chain", TaskID: chainID}
		m.directInputStatus = taskActionConfirmText(taskRecord{TaskID: chainID}, "archive_chain")
		m.directInputStatusErr = false
		return m, nil
	}
	m.tasksConfirm = taskActionConfirmation{}
	m.tasksLoading = true
	return m, archiveTaskChainCmd(data.Tasks)
}

func (m model) confirmOrRunChainAssign() (model, tea.Cmd) {
	data := m.taskData()
	if len(data.Tasks) == 0 {
		m.tasksErr = fmt.Errorf("no active chain to reassign")
		return m, nil
	}
	chainID := firstNonEmpty(data.ActiveChainID, data.RootTaskID, "active-chain")
	if m.tasksConfirm.Action != "assign_chain" || m.tasksConfirm.TaskID != chainID {
		m.tasksConfirm = taskActionConfirmation{Action: "assign_chain", TaskID: chainID}
		m.directInputStatus = taskActionConfirmText(taskRecord{TaskID: chainID}, "assign_chain")
		m.directInputStatusErr = false
		return m, nil
	}
	m.tasksConfirm = taskActionConfirmation{}
	m.tasksLoading = true
	return m, assignTaskChainCmd(data.Tasks, m.currentRow().Name)
}

func createTaskInChainCmd(title, agent, priority string, dependsOn []string) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		args := []string{"task", "create", "--title", title, "--priority", firstNonEmpty(priority, "P1"), "--json"}
		if agent != "" {
			args = append(args, "--agent", agent)
		}
		if len(dependsOn) > 0 {
			args = append(args, "--depends-on", strings.Join(dependsOn, ","), "--task-chain-id", dependsOn[0], "--root-task-id", dependsOn[0])
		}
		out, err := runApprovalCLI(ctx, args...)
		if err != nil {
			return taskActionResult{Action: "create", Err: fmt.Errorf("task create failed: %w: %s", err, string(out))}
		}
		return taskActionResult{Action: "create", Status: "Task created"}
	}
}

func summarizeTaskChainCmd(chainID string) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		if chainID == "" {
			return taskActionResult{Action: "summary", Err: fmt.Errorf("no active chain to summarize")}
		}
		out, err := runApprovalCLI(ctx, "task", "summarize-chain", chainID, "--json")
		if err != nil {
			return taskActionResult{Action: "summary", Err: fmt.Errorf("chain summary failed: %w: %s", err, string(out))}
		}
		return taskActionResult{Action: "summary", Status: "Chain summary refreshed"}
	}
}

func archiveTaskChainCmd(tasks []taskRecord) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		for _, task := range tasks {
			if task.TaskID == "" || taskCompleted(task) {
				continue
			}
			out, err := runApprovalCLI(ctx, "task", "update", task.TaskID, "--status", "archived", "--json")
			if err != nil {
				return taskActionResult{TaskID: task.TaskID, Action: "archive_chain", Err: fmt.Errorf("archive chain failed at %s: %w: %s", task.TaskID, err, string(out))}
			}
		}
		return taskActionResult{Action: "archive_chain", Status: "Chain archived"}
	}
}

func assignTaskChainCmd(tasks []taskRecord, agent string) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		if agent == "" {
			return taskActionResult{Action: "assign_chain", Err: fmt.Errorf("no selected agent to assign chain")}
		}
		for _, task := range tasks {
			if task.TaskID == "" || taskCompleted(task) {
				continue
			}
			out, err := runApprovalCLI(ctx, "task", "update", task.TaskID, "--assign-agent", agent, "--json")
			if err != nil {
				return taskActionResult{TaskID: task.TaskID, Action: "assign_chain", Err: fmt.Errorf("assign chain failed at %s: %w: %s", task.TaskID, err, string(out))}
			}
		}
		return taskActionResult{Action: "assign_chain", Status: "Chain reassigned"}
	}
}
