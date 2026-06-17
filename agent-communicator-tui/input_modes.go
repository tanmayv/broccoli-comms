package main

import "strings"

type inputMode int

const (
	inputModeMessage inputMode = iota
	inputModeText
	inputModeKeys
	inputModeBroadcast
)

func (m inputMode) name() string {
	switch m {
	case inputModeText:
		return "text"
	case inputModeKeys:
		return "key"
	case inputModeBroadcast:
		return "broadcast"
	default:
		return "msg"
	}
}

func composerActionForMode(input string, mode inputMode) composerAction {
	if slashComposerCommand(input) {
		return parseComposerAction(input)
	}
	switch mode {
	case inputModeText:
		return composerAction{Kind: "direct_text", Text: input, Submit: true, Original: input}
	case inputModeKeys:
		return composerAction{Kind: "direct_keys", Keys: strings.Fields(input), Original: input}
	case inputModeBroadcast:
		return composerAction{Kind: "broadcast", Body: input, Original: input}
	default:
		return composerAction{Kind: "message", Body: input, Submit: true, Original: input}
	}
}

func slashComposerCommand(input string) bool {
	trimmed := strings.TrimSpace(input)
	for _, prefix := range []string{"/msg", "/text", "/key", "/keys", "/broadcast", "/approval", "/reject", "/needs", "/memory", "/swarm", "/restart"} {
		if trimmed == prefix || strings.HasPrefix(trimmed, prefix+" ") {
			return true
		}
	}
	return false
}
