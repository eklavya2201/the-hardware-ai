<!-- source: https://adam.new/ fetched 2026-09-07T17:59:58+00:00 -->

# The AI CAD workspace for hardware teams

Continue with GoogleContinue with Email

By continuing, you agree to ourTerms of ServiceandPrivacy Policy
See how it works

YBacked by Y Combinator

## Built for hardware work

Adam has built-in skills that make it an expert in CAD, research, sourcing, and engineering documentation.

### Prepare a design review

Pull the latest build notes, open issues, and CAD screenshots into a design review brief for tomorrow.

### Compare supplier options

Find three suppliers for this anodized aluminum enclosure and compare cost drivers, lead times, and risks.

### Clean up a BOM

Review this BOM, fill missing manufacturer part numbers, flag long-lead items, and prepare it for sourcing.

### Request supplier quotes

Draft a quote request email for the revised bracket with tolerances, finish, quantity breaks, and target delivery.

### Track build blockers

Summarize the blockers from our bring-up channel and turn them into owners, next steps, and dates.

### Find component alternates

Find pin-compatible alternatives for this backordered MCU and explain firmware and supply-chain tradeoffs.

### Package a factory handoff

Collect the drawings, BOM, QA checklist, and open questions into a manufacturer handoff folder.

### Prepare a design review

Pull the latest build notes, open issues, and CAD screenshots into a design review brief for tomorrow.

### Compare supplier options

Find three suppliers for this anodized aluminum enclosure and compare cost drivers, lead times, and risks.

### Clean up a BOM

Review this BOM, fill missing manufacturer part numbers, flag long-lead items, and prepare it for sourcing.

### Request supplier quotes

Draft a quote request email for the revised bracket with tolerances, finish, quantity breaks, and target delivery.

### Track build blockers

Summarize the blockers from our bring-up channel and turn them into owners, next steps, and dates.

### Find component alternates

Find pin-compatible alternatives for this backordered MCU and explain firmware and supply-chain tradeoffs.

### Package a factory handoff

Collect the drawings, BOM, QA checklist, and open questions into a manufacturer handoff folder.

PricingFAQDocs

Talk to usTry Adam

## How it works

01

### Connect your CAD & PLM

Connect Onshape, Fusion, SolidWorks, your PLM, and supplier catalogs. Adam reads your geometry, BOMs, drawings, and revisions in real time, one connected graph, no exported copies.

Update the drawing for rev D

Done. Drawing ready:

2 dims revised · 3 affected items

02

### Tell Adam what you need

Message Adam from wherever you already work: Slack or email. “Thicken the sinking boss,” “build the v3 BOM,” “detail the baseplate drawing.” Adam does the work.

Model edit · Boss draft fix

Branch ready · 2 features changed

EditApprove

03

### Get the finished work

Adam hands back the real output: a branched model edit, a reconciled BOM, an updated drawing, a render set, each linked to its source, ready to ship or adjust.

## Works with your stack

Adam connects to the CAD, PLM, suppliers, and tools your hardware team already runs on. No migration, no exported copies of your IP.

OnshapeAutodesk FusionSolidWorksArenaMcMaster-CarrDigi-KeyXometryGoogle DriveJiraLinearNotionAirtableSlackGmail+ 3,000 more

## What Adam can do for you

Geometry, BOMs, sourcing, renders, docs, vendors. Adam does the recurring work across your CAD and your stack, and hands back the finished output, from model to BOM to drawing to render.

Engineering
The M3 boss keeps sinking on the molded part. Fix it and add draft?

Done. I thinned the boss wall to 1.6 mm, tied it to the floor with a gusset rib, added a 0.5 mm root fillet, and set 1.0° draft toward the A-side. Drawing dims updated to match. Want to see the before/after?

Fusion

BOM
Build the BOM for the v3 assembly and flag anything not released.

47 line items, 31 unique part numbers. Six are still WIP rev, and the gasket references rev B while the latest released drawing is rev C, a mismatch. Two screws duplicate under different PNs; I'd consolidate to McMaster 91290A115. Push the released items to Arena?

OnshapeGoogle SheetsGoogle DriveArena

Drawings
Detail the drawing for the machined baseplate. The dowel bores are the critical fit.

Sheet's done. Dimensioned the bore pattern off datums A|B|C, put an H7 fit and Ø0.05 true position on the two dowel bores, 1.6 Ra on the mating face, and ISO 2768-mK general tolerances in the title block. One dimension chain was over-constrained, so I rebased it from datum B. PDF is in Drive for review.

SolidWorksGoogle Drive

Sourcing
The Omron limit switch on the v3 just went end-of-life. Find a drop-in replacement.

Browsed Digi-Key, Mouser, and Newark for a drop-in. The Panasonic AVL3 matches the 6.5 N force and footprint, in stock at $0.62 for your 250 qty. I logged the top three in a sheet. Want the full comparison?

BrowserGoogle Sheets

Renders
I need a hero shot and an exploded view of the wearable for Friday's review.

Both rendering. Hero is a 3/4 on warm-grey seamless, keyed camera-left with a soft fill and a rim to catch the anodized edge, 35 mm so the proportions read honest. The exploded view pulls the six components along Z with leader callouts. They'll land in ~25 min. Want a turntable too?

OnshapeFigma

Vendors
Get quotes from our two sheet-metal shops for the enclosure, 50 and 250 qty.

Drafted quote requests to both shops with the rev C STEP, the flat-pattern DXF, and the drawing: 5052-H32 at 2.0 mm, 8 bends, clear chromate per MIL-DTL-5541 Type II Class 3, qty breaks and lead time. I left our target price out so we don't anchor them. Both are staged in your drafts to send.

GmailOnshape

## What Adam can do for you

