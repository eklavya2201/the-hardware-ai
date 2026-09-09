# Agent surface — verbs, nouns, queries

> Three things get labelled, not one. Teams usually label only verbs, and
> then the agent is blind because it can't observe state. **Operations**
> (verbs) — create_via, set_track_width. **Entities** (nouns) — net,
> footprint, pad, layer, body, constraint: what exists, its properties, its
> stable identifier. **Queries** (eyes) — get_net_of_pad,
> list_unrouted_ratlines, get_body_bounding_box. These are what replace
> screenshots. Queries are the ones people skip and they're the ones that
> make it work. — founder, 2026-09-07

This file is the inventory of everything an agent can call — the in-app
schematic/PCB agent (`services/agent.py` tool loop) and an external agent
over HTTP — labelled **verb / noun / query**, with input, output shape and a
token estimate for a typical **20-part board** (measured on the 14-part stub
board — ESP32 + USB-C + LDO + BME280 — placed, routed, with an enclosure;
scaled where the size grows with part count; ~4 chars per token). The nouns
themselves are catalogued in [`ENTITIES.md`](ENTITIES.md).

Branch `agent-queries` (2026-09-08). Guards: `python -m
scripts.regression_agent_queries` (77 checks) and the six standing scripts
listed at the end.

## 1. Agent tools (function-calling schemas in `agent.TOOL_SCHEMAS`)

36 schemas: 22 verbs (13 schematic, 1 template, 5 mechanical, `run_drc`,
`acknowledge_finding`, `allocate_pin`), 14 queries. Every call goes through
`tools.dispatch(project, name, args)`; queries fall through to
`queries.run`, mechanical verbs to `mechanical_tools.dispatch`.

### Verbs (mutate the IR; each returns a result + a `DiffEntry`)

| tool | kind | input | output shape | tokens (typical) |
|---|---|---|---|---|
| `add_component` | verb | mpn, refdes?, value?, x?, y? | refdes, mpn, x/y, **full pin list** [number, name, type], applicationNote, requiredCompanions | 100 (resistor) – 480 (ESP32 module, 1,911 chars) |
| `add_component_array` | verb | mpn, count, rows?, cols?, pitch_mm?, … | refdes range, array_key, pins, pcb_grid | 150–450 |
| `arrange_grid` | verb | refdeses[], rows?, cols?, pitch_mm? | array_key, rows, cols, pitch, extent | 40 |
| `set_array_pitch` | verb | array_key, pitch_mm | old/new pitch, extent | 40 |
| `connect_matrix` | verb | refdes_prefix, rows, cols, row_pin, col_pin, … | wired, row_nets, col_nets, note | 60–120 |
| `record_requirements` | verb | items[{description, quantity, mpn?, category?, unavailable?}] | recorded, requirements[], note | 80–250 |
| `set_form_factor` | verb | shape, diameter/width/height, layers?, sides?, points? | form_factor, note | 80 |
| `delete_component` | verb | refdes | refdes | 10 |
| `connect` | verb | net_name, pins[[refdes, pin]] | net, added[], total_pins | 20–40 |
| `disconnect` | verb | net_name, pins[[refdes, pin]] | net, removed, remaining_pins | 20 |
| `set_value` | verb | refdes, value | refdes, old, new | 15 |
| `set_property` | verb | refdes, key, value | refdes, key, value | 15 |
| `run_drc` | verb (rewrites `project.issues`) | — | issue_count, errors, warnings + the compact `list_issues` view (paged, errors first) | 220 (8 issues) – 375 max; **was** a dump of every issue with every field: 250 on 8 issues, ~1,100 on a 20-part board with 20 findings |
| `acknowledge_finding` | verb | issue_id, reason | acknowledged, reason | 20 |
| `apply_template` | verb | template_name, net_map? | template result (parts added, nets, REQUIRED_NEXT_STEP) | 100–400 |
| `derive_from_board` | verb (mechanical) | — | outline, holes, cutouts, enclosure | 150–300 |
| `set_enclosure` | verb (mechanical) | kind, wall_thickness?, standoff_height?, clearances? | enclosure | 40 |
| `set_board_outline` | verb (mechanical) | shape, width?, height?, corner_radius? | outline | 40 |
| `add_mounting_hole` | verb (mechanical) | x, y, diameter?, standoff? | hole (with id) | 30 |
| `add_connector_cutout` | verb (mechanical) | x, y, width, height, edge?, refdes? | cutout (with id) | 40 |

