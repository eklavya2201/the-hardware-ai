<!-- source: https://adam.new/learn fetched 2026-09-07T18:00:02+00:00 -->

PricingFAQDocs

Talk to usTry Adam

Field guide

# Get to know Adam

A field guide to the AI coworker that runs alongside you. Where he lives, how he works, what he remembers, and what it costs.

Contents

01Web app02Email03Tasks04Workers05Subagents06Sandbox07Skills08Tools09Memory10Connections11Multiplayer12Pricing

01The home base

## Web app

The web app at adam.new is the main place you and Adam work together. Chat, watch tasks run live, approve outputs, and pull up anything he's worked on.

### How do I get started?

Sign in at adam.new and you'll land on the home view. Type what you need into the chat input: a question, a task, a paste of context. Adam replies and, if the work is bigger than a quick answer, kicks off a task in the background. You can keep talking while it runs.

### What's the difference between Adam and a task?

Adam is the chat. Tasks are the things he's actively working on for you. Each task gets its own panel where you can watch progress live, see what files were touched, and approve the result.

### How do I share files with Adam?

Drag-and-drop or paste. Files (PDFs, images, docs, spreadsheets), URLs, screenshots, plain text. Adam picks them up and uses them as context. You don't need to describe what they are.

### Can I close the tab and come back later?

Yes. Close the tab and the work keeps going. When you come back, the thread is exactly where it was. Same context, same files, same place. You'll get a notification when the task wraps up or needs your input.

02Adam in your inbox

## Email

Adam gets a real email address tied to your account. CC him on a thread, forward something for him to handle, or let him draft replies you approve before they go out.

### What email address does Adam use?

Each user gets a unique address (yours@adam.new), and you can also let Adam send from a Gmail or Outlook account you've connected. Use the adam.new address for handoffs; use the connected address when you want replies to look like they came from you.

### How do I hand off an email?

Forward a thread to him, or CC him on one. He reads everything in context (earlier messages, attachments, the people involved) and either replies for you or comes back with a draft for approval.

### Does Adam send without me approving?

By default, drafts land in your approval queue. Once you trust a workflow (say, replying to inbound demo requests), you can let Adam send directly. You can change that per workflow at any time.

03Watch and approve work

## Tasks

Anything Adam runs in the background is a task. The tasks view is where you watch work happen live, approve outputs, and revisit anything he's done.

### What do I see inside a task?

Every step Adam took: what he searched, which files he read or wrote, what he decided, where he paused for your input. You can scrub back through the timeline and pull any file out.

### How does approval work?

Adam checkpoints at meaningful moments: plans, drafts, anything that would send an email or change a file you care about. You'll see a proposal with approve / edit / steer. One click and he keeps going.

### Can I branch from a previous task?

Yes. Open any past task and start a follow-up from any step in the timeline. Useful when a task went 80% of the way and you want to nudge the last 20% without starting over.

### What happens to old tasks?

They stay. Old tasks are searchable, the files they produced are still there, and you can hand any of them off to a teammate.

04What's running when he's running

## Workers

When Adam takes on something bigger than a quick reply, he spins up a worker. Workers are the part of Adam actually doing the work (running code, browsing the web, writing files) in a private Linux box just for you.

### What's a worker, in plain terms?

Adam's the conversation; the worker is the doing. The worker has its own little workspace (your sandbox), full tool access, and its own attention span, so the chat with Adam stays snappy while the heavy lifting happens in the background.

### Can I have several running at once?

Yes. Each worker is independent. Fire off three at once (research, draft, file cleanup) and they run in parallel. You'll see each one as a separate task and approve them as they land.

### How does he do twenty things at once?

Workers can spawn helpers: sub-tasks that fan out to do work in parallel. You don't have to think about it; Adam organizes them and rolls up the result. This is how a single "summarize these twenty docs" task finishes in a fraction of the time.

### Can a worker actually run code?

Yes. The sandbox is a real Linux environment. Workers can install packages, run scripts, spin up dev servers, drive a browser, write files. Anything you'd do on a fresh machine. None of it touches your computer.

### Does the sandbox stick around?

Files persist. The sandbox is yours and resumes between sessions. If a worker built a project last week, it's still there this week. Pick it up or start fresh.

05How tasks split up

## Subagents

When a worker has a lot to do (twenty things to research, fifty files to transform), it spawns helpers and divides the job. You don't have to think about it; the result rolls up to one place.

### What's a subagent?

A subagent is a helper a worker spawns to do part of a job in parallel. Same machinery as a worker, just scoped to a smaller piece. They report back when they're done and the worker stitches everything together.

### When do they show up?

Mostly you don't see them by name. The signal is that a task with a lot of independent pieces finishes much faster than you'd expect. That's because subagents took chunks of it in parallel.

### Can I see what each helper is doing?

Yes. Open the task and expand any branched step in the timeline. You'll see what each subagent did, what files it touched, and what it found.

06Adam's private workspace

## Sandbox

Every Adam user gets their own sandbox: a private Linux machine on Adam's servers where workers run. Files, processes, browsers. Everything happens there, never on your laptop.

