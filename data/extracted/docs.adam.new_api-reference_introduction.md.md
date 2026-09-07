<!-- source: https://docs.adam.new/api-reference/introduction.md fetched 2026-09-07T18:00:37+00:00 -->

> ## Documentation Index
> Fetch the complete documentation index at: https://docs.adam.new/llms.txt
> Use this file to discover all available pages before exploring further.

# API overview

> Build on Adam with personal API keys and the versioned REST API.

export const ApiRateLimits = () => {
  const policies = [{
    key: "global",
    label: "Global"
  }, {
    key: "metadata_read",
    label: "Metadata reads"
  }, {
    key: "resource_read",
    label: "Resource reads"
  }, {
    key: "resource_write",
    label: "Resource writes"
  }, {
    key: "task_execution",
    label: "Task execution"
  }, {
    key: "file_upload",
    label: "File uploads"
  }];
  const [requestVersion, setRequestVersion] = useState(0);
  const [request, setRequest] = useState({
    status: "loading",
    data: null
  });
  const bucketFor = (limits, plan, policy) => policy === "global" ? limits[plan].global : limits[plan].policies[policy];
  const validateBucket = bucket => bucket !== null && typeof bucket === "object" && Number.isInteger(bucket.capacity) && bucket.capacity > 0 && Number.isInteger(bucket.windowSeconds) && bucket.windowSeconds > 0;
  const normalizeResponse = value => {
    if (value === null || typeof value !== "object") {
      throw new Error("The rate-limit response is not valid.");
    }
    for (const plan of ["free", "paid"]) {
      if (value[plan] === null || typeof value[plan] !== "object") {
        throw new Error("A rate-limit plan is missing.");
      }
      for (const policy of policies) {
        if (!validateBucket(bucketFor(value, plan, policy.key))) {
          throw new Error("A rate-limit bucket is not valid.");
        }
      }
    }
    return value;
  };
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setRequest({
      status: "loading",
      data: null
    });
    const apiOrigin = window.location.hostname === "docs.adam.new" ? "https://api.adam.new" : window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1" ? "http://localhost:3001" : "https://api.staging.adam.new";
    const rateLimitsUrl = `${apiOrigin}/developer-api/rate-limits`;
    fetch(rateLimitsUrl, {
      headers: {
        Accept: "application/json"
      },
      signal: controller.signal
    }).then(response => {
      if (!response.ok) {
        throw new Error(`The server returned ${response.status}.`);
      }
      return response.json();
    }).then(normalizeResponse).then(data => {
      if (active) setRequest({
        status: "success",
        data
      });
    }).catch(error => {
      if (active && error.name !== "AbortError") {
        setRequest({
          status: "error",
          data: null
        });
      }
    });
    return () => {
      active = false;
      controller.abort();
    };
  }, [requestVersion]);
  const retry = () => setRequestVersion(version => version + 1);
  const formatWindow = seconds => {
    if (seconds === 60) return "minute";
    if (seconds % 3600 === 0) {
      const hours = seconds / 3600;
      return `${hours} ${hours === 1 ? "hour" : "hours"}`;
    }
    if (seconds % 60 === 0) {
      const minutes = seconds / 60;
      return `${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
    }
    return `${seconds} ${seconds === 1 ? "second" : "seconds"}`;
  };
  const formatBucket = bucket => `${bucket.capacity.toLocaleString("en-US")}/${formatWindow(bucket.windowSeconds)}`;
  if (request.status === "loading") {
    return <p aria-live="polite">Loading current rate limits…</p>;
  }
  if (request.status === "error" || request.data === null) {
    return <div role="alert">
      <p>
        Current rate limits are temporarily unavailable. Response headers remain
        authoritative.
      </p>
      <button type="button" onClick={retry}>Try again</button>
    </div>;
  }
  return <table>
      <thead>
        <tr>
          <th>Policy</th>
          <th>Free workspace</th>
          <th>Paid workspace</th>
        </tr>
      </thead>
      <tbody>
        {policies.map(policy => <tr key={policy.key}>
            <td>{policy.label}</td>
            <td>{formatBucket(bucketFor(request.data, "free", policy.key))}</td>
            <td>{formatBucket(bucketFor(request.data, "paid", policy.key))}</td>
          </tr>)}
      </tbody>
    </table>;
};

The Adam API lets you create projects, run AI engineering tasks, manage
automations, upload input files, and download the files Adam produces. The
production base URL is:

```text theme={null}
https://api.adam.new/v1
```

<Warning>
  The Adam API is in beta. Endpoints, schemas, behavior, and availability may
  change at any time without prior notice, including breaking changes. Avoid
  relying on it for production-critical workflows.
</Warning>

<Note>
  Connecting Claude, Codex, Cursor, or another AI client? Use the hosted [Adam
  MCP server](/mcp/overview) instead. It handles OAuth and exposes Adam as
  purpose-built tools.
</Note>

## Authentication

Create a personal API key for the workspace you want to use. Adam shows the
secret once, so store it in a password manager or secret store. Send it as a
Bearer token on every request:

```bash theme={null}
curl https://api.adam.new/v1/projects \
  -H "Authorization: Bearer $ADAM_API_KEY"
```

Personal keys act as you and are restricted to the workspace and scopes chosen
when the key is created. Removing you from the workspace, revoking the key, or
deleting your account immediately invalidates it.

You can inspect the user, workspace, scopes, and credential type associated
with a key:

```bash theme={null}
curl https://api.adam.new/v1/auth/context \
  -H "Authorization: Bearer $ADAM_API_KEY"
```

<Warning>
  Never put an Adam API key in browser code, a mobile app, source control, or a
  client-side environment variable.
</Warning>

## Billing and integrations

Keys with `billing:read` can retrieve a deliberately reduced subscription and
usage snapshot from `GET /v1/billing/summary`. The response includes the
effective plan, subscription state, renewal information, remaining usage
percentages, and top-up balance. It does not expose payment methods, Stripe
identifiers, raw credit accounting, or internal catalog IDs.

Keys with `integrations:read` can list the integrations available to the
selected workspace with `GET /v1/integrations`, or inspect one by stable slug
with `GET /v1/integrations/{integrationSlug}`. Connection state reflects
workspace-wide connections and the authenticated user's own user-scoped
connections. Provider credentials, account metadata, and internal connection
identifiers are never returned.

## Automations

Use `automations:read` to list and inspect scheduled or webhook-triggered
automations. `automations:write` permits creation, editing, pausing, resuming,
and permanent deletion. Automation writes still enforce the same ownership,
workspace-role, subscription, trigger, and scheduling checks as the Adam app.

Recurring schedules require a cron expression; one-time schedules require an
ISO `runAt` timestamp. Webhook automations require a connected integration and
a provider trigger event. Responses use `effort` and omit internal model-tier
and provider-routing identifiers.

## Idempotent writes

Adam accepts an optional `Idempotency-Key` when you create a project or
automation and
requires it when you create a task, send a message, or answer a task
interaction. Use a new UUID for each logical operation and reuse it when
retrying that operation. Adam replays the original response when both the key
and request body match, and returns a conflict if the same key is reused for
different input. Results are retained for 24 hours; using the same key after
that window can execute a new request. Multipart uploads and task cancellation
do not use an idempotency key.

```bash theme={null}
curl https://api.adam.new/v1/projects \
  -X POST \
  -H "Authorization: Bearer $ADAM_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(uuidgen)" \
  -d '{"name":"Gearbox redesign"}'
```

## Versions and limits

The major API version is part of the route. Compatible additions ship within
`/v1`; breaking changes will use a new major version.

Every request consumes from both a global per-key bucket and a bucket for its
operation type. Responses include the selected policy in `X-RateLimit-Policy`
and describe both buckets:

Allowances are higher for workspaces with a paid subscription. These values are
loaded from Adam's current configuration; response headers remain authoritative
for an individual request.

<ApiRateLimits />

| Header                         | Description                                               |
| ------------------------------ | --------------------------------------------------------- |
| `X-RateLimit-Limit`            | Capacity of the selected operation policy                 |
| `X-RateLimit-Remaining`        | Whole requests currently available under that policy      |
| `X-RateLimit-Reset`            | Unix timestamp when that policy bucket will be full again |
| `X-RateLimit-Global-Limit`     | Capacity shared by all operations using the key           |
| `X-RateLimit-Global-Remaining` | Whole requests currently available globally               |
| `X-RateLimit-Global-Reset`     | Unix timestamp when the global bucket will be full again  |

The buckets refill continuously, so remaining quota can increase before the
reset timestamp. If either bucket cannot accept a request, Adam returns `429`
with `Retry-After` set to the number of seconds until both buckets can accept
another request. `Retry-After` is omitted from successful responses.

All errors use one JSON shape and include a request ID you can send to support:

```json theme={null}
{
  "error": {
    "type": "invalid_request",
    "message": "Idempotency-Key header is required",
    "requestId": "019cf..."
  }
}
```

## Asynchronous tasks

Creating a task or sending a message starts work asynchronously. Poll the task
until its status is `completed`, `failed`, or `cancelled`. If the status is
`awaiting_input`, read `pendingInteraction` and answer it with the interaction
endpoint; Adam then continues the same task.

## Paginate collections

Projects, tasks, task files, project files, and task messages use opaque cursor
pagination. Set `limit`, then pass a non-null `nextCursor` back as `cursor` on
the next request. Do not decode cursors or reuse one with a different endpoint.

You can filter tasks by `projectId` or by one public status: `queued`,
`running`, `awaiting_input`, `completed`, `cancelled`, or `failed`.

## Delete projects, tasks, and project files

`DELETE /v1/projects/{projectId}` permanently deletes a project you own. Its
tasks and files survive and become ungrouped. `DELETE /v1/tasks/{taskId}`
permanently deletes an owned task and its message history. Both operations
require the corresponding write scope and cannot be undone.

`DELETE /v1/projects/{projectId}/files/{fileId}` removes a durable shared file
from that project and requires both `projects:write` and `files:write`. Task
files and message attachments are immutable because they form part of the task
history.

## Upload and attach files

Task attachments use a two-step flow. First upload the bytes with
`POST /v1/uploads`. The returned upload ID is valid for 24 hours and is bound to
the user and workspace represented by the credential.

```bash theme={null}
curl https://api.adam.new/v1/uploads \
  -X POST \
  -H "Authorization: Bearer $ADAM_API_KEY" \
  -F "file=@drawing.step"
```

```json theme={null}
{
  "upload": {
    "id": "adam_upload_...",
    "name": "drawing.step",
    "mediaType": "model/step",
    "size": 148320,
    "expiresAt": "2026-08-27T12:00:00.000Z"
  }
}
```

Reference that ID when creating a task or sending a message:

```bash theme={null}
curl https://api.adam.new/v1/tasks/$TASK_ID/messages \
  -X POST \
  -H "Authorization: Bearer $ADAM_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(uuidgen)" \
  -d '{
    "content": "Check this drawing for manufacturing problems.",
    "attachments": [{"uploadId": "adam_upload_..."}]
  }'
```

Uploading and attaching files requires `files:write`. Creating a task or
sending its message also requires `tasks:write`. You can omit the text when the
attachment itself provides enough context.

To add a durable shared file directly to a project, send the multipart file to
`POST /v1/projects/{projectId}/files` instead.

## Read messages

Use `GET /v1/tasks/{taskId}/messages` for a paginated conversation. Messages
contain typed `parts`; there is no separate flattened text field.

```bash theme={null}
curl "https://api.adam.new/v1/tasks/$TASK_ID/messages?limit=50" \
  -H "Authorization: Bearer $ADAM_API_KEY"
```

```json theme={null}
{
  "messages": [
    {
      "id": "message-id",
      "role": "user",
      "createdAt": "2026-08-26T12:00:00.000Z",
      "parts": [
        {"type": "text", "text": "Check this drawing."},
        {
          "type": "file",
          "id": "file-id",
          "name": "drawing.step",
          "mediaType": "model/step",
          "downloadUrl": "https://api.adam.new/v1/files/file-id/content"
        }
      ]
    }
  ],
  "nextCursor": null
}
```

When `nextCursor` is not `null`, pass it as the next request's `cursor` query
parameter. File download URLs are absolute. You can also retrieve one file's
metadata with `GET /v1/files/{fileId}` before downloading its content.

The message-part types returned by an Adam deployment depend on its transparency
policy. Text, reasoning, and tool activity are enabled by default. Administrators
can also expose files, URL sources, document sources, reasoning files, structured
Adam data, provider-specific content, and model step boundaries. Disabled parts
are omitted; a message with no exposed parts is omitted as well.

Assistant tool activity remains in order. Every invocation has a `tool_call`
part. A completed invocation is immediately followed by a `tool_result` with a
`success`, `error`, or `denied` status.

```json theme={null}
[
  {
    "type": "tool_call",
    "toolCallId": "call-1",
    "name": "bash",
    "input": {"command": "python build.py"}
  },
  {
    "type": "tool_result",
    "toolCallId": "call-1",
    "name": "bash",
    "status": "success",
    "output": {"exitCode": 0, "stdout": "Created bracket.step"}
  }
]
```

Tool inputs and successful outputs are JSON values. Error results contain an
`error` string instead of `output`; denied results contain neither.