Dispatchable but not offered to the model (no schema; used by the UI /
templates): `unacknowledge_finding`, `delete_mounting_hole`, `add_keepout`,
`delete_keepout`, `delete_connector_cutout`, `derive_mounting`, `set_panel`,
`derive_panel`, `clear_panel`.

### Read-only tools that pre-date this round

| tool | kind | input | output shape | tokens |
|---|---|---|---|---|
| `search_parts` | query | query?, category?, limit=10 | count, parts[{mpn, category, package, description (one line ≤ 90 chars), v=[min,max], pins, interfaces, unitCost, alternatives[:3]}] | 70 per row (≈ 700 for 10) — was ~65/row with `jlcStock` and the full description |
| `allocate_pin` | query (read-only, deterministic) | role, count?, mcu_refdes? | mcu, role, pins[{number, name}], safe, note | 40–80 |

### Queries — the eyes (new; `services/queries.py`)

All read-only, all under **`MAX_RESULT_CHARS` = 1,500 chars (~375 tokens)**,
all paged (`offset` / `limit`, answering `total`, `truncated`,
`next_offset`), all with stable identifiers, all degrading to a JSON error
(`{"error": "no such component 'ZZ9'", "known": [first 20], "known_total"}`
or `{"error": "bad arguments …", "expected": {field: type}}`). The agent
may ask **`QUERY_BUDGET_PER_TURN` = 12** per turn; the 13th answers
`{"error": "query budget exhausted", "hint": "act on what you know or ask
the user"}`.

