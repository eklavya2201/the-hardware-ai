# Synqit + Synqit CAD: one product, two engines

Synqit (Cursor-for-hardware) turns a prompt into a schematic, PCB, BOM,
enclosure, firmware and build guide. Synqit CAD (`app/` here) turns a prompt
into a parametric 3D model with sliders and STL export. This document is how
they are joined, and the rule that governs every interface between an agent
and either engine.

## The rule: verbs, nouns, queries

Every agent surface labels three things, not one.

| Kind | What it is | Examples |
|---|---|---|
| **Operations (verbs)** | actions that change state | `build_parametric_model`, `auto_place`, `add_component`, `connect` |
| **Entities (nouns)** | what exists, its properties, its stable id | net, footprint, pad, mounting hole, part envelope, cutout, parameter |
| **Queries (eyes)** | cheap, exact reads that replace screenshots | `cad-brief`, model facts (bbox, bodies), `get_net_of_pad`, `list_unrouted_ratlines` |

Queries are the part teams skip and the part that makes an agent accurate on
any decent model: numbers cost a few tokens, a screenshot costs thousands and
still cannot tell 40 mm from 44 mm. Pictures are for what numbers cannot
express (a hole on the wrong face), never for measuring.

Synqit already works this way on the electronics side (typed IR, 96
deterministic rules, `search_parts`, summary and net queries). This
integration brings the same discipline to the 3D side.

## What is wired today (2026-09-08)

### 1. Board → Studio (Synqit exposes nouns, the CAD agent consumes them)

`GET /api/projects/{id}/cad-brief` on the Synqit backend returns three views
of the same facts, built from the very payload the STEP, FreeCAD and Blender
bridges use:

- `entities`: outline (shape, size, corner radius, polygon), thickness,
  mounting holes, every placed part as an envelope with height and layer,
  connector openings with edge and size, enclosure clearances. About 3 KB
  for a 17-part board.
- `openscad`: a prelude with named variables and `module synqit_board()`
  (PCB slab with holes plus part envelopes) and `synqit_hole_pattern(d, h)`.
- `prompt_block`: three sentences of prose plus the rules (paste the prelude
  unchanged, call `synqit_board()`, never redraw the board, keep every cutout
  open, expose only your own dimensions).

In the studio, the **Board** button lists the user's Synqit projects and
inserts a project's brief into the composer; opening the studio with
`?project=<id>` inserts it automatically. The agent prompt has a "Synqit
board" section that enforces the rules. Verified: the model kept the prelude,
called `synqit_board()`, and built a two-part snap-fit enclosure with
standoffs on the real holes and the J1 opening on the right edge, 9 sliders,
in 49 s.

### 2. Model facts (the CAD engine answers a query after every build)

After compiling, the browser measures the STL and sends one line back with
the tool result: bounding box in mm, lowest Z, triangle count, number of
separate bodies. The schema carries it as `facts`. The prompt tells the model
to check the numbers against the request before looking at the seven-view
sheet. A base plus lid reads as 2 bodies; a floating feature shows up as a
third.

### 3. Studio tab inside the Synqit editor

The Synqit editor has a **Studio** tab next to Schematic, PCB and 3D View,
embedding the studio for the current project (`?project=<id>&embed=1`, no
studio sidebar). The parameter sliders are simply that tab's right-hand
panel. The 3D View toolbar also has a Studio button. `VITE_CAD_APP_URL`
picks the studio origin (default `http://localhost:3000`).

### 4. Identity

One Google client id serves both apps; "Continue with Synqit" hands a
signed-in Synqit user to the studio through a 5-minute token. Locally the
studio runs in guest mode so evaluation needs no login. Details in
`LOCAL_SETUP.md`.

## Where the code lives

| Piece | Repo / path |
|---|---|
| `cad-brief` endpoint and `cad_brief.py` | Cursor-for-hardware, branch `cad-sso` (`backend/app/api/mechanical.py`, `backend/app/services/cad_brief.py`) |
| Studio tab, StudioFrame, 3D toolbar button | same branch, `frontend/src/components/editor/StudioFrame.tsx`, `EditorTopBar.tsx`, `Editor.tsx`, `BoardView3D.tsx` |
| Board button, auto-insert | `app/src/components/parts/BoardButton.tsx` |
| Model facts | `app/src/utils/meshFacts.ts`, `app/src/components/chat/ChatSession.tsx`, `app/shared/chatAi.ts` |
| Agent prompt rules | `app/src/server/aiChat.ts` (`PARAMETRIC_AGENT_PROMPT`) |
| Embed mode | `app/src/components/Layout.tsx` |

The `synqit/` folder in this repo is a git submodule pointing at the
`cad-sso` branch of eklavya2201/synqit, so the whole product can be checked
out from one place. `git submodule update --init` after cloning.

## Next queries worth adding (in order of payoff)

1. **Studio → Synqit write-back**: `POST /projects/{id}/files` with the STL and
   SCAD, so the enclosure shows up in the Order panel and the build guide.
2. **Fit check as a query**: the studio asks Synqit "does this enclosure
   interior clear every part envelope by X mm?" and gets a list of
   collisions with refdes, instead of the model eyeballing it.
3. **Synqit library as a Parts source**: the Parts button searches Synqit's
   23,941-part library (with curated heights and footprints) before Octopart.
4. **Electronics-side queries for an external agent**: `get_net_of_pad`,
   `list_unrouted_ratlines`, `get_footprint_bbox` exposed through the same
   `/api` so a Claude or Codex session can drive the PCB without screenshots.
