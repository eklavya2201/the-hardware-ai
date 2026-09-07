# CADAM — how the open-source text-to-CAD app is built

Source: `cadam/` in this repo (Adam-CAD/CADAM, commit recorded in
`data/cadam_upstream_commit.txt`, GPL-3.0). 368 tracked files. Verified on
2026-09-07: `npm ci` (958 packages) and `tsc -b --noEmit` both succeed on
Node 22 on Windows; see the README for the build status.

## Stack

| Layer | Choice |
|---|---|
| Frontend | React 19, TypeScript, TanStack Start + TanStack Router, Vite 8, Tailwind 3, shadcn/ui (Radix), framer-motion |
| 3D view | Three.js 0.160 + React Three Fiber + drei; orbit gizmo, orthographic / perspective toggle, lighting controls |
| CAD kernel | **OpenSCAD compiled to WebAssembly**, run in a Web Worker in the browser (vendored `src/vendor/openscad-wasm`, GPL); BOSL, BOSL2, MCAD libraries fetched on demand |
| Server | TanStack Start server routes (`src/routes/api/*`), Node; no separate backend service |
| LLM | Vercel AI SDK v6 (`ai`), providers Anthropic, Google, OpenRouter (which covers OpenAI, xAI, Moonshot, DeepSeek, Z.AI) |
| Mesh mode | fal.ai: Hunyuan3D v2 mini turbo, Meshy v6 image-to-3D, SAM-3 for 3D objects, moondream3 captions; Flux Pro for image generation |
| Data | Supabase (Postgres + Auth + Storage). Tables: conversations, messages, meshes, images, previews, profiles, prompts |
| Billing | external "billing service" (Stripe behind it), token packs |
| Telemetry | PostHog, Sentry |

Two product modes share one chat UI:

- **Parametric** (the real text-to-CAD): LLM writes OpenSCAD, browser compiles.
- **Creative** (mesh mode): prompt or image goes to a hosted mesh model; edits
  are re-generations with context. Pinned to Claude Sonnet 4.5 as the chat
  model.

## The parametric agent, exactly

File: `src/server/aiChat.ts` (1,713 lines). Prompt constant
`PARAMETRIC_AGENT_PROMPT`.

Tools (from `shared/chatAi.ts`):

1. `build_parametric_model({ title, version, code })` — the whole OpenSCAD
   script, every call. The **client** executes the tool: compiles in the
   worker, renders a seven-view sheet (ISO, front, back, left, right, top,
   bottom), uploads the PNG to Supabase Storage at a path derived from the
   tool-call id, then returns the compile result. On the server,
   `toModelOutput` downloads that PNG and returns it to the model as an
   `image-data` content part together with the compile message. If the
   upload did not land it falls back to text and never blocks the loop.
2. `answer_user({ message })` — the only way to speak to the user.
3. `create_mesh` — creative mode only.

Loop control: `stopWhen: stepCountIs(60)` for parametric (5 for creative).
The prompt tells the model to keep going write → inspect → rewrite and to
"never finalize just because OpenSCAD compiled".

Prompt rules that are worth copying (they are doing real work):

- Every editable value is a top-of-file variable with an OpenSCAD Customizer
  comment (`// [10:1:200]`, `// [round, square, hex]`, boolean → switch,
  `*_color` suffix → colour picker). The UI parses those comments
  (`shared/parseParameters.ts`, `src/server/parseParameter.ts`) to build the
  slider panel, so **parameter tweaks re-compile locally without another LLM
  call**.
- Use BOSL2 for threads (`screws.scad`, `threading.scad`) and for organic /
  swept shapes (`skin.scad`, `beziers.scad`, `rounding.scad`) instead of
  hand-rolled geometry.
- Attached STL: `import()` it, never recreate; parameters only for the
  modifications; expose rotation_x/y/z.
- Feature checklists per object class (phone case, mug, vehicle) before the
  model is allowed to stop.
- Never claim to have built anything without a tool call in that turn.

Cost control: a per-model USD price table (`MODEL_PRICES`) and a fallback
priced at the most expensive model so an unknown id can never be free;
usage is converted to billing tokens at $0.01 each.

Robustness details: legacy model id remapping (`shared/models.ts`),
dangling-tool-call recovery on the client (`stuckToolRecovery.ts`) and
server (`chatToolPersistence.ts`), library detection that ignores commented
`include` lines (`src/worker/openSCAD.ts`).

## Exports

STL (binary), SCAD source, DXF (2D), GLB preview, GIF turntable. No STEP: the
kernel is a mesh CSG engine, so B-rep output is impossible from this path.
Their paid copilot gets STEP by working inside Onshape / Fusion instead.

## Benchmarks folder

Thirteen prompts with the generated `.scad`, a write-up and a turntable GIF
each: V8 engine, radial engine, turbofan, blisk, twisted vase, knurled knob,
M12 bolt + nut with real threads, honeycomb bracket, NACA 2412 wing, threaded
jar, bevel gears, impeller, herringbone planetary stage. Useful as an
evaluation set for any code-generating CAD agent.

## What running it locally needs

Supabase CLI (local Postgres/Auth/Storage), at least one of
ANTHROPIC_API_KEY / OPENROUTER_API_KEY / GOOGLE_API_KEY, FAL_KEY only for
mesh mode, ngrok for the fal webhook, and a billing service URL (or stub).
`.env.local.template` lists everything. Build and typecheck do not need any
of it.