| query | answers | input | output shape | chars measured (stub board) |
|---|---|---|---|---|
| `get_component` | "what is U1, what is on each pin, where is it" | refdes, offset?, limit? | refdes, mpn, value, category, description, footprint, package, body_mm [w,h], height_mm, off_board, pin_count, unconnected_pins, placed{side,x,y,rot,locked}, pins[[number,name,type,net]] (paged) | 515 (R1), 1,482 (U1, 38 pins — page 1) |
| `get_pin_net` | "is this pin on a net?" (pin = number / name / alias) | refdes, pin | refdes, pin, name, type, net, net_role, net_pins | 109 |
| `list_unconnected_pins` | "what is still floating?" supply/ground first | refdes?, offset?, limit? | count, unwired_power_or_ground, by_refdes{}, pins[[pin,name,type]] (paged) | 1,095 |
| `list_components` | roster | offset?, limit? | components[[refdes,mpn,value,category,side]] (paged) | 844 (14 rows) |
| `get_net` | "who is on VCC_3V3, is it routed, which GPIO?" | name, offset?, limit? | net, role, pin_count, mcu_pin, routed, traces, vias, rats, ratsnest_mm, plane, issues[ids], pins[[refdes,pin,name,type]] (paged) | 351 (I2C_SDA), 675 (GND) |
| `list_nets` | roster | offset?, limit? | nets[[name,role,pins,routed]] (paged) | 374 |
| `list_issues` | "what did DRC find?" | severity?, rule prefix?, offset?, limit? | counts{error,warning,info}, open{}, acknowledged, drc_run, issues[{id,sev,rule,msg,refdes?,pin?,net?,src?,ack?}] (paged, errors first) | 332 (errors only), 1,243 (all 8) |
| `list_unrouted_ratlines` | "what is still unrouted, longest first?" | offset?, limit? | count, total_mm, nets, routed, rats[{id "net:U1.12-R3.1", net, from, to, mm}] (paged) | 172 |
| `get_board_summary` | "how big, how many layers, what is where?" | — | board{shape,width,height,thickness,layers,planes,outline_points,ground_pour}, parts{total,top,bottom,unplaced}, parts_bbox, tallest_top, tallest_bottom, holes[:12]+holes_total, routing{}, issues{pcb,mech}, form_factor?, enclosure | 780 |
| `get_part_envelope` | "what space does U1 take, how tall?" (the STEP export's boxes) | refdes | placed, side, x, y, rot, footprint, rects[[x0,y0,x1,y1]], bbox, size_mm, height_mm, off_board, locked — or body/courtyard size when unplaced | 301 |
| `list_holes` | mounting holes | offset?, limit? | holes[{id,x,y,d,plated,standoff}] (paged) | 250 |
| `get_hole` | one hole + nearest part clearance | id | id, x, y, d, plated, standoff, nearest_part{refdes, clearance_mm}, issues | 150 |
| `list_cutouts` | enclosure-wall openings | offset?, limit? | cutouts[{id,refdes,edge,x,y,width,height}] (paged) | 148 |
| `list_keepouts` | keep-out zones | offset?, limit? | keepouts[{id,layer,x,y,width,height,reason}] (paged) | 44 (none) |

Average typical answer: **509 chars ≈ 130 tokens**. Query schemas cost
7,447 chars (~1.9k tokens) per call in the tool list. Geometry comes from
`geometry.solid_rects` and `mechanical_derive.body_height_mm` — the same
sources the STEP export and the overlap rule read — so the eyes and the fab
outputs cannot disagree.

## 2. HTTP endpoints (`/api`, for an external agent)

Auth: session cookie (owner) — reads marked **share** also accept
`?share=<token>` through `_readable_project` (404 for anyone else, never
403). Sizes: `GET /projects/{id}` on the stub board is **52k chars ≈ 13k
tokens** (IR 8.7k chars, board 27k chars — grows with every trace — chat,
BOM, issues, mechanical); a 20-part routed board is ~70k chars ≈ 17k tokens.

### Projects (`api/projects.py`)

| method + path | kind | in | out | tokens |
|---|---|---|---|---|
| `GET /projects` | query | — | Project[] (every project in full) | 13k × N |
| `POST /projects` | verb | name, kind | Project | 13k |
| `GET /projects/{id}` (share) | noun dump | — | the whole Project | **13k** |
| `GET /projects/{id}/history`, `POST …/revert/{commit}` | query / verb | — | commits / Project | 200 / 13k |
| `POST /projects/{id}/chat` | verb (agent turn, SSE) | text | events: user_message, delta, tool_use, error, note, agent_truncated, assistant_message, done, snapshot | 13k+ |
| `POST …/messages/{mid}/diff` | verb | accept / reject | Project | 13k |
| `POST …/components/{refdes}/property` | verb | key, value, target ir/board | Project | 13k |
| `GET …/summary` (share) | query (derived) | — | DesignSummary: components, pinMapping, powerArchitecture, warnings, missingItems | 1–3k |
| `GET …/nets/{name}` | query | — | NetDetail: endpoints, mcuRefdes/Pin, role, routed, traces/vias/rats, issues (full rows) | 200–600 |
| `POST …/run-drc` | verb (rules + LLM critic) | — | Project | 13k |
| `POST …/issues/acknowledge` / `unacknowledge` | verb | issue_id, reason | Project | 13k |
| `POST …/bom/refresh-prices` | verb (network) | — | fetched/failed/… | 50 |
| `GET …/export/readiness` | query (derived) | boards? | manufacturability report: ready, blockers, per-part footprint confidence, sourcing | 500–1,500 |
| `GET …/export/netlist` / `bom` / `kicad_pcb` / `kicad_project` / `gerbers` / `panel-gerbers` | export (file) | force?, format? | text / zip | n/a |
| `GET …/panel` | query (derived) | — | recorded / suggested / max-fit panel | 200 |
| `GET …/order` | query (derived) | boards? | BOM lines, links, readiness | 1–2k |
| `POST/DELETE …/share` | verb | — | token, path | 30 |
| `DELETE /projects/{id}` | verb | — | — | 5 |
| **`GET …/q`** (share) | query index | — | {queries: {name: one line}, usage} | 250 |
| **`GET …/q/{name}?arg=…`** (share) | **query** | the query's args as query-string (ints coerced) | the query's answer (≤ 1,500 chars) | ≤ 375 |

### PCB (`api/pcb.py`)

| method + path | kind | out | tokens |
|---|---|---|---|
| `GET …/pcb` | noun dump | Board (components with full footprints, traces, vias, rats) | **7k** (stub), 15k+ routed 20-part |
| `POST …/pcb/auto-place` / `auto-route` / `finalize-routing?profile=` | verb | Board | 7k+ |
| `GET …/pcb/constraints` | query (derived) | PlacementConstraint[] | 100–300 |
| `POST …/pcb/components/{refdes}/edit` | verb | Board | 7k+ |
| `POST …/pcb/run-drc` / `kicad-drc?profile=` | verb | Project / {project, ran, count} | 13k |
| `GET /pcb/fab-profiles` | query | profiles | 150 |

### Mechanical (`api/mechanical.py`)

| method + path | kind | out | tokens |
|---|---|---|---|
| `GET …/mechanical` | noun dump | MechanicalIR (outline, holes, keepouts, cutouts, enclosure, panel) | 200–600 |
| `POST …/mechanical/tool` | verb | {tool, args} → Project | 13k |
| `GET …/cad-brief` | query (derived, for Synqit CAD) | entities (board, holes, parts envelopes, cutouts, enclosure), openscad prelude, prompt_block | 1–2k entities + 1k prelude |
| `GET …/export/step` / `fcstd` / `idf`, `POST …/render`, `GET …/render.png` | export / render | file / PNG | n/a (a render is the thing queries replace) |
| `POST …/mechanical/import-idf` | verb | Project | 13k |

### Library and the rest

| method + path | kind | out | tokens |
|---|---|---|---|
| `GET /library` | noun dump | every LibraryPart (23,941) | ~2M — never for an agent |
| `GET /library/index` | query (compact) | mpn/category/description index | large; the agent uses `search_parts` |
| `GET /library/footprint/families`, `POST …/generate` | query / verb | family schemas / .kicad_mod | 300 / 1k |
| `POST /library/ingest`, `POST /library/parts`, `DELETE /library/parts/{mpn}` | verb | proposal / part | 1k |
| `GET …/build-guide`, `POST …/generate`, `DELETE`, `GET …/export/build-guide` | noun / verb / export | BuildGuide | 1–2k |
| `GET …/spice-netlist`, `POST …/simulate` | export / verb | netlist / results | 1k |
| `GET …/firmware`, `POST …/generate`, `PUT …/files`, `POST …/files/regenerate`, `POST …/feature`, `DELETE` | noun / verb | Firmware | 2–10k |
| `/auth/*`, `/admin/*`, `/events`, `/feedback`, `/search-events/*` | account / analytics | — | — |

## 3. The gaps this round closed

Before `queries.py`, the in-app agent could observe its own design through
exactly three things: the result of the verb it just called, the per-turn
`CURRENT DESIGN STATE` system message (every part, every net with its
first six pins — 755 chars on the stub, growing with the design), and
`run_drc`'s dump. It could **not** ask, without dumping the whole IR or
looking at a render:

- which net a given pin of a part added in an *earlier* turn is on (the
  state snapshot truncates nets at six pins; the model guessed and R21
  "pin on two nets" was the sweep's single most common defect);
- which pins are still unconnected — the class every "placed but not wired"
  review (R60/R61/R66) is about;
- anything about the **board**: size, layer count, planes, holes, what is
  on which side, how tall the tallest part is, the parts' bounding box;
- what is **unrouted** and how long the airwires are;
- a part's real footprint envelope and height (the numbers the placer, the
  overlap rule and the STEP export use);
- the enclosure's cutouts and keep-outs;
- the verifier's findings *compactly* (the old `run_drc` returned every
  field of every issue — `citationUrl: null` and all).

