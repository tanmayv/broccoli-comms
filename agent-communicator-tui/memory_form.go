package main

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
)

type memoryFormMode int

const (
	memoryFormNone memoryFormMode = iota
	memoryFormNew
)

type memoryFormState struct {
	Mode          memoryFormMode
	Index         int
	Inputs        []textinput.Model
	TrustedManual bool
	Err           error
}

type memoryFormSubmitted struct {
	Action string
	Err    error
}

const (
	memoryFormType = iota
	memoryFormTitle
	memoryFormBody
	memoryFormAgent
	memoryFormSubjectAgent
	memoryFormTags
	memoryFormSourceTask
	memoryFormFieldCount
)

var memoryFormLabels = []string{"Type", "Title", "Body", "Agent", "Subject agent", "Tags", "Source task"}

func newMemoryTextInput(placeholder, value string) textinput.Model {
	input := textinput.New()
	input.Placeholder = placeholder
	input.SetValue(value)
	input.CharLimit = 4000
	return input
}

func (m *model) openNewMemoryForm() {
	m.memoryForm = memoryFormState{Mode: memoryFormNew, Inputs: make([]textinput.Model, memoryFormFieldCount)}
	m.memoryForm.Inputs[memoryFormType] = newMemoryTextInput("fact|habit|episode|expertise|skill", "fact")
	m.memoryForm.Inputs[memoryFormTitle] = newMemoryTextInput("Short memory title", "")
	m.memoryForm.Inputs[memoryFormBody] = newMemoryTextInput("Memory body", "")
	m.memoryForm.Inputs[memoryFormAgent] = newMemoryTextInput("proposer agent", m.ownName)
	m.memoryForm.Inputs[memoryFormSubjectAgent] = newMemoryTextInput("optional subject agent", "")
	m.memoryForm.Inputs[memoryFormTags] = newMemoryTextInput("comma,separated,tags", "")
	m.memoryForm.Inputs[memoryFormSourceTask] = newMemoryTextInput("source task id", "")
	m.focusMemoryFormInput()
}

func (m *model) focusMemoryFormInput() {
	for i := range m.memoryForm.Inputs {
		if i == m.memoryForm.Index && m.memoryFormFieldEditable(i) {
			m.memoryForm.Inputs[i].Focus()
		} else {
			m.memoryForm.Inputs[i].Blur()
		}
	}
}

func (m model) memoryFormFieldEditable(index int) bool {
	return index >= 0 && index < memoryFormFieldCount
}

func (m model) nextMemoryFormIndex(delta int) int {
	idx := m.memoryForm.Index
	for i := 0; i < memoryFormFieldCount; i++ {
		idx = (idx + delta + memoryFormFieldCount) % memoryFormFieldCount
		if m.memoryFormFieldEditable(idx) {
			return idx
		}
	}
	return m.memoryForm.Index
}

func (m model) memoryFormActive() bool { return m.memoryForm.Mode != memoryFormNone }

func (m model) updateMemoryForm(msg tea.Msg) (model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.KeyMsg:
		switch msg.Type {
		case tea.KeyEsc:
			m.memoryForm = memoryFormState{}
			return m, nil
		case tea.KeyTab, tea.KeyDown:
			m.memoryForm.Index = m.nextMemoryFormIndex(1)
			m.focusMemoryFormInput()
			return m, nil
		case tea.KeyShiftTab, tea.KeyUp:
			m.memoryForm.Index = m.nextMemoryFormIndex(-1)
			m.focusMemoryFormInput()
			return m, nil
		case tea.KeyCtrlT:
			m.memoryForm.TrustedManual = !m.memoryForm.TrustedManual
			return m, nil
		case tea.KeyEnter:
			if err := m.validateMemoryForm(); err != nil {
				m.memoryForm.Err = err
				m.memoryErr = err
				return m, nil
			}
			return m, submitMemoryFormCmd(m.local, m.memoryForm)
		}
	}
	if len(m.memoryForm.Inputs) > 0 && m.memoryForm.Index >= 0 && m.memoryForm.Index < len(m.memoryForm.Inputs) {
		var cmd tea.Cmd
		m.memoryForm.Inputs[m.memoryForm.Index], cmd = m.memoryForm.Inputs[m.memoryForm.Index].Update(msg)
		return m, cmd
	}
	return m, nil
}

func (m model) validateMemoryForm() error {
	title := strings.TrimSpace(m.memoryForm.Inputs[memoryFormTitle].Value())
	body := strings.TrimSpace(m.memoryForm.Inputs[memoryFormBody].Value())
	if title == "" {
		return fmt.Errorf("memory title is required")
	}
	if body == "" {
		return fmt.Errorf("memory body is required")
	}
	memoryType := strings.TrimSpace(m.memoryForm.Inputs[memoryFormType].Value())
	if !validMemoryType(memoryType) {
		return fmt.Errorf("memory type must be fact, habit, episode, expertise, or skill")
	}
	if !m.memoryForm.TrustedManual && strings.TrimSpace(m.memoryForm.Inputs[memoryFormSourceTask].Value()) == "" {
		return fmt.Errorf("source task is required unless trusted manual is enabled")
	}
	return nil
}

func validMemoryType(value string) bool {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "fact", "habit", "episode", "expertise", "skill":
		return true
	default:
		return false
	}
}

func submitMemoryFormCmd(local localClient, form memoryFormState) tea.Cmd {
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		params := map[string]any{
			"type":           strings.TrimSpace(form.Inputs[memoryFormType].Value()),
			"title":          strings.TrimSpace(form.Inputs[memoryFormTitle].Value()),
			"body":           strings.TrimSpace(form.Inputs[memoryFormBody].Value()),
			"trusted_manual": form.TrustedManual,
		}

		if agent := strings.TrimSpace(form.Inputs[memoryFormAgent].Value()); agent != "" {
			params["proposed_by"] = agent
		}
		if subject := strings.TrimSpace(form.Inputs[memoryFormSubjectAgent].Value()); subject != "" {
			params["subject_agent"] = subject
		}
		if sourceTask := strings.TrimSpace(form.Inputs[memoryFormSourceTask].Value()); sourceTask != "" {
			params["source_task_id"] = sourceTask
		}

		tags := splitMemoryTags(form.Inputs[memoryFormTags].Value())
		if len(tags) > 0 {
			params["tags"] = tags
		}

		_, err := local.MemoryPropose(ctx, params)
		if err != nil {
			return memoryFormSubmitted{Action: "memory propose", Err: fmt.Errorf("memory form submit failed: %w", err)}
		}
		return memoryFormSubmitted{Action: "memory propose"}
	}
}

func splitMemoryTags(value string) []string {
	parts := strings.Split(value, ",")
	tags := make([]string, 0, len(parts))
	for _, part := range parts {
		if tag := strings.TrimSpace(part); tag != "" {
			tags = append(tags, tag)
		}
	}
	return tags
}
