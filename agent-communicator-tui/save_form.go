package main

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

type agentSaved struct {
	Name string
	Err  error
}

func (m *model) initSaveForm() {
	if len(m.rows) == 0 || m.selected >= len(m.rows) {
		return
	}
	row := m.rows[m.selected]

	m.showingSaveForm = true
	m.saveFormIndex = 0

	m.saveFormInputs = make([]textinput.Model, 4)

	// 1. Prepopulate Name
	nameInput := textinput.New()
	cleanName := row.AgentName
	if strings.Contains(cleanName, "-agent-") {
		cleanName = strings.Split(cleanName, "-agent-")[0]
	}
	nameInput.SetValue(cleanName)
	nameInput.Placeholder = "Agent Configuration Name"
	nameInput.Focus()
	m.saveFormInputs[0] = nameInput

	// 2. Prepopulate Description
	descInput := textinput.New()
	descInput.SetValue(fmt.Sprintf("Remote-saved configuration for agent %s in %s", cleanName, row.CWD))
	descInput.Placeholder = "Description"
	m.saveFormInputs[1] = descInput

	// 3. Prepopulate Command
	cmdInput := textinput.New()
	cmdInput.SetValue(row.AgentCmd)
	cmdInput.Placeholder = "Launcher Command (e.g., cli)"
	m.saveFormInputs[2] = cmdInput

	// 4. Prepopulate CWD
	cwdInput := textinput.New()
	cwdInput.SetValue(row.CWD)
	cwdInput.Placeholder = "Working Directory"
	m.saveFormInputs[3] = cwdInput
}

func (m model) updateSaveForm(msg tea.Msg) (model, tea.Cmd) {
	var cmds []tea.Cmd

	switch msg := msg.(type) {
	case tea.KeyMsg:
		switch msg.Type {
		case tea.KeyEsc:
			m.showingSaveForm = false
			return m, nil
		case tea.KeyTab:
			m.saveFormIndex = (m.saveFormIndex + 1) % 6
			m.focusSaveFormInput()
			return m, nil
		case tea.KeyShiftTab:
			m.saveFormIndex = (m.saveFormIndex - 1 + 6) % 6
			m.focusSaveFormInput()
			return m, nil
		case tea.KeyCtrlS:
			row := m.rows[m.selected]
			m.showingSaveForm = false
			return m, saveAgentCmd(
				m.local,
				row,
				m.saveFormInputs[0].Value(),
				m.saveFormInputs[2].Value(),
				m.saveFormInputs[1].Value(),
				m.saveFormInputs[3].Value(),
			)
		case tea.KeyEnter:
			if m.saveFormIndex == 4 { // Save Button
				row := m.rows[m.selected]
				m.showingSaveForm = false
				return m, saveAgentCmd(
					m.local,
					row,
					m.saveFormInputs[0].Value(),
					m.saveFormInputs[2].Value(),
					m.saveFormInputs[1].Value(),
					m.saveFormInputs[3].Value(),
				)
			}
			if m.saveFormIndex == 5 { // Cancel Button
				m.showingSaveForm = false
				return m, nil
			}
			// Otherwise advance focus
			m.saveFormIndex = (m.saveFormIndex + 1) % 6
			m.focusSaveFormInput()
			return m, nil
		}
	}

	// Update active text inputs
	for i := range m.saveFormInputs {
		if i == m.saveFormIndex {
			var cmd tea.Cmd
			m.saveFormInputs[i], cmd = m.saveFormInputs[i].Update(msg)
			cmds = append(cmds, cmd)
		}
	}

	return m, tea.Batch(cmds...)
}

func (m *model) focusSaveFormInput() {
	for i := range m.saveFormInputs {
		if i == m.saveFormIndex {
			m.saveFormInputs[i].Focus()
		} else {
			m.saveFormInputs[i].Blur()
		}
	}
}

func (m model) renderSaveForm() string {
	var b strings.Builder

	b.WriteString("Save Agent Configuration\n\n")

	labels := []string{"Name:", "Description:", "Command:", "CWD:"}
	for i := range m.saveFormInputs {
		style := lipgloss.NewStyle()
		if i == m.saveFormIndex {
			style = style.Foreground(colors.Info)
		}
		b.WriteString(style.Render(labels[i]) + "\n")
		b.WriteString(m.saveFormInputs[i].View() + "\n\n")
	}

	// Render Buttons
	saveStyle := lipgloss.NewStyle().Padding(0, 2)
	cancelStyle := lipgloss.NewStyle().Padding(0, 2)

	if m.saveFormIndex == 4 {
		saveStyle = saveStyle.Background(colors.Info).Foreground(colors.BaseBg)
	} else {
		saveStyle = saveStyle.Border(lipgloss.NormalBorder()).BorderForeground(colors.Muted)
	}

	if m.saveFormIndex == 5 {
		cancelStyle = cancelStyle.Background(colors.Error).Foreground(colors.BaseBg)
	} else {
		cancelStyle = cancelStyle.Border(lipgloss.NormalBorder()).BorderForeground(colors.Muted)
	}

	buttons := lipgloss.JoinHorizontal(lipgloss.Left,
		saveStyle.Render("Save"),
		"  ",
		cancelStyle.Render("Cancel"),
	)
	b.WriteString("\n" + buttons)

	// Wrap in a box
	boxStyle := lipgloss.NewStyle().
		Border(lipgloss.RoundedBorder()).
		BorderForeground(colors.Info).
		Padding(1, 3).
		Width(60)

	return boxStyle.Render(b.String())
}

func saveAgentCmd(local localClient, row agentRow, agentName, command, description, cwd string) tea.Cmd {
	return func() tea.Msg {
		if row.Scope != "remote" {
			return saveLocalAgentCmd(agentName, command, description, cwd)()
		}
		if local == nil {
			return agentSaved{Name: agentName, Err: fmt.Errorf("local tracker client unavailable")}
		}
		if row.TrackerID == "" {
			return agentSaved{Name: agentName, Err: fmt.Errorf("remote tracker ID unavailable")}
		}
		return publishRemoteSaveRequestCmd(local, row, agentName, command, description, cwd)()
	}
}

func saveTargetID(row agentRow) string {
	if strings.TrimSpace(row.AgentID) != "" {
		return strings.TrimSpace(row.AgentID)
	}
	if strings.TrimSpace(row.AgentName) != "" {
		return strings.TrimSpace(row.AgentName)
	}
	return strings.TrimSpace(row.Name)
}

func saveLocalAgentCmd(agentName, command, description, cwd string) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()

		args := []string{"save", "-a", agentName, "-w", cwd, "-c", command}
		if description != "" {
			args = append(args, "-d", description)
		}
		cmd := broccoliAgentTrackerCommandContext(ctx, args...)
		out, err := cmd.CombinedOutput()
		if err != nil {
			trimmed := strings.TrimSpace(string(out))
			if trimmed != "" {
				err = fmt.Errorf("%w: %s", err, trimmed)
			}
		}
		return agentSaved{Name: agentName, Err: err}
	}
}

func publishRemoteSaveRequestCmd(local localClient, row agentRow, agentName, command, description, cwd string) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()

		payload := map[string]any{"agent_to_save": saveTargetID(row)}
		if agentName != "" {
			payload["agent_name"] = agentName
		}
		if command != "" {
			payload["command"] = command
		}
		if description != "" {
			payload["description"] = description
		}
		if cwd != "" {
			payload["cwd"] = cwd
		}

		err := local.PublishTrackerEvent(ctx, row.TrackerID, "save_request", payload)
		return agentSaved{Name: agentName, Err: err}
	}
}