An external agent had it worse: the whole `Project` (13k+ tokens), the
`summary`, one `NetDetail` at a time, and the CAD brief. There was no way
to ask "is U1.7 on a net" over HTTP without downloading everything.

Still open (documented, not built): a pad-level query (`get_pad`: world
position, layer, drill, net — pads are addressable as `refdes.pin` but have
no query of their own), a trace query (traces and vias carry no id — see
`ENTITIES.md`), silkscreen/courtyard queries, and DRC-marker geometry
(where exactly two bodies overlap). None of these blocked the loop check.

## 4. Prompt + budget

The system prompt gained one block (between `ASK, DON'T GUESS` and `DO NOT
STOP EARLY`) — "ask, don't guess" (query before connecting a pin not added
this turn, `list_unconnected_pins` after wiring an IC, `list_issues` after
every `run_drc` and before declaring done, `get_board_summary` before
reasoning about the PCB; **never ask for, describe, or reason from a
rendered image**) and "NO UNVERIFIED STATUS CLAIMS" (a "0 errors" / "fully
routed" / "all connected" / "fits" sentence needs a query behind it this
turn; `agent._flag_unverified_status_claims` flags one that has none as an
`LLM_unverified_status` warning and never rewrites the text). The fix-up
prompt tells the model to `get_pin_net` before moving a pin and to read
`list_issues` after `run_drc`.

