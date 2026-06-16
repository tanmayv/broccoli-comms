package main

import (
	"sort"
	"strings"
)

func latestStateByTask(states []taskWorkingState) map[string]taskWorkingState {
	byTask := map[string]taskWorkingState{}
	for _, state := range states {
		if state.TaskID == "" {
			continue
		}
		prev, ok := byTask[state.TaskID]
		if !ok || state.UpdatedAt >= prev.UpdatedAt {
			byTask[state.TaskID] = state
		}
	}
	return byTask
}

func approvalsByTask(approvals []taskApprovalRecord) map[string][]taskApprovalRecord {
	byTask := map[string][]taskApprovalRecord{}
	for _, approval := range approvals {
		if approval.TaskID != "" {
			byTask[approval.TaskID] = append(byTask[approval.TaskID], approval)
		}
	}
	return byTask
}

func (m model) activeTaskChainFor(selected agentRow, stateByTask map[string]taskWorkingState) (string, string, string) {
	if selected.Name == "" && selected.CurrentTaskID == "" {
		return "", "", ""
	}
	if selected.CurrentTaskID != "" {
		if taskArchivedByID(m.tasksItems, selected.CurrentTaskID) {
			return "", "", ""
		}
		if state, ok := stateByTask[selected.CurrentTaskID]; ok && selectedAgentMatchesName(selected, state.Agent) {
			return state.TaskChainID, firstNonEmpty(state.RootTaskID, state.TaskChainID), selected.CurrentTaskID
		}
		return "", "", selected.CurrentTaskID
	}
	for _, state := range m.tasksStates {
		if selected.Name != "" && !selectedAgentMatchesName(selected, state.Agent) {
			continue
		}
		if state.Status == "working" && state.TaskID != "" {
			return state.TaskChainID, firstNonEmpty(state.RootTaskID, state.TaskChainID), state.TaskID
		}
	}
	for _, task := range m.tasksItems {
		if taskArchived(task) || selected.Name != "" && !selectedAgentMatchesName(selected, task.AssignedAgent) {
			continue
		}
		if task.Status == "ready" || task.Status == "working" {
			state := stateByTask[task.TaskID]
			return state.TaskChainID, firstNonEmpty(state.RootTaskID, state.TaskChainID), task.TaskID
		}
	}
	return "", "", ""
}

func activeTaskRecords(tasks []taskRecord) []taskRecord {
	archived := map[string]bool{}
	for _, task := range tasks {
		if taskArchived(task) && task.TaskID != "" {
			archived[task.TaskID] = true
		}
	}
	changed := true
	for changed {
		changed = false
		for _, task := range tasks {
			if task.TaskID == "" || archived[task.TaskID] {
				continue
			}
			for _, dep := range task.DependsOn {
				if archived[dep] {
					archived[task.TaskID] = true
					changed = true
					break
				}
			}
		}
	}
	out := make([]taskRecord, 0, len(tasks))
	for _, task := range tasks {
		if task.TaskID == "" || !archived[task.TaskID] {
			out = append(out, task)
		}
	}
	return out
}

func taskArchived(task taskRecord) bool {
	return strings.EqualFold(strings.TrimSpace(task.Status), "archived")
}

func taskArchivedByID(tasks []taskRecord, taskID string) bool {
	for _, task := range tasks {
		if task.TaskID == taskID {
			return taskArchived(task)
		}
	}
	return false
}

func openTaskRecords(tasks []taskRecord, states map[string]taskWorkingState, approvals map[string][]taskApprovalRecord) []taskRecord {
	out := make([]taskRecord, 0, len(tasks))
	for _, task := range tasks {
		status := normalizedTaskStatus(task, states[task.TaskID])
		if taskArchived(task) || status == "validated" || status == "completed" || (taskCompleted(task) && !taskReviewHandoff(task, approvals[task.TaskID])) {
			continue
		}
		out = append(out, task)
	}
	return out
}

func taskFocusFromSelection(tasks []taskRecord, selected int) (string, string, string) {
	return "", "", ""
}

func taskReviewHandoff(task taskRecord, approvals []taskApprovalRecord) bool {
	if hasPendingApproval(approvals) {
		return true
	}
	for _, participant := range task.Participants {
		role := strings.ToLower(strings.TrimSpace(participant.Role))
		if strings.EqualFold(participant.Status, "active") && (role == "reviewer" || role == "verifier") {
			return true
		}
	}
	return false
}

