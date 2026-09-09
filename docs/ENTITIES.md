# Entities — the nouns an agent can refer to

Every entity the schematic / PCB / mechanical agent (and an external agent
over `/api/projects/{id}/q/*`) can name, its **stable identifier**, its
properties, and which query answers for it. Hand-checked against
`backend/app/models.py` on branch `agent-queries` (2026-09-08); the model
file is the truth, this is the index. Units: mm; the board/mechanical frame
is Y-up, origin bottom-left; the schematic canvas is in symbol units.

Verbs and queries are in [`AGENT_SURFACE.md`](AGENT_SURFACE.md).

## Identifier rules

- An identifier is **stable** when it survives a save, a re-place, a
  re-route and a re-run of DRC, and is the thing the verbs and queries take.
- `refdes` (component) and `net.name` are the two primary keys of the
  design; a pin is addressed as **`refdes.pin_number`** everywhere (nets
  store `(refdes, pin_number)` pairs; `connect` / `get_pin_net` also accept
  a pin *name* or alias and canonicalise to the number).
- Mechanical items carry `<prefix>-<8 hex>` ids minted at creation
  (`mh-`, `ko-`, `cut-`) and keep them through edits.
- A verifier finding's `id` is `<RULE>:<subject>` (`R21:U1.7`,
  `PCB_R03:USB_CC2`, `MECH_R04:mh-7da50829:outline`) — stable while the
  finding persists, so acknowledgements key on it.

## The design (`Project`, one per project id)

| entity | model | stable id | properties | query |
|---|---|---|---|---|
| **Project** | `Project` | `id` (URL) | name, kind (hardware / hardware-software), status, layers, componentCount, drcErrorCount, updatedAt, `ir`, `board`, `mechanical`, `bom`, `issues`, `chat`, `firmware`, `buildGuide`, shareToken (owner only) | `GET /projects/{id}` (whole dump); prefer the queries below |
| **SchematicIR** | `SchematicIR` | singleton in the project | components, nets, wires, requirements, arrays, form_factor, acknowledgements, partConstraints, buildIncomplete | `list_components`, `list_nets` |

### Schematic nouns

| entity | model | stable id | properties | query |
|---|---|---|---|---|
| **Component** | `Component` | **`refdes`** (U1, R3, J2…; unique per project) | mpn, value, description, footprint (KiCad `Lib:Name`), datasheetUrl, x/y/rotation (schematic canvas), pins[] | `get_component`, `list_components`, `get_part_envelope` |
| **Pin** | `Pin` | **`refdes.number`** (`number` is the package pin; `name` is the function, e.g. `IO21`) | number, name, side (symbol side), type (`power_in`, `power_out`, `input`, `output`, `bidirectional`, `passive`, `ground`, `unspecified`) | `get_pin_net`, `get_component` (pins page), `list_unconnected_pins` |
| **Net** | `Net` | **`name`** (canonical names: GND, VBUS, VBAT, VCC_3V3, I2C_SDA…) | pins[(refdes, pin_number)]; derived: role power/gnd/signal (`tools._net_role`), routed state, MCU pin reached | `get_net`, `list_nets` |
| **Wire** | `Wire` | none (rendered from nets; the canvas draws stubs + labels) | net, segments[Point] | — (documented gap; wires are not addressable and nothing needs them to be) |
| **DesignRequirement** | `DesignRequirement` | `key` (`led_3mm`, or `unavailable_N`) | description, quantity, mpn / category, unavailable — checked by R43 | in `CURRENT DESIGN STATE`; no query (small) |
| **ComponentArray** | `ComponentArray` | `key` (`D1-D100`) | mpn, refdeses[] (row-major), rows, cols, grid, pitch_mm | `set_array_pitch(array_key)`; no query (small, in design state) |
| **FormFactorSpec** | `FormFactorSpec` | singleton (`ir.form_factor`) | shape rect/round/polygon, diameter/width/height, sides / points, note, layers | `get_board_summary().form_factor` |
| **PartConstraintRecord** | `PartConstraintRecord` | none (kind + target + value is the natural key; R73 reads them) | kind value/mount/size/mpn, target, value, text, tolerance | — (documented gap) |
| **Acknowledgement** | `Acknowledgement` | **`issue_id`** | rule, reason, at | `list_issues` rows carry `ack: true` |

### Verification nouns

