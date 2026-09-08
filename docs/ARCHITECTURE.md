# How the agent works, end to end

Written to be read aloud to someone who has not seen the code. It answers one
question: when a person types a sentence, who does what, in what order, and how
does the system know it is right?

## The one rule everything follows

Every agent surface labels **three** things, not one.

| | what it is | examples here |
|---|---|---|
| **Operations (verbs)** | actions that change state | `design_board`, `build_parametric_model`, `add_component`, `connect`, `auto_place` |
| **Entities (nouns)** | what exists, its properties, its stable id | net, pad, footprint, mounting hole, part envelope, connector cutout, parameter |
| **Queries (eyes)** | cheap exact reads that replace looking at pictures | `cad-brief`, model facts (bounding box, bodies), `get_pin_net`, `list_unrouted_ratlines`, `list_issues` |

Most teams label only the verbs. The agent then cannot observe state, so it
guesses, and you pay for the guess in tokens and in wrong output. Queries are
the part people skip and the part that makes it work: a number costs a few
tokens and is exact, a screenshot costs thousands and still cannot tell 40 mm
from 44 mm. Pictures are for what numbers cannot express, never for measuring.

## Two agents, one conversation

There is no single super-agent. There are two, each with a small tool set, and
one calls the other.

```
person types a sentence
        │
        ▼
┌──────────────────────────────────────────────┐
│ STUDIO AGENT            (runs in the browser)│
│ tools: design_board                          │
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

Measured, not asserted. `backend/scripts/bench_agent_tokens.py` runs the same
five turns twice, once with the query tools hidden and once with them on, on
the same model, and records token usage from the provider's own counters plus
output quality from the verifier.

| | baseline | with queries | change |
|---|---|---|---|
| prompt tokens | 3,202,739 | 1,631,883 | −49% |
| model calls | 81 | 40 | −51% |
| wall time | 443 s | 257 s | −42% |
| verifier errors | 0 | 0 | same |
| warnings | 24 | 19 | −21% |
| edits that landed | 1 of 2 | 2 of 2 | better |

Honest reading: the win is real on this sample but it did not come from the
agent calling queries — it called them three times in ten runs. It came from
finishing in fewer turns, because the prompt rules stopped it wandering. One
repetition per prompt, so treat it as direction, not a measured constant. The
next step is making the agent actually reach for the eyes during a fresh
build, then re-running with more repetitions.

## Guardrails, each with a test

Not promises — `backend/scripts/regression_agent_queries.py`, 77 checks.

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

- The agent still under-uses its own queries during a fresh build. Prompt work,
  then re-benchmark.
- The board agent is one agent with many tools, not a set of specialists. A
  power-supply specialist and a layout specialist would probably beat it, and
  that is a real experiment, not a certainty.
- Nothing yet feeds the enclosure result back into the board (a fit check that
  answers "does the case clear every part by X mm" as a query).