| | before (cad-sso 9f96589) | after | budget |
|---|---|---|---|
| `SYSTEM_PROMPT` | 42,693 chars | 44,665 chars | added **1,972** (rule block) + 118 (fix-up hint) = **2,090 < 2,500** |
| system message (prompt + 564-part core index) | 91,494 chars | 93,466 chars | ≤ 120,000 (`regression_prompt_budget`, 6/6) |
| tool schemas JSON | 16,351 chars (22 verbs) | 21,454 chars (+ 5,103 → 7,447 after paging args; 36 tools) | ≤ 8,000 for the query schemas |
| per-turn design-state message | 755 chars (stub) | unchanged | — |

`SYNQIT_AGENT_QUERIES=0` restores the before-state exactly (query tools
hidden, rule block stripped) — that is the benchmark's baseline.

### What observing state costs, before vs after (stub board, chars)

| question | before — the only way | chars | after — the query | chars |
|---|---|---|---|---|
| is U1.3 on a net? | read the IR (`GET /projects/{id}`, or re-add the part) | 8,699 (IR) / 51,973 (project) | `get_pin_net` | **109** |
| what is on U1's pins? | the `add_component` result, only in the turn that added it | 1,911 | `get_component` | 1,482 (page 1 of 38 pins) |
| what did DRC find? | `run_drc` dump | 1,005 (8 issues; ~4,500 on 20 findings) | `list_issues(severity=error)` | **332** |
| what is unrouted? | `GET …/pcb` | 27,136 | `list_unrouted_ratlines` | **172** |
| how big is the board, how many layers? | `GET …/pcb` | 27,136 | `get_board_summary` | **780** |
| how much room does U1 take? | `GET …/pcb` + `solid_rects` by hand | 27,136 | `get_part_envelope` | **301** |
| which parts are unwired? | read the IR | 8,699 | `list_unconnected_pins` | 1,095 |

## 5. Benchmark

