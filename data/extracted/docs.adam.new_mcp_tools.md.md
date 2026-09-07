<!-- source: https://docs.adam.new/mcp/tools.md fetched 2026-09-07T18:00:36+00:00 -->

> ## Documentation Index
> Fetch the complete documentation index at: https://docs.adam.new/llms.txt
> Use this file to discover all available pages before exploring further.

# MCP tools and workflows

> Use Adam MCP tools to manage projects, run asynchronous tasks, answer interactions, and retrieve files.

Adam exposes focused tools for projects, tasks, conversations, and files. Your
client only sees tools allowed by the permissions granted during authentication.

## Context and models

| Tool               | What it does                                                                        | Main input | Permission       |
| ------------------ | ----------------------------------------------------------------------------------- | ---------- | ---------------- |
| `adam_get_context` | Returns the connected user, workspace, credential type, and granted permissions.    | None       | Always available |
| `adam_list_models` | Lists model slugs available to the workspace. Use it before selecting a task model. | None       | Always available |

## Billing and integrations

| Tool                       | What it does                                                                                                              | Main input        | Permission          |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------- | ----------------- | ------------------- |
| `adam_get_billing_summary` | Returns the workspace subscription, effective plan, and high-level usage summary without payment details or internal IDs. | None              | `billing:read`      |
| `adam_list_integrations`   | Lists available integrations and shows which are connected for the authenticated user.                                    | None              | `integrations:read` |
| `adam_get_integration`     | Gets one integration and its connection status by stable slug.                                                            | `integrationSlug` | `integrations:read` |

## Automations

| Tool                     | What it does                                                                | Main input                                                       | Permission          |
| ------------------------ | --------------------------------------------------------------------------- | ---------------------------------------------------------------- | ------------------- |
| `adam_list_automations`  | Lists automations visible to the authenticated user.                        | None                                                             | `automations:read`  |
| `adam_get_automation`    | Gets one automation, its trigger, schedule, status, and latest run summary. | `automationId`                                                   | `automations:read`  |
| `adam_create_automation` | Creates a recurring, one-time, or webhook-triggered automation.             | Name, prompt, trigger-specific fields; optional `idempotencyKey` | `automations:write` |
| `adam_update_automation` | Edits an automation's name, prompt, effort, schedule, or visibility.        | `automationId` and at least one field to change                  | `automations:write` |
| `adam_pause_automation`  | Pauses future fires without deleting the automation.                        | `automationId`                                                   | `automations:write` |
| `adam_resume_automation` | Resumes and re-arms a paused automation.                                    | `automationId`                                                   | `automations:write` |
| `adam_delete_automation` | Permanently deletes an automation after confirmation.                       | `automationId` and `confirm: true`                               | `automations:write` |

## Projects

| Tool                  | What it does                                                                | Main input                                                                  | Permission       |
| --------------------- | --------------------------------------------------------------------------- | --------------------------------------------------------------------------- | ---------------- |
| `adam_list_projects`  | Lists projects in cursor-paginated pages.                                   | Optional `limit` and `cursor`                                               | `projects:read`  |
| `adam_get_project`    | Gets one project.                                                           | `projectId`                                                                 | `projects:read`  |
| `adam_create_project` | Creates a project.                                                          | `name`; optional description, visibility, memory scope, and idempotency key | `projects:write` |
| `adam_update_project` | Changes a project's name, description, visibility, or memory scope.         | `projectId` and at least one field to change                                | `projects:write` |
| `adam_delete_project` | Permanently deletes an owned project. Its tasks and files become ungrouped. | `projectId` and `confirm: true` after user confirmation                     | `projects:write` |

## Tasks and conversations