| entity | model | stable id | properties | query |
|---|---|---|---|---|
| **VerifierIssue** | `VerifierIssue` | **`id`** = `<rule>:<subject>` | severity error/warning/info, message, refdes, net, pinNumber, rule (e.g. `R21_pin_on_two_nets`, `PCB_R03_unrouted_nets`, `MECH_R04_hole_collision`, `KICAD_*`, `LLM_*`), source ir/verifier/pcb/manual/llm/kicad/erc/ipc/mechanical, citation/citationUrl (critic) | `list_issues`, `get_net().issues`, `get_hole().issues` |
| **Rule** | (function in `verifier.ALL_RULES`, `pcb_verifier`, `mechanical_verifier`) | rule id prefix `R01`…`R81`, `PCB_R01`…`PCB_R10`, `MECH_R01`…`MECH_R08` | 78 schematic + 9 PCB + sync + 8 mechanical (recount from code before quoting) | `list_issues(rule=prefix)` |

### Board nouns (`Board`, one per project once placed)

| entity | model | stable id | properties | query |
|---|---|---|---|---|
| **Board** | `Board` | singleton (`project.board`) | width, height (bbox), shape rect/round/polygon, outline_points, layer_count 2/4, planes, components, traces, vias, rats, issues, auto_placed, auto_routed, has_ground_pour; thickness is `mechanical_bridge.BOARD_THICKNESS` (1.6) | `get_board_summary` |
| **Layer** | `BoardLayer` / `PlaneLayer` | literal name: `top`, `bottom`, `In1`, `In2` | copper; `In1`/`In2` exist only on 4-layer boards and carry a plane | `get_board_summary().board.layers/planes` |
| **Plane** | `Plane` | `layer` (one plane per inner layer) | net | `get_board_summary().board.planes`, `get_net().plane` |
| **PlacedComponent** | `PlacedComponent` | **`refdes`** (same key as the schematic component) | mpn, x, y (footprint ANCHOR — pin 1 on pin-1-anchored footprints; the body centre is the pad-field centroid, see `geometry.solid_rects`), rotation 0/90/180/270, layer top/bottom, footprint, locked, placementReason | `get_component().placed`, `get_part_envelope` |
| **Footprint** | `Footprint` | `name` (KiCad `Lib:Name`, cached per MPN in `footprint_cache.json`) | width, height (body), pads[], confidence verified/approximate/generic, courtyard_w/h, pin1_pad, bodyRects | `get_component().footprint/body_mm`, `get_part_envelope` |
| **Pad** | `FootprintPad` | **`refdes.pin_number`** (footprint-local; world position via `geometry.pad_world`) | x, y (footprint frame), width, height, shape circle/rect/rounded, pad_type smd/tht, layer | named in `list_unrouted_ratlines` endpoints; no pad-level query yet (gap: `get_pad`) |
| **Trace** | `Trace` | **none** | net, layer, width, segments[(x1,y1,x2,y2)] | counted in `get_net().traces`; not individually addressable (gap: traces are regenerated wholesale by every route, so an id would not survive a re-route anyway; a natural key is `net + layer + index`) |
| **Via** | `Via` | **none** | x, y, drill, diameter, net | counted in `get_net().vias` (gap, same reason) |
| **RatsLine** | `RatsLine` | **derived**: `net:<from pad>-<to pad>` (`USB_CC2:J1.B5-R5.1`), endpoints matched to the nearest pad within 0.6 mm | net, x1, y1, x2, y2; length | `list_unrouted_ratlines` |
| **PlacementConstraint** | `PlacementConstraint` | none (derived on read: kind + refdes + target) | kind edge/near/group/separate/fixed/keepout, refdes, target, reason, inferred | `GET …/pcb/constraints` |

### Mechanical nouns (`MechanicalIR`)

| entity | model | stable id | properties | query |
|---|---|---|---|---|
| **MechanicalIR** | `MechanicalIR` | singleton (`project.mechanical`) | outline, mountingHoles, keepouts, cutouts, enclosure, panel | `GET …/mechanical` |
| **BoardOutlineMech** | `BoardOutlineMech` | singleton (`mechanical.outline`) | shape rect/rounded_rect/circle/polygon, width, height, corner_radius, points | `get_board_summary`, `GET …/cad-brief` |
| **MountingHole** | `MountingHole` | **`id`** (`mh-xxxxxxxx`) | x, y (centre), diameter (3.2 = M3 clearance), plated, standoff | `list_holes`, `get_hole`, `get_board_summary().holes` |
| **KeepoutZone** | `KeepoutZone` | **`id`** (`ko-xxxxxxxx`) | x, y (bottom-left), width, height, layer top/bottom/both, reason — the router hard-blocks it | `list_keepouts` |
| **ConnectorCutout** | `ConnectorCutout` | **`id`** (`cut-xxxxxxxx`) | refdes (the connector), x, y, width (span along its edge), height (above the board top), edge left/right/top/bottom/none | `list_cutouts` |
| **Enclosure** | `Enclosure` | singleton | kind none/box/snap_fit, wall_thickness, standoff_height, clearance_top, clearance_bottom, lip | `get_board_summary().enclosure` |
| **PanelSpec** | `PanelSpec` | singleton (`mechanical.panel`) | rows, cols, method v_score/mouse_bites, gap, rail — geometry derived by `panelize`, never stored | `GET …/panel` |
| **Body / envelope** | derived (`geometry.solid_rects` + `mechanical_derive.body_height_mm`) | `refdes` (one part may occupy several rects — shield headers) | rects[(x0,y0,x1,y1)] in the board frame, height above the board | `get_part_envelope`, `GET …/cad-brief` entities.parts |