func tasksForChain(tasks []taskRecord, chainID, rootID, currentTaskID string) []taskRecord {
	if chainID == "" && rootID == "" && currentTaskID == "" {
		return append([]taskRecord{}, tasks...)
	}
	ids := map[string]bool{}
	if rootID != "" {
		ids[rootID] = true
	}
	if currentTaskID != "" {
		ids[currentTaskID] = true
	}
	changed := true
	for changed {
		changed = false
		for _, task := range tasks {
			if ids[task.TaskID] {
				continue
			}
			for _, dep := range task.DependsOn {
				if ids[dep] {
					ids[task.TaskID] = true
					changed = true
					break
				}
			}
		}
	}
	var out []taskRecord
	for _, task := range tasks {
		if ids[task.TaskID] {
			out = append(out, task)
		}
	}
	return out
}

func mergeSelectedAgentTasks(items, all []taskRecord, selected agentRow) []taskRecord {
	if selected.Name == "" {
		return items
	}
	seen := map[string]bool{}
	out := make([]taskRecord, 0, len(items)+1)
	for _, task := range items {
		if selected.Scope == "remote" && task.AssignedAgent != "" && !selectedAgentMatchesName(selected, task.AssignedAgent) {
			continue
		}
		seen[task.TaskID] = true
		out = append(out, task)
	}
	for _, task := range all {
		if !seen[task.TaskID] && selectedAgentMatchesName(selected, task.AssignedAgent) {
			seen[task.TaskID] = true
			out = append(out, task)
		}
	}
	if current, ok := selectedAgentCurrentTaskRecord(selected); ok && !seen[current.TaskID] {
		out = append(out, current)
	}
	return out
}

func selectedAgentCurrentTaskRecord(selected agentRow) (taskRecord, bool) {
	if selected.CurrentTaskID == "" || selected.CurrentTask == "" {
		return taskRecord{}, false
	}
	return taskRecord{
		TaskID:        selected.CurrentTaskID,
		Title:         selected.CurrentTask,
		Status:        firstNonEmpty(selected.CurrentTaskStatus, "working"),
		NextStep:      selected.CurrentTaskNextStep,
		AssignedAgent: firstNonEmpty(selected.AgentName, selected.Name),
		Scope:         "remote_current_task",
	}, true
}

func selectedAgentMatchesName(selected agentRow, name string) bool {
	name = strings.TrimSpace(name)
	if name == "" {
		return false
	}
	for _, candidate := range []string{selected.Name, selected.AgentName, selected.TargetAddress} {
		candidate = strings.TrimSpace(candidate)
		if candidate != "" && candidate == name {
			return true
		}
	}
	return false
}

func bucketTasks(tasks []taskRecord, currentTaskID string, states map[string]taskWorkingState, approvals map[string][]taskApprovalRecord) []taskBucket {
	buckets := []taskBucket{{Name: "Current"}, {Name: "Next"}, {Name: "Queue"}, {Name: "Review"}, {Name: "Completed"}}
	for _, task := range sortedTasks(tasks) {
		idx := 2
		status := normalizedTaskStatus(task, states[task.TaskID])
		switch {
		case status == "working":
			idx = 0
		case status == "ready":
			idx = 1
		case hasPendingApproval(approvals[task.TaskID]) || status == "review" || status == "pending_review" || taskReviewHandoff(task, approvals[task.TaskID]):
			idx = 3
		case status == "completed" || status == "done" || status == "validated" || task.ResultStatus == "good":
			idx = 4
		default:
			idx = 2
		}
		buckets[idx].Tasks = append(buckets[idx].Tasks, task)
	}
	return buckets
}

func sortedTasks(tasks []taskRecord) []taskRecord {
	out := append([]taskRecord{}, tasks...)
	sort.SliceStable(out, func(i, j int) bool {
		if taskDependsOn(out[j], out[i].TaskID) {
			return true
		}
		if taskDependsOn(out[i], out[j].TaskID) {
			return false
		}
		if out[i].CreatedAt == out[j].CreatedAt {
			return out[i].TaskID < out[j].TaskID
		}
		return out[i].CreatedAt < out[j].CreatedAt
	})
	return out
}

func taskDependsOn(task taskRecord, depID string) bool {
	for _, dep := range task.DependsOn {
		if dep == depID {
			return true
		}
	}
	return false
}

func normalizedTaskStatus(task taskRecord, state taskWorkingState) string {
	status := strings.ToLower(strings.TrimSpace(firstNonEmpty(state.Status, task.Status, task.ResultStatus)))
	return strings.ReplaceAll(status, " ", "_")
}