`python -m scripts.bench_agent_tokens` runs the real agent (Azure gpt-5.1)
on the three loop-check prompts plus two edit turns on the rain-alert
result, in both modes, and records `agent.USAGE` (prompt / cached /
completion tokens summed over the turn, LLM calls, tool calls, queries,
tool-output characters), wall-clock, and the IR-derived quality (verifier
errors, warnings, R43 shortfalls, unconnected pins, edit-turn change
present). Results: `bench/results/<timestamp>.json` + `.md`.

Run 2026-09-08 06:14 UTC, `bench/results/20260908T061442Z.{json,md}`,
model `gpt-5.1` (Azure), 1 repetition, 5 turns per mode, all 10 runs
completed, none crashed. `prompt_tok` / `cached_tok` / `completion_tok` are
summed over every LLM call of the turn (build + fix-up turns); `secs` is
wall-clock; quality columns are read from the IR after the turn.

| mode | prompt | prompt_tok | cached_tok | completion_tok | llm_calls | tool_calls | queries | tool_out_chars | secs | errors | warnings | r43 | unconnected | change_ok |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | rain-alert | 347892 | 306048 | 1771 | 9 | 30 | 0 | 9733 | 68.0 | 0 | 4 | 0 | 16 | — |
| baseline | rain-edit-button | 345269 | 338944 | 795 | 9 | 12 | 0 | 7414 | 43.6 | 0 | 4 | 0 | 15 | False |
| baseline | rain-edit-led | 308338 | 303744 | 567 | 8 | 7 | 0 | 3334 | 80.2 | 0 | 6 | 0 | 14 | True |
| baseline | counter-7seg | 1171695 | 1159040 | 2390 | 30 | 43 | 0 | 12476 | 123.0 | 0 | 0 | 0 | 16 | — |
| baseline | smartwatch | 1029545 | 1014144 | 3436 | 25 | 64 | 0 | 28156 | 128.3 | 0 | 10 | 0 | 14 | — |
| queries | rain-alert | 279692 | 274944 | 1884 | 7 | 32 | 0 | 10059 | 55.4 | 0 | 4 | 0 | 14 | — |
| queries | rain-edit-button | 523279 | 517376 | 1298 | 13 | 15 | 3 | 5798 | 62.7 | 0 | 2 | 0 | 14 | True |
| queries | rain-edit-led | 285469 | 282368 | 452 | 7 | 8 | 0 | 3426 | 29.4 | 0 | 2 | 0 | 13 | True |
| queries | counter-7seg | 241562 | 238208 | 1542 | 6 | 30 | 0 | 6927 | 39.1 | 0 | 1 | 0 | 17 | — |
| queries | smartwatch | 301881 | 292480 | 3153 | 7 | 64 | 0 | 20410 | 70.0 | 0 | 10 | 0 | 17 | — |
| **baseline total** | | 3202739 | 3121920 | 8959 | 81 | 156 | 0 | 61113 | 443.1 | 0 | 24 | 0 | 75 | 1/2 |
| **queries total** | | 1631883 | 1605376 | 8329 | 40 | 149 | 3 | 46620 | 256.6 | 0 | 19 | 0 | 75 | 2/2 |
| **queries vs baseline** | | -49% | -49% | -7% | -51% | -4% | n/a | -24% | -42% | n/a | -21% | n/a | +0% | |

Per-call detail (from the JSON): prompt tokens **per LLM call** are
38.4–41.2k in baseline and 40.0–43.1k with queries (+~2k for the 14 schemas
and the rule block); **uncached** prompt tokens — the part that is actually
billed at full price — were 80,819 baseline vs 26,507 with queries (−67%).
The query-mode `rain-alert` turn ended with **2 `LLM_unverified_status`
warnings** (the model wrote a status claim without reading `list_issues`),
every other run 0.

