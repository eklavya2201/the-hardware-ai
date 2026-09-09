# How the agent works, end to end

Written to be read aloud to someone who has not seen the code. It answers one
question: when a person types a sentence, who does what, in what order, and how
does the system know it is right?

## Start here: what is actually hard to copy

Generating a schematic from a sentence is the easy half. Any competent team
with a good model can do it in a fortnight, and the output looks convincing
whether or not it works.

The hard half is knowing whether the thing is right, and that is where nearly
all of this system's substance sits:

- **78 deterministic verifier rules** run over the whole design after every
  agent turn — power rails, pin types and directions, footprint sanity, board
  size, duplicate controllers, unconnected pins. They are code, not model
  opinion, which is why "0 errors" means something and why every finding names
  the rule that produced it.
- **A 23,941-part library** where each part carries a real footprint, real pin
  names and a real order code. The agent picks from it rather than inventing
  part numbers, so a BOM line is something you can buy.
- **Real placement and routing** — force-directed placement, A\* for the
  interactive preview, FreeRouting for a manufacturing-grade finish, and one
  geometry authority that decides where the copper may go, so the placer, the
  router, DRC and the Gerber all agree on the board's edge.

Everything below is how the agent reaches those three things. Lead with them.

## How the agent surface is labelled

Every agent surface here labels **three** things, not one.

| | what it is | examples here |
|---|---|---|
| **Operations (verbs)** | actions that change state | `design_board`, `layout_board`, `add_component`, `connect`, `auto_place` |
| **Entities (nouns)** | what exists, its properties, its stable id | net, pad, footprint, mounting hole, part envelope, connector cutout, parameter |
| **Queries (eyes)** | cheap exact reads instead of pictures | `cad-brief`, model facts (bounding box, bodies), `get_pin_net`, `list_unrouted_ratlines`, `list_issues` |

Label only the verbs and the agent cannot observe what it built, so it guesses,
and a bigger model does not fix guessing. The reads deserve to be first-class
tools.

Two honest notes on this, because it is easy to oversell:

- It is command/query separation — a design principle older than any of this —
  applied to agent tools. Applying a known-good idea somewhere new is
  legitimate, but it is not the invention, and a technical listener will
  recognise it.
- The claim that the queries pay for themselves in tokens **is not supported by
  our own measurement**. See "Does it actually save anything" below. The
  argument that survives is narrower and still worth making: a number is exact
  and costs a few tokens, while a screenshot costs thousands and still cannot
  tell 40 mm from 44 mm. In hardware that difference is the whole job.

## Two agents, one conversation

There is no single super-agent. There are two, each with a small tool set, and
one calls the other.

```
person types a sentence
        │
        ▼
┌──────────────────────────────────────────────┐
│ STUDIO AGENT            (runs in the browser)│
│ tools: design_board, layout_board            │
│        build_parametric_model                │
│        answer_user                           │
└───────────────┬──────────────────────────────┘
                │ design_board(name, brief)
                ▼
┌──────────────────────────────────────────────┐
│ BOARD AGENT           (Synqit, FastAPI)      │
│ tools: search_parts, add_component, connect, │
│        record_requirements, set_form_factor, │
│        auto_place, auto_route, run_drc, …    │
│ guards: 78 deterministic verifier rules      │
└───────────────┬──────────────────────────────┘
                │ returns COUNTS, not prose
                ▼
   components · nets · placed · traces ·
   unrouted · errors · warnings · board size
```

### The studio agent (3D and orchestration)

Lives in `app/src/server/aiChat.ts`, executes its tools in the user's browser.

- `design_board(name, brief)` — when the request involves electronics. It
  creates a project in the user's own Synqit account, runs the board agent on
  the brief, then places and routes. It returns numbers.
- `layout_board()` — places and routes the board already linked to the
  conversation. Without it, "place and route this board" produced a CAD model,
  because designing a new one was the only board verb the agent had.
- `build_parametric_model(title, code)` — writes a complete OpenSCAD script.
  The browser compiles it, measures it, and renders a seven-view sheet.
- `answer_user(message)` — the only way it can speak to the person.

The loop is: write, look at what came back, rewrite if wrong, then answer.
Budget is 60 steps. It may not claim it built something without the tool call
in that same turn.

### The board agent (electronics)

Lives in Synqit's `backend/app/services/agent.py`. It owns a typed
intermediate representation: components, pins, nets, board, mechanical. It
never writes free-form netlists; every change goes through a tool that
validates. After each turn, 78 deterministic rules run over the result — power
rails, pin types, footprint sanity, requirement counts, board size, duplicate
controllers. Those rules are code, not opinion, which is why "0 errors" means
something.

## What replaces the screenshot

Both agents get facts back, not just images.

- After a 3D build: bounding box in millimetres, lowest point, triangle count,
  and how many **separate bodies** exist. A base plus a lid reads as 2. A part
  that floated off reads as 3. The agent checks the numbers before it looks at
  the render.