func hasPendingApproval(approvals []taskApprovalRecord) bool {
	for _, approval := range approvals {
		if strings.EqualFold(approval.Status, "pending") {
			return true
		}
	}
	return false
}

func orderedTaskRows(buckets []taskBucket) []taskRecord {
	var rows []taskRecord
	for _, bucket := range buckets {
		rows = append(rows, bucket.Tasks...)
	}
	return rows
}

func (m *model) clampTaskSelection() {
	data := m.taskData()
	count := len(data.Chains)
	if m.tasksChainFocused {
		count = len(orderedTaskRows(data.Buckets))
		m.tasksChainSelected = min(max(0, m.tasksChainSelected), max(0, len(data.Chains)-1))
	}
	if count == 0 {
		m.tasksSelected = 0
		m.tasksOffset = 0
		return
	}
	m.tasksSelected = min(max(0, m.tasksSelected), count-1)
	m.tasksOffset = min(max(0, m.tasksOffset), max(0, count-1))
}

func (m *model) moveTaskSelection(delta int) {
	data := m.taskData()
	count := len(data.Chains)
	if m.tasksChainFocused {
		count = len(orderedTaskRows(data.Buckets))
	}
	if count == 0 {
		m.tasksSelected = 0
		m.tasksOffset = 0
		return
	}
	m.tasksSelected = min(max(0, m.tasksSelected+delta), count-1)
	m.tasksOffset = m.tasksSelected
}

func countTaskBuckets(buckets []taskBucket) taskCounts {
	counts := taskCounts{}
	for _, bucket := range buckets {
		counts.Total += len(bucket.Tasks)
		switch bucket.Name {
		case "Current":
			counts.Working += len(bucket.Tasks)
		case "Next":
			counts.Ready += len(bucket.Tasks)
		case "Queue":
			for _, task := range bucket.Tasks {
				if strings.TrimSpace(task.BlockedReason) != "" || strings.EqualFold(task.Status, "blocked") {
					counts.Blocked++
				} else {
					counts.Queued++
				}
			}
		case "Review":
			counts.Review += len(bucket.Tasks)
		case "Completed":
			counts.Completed += len(bucket.Tasks)
		}
	}
	return counts
}

func collectTaskBlockers(tasks []taskRecord, states map[string]taskWorkingState) []string {
	seen := map[string]bool{}
	var blockers []string
	for _, task := range tasks {
		for _, blocker := range append([]string{task.BlockedReason}, states[task.TaskID].Blockers...) {
			blocker = strings.TrimSpace(blocker)
			if blocker != "" && !seen[blocker] {
				seen[blocker] = true
				blockers = append(blockers, blocker)
			}
		}
	}
	return blockers
}

func approvalsForChain(approvals []taskApprovalRecord, chainID, rootID string) []taskApprovalRecord {
	if chainID == "" && rootID == "" {
		return append([]taskApprovalRecord{}, approvals...)
	}
	var out []taskApprovalRecord
	for _, approval := range approvals {
		if (chainID != "" && approval.TaskChainID == chainID) || (rootID != "" && approval.RootTaskID == rootID) {
			out = append(out, approval)
		}
	}
	return out
}

func approvalsForTasks(approvals []taskApprovalRecord, tasks []taskRecord) []taskApprovalRecord {
	visible := map[string]bool{}
	for _, task := range tasks {
		if task.TaskID != "" && task.Scope != "remote_current_task" {
			visible[task.TaskID] = true
		}
	}
	var out []taskApprovalRecord
	for _, approval := range approvals {
		if visible[approval.TaskID] {
			out = append(out, approval)
		}
	}
	return out
}