**Reading it honestly.** Queries mode used half the prompt tokens and half
the wall-clock, with equal or slightly better output — but the saving did
**not** come from the queries being called: the model asked **3 queries in
one edit turn and none in the four other turns** (every part was added in
the same turn, so the "ask before connecting a pin you did not add this
turn" rule never applied, and it skipped `list_issues` on the way out).
The saving came from **fewer agent turns**: two baseline builds ran long
(smartwatch 25 calls, counter 30 calls = `MAX_AGENT_TURNS`, i.e. a
truncated build) while every query-mode build finished in 6–7 calls, and
each call carries the whole ~40k-token context, so turn count dominates.
With one repetition per prompt that difference is inside normal LLM
run-to-run variance (the earlier review rounds saw the same prompt swing
between clean and broken), so the −49% is a promising single sample, not a
measured property of the query surface. What the queries *did* measurably
do: the one turn that used them (`rain-edit-button`, 3 queries) delivered
the asked change (`pullup: True`) where the baseline did not (`pullup:
False`) — and cost the most tokens of the query-mode set (13 calls). Quality
otherwise: 0 errors in every run, warnings 24 → 19, R43 shortfalls 0 → 0,
unconnected pins 75 = 75 (that metric counts unused MCU GPIOs and is noise
here), both edit asks present (1/2 → 2/2). Completion tokens −7% is flat.
Conclusion: the surface is cheap to carry (+2k tokens per call, −67%
uncached tokens overall in this sample) and the eyes work when used; the
prompt has not yet made the model *use* them during a fresh build, and the
`LLM_unverified_status` warnings show exactly where — that is the next
prompt iteration, to be judged by a ≥2-repetition run, not by this table.

## 6. Assumptions and decisions

- **Queries are tools, not a second channel.** They share `tools.dispatch`
  and the same `tool_use` SSE event, so the UI's "What I did" trace shows
  them and the fix-up loop can use them. They return no `DiffEntry`.
- **One cap, one place.** `queries.MAX_RESULT_CHARS` (1,500) is enforced
  by `queries.run` after the page logic, trimming the page and stamping
  `truncated`/`next_offset`; the founder's "< 400 tokens typical" holds
  with an average of ~130.
- **`run_drc` stays a verb** and now returns the compact view; a bare
  `run_drc` does **not** count as having read `list_issues` for the
  status-claim check — the rule is "a query in THIS turn", and the prompt
  says to call `list_issues` after `run_drc`.
- **`search_parts` and `allocate_pin` were already read-only** and are
  labelled queries here; `search_parts` was compacted (one-line
  description, no fake `jlcStock`, ≤ 3 alternatives, pin count and
  interfaces added).
- **Rats and traces get derived identifiers, not model fields.** A rats
  line is named by its pads (`net:U1.12-R3.1`, nearest pad within 0.6 mm
  — routers snap to a grid); traces/vias are left without ids (adding a
  field to `Trace` would ripple into the frontend IR types and the
  exporters, which this round must not touch).
- **The stub path never queries** (it is a fixed plan), so the
  status-claim post-check runs only on the OpenAI path.
- **Untouched by construction** (verified by `git diff --stat cad-sso..HEAD`
  in the regression script): `verifier*`, `placement*`, `routing*`,
  `export*`, `mechanical_bridge.py`, `pricing*`. Everything the queries need
  from them is imported.
- KiCad is absent on the dev box: `footprints.for_mpn` reads the committed
  cache; on a box with KiCad an uncached MPN would resolve through the
  bridge and persist to that cache (the library cache the purity rule
  allows). No query calls the network or an LLM (guarded).

## 7. Verify

From `backend/` with the venv python and `PYTHONIOENCODING=utf-8`:

```
python -m scripts.regression_agent_queries      # 77 checks
python -m scripts.regression_prompt_budget      # 6
python -m scripts.regression_prompt_to_product  # 12
python -m scripts.regression_honest_surfaces    # 32
python -m scripts.regression_design_summary
python -m scripts.regression_pin_safety
python -m scripts.regression_says_vs_builds
python -m scripts.bench_agent_tokens --reps 1   # real LLM spend
```
