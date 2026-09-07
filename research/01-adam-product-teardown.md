# Adam (adam.new) — product teardown, 2026-09-07

Everything here was read from public pages on 2026-09-07 (marketing site,
docs, changelog, the open-source repo). Nothing came from a logged-in session
or from their backend. Re-run `scripts/research_extract_competitor.py` to refresh the raw
pages in `data/extracted/`.

## 1. What Adam is today

Adam started (2025) as **CADAM**: a browser text-to-CAD app where an LLM writes
OpenSCAD, the browser compiles it with OpenSCAD-WASM, and sliders let you tweak
the parameters. That product is still live at adam.new/cadam and is open
source (GPL-3.0, github.com/Adam-CAD/CADAM, ~5.1k stars, 638 forks, last push
2026-09-02).

In 2026 the company pivoted the paid product to an **"AI CAD copilot for
hardware teams"**: an agent that lives inside Onshape, Autodesk Fusion and
SolidWorks (via extension / desktop connector), reads your real CAD, PLM
(Arena), BOM spreadsheets, Slack, Gmail and Drive, and hands back finished
work (a branched model edit, a reconciled BOM, a detailed drawing, a render
set, a supplier quote email). Y Combinator company, $4.1M seed led by TQ
Ventures, founders are UC Berkeley alumni and ex-Adept (Zach Dive).

The homepage's example tasks are the clearest statement of the target
workflows:

| Skill | Example prompt on the site | Tools it touches |
|---|---|---|
| Design review prep | "Pull build notes, open issues, CAD screenshots into a brief" | Onshape, Notion, Calendar |
| Supplier comparison | "Find three suppliers for this anodized enclosure" | Browser, Sheets |
| BOM cleanup | "Fill missing MPNs, flag long-lead items" | Sheets, Gmail |
| Quote requests | "Draft RFQ with tolerances, finish, qty breaks" | Gmail, Drive |
| Build blockers | "Summarize bring-up channel into owners and dates" | Slack, Sheets |
| Component alternates | "Pin-compatible alternatives for a backordered MCU" | Browser, Sheets |
| Factory handoff | "Collect drawings, BOM, QA checklist into a folder" | Drive, Onshape, Notion |

## 2. Architecture as they describe it (adam.new/learn)

- **Chat + tasks.** Chat is the front door. Anything bigger than a quick reply
  becomes a background *task* with its own live timeline (every search, file
  read/write, decision, approval checkpoint). Close the tab, work continues.
- **Workers in a per-user Linux sandbox.** Each user gets a persistent Linux
  box on Adam's servers. Workers run code, browse with a real browser, write
  files, start dev servers (with preview URLs). Files persist between sessions.
- **Subagents.** A worker fans out helpers for parallelisable work; results
  roll up into one task.
- **Skills.** 70+ markdown playbooks loaded on demand (design a landing page,
  security audit, ship a PR, and CAD-specific ones). Users can write their own
  (a folder with a markdown file + helper scripts).
- **Tools.** File read/write, shell, real browser, web search, image and audio
  generation, OCR, document parsing, plus connected apps. Slow tools run async.
- **Memory.** Editable memory files per user; a separate shared team layer;
  nightly consolidation (changelog, July 15).
- **Connections.** Per-user OAuth to GitHub, Linear, Notion, Gmail, Drive,
  Calendar, Slack, Arena, "3,000 more" (logos are served from
  logos.composio.dev, so the integration layer is almost certainly Composio).
- **Approvals.** Sensitive actions (send email, change a file) pause for
  approve / edit / steer. Adam also gets an email address (you@adam.new).
- **Multiplayer.** Shared memory, shared connections, shared folders, task
  hand-off between teammates.

## 3. The CAD agent design (blog: "The Bitter Lesson of AI CAD")

Their stated thesis, verbatim in spirit: they threw away hundreds of
feature-specific CAD tools, a CAD DSL and a plane resolver. What is left is
two tools: **code generation** (the model writes CAD as code) and
**screenshot** (the model looks at the part), in a loop. Reasons given: models
are RL'd on code, and they can now see. They are openly bearish on
foundation-CAD-model startups.