func chainSummaries(tasks []taskRecord, states []taskWorkingState, approvals []taskApprovalRecord) []taskChainSummary {
	stateByTask := latestStateByTask(states)
	approvalByTask := approvalsByTask(approvals)
	chainByTask := map[string]string{}
	rootByTask := map[string]string{}
	for _, task := range tasks {
		chainID, rootID := taskChainIDs(task, stateByTask[task.TaskID])
		chainByTask[task.TaskID] = chainID
		rootByTask[task.TaskID] = rootID
	}
	changed := true
	for changed {
		changed = false
		for _, task := range tasks {
			for _, dep := range task.DependsOn {
				if depChain := chainByTask[dep]; depChain != "" && chainByTask[task.TaskID] != depChain {
					oldChain := chainByTask[task.TaskID]
					chainByTask[task.TaskID] = depChain
					rootByTask[task.TaskID] = firstNonEmpty(rootByTask[dep], dep)
					if oldChain != "" {
						for tid, c := range chainByTask {
							if c == oldChain {
								chainByTask[tid] = depChain
							}
						}
					}
					changed = true
				}
			}
		}
	}
	groups := map[string][]taskRecord{}
	roots := map[string]string{}
	for _, task := range tasks {
		chainID := firstNonEmpty(chainByTask[task.TaskID], task.TaskID)
		groups[chainID] = append(groups[chainID], task)
		if roots[chainID] == "" {
			roots[chainID] = rootByTask[task.TaskID]
		}
	}
	out := make([]taskChainSummary, 0, len(groups))
	for chainID, group := range groups {
		group = sortedTasks(group)
		rootID := firstNonEmpty(roots[chainID], chainID)
		buckets := bucketTasks(group, "", stateByTask, approvalByTask)
		participants, agents := participantsForChain(group, states)
		current, next := currentNextTasks(buckets)
		latestActivity, latestUpdate := latestChainActivity(group, states)
		out = append(out, taskChainSummary{
			ChainID:        chainID,
			RootTaskID:     rootID,
			RootTitle:      chainRootTitle(group, rootID),
			Tasks:          group,
			Buckets:        buckets,
			Counts:         countTaskBuckets(buckets),
			Participants:   participants,
			Agents:         agents,
			CurrentTask:    current,
			NextTask:       next,
			LatestActivity: latestActivity,
			LatestUpdate:   latestUpdate,
			Blockers:       collectTaskBlockers(group, stateByTask),
			Approvals:      approvalsForTasks(approvals, group),
		})
	}
	sort.SliceStable(out, func(i, j int) bool {
		if out[i].LatestUpdate == out[j].LatestUpdate {
			return out[i].ChainID < out[j].ChainID
		}
		return out[i].LatestUpdate > out[j].LatestUpdate
	})
	return out
}

func taskChainIDs(task taskRecord, state taskWorkingState) (string, string) {
	rootID := firstNonEmpty(state.RootTaskID)
	chainID := firstNonEmpty(state.TaskChainID)
	for _, participant := range task.Participants {
		rootID = firstNonEmpty(rootID, participant.RootTaskID)
		chainID = firstNonEmpty(chainID, participant.TaskChainID)
	}
	rootID = firstNonEmpty(rootID, firstDep(task), task.TaskID)
	chainID = firstNonEmpty(chainID, rootID, task.TaskID)
	return chainID, rootID
}

func firstDep(task taskRecord) string {
	if len(task.DependsOn) == 0 {
		return ""
	}
	return task.DependsOn[0]
}

func chainRootTitle(tasks []taskRecord, rootID string) string {
	for _, task := range tasks {
		if task.TaskID == rootID {
			return firstNonEmpty(task.Title, task.TaskID)
		}
	}
	if len(tasks) > 0 {
		return firstNonEmpty(tasks[0].Title, tasks[0].TaskID)
	}
	return "Untitled chain"
}

func participantsForChain(tasks []taskRecord, states []taskWorkingState) ([]taskParticipant, []string) {
	seen := map[string]bool{}
	var participants []taskParticipant
	var agents []string
	add := func(agent, role, status string) {
		agent = strings.TrimSpace(agent)
		if agent == "" || seen[agent] {
			return
		}
		seen[agent] = true
		agents = append(agents, agent)
		participants = append(participants, taskParticipant{Agent: agent, Role: firstNonEmpty(role, "participant"), Status: firstNonEmpty(status, "active")})
	}
	for _, task := range tasks {
		add(task.AssignedAgent, "assignee", "active")
		for _, p := range task.Participants {
			add(p.Agent, p.Role, p.Status)
		}
	}
	ids := map[string]bool{}
	for _, task := range tasks {
		ids[task.TaskID] = true
	}
	for _, state := range states {
		if ids[state.TaskID] {
			add(state.Agent, "state", state.Status)
		}
	}
	sort.Strings(agents)
	return participants, agents
}

func currentNextTasks(buckets []taskBucket) (taskRecord, taskRecord) {
	var current, next taskRecord
	for _, bucket := range buckets {
		if bucket.Name == "Current" && len(bucket.Tasks) > 0 {
			current = bucket.Tasks[0]
		}
		if bucket.Name == "Next" && len(bucket.Tasks) > 0 {
			next = bucket.Tasks[0]
		}
	}
	if next.TaskID == "" {
		for _, bucket := range buckets {
			if bucket.Name == "Queue" && len(bucket.Tasks) > 0 {
				next = bucket.Tasks[0]
			}
		}
	}
	return current, next
}

