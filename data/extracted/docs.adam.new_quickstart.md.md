<!-- source: https://docs.adam.new/quickstart.md fetched 2026-09-07T18:00:17+00:00 -->

> ## Documentation Index
> Fetch the complete documentation index at: https://docs.adam.new/llms.txt
> Use this file to discover all available pages before exploring further.

# Quickstart

> Complete your first useful task in Adam.

You can start using Adam immediately. You do not need to connect another tool first.

## Before you begin

Sign in at [adam.new](https://adam.new). If you want Adam to review a file, have it ready to upload.

<Frame caption="Use **+** to attach a file, then describe the result you need in the message box.">
  <img src="https://mintcdn.com/adam-585915d9/SOvZH_gvRGaOooNN/images/product/new-task-composer.jpg?fit=max&auto=format&n=SOvZH_gvRGaOooNN&q=85&s=d723f864c52e1a7ada45370843f27c91" alt="Adam New Task composer with an attachment button and example task suggestions" width="750" height="205" data-path="images/product/new-task-composer.jpg" />
</Frame>

<Steps>
  <Step title="Start a task">
    Select **New Task** in the sidebar.
  </Step>

  <Step title="Add anything Adam should review">
    Attach a file, drag it into the message box, or paste an image. Skip this step if your question does not need a file.
  </Step>

  <Step title="Describe what you need">
    Tell Adam the outcome you want. Mention anything it should preserve, check, or avoid changing.
  </Step>

  <Step title="Send your message">
    Adam shows its progress in the conversation. If it needs more information or your approval, answer in the task.
  </Step>

  <Step title="Review the work">
    Read the response and open any files Adam creates. Reply in the same task to ask questions or refine the result.
  </Step>
</Steps>

## Write a useful first request

Say what Adam should review, what you want back, and anything it must not change.

<Prompt description="Review an attached drawing" actions={["copy"]}>
  Review the attached engineering drawing. Summarize what the part does, list its critical dimensions and tolerances, and flag anything that looks missing or ambiguous. Do not change the file.
</Prompt>

<Prompt description="Ask an engineering question" actions={["copy"]}>
  Explain the main design considerations for a snap-fit enclosure that will be injection molded in ABS. Organize the answer into geometry, tolerances, material behavior, and common failure modes.
</Prompt>

<Tip>
  Keep replying in the same task when Adam needs to remember the earlier conversation or files.
</Tip>

## Connect a CAD tool when you need it

To work directly with a CAD document, follow the setup guide for [Onshape](/integrations/onshape), [Autodesk Fusion](/integrations/fusion), or a [supported desktop application](/integrations/desktop-connector). You can do this at any time from **Integrations**.

