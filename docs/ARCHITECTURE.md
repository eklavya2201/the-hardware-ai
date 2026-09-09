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

- **79 deterministic verifier rules** run over the whole design after every
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
- They do **not** pay for themselves in tokens -- they cost about 9% more. What
  they buy is measured below: 46% fewer wrong-state operations and 67% fewer
  verifier errors. Sell the second sentence, never the first.
- The argument underneath is the one to make: a number is exact and costs a few
  tokens, while a screenshot costs thousands and still cannot tell 40 mm from
  44 mm. In hardware that difference is the whole job.

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
│ guards: 79 deterministic verifier rules      │
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
validates. After each turn, 79 deterministic rules run over the result — power
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

## Do the query tools work

Two questions, and they have different answers. The token one was asked first
and answered wrongly for a week.

### Tokens: no

The first run showed -49% prompt tokens for the query mode. Repeating it with
two repetitions gave **+5%**, and the current measurement gives **+9%**.
The saving was noise and should not be quoted.

What explains it is variance: the same prompt, in the same mode, run twice,
ranged from 162k to 562k prompt tokens -- a **3.5x spread**, far larger than
the effect. Two follow-ups failed the same way. Forcing the agent to call its
queries cost 42% more tokens; trimming 24% of the per-call payload bought only
7% back, because the call count rose 18% and each call replays the prefix.
All three traded context for a question and lost: a cached prefix token costs
$0.50/M here, 96-98% of the prompt is cached, and one extra call replays all
~29,000 of them.

### Wrong-state operations: yes, and this is the claim that matters

Tokens were always a proxy, and a bad one -- dominated by how many turns a run
happens to take. The thing read tools exist to prevent is the agent acting on
a belief about state it never checked, and that is countable.

`connect()` refuses a pin already on another net (R21, the commonest defect in
the prompt sweep at 12 hits in 12 builds), refuses a rail short, and refuses a
refdes that is not there. Each refusal is a state guess, caught at the source.
`agent.USAGE` counts them, separating a false belief from a merely malformed
call. Same five prompts, two repetitions, reads hidden against reads offered:

| | reads hidden | reads available | |
|---|---|---|---|
| **state guesses** | **61** | **33** | **-46%** |
| tool errors | 71 | 38 | -46% |
| verifier errors | 3 | 1 | -67% |
| unconnected pins | 157 | 137 | -13% |
| prompt tokens | -- | -- | +9% |

Every quality measure moves the right way, and the 9% is the cost of asking.

The effect scales with the design's complexity, which is the useful part:

| prompt | hidden (r1, r2) | reads (r1, r2) |
|---|---|---|
| rain-edit-led | 0, 0 | 0, 0 |
| rain-edit-button | 1, 1 | 3, 1 |
| rain-alert | 2, 3 | 8, 2 |
| counter-7seg | 0, **29** | 1, 5 |
| smartwatch | **15, 10** | 2, 11 |

On a board with three parts there is nothing to be confused about and the
reads earn nothing. On the two hardest boards, where the blind agent produced
29 and 15 wrong-state calls, they earn their keep.

Caveats that belong next to the number: n=2, and the variance is still brutal
-- baseline `counter-7seg` scored 0 on one run and 29 on the next. What makes
this believable where -49% was not is that the effect is larger than the noise
and every metric points the same way.

**So: read tools do not save tokens, they cost 9%. They cut wrong-state
operations 46% and verifier errors 67%.** That is the defensible sentence.

Raw results: `docs/bench-20260909-read-tools.json`, and the two token runs
beside it.

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

- The token half of the benchmark still cannot answer anything: run-to-run
  variance on an identical prompt reached 3.5x, so no token effect smaller than
  that is visible at two repetitions. The state-guess counter is the half worth
  trusting, and it wants more repetitions too.
- Reads earn nothing on simple boards and a lot on complex ones. Nobody has
  tried offering them only when the design is large enough to warrant the extra
  turns; on this evidence that is the obvious next experiment.
- Forcing the agent to call its queries made the output worse. It asks on its
  own often enough (71 calls across ten runs) that it is better left alone.
- The board agent is one agent with many tools, not a set of specialists. A
  power-supply specialist and a layout specialist would probably beat it, and
  that is a real experiment, not a certainty.
- Nothing yet feeds the enclosure result back into the board (a fit check that
  answers "does the case clear every part by X mm" as a query).
