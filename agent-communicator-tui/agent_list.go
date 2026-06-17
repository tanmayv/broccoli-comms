package main

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"sort"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/tanmayvijay/home-manager-core/agent-communicator-tui/internal/tracker"
)

var agentListProvider = loadAgentsFromBroccoliComms

func broccoliAgentTrackerCommand(args ...string) *exec.Cmd {
	return broccoliAgentTrackerCommandContext(context.Background(), args...)
}

func broccoliAgentTrackerCommandContext(ctx context.Context, args ...string) *exec.Cmd {
	cli, wrapperStyle := broccoliAgentTrackerCLI()
	cmdArgs := append([]string{}, args...)
	if wrapperStyle {
		cmdArgs = append([]string{"agent-tracker"}, args...)
	}
	return exec.CommandContext(ctx, cli, cmdArgs...)
}

func broccoliAgentTrackerCLI() (string, bool) {
	if cli := os.Getenv("BROCCOLI_COMMS_CLI"); cli != "" {
		return cli, true
	}
	return "broccoli-comms", true
}

func loadHealth(local localClient) tea.Cmd {
	return func() tea.Msg {
		if local == nil {
			return healthLoaded{}
		}
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		info, err := local.TrackerInfo(ctx)
		return healthLoaded{Info: info, Err: err}
	}
}

func loadAgents(local localClient) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		rows, err := agentListProvider(ctx, local)
		return agentsLoaded{Rows: rows, Err: err}
	}
}

func loadAgentsFromBroccoliComms(ctx context.Context, local localClient) ([]agentRow, error) {
	// The primary agent list is the live agent switcher. Prefer the tracker RPC
	// list, which only returns agents currently known to the live tracker, before
	// falling back to the broader configured-agent CLI inventory.
	if local != nil {
		rows, err := loadAgentsFromRPC(ctx, local)
		if err == nil {
			return rows, nil
		}
	}

	cmd := broccoliCommsCommandContext(ctx, "agent", "list", "--include-remote", "--json")
	out, err := cmd.CombinedOutput()
	if err != nil {
		return nil, err
	}
	var payload broccoliAgentListPayload
	if err := json.Unmarshal(out, &payload); err != nil {
		return nil, err
	}
	rows := make([]agentRow, 0, len(payload.Agents))
	for key, row := range payload.Agents {
		if !row.Running {
			continue
		}
		rows = append(rows, rowFromBroccoliAgent(key, row))
	}
	rows = dedupeLocalRegistryRows(rows)
	sortRows(rows)
	return rows, nil
}

func loadAgentsFromRPC(ctx context.Context, local localClient) ([]agentRow, error) {
	if local == nil {
		return nil, nil
	}
	agents, err := local.ListWithOptions(ctx, tracker.ListOptions{
		IncludeRemote: true,
		AgentID:       os.Getenv("AGENT_ID"),
		AgentName:     os.Getenv("AGENT_NAME"),
	})
	if err != nil {
		return nil, err
	}
	rows := make([]agentRow, 0, len(agents))
	for key, agent := range agents {
		rows = append(rows, rowFromTrackerAgent(key, agent))
	}
	rows = dedupeLocalRegistryRows(rows)
	sortRows(rows)
	return rows, nil
}

func rowFromBroccoliAgent(key string, agent broccoliAgentListRow) agentRow {
	scope := "local"
	if agent.Remote || agent.ScopeKind == "remote" {
		scope = "remote"
	}
	name := fallback(agent.Name, key)
	if scope == "remote" {
		host, remoteName := splitRemoteTarget(fallback(agent.TargetAddress, key))
		if agent.Hostname != "" {
			host = agent.Hostname
		}
		if remoteName == "" {
			remoteName = name
		}
		name = remoteDisplayName(fallback(agent.TargetAddress, key), host, remoteName)
		return agentRow{Name: name, TargetAddress: fallback(agent.TargetAddress, key), AgentName: remoteName, Scope: scope, Status: agent.Status, CWD: fallback(agent.CWD, "unavailable"), Hostname: host, TrackerID: agent.TrackerID, RegistryName: agent.RegistryName, Configured: agentBoolPtr(agent.IsConfigured), Running: agentBoolPtr(agent.Running), Launchable: agentBoolPtr(agent.Launchable), CurrentTask: agent.CurrentTask, CurrentTaskID: agent.CurrentTaskID, CurrentTaskStatus: agent.CurrentTaskStatus, CurrentTaskNextStep: agent.CurrentTaskNextStep}
	}
	return agentRow{Name: name, TargetAddress: fallback(agent.TargetAddress, key), AgentName: name, Scope: scope, Status: agent.Status, CWD: fallback(agent.CWD, "unknown"), Hostname: agent.Hostname, Configured: agentBoolPtr(agent.IsConfigured), Running: agentBoolPtr(agent.Running), Launchable: agentBoolPtr(agent.Launchable), CurrentTask: agent.CurrentTask, CurrentTaskID: agent.CurrentTaskID, CurrentTaskStatus: agent.CurrentTaskStatus, CurrentTaskNextStep: agent.CurrentTaskNextStep}
}