CADAM's source confirms this is real, not marketing (see
`02-cadam-architecture.md`): the parametric agent has exactly two tools,
`build_parametric_model` and `answer_user`, and a 60-step budget. After every
build the browser compiles the OpenSCAD and uploads a seven-view inspection
sheet (ISO, front, back, left, right, top, bottom) that is fed back to the
model as an image before it may answer.

## 4. CAD-specific capabilities shipped in 2026 (changelog, Jun 29 to Aug 10)

- Interference / collision detection with red highlighting in the online CAD
  viewer; clearance and fit checks across Onshape, Fusion and workspace CAD.
- `@cad` references: attach parts of a model to a message as pills.
- STEP viewer with authored colours, persistent result cache, faster topology
  extraction; OpenSCAD viewer gained a customizer panel.
- Offscreen rendering engine with camera presets and part isolation.
- Blender and SketchUp connectors through the desktop connector.
- Adversarial "review pass" over finished work before hand-back.
- Model picker: Claude Sonnet 5, GPT-5.6 Sol (replaced 5.5), Grok 4.5;
  orchestration moved to GPT-5.5 on July 1.
- Password-protected share links; PWA install; full-text search; deploy
  websites built in the sandbox to a permanent URL.
- Onshape panel with full workspace conversation at onshape.adam.new; Fusion
  add-in with its own toolbar button.

## 5. Pricing (adam.new/pricing)

| Tier | USD / month | Notes |
|---|---|---|
| Free | 0 | light daily quota, Lite model only, CAD skills, CAD integrations |
| Pro | 40 | weekly usage reset, frontier models, shared folders, persistent memory, 7-day trial |
| Max | 200 | 5x Pro usage, higher frontier quotas |
| Team / Enterprise | contact | pooled weekly usage, SSO, audit, admin scopes |

Usage is metered by *task tiers* (Lite / Pro / Ultra), not just model. In the
open-source CADAM the metering is explicit: one billing token = $0.01 of
inference at list price, a Pro plan holds 5,000 tokens (about $50 of
inference), and every model has a hand-maintained price table in
`src/server/aiChat.ts`.

## 6. Public API and MCP (docs.adam.new, beta)

- REST at `https://api.adam.new/v1`: projects, tasks (async, poll until
  completed / failed / cancelled / awaiting_input), messages with typed parts
  (text, reasoning, tool_call, tool_result, file), uploads (24 h TTL),
  automations (cron or one-shot or webhook), billing summary, integrations.
- Idempotency-Key required on task creation and messages; 24 h replay window.
- Rate limits: free workspace 10/min global, 2/min task execution; paid
  480/min global, 80/min task execution.
- Hosted MCP server at `https://adam.new/mcp` with OAuth; tools
  `adam_list_models`, `adam_create_task`, `adam_get_task`,
  `adam_answer_interaction`, `adam_list_files`, `adam_get_file`,
  `adam_upload_attachment`.
- Docs site is Mintlify and publishes `llms.txt` plus every page as `.md`,
  which is what the extractor script uses.

## 7. What is NOT public

- The copilot backend (sandbox orchestration, workers, skills library, Onshape
  and Fusion extensions, the STEP viewer, interference checks). Only CADAM is
  open source.
- Any training data or fine-tuned model. Their own blog says there is none by
  design: "99% of the work is in the model."

## 8. Positioning relative to Synqit (one paragraph, details in 03)

Adam is mechanical-first and lives inside someone else's CAD. Synqit is
electronics-first and owns the whole artefact (schematic, PCB, BOM, enclosure,
build guide) from one prompt. The overlap is the enclosure / product-3D step
and the "agent that does hardware chores" framing (BOM cleanup, alternates,
RFQs). The technique worth taking is the code-plus-screenshot loop; the
business pattern worth noting is per-user sandboxes plus usage-metered plans
plus bring-your-own-model, which Synqit already partly has.
