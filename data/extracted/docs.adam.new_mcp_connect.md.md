<!-- source: https://docs.adam.new/mcp/connect.md fetched 2026-09-07T18:00:35+00:00 -->

> ## Documentation Index
> Fetch the complete documentation index at: https://docs.adam.new/llms.txt
> Use this file to discover all available pages before exploring further.

# Connect an MCP client

> Add the hosted Adam MCP server to Claude, Claude Code, Codex, Cursor, or another compatible client.

Connect your MCP client to the hosted Adam server:

```text theme={null}
https://adam.new/mcp
```

The recommended setup uses OAuth. You do not need to create an API key or
register an OAuth application first.

<Tabs>
  <Tab title="Claude">
    <Steps>
      <Step title="Add a custom connector">
        Open **Settings** → **Connectors**, choose **Add custom connector**, and
        enter `https://adam.new/mcp` as the server URL.
      </Step>

      <Step title="Leave the client ID empty">
        Adam supports automatic client registration. Do not add a manual OAuth
        Client ID.
      </Step>

      <Step title="Connect Adam">
        Start the connection, sign in to Adam, choose the workspace Claude may
        access, and approve the permissions.
      </Step>
    </Steps>
  </Tab>

  <Tab title="Claude Code">
    Add Adam for your user account:

    ```bash theme={null}
    claude mcp add --transport http --scope user adam https://adam.new/mcp
    ```

    Start Claude Code, enter `/mcp`, select **adam**, and authenticate. Sign in
    to Adam, choose a workspace, and approve the permissions.
  </Tab>

  <Tab title="Codex">
    Add the remote server, then start the OAuth connection:

    ```bash theme={null}
    codex mcp add adam --url https://adam.new/mcp
    codex mcp login adam
    ```

    Sign in to Adam, choose a workspace, and approve the permissions. The
    server is then available to Codex as `adam`.
  </Tab>

  <Tab title="Cursor">
    Add Adam to your Cursor MCP configuration:

    ```json theme={null}
    {
      "mcpServers": {
        "adam": {
          "url": "https://adam.new/mcp"
        }
      }
    }
    ```

    Enable the server in Cursor, then complete the Adam sign-in, workspace
    selection, and permission screens.
  </Tab>

  <Tab title="Other clients">
    Use these connection settings:

    | Setting             | Value                            |
    | ------------------- | -------------------------------- |
    | Transport           | Streamable HTTP                  |
    | Server URL          | `https://adam.new/mcp`           |
    | Authentication      | OAuth 2.1 with PKCE              |
    | Client registration | Automatic registration supported |

    Adam publishes the discovery metadata the client needs. If your client
    cannot use OAuth but supports custom HTTP headers, you can instead send a
    [personal API key](/api-reference/introduction) as
    `Authorization: Bearer <key>`.
  </Tab>
</Tabs>

## What you approve

During OAuth, Adam asks you to choose one workspace. The permission screen may
include access to models, projects, tasks, and files. Tools that are not covered
by the granted permissions do not appear to the client.

To remove access, open **Settings → Developer → Connected apps** in Adam and
revoke the client, or disconnect Adam in the client itself. Connect it again to
choose a different workspace or set of permissions.

<Warning>
  Only connect clients you trust. An approved client can read or change data in
  the selected workspace within the permissions shown by Adam.
</Warning>

## Troubleshooting

<AccordionGroup>
  <Accordion title="The client asks for an OAuth Client ID">
    Adam supports automatic client registration, so a Client ID is not needed.
    Remove the existing Adam connector, add it again with the exact server URL
    `https://adam.new/mcp`, and retry the connection. Also update the MCP client
    if it is not using a current version.
  </Accordion>

  <Accordion title="The wrong workspace is connected">
    Disconnect Adam in the MCP client and reconnect it. Choose the intended
    workspace on the Adam authorization screen.
  </Accordion>

  <Accordion title="A tool is missing">
    Adam only advertises tools allowed by the connection's granted permissions.
    Reconnect and approve the required project, task, model, or file access.
  </Accordion>

  <Accordion title="A task has not finished">
    Task creation and messages start asynchronous work. Ask the client to call
    `adam_get_task` until the task completes, fails, is cancelled, or requests
    input.
  </Accordion>

  <Accordion title="The client receives a rate-limit error">
    Adam currently allows 120 requests per minute per credential. Wait for the
    retry interval before continuing, and avoid polling a task continuously.
  </Accordion>
</AccordionGroup>

If the connection still fails, check [Adam status](https://status.adam.new) or
contact [hello@adam.new](mailto:hello@adam.new).

