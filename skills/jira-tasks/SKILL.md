---
name: jira-tasks
description: Lists the user's To Do and In Progress Jira tasks or prepares a selected Jira issue for implementation
type: prompt
whenToUse: When the user says "Дай задачи", asks for their Jira tasks, or selects a Jira issue to work on
---

Delegate the request to the `jira-reader` sub-agent.

For "Дай задачи" or an equivalent list request, ask the sub-agent to return all tasks assigned to the current user in the To Do and In Progress status category. Relay its complete list to the user without truncating it.

When the user selects a task for implementation, ask `jira-reader` for the full issue context. Then act as coordinator and delegate implementation to the `jira-implementer` sub-agent. Pass it the complete Jira context, the issue key, relevant project paths, and explicit verification requirements. Do not pass credentials to any sub-agent. Relay the implementer's summary, verification results, and any blockers to the user.
