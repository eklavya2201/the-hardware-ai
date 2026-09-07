<!-- source: https://docs.adam.new/guides/integrations.md fetched 2026-09-07T18:00:25+00:00 -->

> ## Documentation Index
> Fetch the complete documentation index at: https://docs.adam.new/llms.txt
> Use this file to discover all available pages before exploring further.

# Connect tools and accounts

> Connect Adam to CAD applications, Arena, and other tools you use.

Open **Integrations** in the sidebar to find a product or manage an existing connection. Use **Connected** to see the accounts and computers already linked to your team.

## Choose the right connection method

| Connection                                                                          | How it connects                                                  | Keep the application open?          |
| ----------------------------------------------------------------------------------- | ---------------------------------------------------------------- | ----------------------------------- |
| [Onshape](/integrations/onshape)                                                    | Connect your Onshape account; optionally add Adam inside Onshape | Only when using Adam inside Onshape |
| [Autodesk Fusion](/integrations/fusion)                                             | Install the Adam add-in for Fusion                               | Yes                                 |
| [SolidWorks, Revit, Blender, SketchUp, and Unreal](/integrations/desktop-connector) | Install the Adam Desktop Connector                               | Yes                                 |
| [Arena PLM](/integrations/arena)                                                    | Sign in to your Arena account                                    | No                                  |
| Other cloud products                                                                | Follow the sign-in steps shown in Adam                           | No                                  |

## Connect an integration

<Steps>
  <Step title="Choose your team">
    If you use more than one team, select the team that should have access to the connection.
  </Step>

  <Step title="Open the integration">
    Go to **Integrations**, search for the tool, and open its connection card.
  </Step>

  <Step title="Follow the setup steps">
    Adam will ask you to sign in, install an add-in, pair a computer, or enter a key depending on the product.
  </Step>

  <Step title="Confirm it is connected">
    Return to **Connected** and check that the account or computer appears.
  </Step>
</Steps>

## Use an integration in a task

Tell Adam which product, document, or record to use and what you want it to do. If you connected more than one account, say which one you mean.

For important changes, ask Adam to review the work and wait for you before editing anything:

<Prompt description="Review before changing a connected system" actions={["copy"]}>
  Review \[document or record name]. Summarize what you find and explain what you would change. Do not make changes until I approve them.
</Prompt>

## Credentials and access

For integrations that require an API key, use a dedicated key with only the permissions Adam needs. Adam will not display the full key again after you save it.

Disconnect an account from its integration page or the **Connected** view when your team no longer needs it.

<Warning>
  Connected products may contain sensitive design or business information. Only connect accounts that the team is allowed to use.
</Warning>

