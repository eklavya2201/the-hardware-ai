# Can Synqit do this? Feasibility, 2026-09-07

Question asked: copy Adam's UI and backend into a repo and see whether we can
do what they do. Short answer: **the text-to-CAD part, yes, and mostly with
what Synqit already has; the copilot-inside-Onshape part, no, and it is the
wrong fight.** Details below, then a concrete plan.

## 1. What can actually be copied

| Piece | Public? | Can we reuse the code? |
|---|---|---|
| CADAM web app (UI + server routes + agent) | yes, GPL-3.0 | Only if the product that ships it is also GPL-3.0. Synqit is closed source, so **do not paste CADAM code into Cursor-for-hardware**. Reading it and re-implementing the ideas is fine. |
| OpenSCAD-WASM | yes, GPL-2.0+ | Same problem, and Synqit already has a B-rep kernel (OpenCASCADE via CadQuery) that produces STEP, which OpenSCAD cannot. |
| Adam copilot (sandbox, workers, skills, Onshape/Fusion extensions) | no | Nothing to copy. Only the behaviour is observable. |
| Marketing copy, screenshots, logos | yes | Facts and structure only. Verbatim copy or their name/branding on our site is off limits. |

So the honest scope is: **re-implement the loop, not fork the app.**

## 2. Where Synqit already stands (read from the code, not from memory)

- `app/services/mechanical_bridge.py`: CadQuery / OpenCASCADE builds the
  board solid plus enclosure from the mechanical IR and exports **STEP**.
- `app/services/freecad_bridge.py`: writes an editable `.FCStd` with named
  features (Board, Hole_1, Part_U1, Case, Standoff_3, Cutout_J1).
- `app/services/blender_bridge.py`: Cycles render of the real geometry.
- Routes exist for `/export/step`, `/export/fcstd`, `/export/idf`, `/render`,
  and a `mechanical/tool` endpoint the agent can call.
- Staging already reports `mechanical_mode`, `freecad_mode`, `blender_mode`
  all available; prod has none of it yet (deploy pending).
- Bring-your-own-model with a per-user encrypted key and a provider list is
  live on staging. That is Adam's "pick the model" feature.

What Synqit does **not** have that CADAM has:

1. A **code-writing CAD agent with a screenshot loop.** Our enclosure is
   derived deterministically from the board IR (good: it is always right about
   the board), but a user cannot say "add a lanyard loop and a 15° kickstand"
   and get it. CADAM's whole product is that sentence.
2. **Parametric controls without an LLM round-trip.** Their Customizer-comment
   convention gives sliders for free.
3. **Multi-view inspection** fed back to the model. We render once for the
   user, not for the agent.

## 3. The proposal: "CadQuery + screenshot" inside Synqit

Same two-tool design as Adam, but on our kernel, so the output is STEP and
FCStd rather than STL.

- Tool 1 `build_case_script(code)`: the model writes a **CadQuery** Python
  script against a fixed prelude that already contains the board outline,
  hole positions, tallest-part envelopes and connector cutouts as variables
  (we own these, the model must not guess them). Run it in a subprocess with
  a timeout inside the existing CadQuery image; on success export STEP and
  render six views with the Blender bridge or a cheap OCC offscreen render.
- Tool 2 `answer_user(message)`.
- Loop budget 20 to 30 steps (theirs is 60 with a mesh kernel that compiles
  in milliseconds; OCC is slower, so cap lower and cache).
- Parameters: adopt a header convention (`wall = 2.0  # [1.2:0.2:4]`) parsed
  into the same slider panel, re-executed locally without the LLM. That maps
  one-to-one onto the FreeCAD named features.
- Verification stays ours: after every build, the existing mechanical
  verifier (clearance to tallest part, connector reachability, hole match) runs
  and its findings go back to the model as text alongside the images. This is
  the thing Adam cannot do for a PCB because they do not have the board.

Effort estimate (one engineer, using the bridges that already exist):

| Step | Days |
|---|---|
| Prelude generator from mechanical IR + sandboxed CadQuery runner + STEP out | 2 |
| Six-view render per build (Blender bridge exists; add camera presets) | 1 |
| Agent loop with the two tools, wired through `llm_config.runtime_for_user` | 2 |
| Parameter header convention, parser, slider panel, local re-run | 2 |
| Regression script with 8 to 10 prompts (reuse CADAM's benchmark prompts adapted to enclosures) | 1 |
| Total | about 8 working days |

Risks: CadQuery scripts fail more often than OpenSCAD (fillets on bad edges,
boolean failures). Mitigation is the loop itself plus a library of
known-good helper functions in the prelude (rounded box, snap lip, screw
boss, vent slots, connector cutout) so the model composes rather than
invents.

## 4. What not to do

- Do not chase the Onshape / Fusion copilot. That requires their extensions,
  a sandbox fleet and an integrations layer (they use Composio); it also
  means living inside someone else's CAD, which is the opposite of Synqit's
  "one prompt, all six outputs" pitch.
- Do not switch the kernel to OpenSCAD for the enclosure. We would lose STEP,
  lose the editable FreeCAD document and inherit GPL.
- Do not vendor CADAM's UI components. shadcn/Radix are MIT and we already
  have our own Tailwind system; the value is the interaction pattern, not the
  files.

## 5. Things worth stealing beyond CAD

- **Per-model price table with an expensive fallback** so an unknown model id
  can never be billed for free. Cheap to add to `llm_config`.
- **"Never say you built something unless the tool ran this turn."** A
  one-line prompt rule that removes a whole class of false claims.
- **Nightly memory consolidation and a task timeline** are product ideas for
  later, not for the 10-day launch.
- Their **benchmark folder format** (prompt, source, write-up, turntable GIF
  per case) is a good shape for Synqit's own public eval set.

## 6. Decision needed from the founder

1. Approve the CadQuery-plus-screenshot enclosure agent as post-launch work
   (about 8 days), or park it.
2. Confirm nobody merges CADAM source into the company repo (GPL).