func latestChainActivity(tasks []taskRecord, states []taskWorkingState) (string, string) {
	ids := map[string]bool{}
	latest := ""
	activity := ""
	for _, task := range tasks {
		ids[task.TaskID] = true
		if task.UpdatedAt >= latest {
			latest = task.UpdatedAt
			activity = firstNonEmpty(task.NextStep, task.ResultSummary, task.Title)
		}
	}
	for _, state := range states {
		if ids[state.TaskID] && state.UpdatedAt >= latest {
			latest = state.UpdatedAt
			activity = firstNonEmpty(state.CurrentActivity, state.NextStep, state.Status)
		}
	}
	return activity, latest
}

func filterTasksByAgent(tasks []taskRecord, states []taskWorkingState, selected agentRow, query string) []taskRecord {
	if query == "" {
		return tasks
	}
	var out []taskRecord
	for _, task := range tasks {
		if taskMatchesAgentFilter(task, states, selected, query) {
			out = append(out, task)
		}
	}
	return out
}

func filterChainsByAgent(chains []taskChainSummary, selected agentRow, query string) []taskChainSummary {
	if query == "" {
		return chains
	}
	var out []taskChainSummary
	for _, chain := range chains {
		matched := false
		for _, agent := range chain.Agents {
			if agentMatchesFilter(agent, selected, query) {
				matched = true
				break
			}
		}
		if matched {
			out = append(out, chain)
		}
	}
	return out
}

func taskMatchesAgentFilter(task taskRecord, states []taskWorkingState, selected agentRow, query string) bool {
	if agentMatchesFilter(task.AssignedAgent, selected, query) {
		return true
	}
	for _, p := range task.Participants {
		if agentMatchesFilter(p.Agent, selected, query) {
			return true
		}
	}
	for _, state := range states {
		if state.TaskID == task.TaskID && agentMatchesFilter(state.Agent, selected, query) {
			return true
		}
	}
	return false
}

func agentMatchesFilter(agent string, selected agentRow, query string) bool {
	agent = strings.TrimSpace(agent)
	query = strings.ToLower(strings.TrimSpace(query))
	if agent == "" || query == "" {
		return false
	}
	lowerAgent := strings.ToLower(agent)
	if strings.Contains(lowerAgent, query) || strings.Contains(query, lowerAgent) {
		return true
	}
	if strings.Contains(query, "/") {
		parts := strings.Split(query, "/")
		if parts[len(parts)-1] == lowerAgent {
			return true
		}
	}
	if selected.Name != "" && (strings.EqualFold(selected.Name, query) || strings.EqualFold(selected.AgentName, query) || strings.EqualFold(selected.TargetAddress, query)) && selectedAgentMatchesName(selected, agent) {
		return true
	}
	return false
}

func taskFilterAgents(tasks []taskRecord, states []taskWorkingState) []string {
	seen := map[string]bool{}
	var agents []string
	add := func(agent string) {
		agent = strings.TrimSpace(agent)
		if agent != "" && !seen[agent] {
			seen[agent] = true
			agents = append(agents, agent)
		}
	}
	for _, task := range tasks {
		add(task.AssignedAgent)
		for _, p := range task.Participants {
			add(p.Agent)
		}
	}
	for _, state := range states {
		add(state.Agent)
	}
	sort.Strings(agents)
	return agents
}

func aggregateChainCounts(chains []taskChainSummary) taskCounts {
	var counts taskCounts
	for _, chain := range chains {
		counts.Total += chain.Counts.Total
		counts.Working += chain.Counts.Working
		counts.Ready += chain.Counts.Ready
		counts.Queued += chain.Counts.Queued
		counts.Blocked += chain.Counts.Blocked
		counts.Review += chain.Counts.Review
		counts.Completed += chain.Counts.Completed
	}
	return counts
}

func (m *model) cycleTaskAgentFilter() {
	agents := taskFilterAgents(activeTaskRecords(m.tasksItems), m.tasksStates)
	if len(agents) == 0 {
		m.tasksAgentFilter = nil
		m.tasksAgentChip = 0
		return
	}
	m.tasksAgentChip = (m.tasksAgentChip + 1) % (len(agents) + 1)
	if m.tasksAgentChip == 0 {
		m.tasksAgentFilter = nil
	} else {
		m.tasksAgentFilter = []rune(agents[m.tasksAgentChip-1])
	}
	m.tasksSelected = 0
	m.tasksOffset = 0
	m.tasksChainSelected = 0
	m.tasksChainFocused = false
}
