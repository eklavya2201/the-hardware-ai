<!-- source: https://docs.adam.new/guides/automations.md fetched 2026-09-07T18:00:24+00:00 -->

> ## Documentation Index
> Fetch the complete documentation index at: https://docs.adam.new/llms.txt
> Use this file to discover all available pages before exploring further.

# Scheduled tasks

> Run a task once in the future or repeat it on a schedule.

Scheduled tasks let Adam run saved instructions once in the future or on a repeating schedule.

## Create a schedule

<Steps>
  <Step title="Open Scheduled">
    Select **Scheduled** in the sidebar, then select **New scheduled task**.
  </Step>

  <Step title="Describe the work">
    Enter the instructions Adam should follow each time. You can also give the task a name.
  </Step>

  <Step title="Choose when it runs">
    Select a one-time date, hourly, daily, weekly, monthly, or custom schedule. Check the time zone shown before saving.
  </Step>

  <Step title="Create and review">
    Save the task. Open it later to see previous runs, change its instructions, or adjust the schedule.
  </Step>
</Steps>

## Manage a scheduled task

From **Scheduled**, you can search your scheduled tasks and use the action menu to:

* edit the name, instructions, or schedule;
* pause or resume future runs;
* review previous runs; or
* delete the automation.

Pausing keeps the task but stops future runs until you resume it.

## Event-triggered work

Adam can also create tasks that run after supported events in a connected product. Tell Adam what event should start the task and what it should do.

You can edit the task's name and instructions from **Scheduled**. To change the event that starts it, ask Adam to update or recreate the task.

<Warning>
  Scheduled tasks can run while you are away. Be clear about what Adam may change. For sensitive work, ask it to report what it finds without making changes.
</Warning>

## Write reliable instructions

Include:

* what Adam should review;
* the time window to inspect;
* the expected output and destination;
* what counts as an exception; and
* whether Adam may make changes or should only report what it finds.

<Prompt description="Example weekly research task" actions={["copy"]}>
  Every Monday at 9:00 AM, search for engineering news about injection molding materials published during the previous seven days. Summarize the five most relevant updates, include a link to each source, and explain why each update matters to a product design team.
</Prompt>

