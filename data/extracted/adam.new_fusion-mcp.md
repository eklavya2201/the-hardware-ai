<!-- source: https://adam.new/fusion-mcp fetched 2026-09-07T18:00:10+00:00 -->

PricingFAQDocs

Talk to usTry Adam

Explainer

# Autodesk Fusion MCP, explained: what Claude can (and can't) do in Fusion

August 19, 2026

In April 2026, Autodesk and Anthropic gave Claude the ability to drive a live Fusion session. Type a request in Claude Desktop, and it can sketch, extrude, fillet, and export inside a Fusion file that's already open on your machine. It's a real capability, not a demo, and it's worth understanding exactly what it does, because the marketing and the mechanics tell two different stories.

## What is the Autodesk Fusion MCP server?

Fusion MCP is Autodesk's official integration with the Model Context Protocol (MCP), an open standard for connecting AI models to external tools. It shipped as two pieces: a local Fusion MCP server that runs on your machine and executes commands inside a live Fusion desktop session (reading geometry, taking screenshots, running scripts, writing parametric timeline history), and a separate, cloud-hosted Fusion Data MCP server that answers questions about Fusion Team project data without Fusion running at all. Both are bridges. They give an AI model access to Fusion's API surface. Neither is a CAD engine of its own. Fusion's geometry kernel still does the actual modeling work underneath.

## How do you set it up?

Setup happens inside Fusion itself: Preferences, then General, then API, where a toggle enables the local MCP server, listening on port 27182 by default. Autodesk built and markets this specifically around Claude Desktop: from there you install the Autodesk Fusion connector in Claude Desktop and match the port. Two things matter here. First, it has to be a local MCP client. A browser-based client like claude.ai has no way to reach a server running on your machine (other MCP-compatible desktop clients can technically connect too, but Claude Desktop is the supported path). Second, Fusion has to already be open with a file active; the MCP server modifies the session you're in, it doesn't spin one up. If either app isn't running, the connection has nothing to talk to.

## What can it actually do?

In hands-on testing, Fusion MCP handles well-specified geometry cleanly: a pyramid built from explicit dimensions, a fillet pattern applied consistently across two dozen edges with a stated radius. It's also genuinely useful for chores on models that already exist, renaming components, sweeping a fillet across a set of edges, adjusting parameters, exporting files, because that's editing a known structure, not inventing one. In practice, that's a fairly narrow list of things it's reliably good at:

Fillets, chamfers, and parameter edits on a design you already have open

Renaming and organizing existing components in batch

Simple geometry built from explicit dimensions

Exporting files and running Fusion API scripts

Where it struggles is starting from nothing. The same hands-on tests found organic or loosely specified shapes (a badge, a stylized part) came back wrong more often than not. That tracks with how the integration works: Claude is a general-purpose reasoning model with no CAD-specific training, calling Fusion's API and inferring what commands to issue from a text prompt. It's good at following explicit instructions against a known API. It has no engineering judgment of its own about what a mechanical part should look like.

## Does this replace CAD engineers?

No, and Autodesk isn't claiming it does. What shifts is where the AI sits in the process. Rote, well-specified modeling tasks are fair game for an AI to handle directly, while judgment calls about tolerances, material selection, and manufacturability still need an engineer in the loop, because nothing about Fusion MCP validates a design against physical constraints or your team's own standards. The AI executes. The engineering judgment is still yours.

## What gets in the way?

The biggest constraint is that there's no in-product way to switch models. Autodesk built and markets Fusion MCP specifically around Claude Desktop, and the setup path is Claude-specific. Other MCP-compatible clients can technically point at the same local server, but nothing here gives you a model picker the way a dedicated CAD agent would.

None of this is unique to Autodesk's execution. Before the official launch, roughly a dozen community-built Fusion MCP servers already existed on GitHub (the largest with just over a hundred stars), all sharing the same architecture: a Python add-in running inside desktop Fusion, bridged to a local MCP process. They carry the same constraints the official version does: Fusion has to be installed, licensed, and running; everything is single-machine; and the AI on the other end is a general-purpose model, not one built for engineering.

## Is this the same as Autodesk's other AI features?

Not quite. It's worth separating them. Autodesk Assistant is a guided-actions chatbot across Fusion, Inventor, and other Autodesk products. Automated Modeling is generative shape synthesis between bodies you already have, not text-to-CAD. Fusion MCP is neither of those; it's an open door that lets an external AI model, currently just Claude, reach into your Fusion session and issue commands through the same API a script would use.

## So what's the real gap?

Fusion MCP is a genuine step forward for Autodesk, and treating it as a toy would undersell it. It does real, useful work on files you already have open. But look at what it actually is: a general-purpose chat model, with no engineering-specific training, given API access to one CAD program, reachable through exactly one AI vendor's desktop app. That's an integration, not an engineering agent.

The gap it leaves is the one you'd expect from bolting a chat model onto an API: it doesn't generate parts reliably from a blank page, it can't touch your BOM or your supplier quotes once the geometry is done, and you have no say in which model is doing the reasoning. Closing that gap takes a different kind of product: one built specifically for mechanical engineering work, with tooling for feature trees and BOMs and manufacturability rather than a general model guessing at an API, and without locking you to a single AI vendor to use it. That's the problem Adam is built to solve.

See the full Adam vs Fusion MCP comparison: setup, model choice, and what each one can and can't generate.

## Put Adam to work on your CAD.

Generate parametric models from a prompt, edit parts inside the CAD tools you already use, pick the AI model that powers your work, and hand the busywork around CAD to a dedicated mechanical-engineering agent.

Get started with AdamTalk to us

### Products

CopilotCADAM

### Website

PricingBlogChangelogCareers

### Resources

DevelopersDocsGithubDiscord

### Socials

XInstagramYoutube

### Company

Terms of ServicePrivacy PolicyTrust & SecurityBug Bounty

COPYRIGHT ADAM AI LABS, INC 2026
