<!-- source: https://docs.adam.new/integrations/arena.md fetched 2026-09-07T18:00:30+00:00 -->

> ## Documentation Index
> Fetch the complete documentation index at: https://docs.adam.new/llms.txt
> Use this file to discover all available pages before exploring further.

# Connect Arena PLM

> Connect an Arena account so Adam can work with items, BOMs, suppliers, files, and changes.

Connect Arena so Adam can work with the items, bills of materials, suppliers, files, and changes that you can access.

## Connect Arena

<Steps>
  <Step title="Open Arena in Integrations">
    In Adam, select **Integrations**, search for **Arena**, and open its connection card.
  </Step>

  <Step title="Enter your Arena credentials">
    Sign in with the Arena email address and password for the account Adam should use.
  </Step>

  <Step title="Verify the account">
    Confirm Arena appears under **Connected**, then ask Adam to inspect a known item or change without editing it.
  </Step>
</Steps>

Adam does not save your Arena password.

## Work safely with PLM data

Adam has the same Arena access as the account you connect. Use an account with the right permissions for the work you want Adam to do.

For important changes, ask Adam to review the record before editing it:

<Prompt description="Review an Arena change before editing" actions={["copy"]}>
  Review change order \[number]. Summarize the affected items, BOM changes, missing approvals, and missing attachments. Do not change anything in Arena. Recommend the next steps and wait for my approval.
</Prompt>

<Warning>
  Changes to items, bills of materials, and change orders can affect manufacturing work. Tell Adam exactly which records it may change and ask it to wait for approval when you are still exploring.
</Warning>

## Disconnect or reconnect Arena

Open **Integrations** → **Connected**, select Arena, and disconnect the account when it should no longer be used by the team.

If Arena stops responding:

* confirm the Arena account can sign in directly and is not locked;
* check whether the account's password, role, or organization access changed;
* disconnect and reconnect the account; and
* retry with a specific record that the account is known to access.

