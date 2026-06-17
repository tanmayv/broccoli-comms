package main

import (
	"context"
	"fmt"
	"time"

	tea "github.com/charmbracelet/bubbletea"
)

type taskActionConfirmation struct {
	Action string
	TaskID string
}

type taskActionResult struct {
	TaskID string
	Action string
	Err    error
	Status string
}

type taskEditClosed struct {
	TaskID string
	Field  string
	Err    error
	Status string
}

func (c taskActionConfirmation) Active() bool { return c.Action != "" && c.TaskID != "" }

func (m model) selectedTaskRecord() (taskRecord, bool) {
	return selectedTaskRecord(m.taskData())
}

func (m model) taskConfirmationMatches(task taskRecord, action string) bool {
	return m.tasksConfirm.Action == action && m.tasksConfirm.TaskID == task.TaskID
}

func (m model) confirmOrRunTaskAction(task taskRecord, action string) (model, tea.Cmd) {
	if task.TaskID == "" {
		m.tasksErr = fmt.Errorf("task id is required")
		return m, nil
	}
	if !m.taskConfirmationMatches(task, action) {
		m.tasksConfirm = taskActionConfirmation{Action: action, TaskID: task.TaskID}
		m.directInputStatus = taskActionConfirmText(task, action)
		m.directInputStatusErr = false
		return m, nil
	}
	m.tasksConfirm = taskActionConfirmation{}
	m.tasksLoading = true
	return m, taskActionCmd(task, action, m.currentRow().Name)
}

func taskActionConfirmText(task taskRecord, action string) string {
	switch action {
	case "archive":
		return "press enter again to remove/archive " + task.TaskID
	case "archive_chain":
		return "press D again to archive active chain"
	case "assign":
		return "press x again to reassign " + task.TaskID
	case "assign_chain":
		return "press X again to reassign active chain"
	default:
		return "press again to confirm " + action + " " + task.TaskID
	}
}

func taskParticipantAgentName(row agentRow) string {
	return firstNonEmpty(row.AgentName, row.Name, row.TargetAddress)
}

func taskParticipantForAgentRole(task taskRecord, row agentRow, role string) (taskParticipant, bool) {
	for _, participant := range task.Participants {
		if participant.Role == role && selectedAgentMatchesName(row, participant.Agent) && participant.Status != "inactive" {
			return participant, true
		}
	}
	return taskParticipant{}, false
}

func taskParticipantActionCmd(task taskRecord, role, agent string) tea.Cmd {
	return func() tea.Msg {
		if task.TaskID == "" {
			return taskActionResult{TaskID: task.TaskID, Action: "participant_" + role, Err: fmt.Errorf("task id is required")}
		}
		if agent == "" {
			return taskActionResult{TaskID: task.TaskID, Action: "participant_" + role, Err: fmt.Errorf("no selected agent to add as %s", role)}
		}
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		args := []string{"task", "participant", "add", task.TaskID, "--agent", agent, "--role", role, "--json"}
		out, err := runApprovalCLI(ctx, args...)
		if err != nil {
			return taskActionResult{TaskID: task.TaskID, Action: "participant_" + role, Err: fmt.Errorf("task participant add failed: %w: %s", err, string(out))}
		}
		return taskActionResult{TaskID: task.TaskID, Action: "participant_" + role, Status: "Added " + agent + " as " + role}
	}
}

func taskParticipantDeactivateCmd(task taskRecord, participant taskParticipant) tea.Cmd {
	return func() tea.Msg {
		if participant.ParticipantID == "" {
			return taskActionResult{TaskID: task.TaskID, Action: "participant_deactivate", Err: fmt.Errorf("participant id is required")}
		}
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		args := []string{"task", "participant", "update", participant.ParticipantID, "--status", "inactive", "--json"}
		out, err := runApprovalCLI(ctx, args...)
		if err != nil {
			return taskActionResult{TaskID: task.TaskID, Action: "participant_deactivate", Err: fmt.Errorf("task participant deactivate failed: %w: %s", err, string(out))}
		}
		return taskActionResult{TaskID: task.TaskID, Action: "participant_deactivate", Status: "Deactivated " + participant.Agent + " as " + participant.Role}
	}
}

func taskParticipantChangeRoleCmd(task taskRecord, participant taskParticipant, newRole, agent string) tea.Cmd {
	return func() tea.Msg {
		if participant.ParticipantID == "" {
			return taskActionResult{TaskID: task.TaskID, Action: "participant_change_role", Err: fmt.Errorf("participant id is required")}
		}
		if agent == "" {
			return taskActionResult{TaskID: task.TaskID, Action: "participant_change_role", Err: fmt.Errorf("no selected agent to change role")}
		}
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		deactivate := []string{"task", "participant", "update", participant.ParticipantID, "--status", "inactive", "--json"}
		out, err := runApprovalCLI(ctx, deactivate...)
		if err != nil {
			return taskActionResult{TaskID: task.TaskID, Action: "participant_change_role", Err: fmt.Errorf("task participant deactivate failed: %w: %s", err, string(out))}
		}
		add := []string{"task", "participant", "add", task.TaskID, "--agent", agent, "--role", newRole, "--json"}
		out, err = runApprovalCLI(ctx, add...)
		if err != nil {
			return taskActionResult{TaskID: task.TaskID, Action: "participant_change_role", Err: fmt.Errorf("task participant add failed: %w: %s", err, string(out))}
		}
		return taskActionResult{TaskID: task.TaskID, Action: "participant_change_role", Status: "Changed " + agent + " from " + participant.Role + " to " + newRole}
	}
}

func taskActionCmd(task taskRecord, action, agent string) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		args := []string{"task", "update", task.TaskID, "--json"}
		switch action {
		case "archive":
			args = append(args, "--status", "archived")
		case "assign":
			if agent == "" {
				return taskActionResult{TaskID: task.TaskID, Action: action, Err: fmt.Errorf("no selected agent to assign")}
			}
			args = append(args, "--assign-agent", agent)
		case "start":
			args = append(args, "--status", "working")
		case "complete":
			args = append(args, "--status", "done")
		case "ready":
			args = append(args, "--status", "ready")
		default:
			return taskActionResult{TaskID: task.TaskID, Action: action, Err: fmt.Errorf("unsupported task action %q", action)}
		}
		out, err := runApprovalCLI(ctx, args...)
		if err != nil {
			return taskActionResult{TaskID: task.TaskID, Action: action, Err: fmt.Errorf("task %s failed: %w: %s", action, err, string(out))}
		}
		status := "Task " + action + " saved"
		if action == "complete" {
			status = "Task marked complete"
		}
		return taskActionResult{TaskID: task.TaskID, Action: action, Status: status}
	}
}