Geometry, BOMs, sourcing, renders, docs, vendors. Adam does the recurring work across your CAD and your stack, and hands back the finished output, from model to BOM to drawing to render.

BOMRendersVendorsDrawingsSourcingEngineering

Build the BOM for the v3 assembly and flag anything not released.

47 line items, 31 unique part numbers. Six are still WIP rev, and the gasket references rev B while the latest released drawing is rev C, a mismatch. Two screws duplicate under different PNs; I'd consolidate to McMaster 91290A115. Push the released items to Arena?

OnshapeGoogle SheetsGoogle DriveArena

## Pricing

PersonalTeams & Enterprise

## Frequently Asked Questions

CAD and design

Will Adam break my feature tree or design intent when it edits a model?+

Adam edits through your CAD file the way a careful teammate would, adjusting a dimension, adding a feature, or mating a component while respecting the existing history and references. It regenerates the model, shows you which downstream features and BOM lines changed, and flags any edit that would break a parent-child relationship instead of forcing a rebuild.

How does Adam handle BOMs and revision control without making a mess?+

Adam reads your assembly and PLM (Arena and others) and keeps the BOM in sync as the model changes, flagging part-number, quantity, and revision mismatches instead of silently editing released data.

Where does my IP go, and is it used to train models?+

Your geometry, drawings, BOMs, and correspondence stay in your own accounts and PLM. Adam connects over scoped, authenticated access (OAuth where supported) and reads only what a task needs, never copying your CAD into a separate store. Nothing you connect trains models, nothing is shared between accounts, and every workspace is isolated and encrypted. Enterprise plans add SSO, audit logging, and admin scope controls.

Is this for industrial designers too, or just mechanical engineers?+

Both are first-class. For industrial designers, Adam rebuilds pinched fillets as curvature-continuous blends and batches photoreal renders that track the latest geometry. For mechanical engineers, it leans into BOMs, drawings, sourcing, and vendor email, detailing drawings with the tolerances and datum callouts you specify. Most teams run both roles on one product, and Adam is the connective layer between them.

Getting started

What kinds of tasks should I give Adam?+

Give Adam work that normally requires switching between CAD, spreadsheets, supplier emails, team threads, and engineering docs.

Good examples are cleaning a BOM, preparing a design review, comparing suppliers, packaging a factory handoff, or turning a build thread into clear next steps.

How does Adam know what to do?+

You can start with a plain-language request. Adam reads the connected context, creates a workstream, and shows the result before anything important leaves your hands.

If a task needs clarification, Adam asks instead of guessing. If a task can be completed safely, Adam prepares the result and waits for review.

Do I have to change my workflow?+

No. Adam is designed to work across the tools you already use. You do not need to move your team into a new project management system to get value.

The product works best when Gmail, Calendar, Drive, Slack, Notion, Sheets, and web research are connected, but you can begin with only a few tools.

Approvals and control

Can I approve work before Adam sends or changes anything?+

Yes. Adam can prepare emails, calendar holds, document edits, summaries, and research notes, then pause for approval.

The point is to keep work moving without removing human judgment. You stay in control of what gets sent, changed, or shared.

What does approval look like?+

Approval steps appear in the workstream with the relevant source context, proposed output, and next action. You can approve, edit, or ask Adam to revise.

For sensitive actions, Adam returns the work for your review rather than executing silently.

Can I see where Adam got an answer?+

Adam cites the sources it used whenever the work depends on research, past messages, documents, or calendar context.

That makes it easier to check the answer, spot missing context, and decide whether the result is ready.

Tools and context

Which tools does Adam work with?+

Adam is designed around the places hardware work already lives: Onshape, Gmail, Calendar, Notion, Drive, Sheets, Slack, Messages, GitHub, Linear, and the web.

The exact set of live integrations can vary by workspace, and new ones ship regularly. If a tool you rely on is missing, tell us.

Can one task use multiple apps?+

Adam can use multiple tools inside one workstream. For example, he can read a Slack thread, check a CAD note, draft a supplier email in Gmail, and organize supporting files in Drive.

It feels like one delegated task, not a pile of app-specific automations.

Does Adam remember the context of the work?+

Adam is built to preserve context across a task. It can reference the original request, connected files, message history, meeting notes, and previous outputs in the same workstream.

That context is what lets Adam return useful work instead of generic AI text.

Plans and billing

Which plan should I pick?+

Free is for trying Adam on lighter tasks. Pro fits most individual engineers, and Max is for heavy daily workloads with 10x the usage.

Individual plans are for people delegating their own work. Team and Enterprise are for groups that need shared context, approvals, administration, and support.

How do usage limits work?+

Every plan includes a weekly usage limit covering the work Adam performs across research, reasoning, tool use, and generated outputs.

Larger workstreams use more of the weekly limit because they involve more connected context, more tool calls, or longer-running work.

What is the difference between Team and Enterprise?+

Team is for small groups that want shared workstreams and individual usage limits per assigned seat. Enterprise is for companies that need SSO, audit trails, governance, and rollout support.

Enterprise starts with a conversation with our team rather than a self-serve checkout.

Security and trust

How does Adam handle connected app access?+

Adam requests only the permissions needed for the connected workflow. Workspace admins decide which tools are connected and who can use them.

For team deployments, access controls and approval trails are visible and auditable, not hidden implementation details.

Is Adam safe for company work?+

Yes. Adam is built for delegated work where sensitive actions are prepared for review, not executed without visibility.

Enterprise customers can discuss retention, governance, and security review with us during onboarding.

What should Adam not do automatically?+

Adam helps with your workday without asking your team to surrender control to an agent.

The model is careful delegation: Adam drafts, checks, organizes, and prepares. You approve the work that matters.

Don't see your question answered? Email founders@adam.new

## Put Adam

to work
Get Started

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
