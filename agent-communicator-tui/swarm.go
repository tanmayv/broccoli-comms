package main

import (
	"context"
	"fmt"
	"sort"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/tanmayvijay/home-manager-core/agent-communicator-tui/internal/tracker"
)

type swarmRow struct {
	Name        string
	Main        agentRow
	Members     []agentRow
	MainMissing bool
	Warning     string
}

func loadSwarms(local localClient) tea.Cmd {
	return func() tea.Msg {
		if local == nil {
			return swarmsLoaded{}
		}
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		result, err := local.ListSwarms(ctx)
		return swarmsLoaded{Rows: swarmRowsFromTracker(result.Swarms), Err: err}
	}
}

func loadSelectedSwarmTimeline(local localClient, swarmName string) tea.Cmd {
	return func() tea.Msg {
		if local == nil || strings.TrimSpace(swarmName) == "" {
			return swarmTimelineLoaded{Swarm: swarmName}
		}
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		result, err := local.GetSwarmTimeline(ctx, swarmName, advancedInboxFetchLimit)
		return swarmTimelineLoaded{Swarm: swarmName, Messages: result.Messages, Err: err}
	}
}

func swarmRowsFromTracker(swarms []tracker.Swarm) []swarmRow {
	rows := make([]swarmRow, 0, len(swarms))
	for _, swarm := range swarms {
		row := swarmRow{Name: swarm.Name, Warning: strings.Join(swarm.Warnings, " · ")}
		row.Main = swarmMemberToAgentRow(swarm.Main)
		row.MainMissing = row.Main.Name == ""
		for _, member := range swarm.Members {
			row.Members = append(row.Members, swarmMemberToAgentRow(member))
		}
		if row.MainMissing && row.Warning == "" {
			row.Warning = "No main agent configured/running"
		}
		rows = append(rows, row)
	}
	return rows
}

func swarmMemberToAgentRow(member tracker.SwarmMember) agentRow {
	name := fallback(member.Name, member.AgentName)
	return agentRow{
		Name:          name,
		AgentName:     fallback(member.AgentName, name),
		TargetAddress: member.TargetAddress,
		Configured:    member.Configured,
		Running:       member.Running,
		Launchable:    member.Launchable,
		Role:          member.Role,
		Scope:         fallback(member.Scope, "local"),
		Status:        member.Status,
		Hostname:      member.Hostname,
		AgentID:       member.AgentID,
		TrackerID:     member.TrackerID,
		RegistryName:  member.RegistryName,
		ModelType:     member.ModelType,
		AgentType:     member.AgentType,
		AgentCmd:      member.AgentCmd,
	}
}

func swarmCanSendToMain(swarm swarmRow) bool {
	return !swarm.MainMissing && swarm.Main.TargetAddress != "" && !boolPtrFalse(swarm.Main.Running)
}

func boolPtrTrue(value *bool) bool {
	return value != nil && *value
}

func boolPtrFalse(value *bool) bool {
	return value != nil && !*value
}

func swarmMemberStateText(member agentRow) string {
	parts := []string{}
	if member.Running != nil {
		if *member.Running {
			parts = append(parts, "running")
		} else if boolPtrTrue(member.Configured) {
			parts = append(parts, "configured offline")
		} else {
			parts = append(parts, "offline")
		}
	} else if boolPtrTrue(member.Configured) && member.TargetAddress == "" {
		parts = append(parts, "configured offline")
	} else if boolPtrTrue(member.Configured) {
		parts = append(parts, "configured")
	} else if member.Status != "" {
		parts = append(parts, member.Status)
	}

	if member.Launchable != nil {
		if *member.Launchable {
			parts = append(parts, "launchable")
		} else {
			parts = append(parts, "non-launchable")
		}
	} else if member.Scope == "remote" {
		parts = append(parts, "remote")
	}
	return strings.Join(parts, " · ")
}

func swarmMemberLabel(member agentRow, role string) string {
	label := member.Name
	if role != "" {
		label += " · " + role
	}
	if state := swarmMemberStateText(member); state != "" {
		label += " · " + state
	}
	return label
}

func (m model) selectedSwarmRow() (swarmRow, bool) {
	if len(m.swarms) == 0 || m.selectedSwarm < 0 || m.selectedSwarm >= len(m.swarms) {
		return swarmRow{}, false
	}
	return m.swarms[m.selectedSwarm], true
}

func (m model) selectedSwarmName() string {
	if swarm, ok := m.selectedSwarmRow(); ok {
		return swarm.Name
	}
	return ""
}

func (m *model) clampSelectedSwarm() {
	if m.selectedSwarm >= len(m.swarms) {
		m.selectedSwarm = max(0, len(m.swarms)-1)
	}
	if m.selectedSwarm < 0 {
		m.selectedSwarm = 0
	}
}