### Library nouns (read-only, shared by every project)

| entity | model | stable id | properties | query |
|---|---|---|---|---|
| **LibraryPart** | `LibraryPart` | **`mpn`** (exact, case-sensitive; `alternatives[]` name substitutes) | description, category (mcu, regulator, charger, sensor, passive, led, switch, transistor, diode, connector, crystal, protection, mechanical, display, actuator, memory, interface, audio, logic), package, footprint, symbol, pins[LibraryPin], voltageMin/Max, recommendedSupplyV, logicLevelV, currentTypMa/MaxMa, powerMaxW, bodyHeightMm, matingFaceDir, onboardPower, offBoard, interfaces[], defaultI2cAddress, companions[CompanionPart], typicalApplication, provenance, curationStatus, offers, sourcing, unitCost | `search_parts` (compact rows); `add_component` returns the pin table + applicationNote + requiredCompanions |
| **CompanionPart** | `CompanionPart` | (parent mpn, mpn, role) | role decoupling/pullup/load_cap/crystal_load/current_limit/level_shift/filter, pin, quantity, notes | in `add_component().requiredCompanions` |
| **Template** | `services/templates.TEMPLATES`, `MECH_TEMPLATES`, `form_factors.FORM_FACTORS` | `template_name` (`usb_c_5v_input`, `esp32_c3_core`, `rpi_hat`, `fc_stack_30_5`…) | placeholder nets, parts, topology; validity envelope | `apply_template` schema lists them |
| **Fab profile** | `fab_profiles` | key (`jlcpcb_2layer`, `pcbway_2layer`, `conservative`) | min trace/space/drill/via/annular/edge, copper oz | `GET /pcb/fab-profiles` |

### Other project nouns

| entity | model | stable id | properties |
|---|---|---|---|
| **BomLine** | `BomLine` | `mpn` (one line per MPN; `refdes` is the first of the group) | description, qty, unitCost, priceSource live/reference/none, currency, stock, vendor, asOf, stale, breaks, sku, moq, lifecycle, leadDays |
| **ChatMessage** | `ChatMessage` | `id` (`msg-xxxxxxxx`) | role, text, toolCalls[ToolCallSummary{tool, args, rationale}], diff{status, changes[DiffEntry]} |
| **Firmware / FirmwareFile** | `Firmware`, `FirmwareFile` | singleton / `path` | framework, target_mcu, files, summary, model_used |
| **BuildGuide / BuildStep** | `BuildGuide`, `BuildStep` | singleton / `n` | summary, tools, partsToOrder, steps{n, title, body, refdes[], check}, powerUpChecks, gotchas, enclosure |
| **Commit** | `history` | commit id | message, author, snapshot — `POST …/revert/{commit}` |

## Gaps (no stable id today, deliberately not added this round)

| entity | why no id | how to address it today | cost of adding one |
|---|---|---|---|
| Trace, Via | regenerated wholesale by every `auto_route` / finalize; an id minted at creation would not survive the next route | `get_net(name)` for counts / routed state; the Gerber and `.kicad_pcb` are the addressable artefacts | a `Trace.id` field ripples into `frontend/src/types/ir.ts`, `build_pcb.py`, `gerber.py`, `apply_ses.py` — outside this round's untouched set |
| Wire | schematic wires are derived from nets for rendering | address the net | none needed |
| PartConstraintRecord | parsed from the prompt each turn; natural key (kind, target, value) | R73 findings name the part | small, no consumer asks for it |
| PlacementConstraint | derived on every read | `GET …/pcb/constraints` | none needed |
| Pad | addressed as `refdes.pin_number` (stable) but has no query of its own | `get_component` pins + `get_part_envelope` for position | a `get_pad` query (world x/y/layer/drill/net) is cheap; add when a consumer needs it |
