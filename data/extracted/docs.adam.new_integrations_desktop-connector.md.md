<!-- source: https://docs.adam.new/integrations/desktop-connector.md fetched 2026-09-07T18:00:29+00:00 -->

> ## Documentation Index
> Fetch the complete documentation index at: https://docs.adam.new/llms.txt
> Use this file to discover all available pages before exploring further.

# Use the Desktop Connector

> Connect Adam to supported CAD and 3D applications running on your computer.

The Adam Desktop Connector links Adam to supported applications running on your computer, including **SolidWorks, Revit, Blender, SketchUp**, and **Unreal Engine**.

## Pair a desktop application

<Steps>
  <Step title="Open the integration in Adam">
    Select **Integrations**, choose the desktop application, and start its setup flow.
  </Step>

  <Step title="Install the connector">
    Download and install the Adam Desktop Connector on the computer that runs the target application.
  </Step>

  <Step title="Generate a pairing code">
    In Adam, generate a new pairing code. Codes expire, so keep the setup window open.
  </Step>

  <Step title="Enter the code locally">
    Open the Desktop Connector and enter the code.
  </Step>

  <Step title="Open the target application">
    Keep the connector and the application open while Adam works with it. Return to Adam and confirm the computer is connected.
  </Step>
</Steps>

## Application notes

<Tabs>
  <Tab title="SolidWorks">
    SolidWorks support is available on Windows. Start SolidWorks and keep it open while Adam works with it.
  </Tab>

  <Tab title="Revit">
    Adam currently supports Revit 2027 through Autodesk's Public MCP Server Tech Preview. Follow the Revit setup steps shown in Adam.
  </Tab>

  <Tab title="Blender, SketchUp, Unreal">
    Follow the setup steps shown in Adam, then keep the application and Desktop Connector open while Adam works with it.
  </Tab>
</Tabs>

## Connect a custom MCP server

This is an advanced option for connecting your own local tools. Use **Custom MCP** if your server provides a **Streamable HTTP** MCP endpoint.

Enter the local endpoint URL in the Desktop Connector, verify the server is running, and test it with a read-only request. A command-line stdio server is not a URL and cannot be entered directly; expose a supported HTTP endpoint first.

<Warning>
  Only connect servers you trust. Their tools may be able to change files or data on your computer and in other products.
</Warning>

## Manage paired devices

Open the integration page to rename, repair, or unpair a computer. Unpair computers you no longer control.

If a device appears offline:

* open the Desktop Connector on the paired computer;
* confirm the target application and its plug-in are running;
* verify the computer can reach Adam over the network; and
* generate a new pairing code and connect the computer again.