- After a board run: component and net counts, how many parts were placed, how
  many traces, how many nets are still unrouted, and the error and warning
  counts from the rules.
- For the enclosure: `GET /projects/{id}/cad-brief` returns the board as
  nouns — outline, thickness, mounting holes, every part as an envelope with a
  height, connector openings, clearances — about 3 KB, plus a generated
  OpenSCAD `synqit_board()` module. The agent designs around measured geometry
  and is told never to redraw the board.

## Does it actually save anything

**On the evidence so far: no, and the earlier claim that it did was noise.**

`backend/scripts/bench_agent_tokens.py` runs the same five turns in two modes —
query tools hidden, query tools on — and records token usage from the
provider's own counters plus output quality from the verifier.

The first run (one repetition, 2026-09-08) showed −49% prompt tokens for the
query mode. Repeating it with two repetitions the same day gave **+5%**:

| | baseline | with queries |
|---|---|---|
| prompt tokens | 5,896,971 | 6,176,313 (+5%) |
| model calls | 145 | 150 (+3%) |
| wall time | 764 s | 700 s (−8%) |
| verifier errors | 0 | 0 |
| warnings | 52 | 48 (−8%) |

The saving did not survive replication, so it should not be quoted. What
explains both numbers is variance: the same prompt in the same mode, run
twice, ranged from 162k to 562k prompt tokens — a **3.5× spread**, far larger
than the effect anyone is trying to measure. At this sample size the benchmark
cannot resolve a difference of this size in either direction.

We then tried to fix the thing the numbers pointed at. The agent was barely
calling its queries — three times in ten runs — because the design-state block
in its context already listed each net's pins, so `get_net` and `get_pin_net`
had nothing to add. Removing that made it ask properly (33 → 75 calls) and cost
**42% more prompt tokens, 37% more model calls and 33% more warnings** for 6%
fewer unconnected pins. It has been reverted. The state block was not the token
problem either: it already capped membership at six pins per net, so slimming it
saves 28 tokens on a 46-part board.

The mechanism was real — a two-pin net printed in full does make `get_pin_net`
pointless — but the fix costs more than it buys.

So the honest state is: the query tools are a good idea for a reason that is
still unproven here — a number is exact where a picture is not — and the
instrument needs fixing (more repetitions, or a way to damp the variance)
before any token claim is made from it.

## Guardrails, each with a test

Not promises — `backend/scripts/regression_agent_queries.py`, 77 checks. The
budget check below is the reason the prompt rule is as short as it is: an
addition came in at 2,516 characters against a 2,500 cap, and raising the cap
to fit it would have defeated the guard.

1. **Purity** — the project is serialised before and after every query and must
   be byte-identical. A query can never change state.
2. **Size cap** — every query answer is capped at 1,500 characters, lists
   paginate and say when they were cut. Tested against a synthetic 200-part board.
3. **Typed inputs** — bad arguments return a structured error, unknown ids
   return "no such thing" plus the first twenty that exist. Never a stack trace.
4. **Loop guard** — at most 12 queries per turn, then the tool tells the agent
   to act or ask.
5. **No unverified claims** — the agent may not say "connected", "routed" or
   "0 errors" unless a query returned it that turn; a deterministic check flags
   it if it does, the same way an existing rule catches invented parts.
6. **Access** — query endpoints reuse the project's own permission check.
   Another user's project is 404, never 403.
7. **Prompt budget** — the additions stay under 2,500 characters and the whole
   system prompt under 120,000, enforced by a test.
8. **Blast radius** — the verifier, placement, routing, exports and the
   mechanical bridges must show zero diff.

## Where the code is

| Piece | Path |
|---|---|
| Studio agent, prompt, tools | `app/src/server/aiChat.ts`, `app/shared/chatAi.ts` |
| Browser-side tool execution | `app/src/components/chat/ChatSession.tsx` |
| `design_board` driver | `app/src/lib/synqitBoardAgent.ts` |
| Model facts | `app/src/utils/meshFacts.ts` |
| Board agent + tools | `backend/app/services/agent.py`, `backend/app/services/tools.py` |
| Query tools and `/q/` endpoints | `backend/app/services/queries.py` (branch `agent-queries`) |
| Verifier rules | `backend/app/services/verifier.py` |
| Board brief for the enclosure | `backend/app/services/cad_brief.py` |
| Benchmark | `backend/scripts/bench_agent_tokens.py` |

## The honest gaps

- The benchmark cannot answer the question it was built for. Run-to-run
  variance on an identical prompt reached 3.5x, so at two repetitions no effect
  smaller than that is visible. Fix the instrument before tuning anything
  against it: more repetitions, or judge on verifier output rather than tokens.
- The agent under-uses its own queries, and the one attempt to change that made
  the output worse. Left alone deliberately.
- The board agent is one agent with many tools, not a set of specialists. A
  power-supply specialist and a layout specialist would probably beat it, and
  that is a real experiment, not a certainty.
- Nothing yet feeds the enclosure result back into the board (a fit check that
  answers "does the case clear every part by X mm" as a query).
