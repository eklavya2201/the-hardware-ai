<!-- source: https://docs.adam.new/mcp/overview.md fetched 2026-09-07T18:00:34+00:00 -->

> ## Documentation Index
> Fetch the complete documentation index at: https://docs.adam.new/llms.txt
> Use this file to discover all available pages before exploring further.

# Adam MCP

> Connect an AI client to Adam and use tools for engineering projects, tasks, automations, messages, and files.

Adam MCP lets Claude, Codex, Cursor, and other MCP clients work in your Adam
workspace. Ask from the client you already use, and it can organize projects,
start engineering tasks, continue conversations, and find the resulting files.

```text theme={null}
https://adam.new/mcp
```

<Warning>
  The Adam MCP server is in beta. Tools, schemas, behavior, and availability may
  change at any time without prior notice, including breaking changes. Avoid
  relying on it for production-critical workflows.
</Warning>

<Columns cols={2}>
  <Card title="Connect a client" icon="plug" href="/mcp/connect">
    Add the hosted server, sign in with Adam, and choose a workspace.
  </Card>

  <Card title="Explore the tools" icon="wrench" href="/mcp/tools">
    See every available tool, its inputs, and the workflows they support.
  </Card>
</Columns>

## What you can do

* **Organize engineering work.** List, create, inspect, update, and—with your
  confirmation—delete Adam projects and tasks.
* **Run Adam tasks.** Start a task with an available model, inspect its progress,
  continue it with a message, or cancel it.
* **Handle questions and approvals.** Answer an interaction when a task needs
  input, then let Adam continue the same task.
* **Use files.** Upload task attachments or shared project files, list generated
  files, retrieve short-lived resource links for their contents, and—with your
  confirmation—remove shared project files.
* **Inspect the workspace.** With permission, read the current subscription and
  usage summary or see which integrations are available and connected.
* **Manage automations.** Create and edit scheduled or webhook-triggered work,
  pause and resume it, or permanently delete it after confirmation.

## How task work runs

Adam tasks are asynchronous. An MCP call starts the work and returns promptly;
the AI client checks the task until it reaches a final state.

<Steps>
  <Step title="Choose a model">
    The client calls `adam_list_models` to use a model available to your
    workspace.
  </Step>

  <Step title="Start the task">
    The client calls `adam_create_task` and receives the new task ID and status.
  </Step>

  <Step title="Follow progress">
    The client calls `adam_get_task` until the task is `completed`, `failed`,
    `cancelled`, or `awaiting_input`.
  </Step>

  <Step title="Respond when needed">
    If the task is `awaiting_input`, the client reads the pending question or
    approval and uses `adam_answer_interaction` to continue.
  </Step>

  <Step title="Collect the result">
    The client reads the final response, calls `adam_list_files`, and uses
    `adam_get_file` when it needs the file contents.
  </Step>
</Steps>

## Try it after connecting

<Prompt description="Create and start an engineering task" actions={["copy"]}>
  List my Adam projects. Create a project called Gearbox redesign if it does not
  exist, list the available models, then start a task in that project to identify
  the main design risks for a compact two-stage gearbox. Keep me updated until the
  task finishes or needs my input.
</Prompt>

## Access and permissions

OAuth is the recommended way to connect. Adam asks you to sign in, choose one
workspace, and approve the requested permissions. The connection acts as you
and can only use the tools allowed by those permissions. Removing you from the
workspace or revoking the connection ends that access.

<Note>
  File-aware clients can call `adam_upload_attachment` and pass the returned
  upload ID to a task or message. The tool accepts base64 content, so your MCP
  client must make the file bytes available to the model.
</Note>

