<!-- source: https://docs.adam.new/resources/troubleshooting.md fetched 2026-09-07T18:02:17+00:00 -->

> ## Documentation Index
> Fetch the complete documentation index at: https://docs.adam.new/llms.txt
> Use this file to discover all available pages before exploring further.

# Troubleshooting

> Diagnose task, CAD connection, file, scheduled-work, integration, and billing issues in Adam.

Start by checking [Adam service status](https://status.adam.new). Then check whether the problem affects one task, one connected product, or everything in Adam.

## A task will not start or continue

1. Open the task and check for a question, approval card, or **Needs input** state.
2. Open **Settings** → **Billing** and verify that included usage or add-on funds are available.
3. If the task uses a desktop CAD application, confirm that the application and its Adam connection are open.
4. Retry with a simpler request that names the document, file, or record.

If Adam may have changed something before the error appeared, check the file or record before asking it to try again.

## Onshape will not connect

* Allow the Onshape sign-in pop-up and retry from **Integrations** → **Onshape**.
* Confirm you signed in to the intended Onshape account and active Adam team.
* Check the Onshape account's permission to the target document.
* Disconnect and reconnect Onshape if the account access changed.

If the problem only happens inside Onshape, reload the Onshape tab and sign in to Adam again.

## Fusion appears offline

* Open Fusion and the intended design.
* Select **Adam** in the Fusion toolbar.
* If the button is missing, press **Shift+S**, open **Add-Ins**, find **AdamFusion**, and choose **Run**.
* If AdamFusion is absent, rerun the installer from [fusion.adam.new/install](https://fusion.adam.new/install), then restart Fusion.

## A Desktop Connector application is offline

1. Open the Desktop Connector on the computer running the target application.
2. Start the application and its Adam plug-in.
3. Confirm the device still appears in the integration detail.
4. If necessary, generate a fresh expiring pairing code and pair again.

SolidWorks support requires Windows. Adam currently supports Revit 2027 through Autodesk's Public MCP Server Tech Preview.

## A connected cloud account stopped working

Your sign-in to another product may expire. Open **Integrations** → **Connected**, select the account, and reconnect it. If Adam cannot open one particular file or record, confirm that your account can access it directly.

For an API-key connection, create a new key in the connected product and give it only the permissions Adam needs. Never paste a key into a task or support email.

## An expected file is missing

* Open the task that created the file and check its file tabs.
* Search **Files** by filename, type, source, or platform.
* Check whether the task paused before file creation.
* Ask Adam whether it finished creating the file before asking it to try again.

## A scheduled task did not run

* Confirm the automation is not paused.
* Review its next run and the time zone shown.
* Open the previous runs to find the latest result or error.
* Confirm that any connected account it needs still works and that your plan has usage available.
* For an event-triggered task, confirm that the expected event happened in the connected product.

To change the event that starts a task, ask Adam to update or recreate it.

## Contact support

Email [hello@adam.new](mailto:hello@adam.new) and include:

* the Adam team and task URL;
* the integration and application version, if relevant;
* the approximate time and time zone;
* the expected result and the result you observed; and
* a screenshot or non-secret error message.

Never send a password, API key, full payment-card number, or Desktop Connector pairing code.

