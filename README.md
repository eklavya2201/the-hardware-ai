# the-hardware-ai — Synqit

**This repo is Synqit.** Not a prototype beside it, not a CAD experiment: the
studio in `app/` is what a visitor to <https://synqit.in> gets, and the engine
in `backend/` is what answers it. One product, one deployment, one account.

Type "a phone stand with a cable slot" and get an editable 3D model. Type "an
ESP32 board with a BNO055" and get a schematic, a placed and routed PCB, and a
BOM you can order from. The two halves are joined by facts, not screenshots —
see [How the halves fit](#how-the-halves-fit).

---

## Read this first

| | |
|---|---|
| **Live at** | <https://synqit.in> and `www`, served from EC2 `3.111.115.126` |
| **Stack on the box** | one Docker Compose project, `synqit`: `api` (engine) + `studio` + `caddy` (TLS) |
| **Database** | SQLite on the external volume `aws_synqit-data`. 57 users, 116 projects, 17 studio conversations |
| **Default model** | `azure/gpt-5.1` on the `aatreus` Azure OpenAI resource |
| **Active branch** | `agent-surface`. `main` is **11 commits behind** and is not what is deployed |
| **Remotes** | `origin` = `eklavya2201/the-hardware-ai`, `company` = `Jdhroov/Synqit`. Both carry `agent-surface` |
| **Render** | gone. Services suspended, `render.yaml` and `docs/DEPLOY_RENDER.md` are dead files |
| **Supabase** | gone. Removed entirely (97 files, −9,644 lines). Remaining mentions in the code are historical comments |

## Status by lane

| Lane | State |
|---|---|
| Electronics: schematic, placement, routing, BOM, build guide | live and deployed |
| 79 verifier rules, plus 9 PCB and 8 mechanical | live; `R82_flow_loop_direction` is the newest |
| Part library | 307 curated + 23,941 harvested = **24,248**. Both tracked in git; `user_parts.json` is 92 MB |
| Text-to-CAD: prompt → OpenSCAD → 3D → STL/SCAD/DXF | live |
| CAD mechanical gate (empty, degenerate, disconnected, floating) | live |
| Revert-on-regression: a turn that adds a blocking error is rolled back | live |
| Board STEP export | live via `kicad-cli` |
| STEP **rendered in the studio** | not built; needs FreeCAD in the image |
| Public share links | live |
| Octopart / Nexar part search | code is in, **credentials missing** |
| Mouser price checks | key is broken; the `auditor` container is stopped to free memory |
| Local development | **broken** since the Supabase removal — see [Running locally](#running-locally) |

## Where the work is right now

The last five commits, newest first, all on `agent-surface`:

```
52b8285  Prove the request fits instead of estimating that it does
1f8fb31  Let the model actually see the render on the default provider
8a31714  Use the repo's own test runner for the context budget
28c1f89  Let the deploy scripts run again after the Supabase removal
ae7ffa8  Keep a long conversation inside the model's input window
```

They are one story, and it is worth knowing before touching the studio agent.

A user hit `Input tokens exceed the configured limit of 272000 tokens`. Nothing
bounded the conversation, so the failure was permanent: every retry re-sent the
same oversized branch. The first fix added a compaction ladder. It did not
work, because the estimator behind it added up a guess per part type and
charged a flat 2,000 tokens for anything image-shaped.

What it was missing: `@ai-sdk/openai` maps a `content` tool result for the
chat-completions API as `contentValue = JSON.stringify(output.value)`. There is
no image block in an OpenAI tool message, so the whole base64 render travelled
as **raw text**. Anthropic, Google and OpenRouter each map it to a real image
block. Azure — the default model — does not.

Two things followed, and both had been true since the inspection loop was
written:

- **The model never saw the render.** It was told to "inspect the multi-view
  render in this tool result" and handed a hundred thousand characters of
  base64. The CAD side's only visual self-correction has been blind on the
  default provider from the start. Only the measured facts ever got through.
- **The sheets cost about twenty times what they should.** Four renders from
  one real conversation: 73, 90, 88 and 89 KB on disk, which is 454,485 base64
  characters, which Azure billed at roughly 300,000 tokens. As images they are
  about 1.5k each.

`app/src/server/contextBudget.ts` now does three things, and the third is the
one that matters:

1. **Measures instead of guessing.** Serialize the message, count every
   character, then subtract only what can be *proved* to travel as a compact
   image block. A field nobody anticipated is counted by default.
2. **Prices dense strings honestly.** Base64 has no word boundaries for a
   tokenizer to merge on, so it runs about two characters per token, not four.
3. **Enforces a bound, not an estimate.** A BPE token never consumes less than
   one character, so a payload of N characters can never exceed N tokens.
   `Size.ceiling` is that bound; the ladder runs until it holds, and a final
   gate strips images and halves the longest remaining string until it does.
   No message is ever removed there, so no tool call loses its result.

On the provider that stringifies, the image is also lifted out of the tool
result into a user message right after it — the one place every provider
accepts a picture.

`src/server/contextBudget.test.ts` has 20 tests, three of which replay that
exact conversation using sheet sizes read off the production disk. One pins the
estimate to within 40% of what Azure actually billed, so a future change that
makes it drift low fails there before a user finds it.

Run them with the repo's own runner (there is no vitest here):

```bash
cd app
node --experimental-strip-types --import ./scripts/ts-register.mjs --test src/server/contextBudget.test.ts
```

## What is next

In the order I would do them.

1. **Verify the CAD quality claim.** The visual loop has never once run on the
   default model, so nobody knows how good it is. The reviewer's complaint was
   "the cad model is not that good even for very basic stuffs" over an Apple
   logo that came out a blob. Ask for a retry of that exact prompt. If it still
   wanders after two or three revisions, the problem is genuinely the model or
   the prompt, and only then are we measuring that.
2. **Fix local development.** `app/scripts/bootstrap.mjs` is entirely
   Supabase-based and therefore dead. Nothing has replaced it.
3. **Merge `agent-surface` into `main`.** 11 commits, all deployed and live.
4. **Rotate every credential pasted into chat.** Render API keys, the Supabase
   personal access token. They are in transcripts.
5. **Get Nexar (Octopart) credentials**, and either fix or retire the Mouser key
   that had the `auditor` container looping on 403s.
6. **The fix-up phase gate.** Telemetry is recorded (`tool_errors`,
   `state_guesses`, `rules_fixed`, `rule_regressions`, `fixup_stalled`) and
   deliberately not enforced. Worth a week of data before adding a gate.
7. **Decide on FreeCAD.** Rendering STEP in the studio needs it in the image,
   which is 2–4 GB on a box with about 4.7 GB free. A cost call, not a
   technical one.
8. **Delete the dead deploy path**: `render.yaml`, `docs/DEPLOY_RENDER.md`.
9. **Research follow-ups** that were justified but not built: a failure-to-action
   taxonomy with a `RESOLVE` branch, and server-side contact and grounded
   predicates on the real kernel rather than on mesh facts.

## Traps

Things that have already cost time. Read before repeating them.

- **`main` is not what is deployed.** `agent-surface` is.
- **Editing files with a Python script on Windows writes CRLF.** That broke
  `deploy.sh` on the box with `sudo: unable to execute ./deploy.sh: No such
  file or directory` — a mangled shebang. Git normalizes on commit, so the
  commit looks fine; `ship.sh` tars the *working tree*. Convert to LF before
  shipping.
- **Do not pass `--build-arg APP_BASE=/` from Git Bash.** MSYS rewrites the
  bare `/` into `C:/Program Files/Git/`, and the studio then fails with
  `spa.maskPath must be a path`. The Dockerfile already defaults it.
- **The box cannot build the studio.** Two cores, no spare memory. Build the
  image locally and `docker load` it — that is what `deploy/aws/ship.sh` does.
- **`routeTree.gen.ts` goes stale** and breaks the Docker build with "not
  assignable to keyof FileRoutesByPath". `app/sync-to-dev.sh` mirrors the source
  to a working copy and copies the generated tree back.
- **An unanswered API call is not an answer.** A 403 from a supplier once marked
  245 real parts unorderable. Treat a failed lookup as unknown, never as no.
- **`vitest` is not a dependency of `app/`.** Tests use `node:test`; a vitest
  import fails the Docker build's `tsc -b`.

## Repo map

| Path | What it is |
|---|---|
| `app/` | **The studio** — the product's UI and the agent that drives the engine. React 19, TanStack Start, OpenSCAD-WASM, three.js. Server routes are not optional: `/api/parametric-chat` runs the agent, `/api/synqit/*` proxies the engine same-origin |
| `app/src/server/aiChat.ts` | The studio agent: system prompt, intent gate, tools, the 60-step loop |
| `app/src/server/contextBudget.ts` | What may be sent to the model, and the proof that it fits |
| `app/src/utils/modelGate.ts` | The CAD mechanical gate over measured mesh facts |
| `backend/` | **The engine** — FastAPI: schematic agent, verifier rules, library, placement, routing, BOM, build guide, KiCad/Gerber/STEP export |
| `backend/app/services/agent.py` | The board agent: 18 mutation tools, 14 query tools, 30 turns, 3 fix-up turns |
| `backend/app/services/verifier.py` | The 79 rules. `flow_rules.py`, `pcb_verifier.py`, `mechanical_verifier.py` hold the rest |
| `deploy/aws/` | The only live deploy path: `ship.sh` (build here, load there), `deploy.sh` (run on the box), compose, Caddyfile |
| `docs/ARCHITECTURE.md` | How the agents work, end to end. Start here for the design |
| `docs/AGENT_SURFACE.md` | Every tool and query the board agent has, with contracts |
| `docs/INTEGRATION.md` | The verbs / nouns / queries rule and the CAD brief |
| `docs/ENTITIES.md` | The IR: what a component, net and project actually are |
| `docs/DEPLOY_AWS.md` | The deployment in detail, including how to add a model |
| `research/` | Competitor teardown, architecture notes, feasibility. Internal, and a reason not to make this repo public |

## How the halves fit

Electronics and CAD are one product joined by **queries, not pictures**.

The engine answers `GET /projects/{id}/cad-brief` with the board as facts plus
an OpenSCAD `synqit_board()` prelude. The studio inserts it (the Board button,
or `?project=<id>`) and the agent builds an enclosure around real holes, real
parts and real connector openings. After every build the studio *measures* the
model — bounding box, lowest point, triangle count, separate bodies — and hands
those numbers to the agent before it looks at any render.

That ordering is the rule, and it is why the render being unreadable went
unnoticed for so long: the facts alone were enough to keep the loop turning,
just not enough to tell the agent that a shape did not look like an apple.

## Deploying

From the repo root, with Docker running:

```bash
deploy/aws/ship.sh          # builds the studio image, loads it on the box, ships the repo
```

Then on the box:

```bash
cd ~/the-hardware-ai/deploy/aws && sudo ./deploy.sh
```

`deploy.sh` checks that `backend.env` and `studio.env` exist and that
`STUDIO_SERVICE_SECRET` matches between them, frees memory by stopping the
`auditor` and staging containers, rebuilds the engine overlay, brings the stack
up and waits for health. It ends by printing a DNS step that is **already
done** — `synqit.in` resolves to the EC2 box.

Rolling back is in the script's own output. Full detail: `docs/DEPLOY_AWS.md`.

## Running locally

**This does not currently work.** `app/scripts/bootstrap.mjs` starts a local
Supabase stack and writes Supabase keys into `.env.local`, and Supabase is
gone. Nothing has replaced it.

The shape of what it should become, for whoever picks this up: run the engine
on `:8000` (the studio defaults `SYNQIT_API_BASE` to `http://localhost:8000/api`),
give the studio `AZURE_OPENAI_API_KEY`, `AZURE_RESOURCE_NAME=aatreus`,
`STUDIO_SERVICE_SECRET` matching the engine's, and `ENVIRONMENT=local` to
bypass billing, then `npm run dev` in `app/`. The studio is served at `/`, not
`/cad` — that path moved when the landing page became the front door.

That path is unverified. Do not trust this section until someone has run it.

`deploy/aws/studio.env.example` is the accurate list of what the studio needs
at runtime.

## Licence

`app/` is GPL-3.0, derived from the upstream project named in `app/NOTICE.md`,
with GPL-2.0+ OpenSCAD-WASM binaries (`app/LICENSE`,
`app/src/vendor/openscad-wasm/SOURCE-OFFER.txt`). Our changes are under the
same licence. Everything outside `app/` is internal.