func agentBoolPtr(v bool) *bool { return &v }

func dedupeLocalRegistryRows(rows []agentRow) []agentRow {
	localNames := map[string]bool{}
	localIDs := map[string]bool{}
	for _, row := range rows {
		if row.Scope == "remote" {
			continue
		}
		for _, name := range []string{row.Name, row.AgentName} {
			name = strings.TrimSpace(name)
			if name != "" {
				localNames[name] = true
			}
		}
		if id := strings.TrimSpace(row.AgentID); id != "" {
			localIDs[id] = true
		}
	}
	out := make([]agentRow, 0, len(rows))
	for _, row := range rows {
		if row.Scope == "remote" && remoteRowIsLocalDuplicate(row, localNames, localIDs) {
			continue
		}
		out = append(out, row)
	}
	return out
}

func remoteRowIsLocalDuplicate(row agentRow, localNames, localIDs map[string]bool) bool {
	if !sameHost(remoteRowHost(row), localHostname()) {
		return false
	}
	if row.AgentID != "" && localIDs[row.AgentID] {
		return true
	}
	return localNames[strings.TrimSpace(row.AgentName)]
}

func remoteRowHost(row agentRow) string {
	if strings.TrimSpace(row.Hostname) != "" {
		return row.Hostname
	}
	host, _ := splitRemoteTarget(fallback(row.TargetAddress, row.Name))
	return host
}

func sameHost(a, b string) bool {
	return strings.EqualFold(strings.TrimSpace(a), strings.TrimSpace(b))
}

func sortRows(rows []agentRow) {
	sort.Slice(rows, func(i, j int) bool {
		if rows[i].Scope != rows[j].Scope {
			return rows[i].Scope < rows[j].Scope
		}
		return rows[i].Name < rows[j].Name
	})
}

func rowFromTrackerAgent(key string, agent tracker.Agent) agentRow {
	scope := fallback(agent.Scope, "local")
	target := fallback(agent.TargetAddress, key)
	if scope != "remote" {
		return agentRow{
			Name:                key,
			TargetAddress:       target,
			AgentName:           key,
			Scope:               "local",
			Status:              agent.Status,
			CWD:                 fallback(agent.CWD, "unknown"),
			Hostname:            agent.Hostname,
			TmuxPane:            agent.TmuxPane,
			AgentCmd:            agent.AgentCmd,
			AgentType:           agent.AgentType,
			AgentID:             agent.AgentID,
			TrackerID:           agent.TrackerID,
			RegistryName:        agent.RegistryName,
			ModelType:           agent.ModelType,
			CurrentTask:         agent.CurrentTask,
			CurrentTaskID:       agent.CurrentTaskID,
			CurrentTaskStatus:   agent.CurrentTaskStatus,
			CurrentTaskNextStep: agent.CurrentTaskNextStep,
			Detection:           agent.Detection,
		}
	}
	host, name := splitRemoteTarget(target)
	if agent.Hostname != "" {
		host = agent.Hostname
	}
	if name == "" {
		name = fallback(agent.Name, key)
	}
	return agentRow{
		Name:                remoteDisplayName(target, host, name),
		TargetAddress:       target,
		Hostname:            host,
		AgentName:           name,
		Scope:               "remote",
		Status:              agent.Status,
		CWD:                 fallback(agent.CWD, "unavailable"),
		TmuxPane:            agent.TmuxPane,
		AgentCmd:            agent.AgentCmd,
		AgentType:           agent.AgentType,
		AgentID:             agent.AgentID,
		TrackerID:           agent.TrackerID,
		RegistryName:        agent.RegistryName,
		ModelType:           agent.ModelType,
		CurrentTask:         agent.CurrentTask,
		CurrentTaskID:       agent.CurrentTaskID,
		CurrentTaskStatus:   agent.CurrentTaskStatus,
		CurrentTaskNextStep: agent.CurrentTaskNextStep,
		Detection:           agent.Detection,
	}
}

func splitRemoteTarget(target string) (string, string) {
	if strings.Contains(target, ":") {
		target = strings.SplitN(target, ":", 2)[1]
	}
	parts := strings.SplitN(target, "/", 2)
	if len(parts) != 2 {
		return "", target
	}
	return parts[0], parts[1]
}

func remoteDisplayName(target, host, name string) string {
	prefix := ""
	if strings.Contains(target, ":") {
		prefix = strings.SplitN(target, ":", 2)[0] + ":"
	}
	return prefix + host + "/" + name
}