func (m *model) selectSwarm(delta int) {
	if len(m.swarms) == 0 {
		m.selectedSwarm = 0
		return
	}
	m.selectedSwarm = (m.selectedSwarm + delta + len(m.swarms)) % len(m.swarms)
	m.swarmMessages = nil
	m.selectLatestMessage()
}

func (m model) liveAgentNames() map[string]bool {
	live := map[string]bool{}
	for _, row := range m.rows {
		if row.Scope == "remote" || rowTarget(row) == "" || boolPtrFalse(row.Running) {
			continue
		}
		live[row.Name] = true
		if row.AgentName != "" {
			live[row.AgentName] = true
		}
	}
	return live
}

func (m model) validateSwarmCreateAction(action composerAction) error {
	if action.SwarmName == "" || action.MainAgent == "" || len(action.Subagents) == 0 {
		return fmt.Errorf("/swarm create requires NAME --main AGENT --subagent AGENT")
	}
	live := m.liveAgentNames()
	if !live[action.MainAgent] {
		return fmt.Errorf("main agent %q is not a live local agent", action.MainAgent)
	}
	seen := map[string]bool{action.MainAgent: true}
	for _, agent := range action.Subagents {
		if !live[agent] {
			return fmt.Errorf("subagent %q is not a live local agent", agent)
		}
		if seen[agent] {
			return fmt.Errorf("swarm agents must be unique")
		}
		seen[agent] = true
	}
	return nil
}

func (m model) swarmLines(width int) []string {
	width = max(1, width)
	if len(m.swarms) == 0 {
		return m.swarmEmptyLines(width)
	}
	swarm, _ := m.selectedSwarmRow()
	lines := []string{fgOnBg(colors.Accent, colors.BaseBg).Bold(true).Render("Swarm " + swarm.Name)}
	if swarm.Warning != "" {
		lines = append(lines, wrapBackgroundStyledText("warning · "+swarm.Warning, width, colors.Warning, colors.BaseBg)...)
	}
	if swarm.MainMissing {
		lines = append(lines, wrapBackgroundStyledText("No main agent configured/running. Swarm messaging will be enabled after a main agent is available.", width, colors.Warning, colors.BaseBg)...)
	} else {
		lines = append(lines, wrapBackgroundStyledText("main · "+swarmMemberLabel(swarm.Main, ""), width, colors.TextSubtle, colors.BaseBg)...)
		if !swarmCanSendToMain(swarm) {
			lines = append(lines, wrapBackgroundStyledText("Main agent is offline or has no target address. Swarm messaging is disabled until it is running.", width, colors.Warning, colors.BaseBg)...)
		}
	}
	messages := m.swarmDisplayMessages()
	if len(messages) == 0 {
		lines = append(lines, "", mutedStyle.Render("No swarm timeline messages yet."))
		return lines
	}
	lines = append(lines, "")
	for i, msg := range messages {
		if i > 0 {
			lines = append(lines, bgSpaces(width, colors.BaseBg))
		}
		lines = append(lines, m.messageBubbleLines(msg.Message, i, width)...)
	}
	return lines
}

func (m model) swarmDisplayMessages() []swarmDisplayMessage {
	messages := make([]swarmDisplayMessage, 0, len(m.swarmMessages))
	seen := map[string]bool{}
	for _, msg := range m.swarmMessages {
		body := msg.Body
		if body == "" {
			body = msg.Message
		}
		label := strings.TrimSpace(msg.Sender)
		if msg.Recipient != "" {
			label = strings.TrimSpace(label + " → " + msg.Recipient)
		}
		if msg.MessageID != "" {
			seen[msg.MessageID] = true
		}
		messages = append(messages, swarmDisplayMessage{ID: msg.MessageID, Message: tracker.Message{Sender: label, Body: body, Timestamp: msg.Timestamp, ContentType: msg.ContentType, MessageID: msg.MessageID}})
	}
	swarm, ok := m.selectedSwarmRow()
	if !ok || swarm.MainMissing || swarm.Main.TargetAddress == "" {
		return messages
	}
	appendSent := func(msg tracker.Message) {
		if msg.MessageID != "" && seen[msg.MessageID] {
			return
		}
		if msg.MessageID != "" {
			seen[msg.MessageID] = true
		}
		messages = append(messages, swarmDisplayMessage{ID: msg.MessageID, Message: tracker.Message{Sender: "You → " + swarm.Main.Name, Body: msg.Body, Timestamp: msg.Timestamp, ContentType: msg.ContentType, MessageID: msg.MessageID, Delivered: msg.Delivered, Notified: msg.Notified, Read: msg.Read}})
	}
	for _, msg := range m.sentMessages[conversationKey(swarm.Main)] {
		appendSent(msg)
	}
	for _, rec := range m.outbox {
		if outboxRecordMatchesRow(rec, swarm.Main) {
			appendSent(outboxMessage(rec, false))
		}
	}
	sort.SliceStable(messages, func(i, j int) bool {
		ti, okI := parseMessageTime(messages[i].Message.Timestamp)
		tj, okJ := parseMessageTime(messages[j].Message.Timestamp)
		if !okI || !okJ || ti.Equal(tj) {
			return false
		}
		return ti.After(tj)
	})
	return messages
}