| Tool                      | What it does                                                                             | Main input                                                                                  | Permission                                    |
| ------------------------- | ---------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- | --------------------------------------------- |
| `adam_list_tasks`         | Lists cursor-paginated tasks, optionally filtered by project or status.                  | Optional `projectId`, `status`, `limit`, and `cursor`                                       | `tasks:read`                                  |
| `adam_get_task`           | Gets a task, its messages, status, and pending interaction.                              | `taskId`                                                                                    | `tasks:read`                                  |
| `adam_list_task_messages` | Lists structured message parts, including file references, tool calls, and tool results. | `taskId`; optional `limit` and `cursor`                                                     | `tasks:read`                                  |
| `adam_create_task`        | Creates and asynchronously starts a task.                                                | `prompt`, `model`, and `idempotencyKey`; optional title, project, and attachment upload IDs | `tasks:write`; `files:write` with attachments |
| `adam_update_task`        | Changes a task title or moves it into or out of a project.                               | `taskId` and a title or `projectId`                                                         | `tasks:write`                                 |
| `adam_send_message`       | Adds a user message and asynchronously continues a task.                                 | `taskId`, `content`, and `idempotencyKey`; optional model and attachment upload IDs         | `tasks:write`; `files:write` with attachments |
| `adam_answer_interaction` | Answers a pending question or approval.                                                  | `taskId`, `interactionId`, `response`, and `idempotencyKey`                                 | `tasks:write`                                 |
| `adam_cancel_task`        | Cancels queued, running, or awaiting-input work. Repeated calls are safe.                | `taskId`                                                                                    | `tasks:write`                                 |
| `adam_delete_task`        | Permanently deletes an owned task and its message history.                               | `taskId` and `confirm: true` after user confirmation                                        | `tasks:write`                                 |

The `model` object contains the model `slug`, the `fast` option, and an optional
`effort` of `low`, `medium`, `high`, or `xhigh`. Call
`adam_list_models` instead of guessing a model slug.

## Files

| Tool                       | What it does                                                                                                          | Main input                                                         | Permission                                                     |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ | -------------------------------------------------------------- |
| `adam_list_files`          | Lists a cursor-paginated page of files for exactly one task or project.                                               | Either `taskId` or `projectId`; optional `limit` and `cursor`      | `files:read` plus the matching task or project read permission |
| `adam_get_file`            | Gets file metadata and a short-lived resource link for the current contents.                                          | `fileId`                                                           | `files:read`                                                   |
| `adam_upload_attachment`   | Uploads base64-encoded content and returns an upload ID for a task or message.                                        | `name`, `mediaType`, and `contentBase64`                           | `files:write`                                                  |
| `adam_upload_project_file` | Uploads base64-encoded content as a durable shared project file.                                                      | `projectId`, `name`, `mediaType`, and `contentBase64`              | `files:write` and `projects:write`                             |
| `adam_delete_project_file` | Removes a durable shared file from a project after confirmation. Task files and message attachments remain immutable. | `projectId`, `fileId`, and `confirm: true` after user confirmation | `files:write` and `projects:write`                             |

Use `adam_upload_attachment`, then pass the returned upload ID in the
`attachments` array of `adam_create_task` or `adam_send_message`. Upload IDs
expire after 24 hours. For durable shared inputs, use
`adam_upload_project_file` instead. Use `adam_delete_project_file` only after
the user confirms which shared project file to remove.

## Run a task reliably

<Steps>
  <Step title="Inspect the connection">
    Use `adam_get_context` when the client needs to confirm the connected user,
    workspace, or permissions.
  </Step>

  <Step title="Resolve IDs instead of guessing">
    Use the list tools to find project IDs and model slugs. Adam validates project and
    task IDs as UUIDs.
  </Step>

  <Step title="Create the task once">
    Pass a new UUID as `idempotencyKey` to `adam_create_task`. Reuse that key
    only when retrying the exact same operation.
  </Step>

  <Step title="Poll at a sensible interval">
    Call `adam_get_task` until the status is `completed`, `failed`, `cancelled`,
    or `awaiting_input`. Do not start a duplicate task while the first one runs.
  </Step>

  <Step title="Handle input or collect files">
    Answer a pending interaction with `adam_answer_interaction`. After
    completion, use `adam_list_files` to find the resulting files and
    `adam_get_file` to retrieve a resource link.
  </Step>
</Steps>

## Example requests

<Prompt description="Continue a task that needs input" actions={["copy"]}>
  Find my Adam tasks that are waiting for input. Show me each question or approval
  request before answering anything. After I respond, continue the selected task
  and monitor it until it reaches another decision point or finishes.
</Prompt>

<Prompt description="Review completed project work" actions={["copy"]}>
  List the tasks in my Gearbox redesign project. Summarize the completed tasks,
  identify any failed or cancelled work, and list the output files with their
  download links.
</Prompt>

<Prompt description="Update and continue existing work" actions={["copy"]}>
  Find the Adam task about the enclosure drawing, rename it to Enclosure drawing
  review, move it into the Enclosure project, and send a follow-up asking Adam to
  check the design for injection-molding risks. Monitor it until it finishes or
  needs my input.
</Prompt>

