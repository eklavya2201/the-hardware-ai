<!-- source: https://adam.new/featurescript-mcp fetched 2026-09-07T18:00:10+00:00 -->

PricingFAQDocs

Talk to usTry Adam

Explainer

# Onshape FeatureScript MCP, explained: what PTC's new AI tool actually does

August 19, 2026

PTC spent years watching engineers ask an AI to write FeatureScript by hand, copy-pasting between a chatbot and a Feature Studio tab. In August 2026, it shipped an answer: an official FeatureScript MCP server that lets an AI client write, test, and iterate FeatureScript directly inside Onshape. PTC calls the approach "text-to-code-to-CAD." Here's what that actually means, and where it still leaves you holding the debugger.

## What is FeatureScript, and why does it matter here?

FeatureScript is the programming language Onshape built for parametric modeling, a real, C-family language, released in 2016, that every native Onshape feature (Extrude, Fillet, Helix) is itself written in. You write it inside a Feature Studio, a code-editor tab in an Onshape document: a precondition block declares a feature's parameters, a body executes the geometry operations. Get it right, and you have a custom feature that behaves like any built-in tool: parametric, shareable, reusable across a team. Get it wrong, and you're debugging regeneration errors in a real programming language.

## What is the FeatureScript MCP server?

It's PTC's own Model Context Protocol server, released through the early-access Onshape Labs program. You subscribe through the Onshape App Store, sign in with your Onshape account, and connect an AI client. Claude, ChatGPT, and Gemini are all supported. From there, the AI can create Feature Studios, write and edit FeatureScript, search Onshape's FeatureScript documentation, and test a feature against a live document until it regenerates cleanly.

That tooling exists to fix a specific, well-documented problem: FeatureScript is a low-resource language, and AI models, across every major vendor, are known to hallucinate standard-library functions and confuse API versions when writing it unassisted. Onshape's own community forum has threads describing exactly this: ChatGPT inventing functions that don't exist, Gemini insisting a current API is outdated. The MCP server's documentation search and live-test loop are a direct answer to that failure mode, and a reasonable one.

## What does it actually produce?

Code. The output of a FeatureScript MCP session is a custom feature (a reusable tool you can add to the Onshape toolbar), not a finished part. That's a meaningfully different deliverable than "model me a bracket." If what you want is a specific part, you're asking the AI to write a tool that could build that part, then running the tool, then checking the part came out right. It's powerful when the goal really is a reusable feature. It's a roundabout way to get one design.

## Do you still need to read the code?

Yes. The debugging loop for a FeatureScript MCP session happens at the level of reading FeatureScript and reading regeneration errors. The documentation search and live-test tools reduce how often the AI gets it wrong, but they don't remove the step where a human reviews what the AI wrote. That's fine if you're already comfortable in a Feature Studio. It's a real barrier if you're not.

## So who is this actually for?

FeatureScript MCP is aimed squarely at Onshape power users: people who already author custom features, or would if writing FeatureScript from scratch weren't so slow. For that audience, it's a legitimate, well-built tool. PTC took the documented failure mode seriously and built real infrastructure against it, and the reusability of a custom feature is a genuine advantage: build it once, and your whole team uses it with no further AI cost.

For everyone else, an engineer who wants a part modeled, edited, or cleaned up without opening a Feature Studio at all, it's the wrong tool for the job. The AI is still a general-purpose chat model pointed at a code editor; it has no mechanical-engineering training of its own, and the thing it hands you back is something you still have to read.

## So what's the real gap?

PTC's framing, "text-to-code-to-CAD," is honest about what this is: an extra layer of code between your prompt and your part. That layer is valuable if the tool is the point. It's overhead if the part is the point.

The gap that leaves is a dedicated mechanical-engineering agent that does the FeatureScript work itself, invisibly, the way Adam does, and hands back the finished model instead of a Feature Studio tab to debug, without locking you into one AI vendor's subscription and without a metered allocation to run out of.

See the full Adam vs FeatureScript MCP comparison: setup and what each one actually produces.

## Put Adam to work in Onshape.

Edit Onshape parts from prompts, generate parametric models from scratch, pick the AI model that powers your work, and let a dedicated mechanical-engineering agent handle the FeatureScript layer, plus the BOMs and sourcing around it.

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