type swarmDisplayMessage struct {
	ID      string
	Message tracker.Message
}

func (m model) swarmEmptyLines(width int) []string {
	lines := []string{}
	if m.swarmErr != nil {
		lines = append(lines, wrapBackgroundStyledText("Swarm API unavailable: "+m.swarmErr.Error(), width, colors.Warning, colors.BaseBg)...)
	}
	text := []string{
		"No swarms found.",
		"Create from live agents: /swarm create backend-fix --main planner --subagent coder-a",
		"Start a configured swarm: broccoli-comms agent start-swarm backend-fix",
		"Configure members in config.json under swarms.backend-fix.members.",
	}
	for _, line := range text {
		lines = append(lines, wrapBackgroundStyledText(line, width, colors.Muted, colors.BaseBg)...)
	}
	return lines
}

func (m model) swarmSidebarView(width, height int) string {
	currentH := min(8, max(5, height/3))
	current := m.swarmCurrentPanel(width, currentH)
	list := m.swarmListPanel(width, max(1, height-currentH))
	return lipgloss.JoinVertical(lipgloss.Left, current, list)
}

func (m model) swarmCurrentPanel(width, height int) string {
	title := shellTitleStyle.Render("Swarm Mode")
	body := title
	if swarm, ok := m.selectedSwarmRow(); ok {
		main := "missing main"
		if !swarm.MainMissing {
			main = swarm.Main.Name
		}
		body += "\n" + fgOnBg(colors.SelectedFg, colors.SelectedBg).Bold(true).Render(truncateCells(swarm.Name, max(1, width-4)))
		mainLine := "main · " + main
		if !swarm.MainMissing {
			if state := swarmMemberStateText(swarm.Main); state != "" {
				mainLine += " · " + state
			}
		}
		body += "\n" + mutedStyle.Render(truncateCells(mainLine, max(1, width-4)))
		body += "\n" + mutedStyle.Render(fmt.Sprintf("members · %d", len(swarm.Members)))
		if swarm.Warning != "" {
			body += "\n" + fgOnBg(colors.Warning, colors.RightColumnBg).Render(truncateCells(swarm.Warning, max(1, width-4)))
		}
	} else {
		body += "\n" + mutedStyle.Render("no swarms")
	}
	return lipgloss.NewStyle().Width(width).Height(height).Padding(1, 1).Background(colors.RightColumnBg).Render(truncateLines(body, max(1, height-1)))
}

func (m model) swarmListPanel(width, height int) string {
	header := fgOnBg(colors.Accent, colors.RightColumnBg).Bold(true).Render("Swarms")
	var lines []string
	if len(m.swarms) == 0 {
		lines = append(lines, mutedStyle.Render("no swarms"), mutedStyle.Render("/swarm create or agent start-swarm"))
	} else {
		for i, swarm := range m.swarms {
			bg := colors.RightColumnBg
			fg := colors.Text
			prefix := "  "
			if i == m.selectedSwarm {
				bg = colors.SelectedBg
				fg = colors.SelectedFg
				prefix = "> "
			}
			lines = append(lines, fgOnBg(fg, bg).Bold(i == m.selectedSwarm).Render(truncateCells(prefix+swarm.Name, max(1, width-4))))
			main := "main missing"
			if !swarm.MainMissing {
				main = "main " + swarmMemberLabel(swarm.Main, "")
			}
			lines = append(lines, fgOnBg(colors.Muted, bg).Render(truncateCells("  "+main, max(1, width-4))))
		}
	}
	if swarm, ok := m.selectedSwarmRow(); ok && len(swarm.Members) > 0 {
		lines = append(lines, "", sectionHeaderStyle.Render("Members"))
		for _, member := range swarm.Members {
			role := fallback(member.Role, "subagent")
			if role == "subagent" && member.Name == swarm.Main.Name && !swarm.MainMissing {
				role = "main"
			}
			lines = append(lines, mutedStyle.Render(truncateCells("• "+swarmMemberLabel(member, role), max(1, width-4))))
		}
	}
	body := header + "\n" + strings.Join(lines, "\n")
	return lipgloss.NewStyle().Width(width).Height(height).Padding(1, 1).Background(colors.RightColumnBg).Render(truncateLines(body, max(1, height-1)))
}