### What is the sandbox?

It's a real Linux environment. Workers can install software, run scripts, build projects, drive a browser, and write files inside it. Treat it like a personal cloud machine that's always on.

### Does it persist between sessions?

Yes. Files and installed software stick around between sessions. If a worker built a project last week, it's still there this week. Pick it up or start fresh.

### Can I poke around in it myself?

Open the sandbox view from any task. You'll see a file tree, a terminal, and any web servers a worker started. You can poke around, run your own commands, or download files.

### What about preview links?

When a worker spins up a dev server or generates a webpage, Adam gives you a live preview URL. Click it and you're looking at what's running inside the sandbox in real time.

07Focused playbooks

## Skills

Skills are little playbooks Adam reaches for when relevant: how to design a landing page, how to do a security audit, how to ship a pull request. Seventy-plus ship with Adam, each tuned for one kind of work.

### What's a skill?

A focused, self-contained set of instructions for a specific kind of task. Skills aren't always loaded. Adam pulls one in when it matches what you're doing, then sets it down when he's done.

### How does Adam choose one?

He matches your request to a skill if there's a good fit. You'll usually see him say something like "using the design skill." That's him loading it. Loading on demand keeps him sharp instead of overwhelmed.

### Can I write my own?

Yes. A skill is a folder with a markdown file describing when to use it, plus any helper scripts. Anything you'd document for a teammate (your team's deploy process, a code-review rubric, a brand-voice guide) becomes a skill Adam can pick up next time it's relevant.

08What workers reach for

## Tools

Workers have a curated toolkit. Each task gets a subset chosen for the job: focused enough to stay sharp, broad enough to actually finish.

### What can a worker actually do?

Reading and writing files, running shell commands, browsing the web with a real browser, web search, image generation, audio generation and transcription, OCR, document parsing, and any app you've connected.

### How are tools picked for a task?

Adam picks the subset based on what you asked for. A coding task gets file + shell + skills. Research gets web search + browser. Heavy media work gets generation tools. The worker isn't drowning in options it doesn't need.

### What about slow tools?

Long-running tools (video generation, big browsing sessions, batch transcription) run async. The worker keeps moving on other parts of the task and you watch the result land when it's ready.

09Context that sticks

## Memory

Memory is what makes Adam feel like a coworker instead of a chat window. He remembers who you are, what you're working on, and the way you like to work.

### What does Adam actually remember?

Your role, the projects you're on, the people you work with, the systems you reference, and feedback you've given him. Anything he'd need to pick up your context cold the next time you message him.

### Can I see and edit my memory?

Yes. Memory is a set of files in your account you can read, edit, and prune. Adam writes to it, but you have final say. If something's wrong, change it.

### How do I tell Adam to remember or forget something?

Just tell him: "remember that" or "forget that." Or open the memory page and edit it directly. Both work.

### Does memory leak between accounts?

No. Personal memory is yours. Teams have a separate shared layer for things the whole team should know. That's the only memory other people on your team can see.

10Your other apps

## Connections

Connections plug Adam into the apps you already use: GitHub, Linear, Notion, Gmail, Drive, Calendar, and a thousand more. Connect once and tasks use them when relevant.

### How do I connect an app?

Open connections, pick the app, and authenticate. The OAuth flow is the same one you'd do anywhere else. Adam only uses the connection when a task actually needs it.

### Is the app I use supported?

Most likely yes. If it's a popular SaaS app, it's probably already supported. If it's something niche, you can wire a custom connection with an API key and Adam will use it the same way.

### How private are my connections?

Per-user OAuth, scoped to the connection's permissions. Every call Adam makes shows up in the task's activity feed. You can see what was read, what was written, and to which account.

### Can I revoke access?

Disconnect at any time from the connections page. The token is revoked immediately and Adam can't use that app anymore until you reconnect.

11Bring your team

## Multiplayer

Teams share memory, connections, and tasks. Hand a task to a teammate, drop a file in a shared folder, or let everyone benefit from the same house style.

### How do I add teammates?

Invite teammates from settings. Each person works in their own threads on their own sandbox; what's shared is what your team explicitly chooses to share.

### What gets shared?

Three things: a shared memory layer (house style, infra notes, customer context), shared connections (one Notion workspace, one shared GitHub org), and shared folders any teammate can drop files into.

### Can I hand a task to a teammate?

Open any task, hit hand off, and pick a teammate. They get the full thread (context, files, plan) and can take it from where you stopped.

12What it costs

## Pricing

Per-seat subscription with included usage. If you're using Adam heavily you can top up; we tell you before you spend anything extra.

### What's included in a seat?

A generous pool of task minutes and model usage. Most users don't go over. If you do, you can top up, but you'll see it coming.

### Is there a free trial?

There's a free trial for trying it. Long enough to feel whether Adam fits how you work before you're charged.

### How do team plans work?

Team plans pool weekly usage across every seat, add shared memory and connections, and give you one place to see what the whole team is shipping. One bill, one admin.

That's the tour

Ready to put a Worker on something?

Open Adam
