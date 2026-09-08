"""Build guide — "step-by-step: assembly order, tools, gotchas" (Prompt to
Product, 2026-09).

Two layers, same stance as firmware.py and the description enricher:

  1. `skeleton(project)` derives EVERY fact deterministically from the IR, the
     placed board (pad types, footprint sizes), the BOM, the verifier findings
     and the mechanical IR: which parts go down first, what to measure before
     the MCU is fed, which parts are polarised, what the enclosure needs.
     Runs offline; this alone is a usable guide.
  2. `generate(project)` lets the LLM rewrite the prose of those steps so it
     reads like a person wrote it — but it may only REWORD. The refdes lists,
     the step count and every number come from the skeleton, and anything the
     model adds that names a part not in the design is dropped in
     `_apply_prose`. No key → the skeleton ships as-is.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

from app.models import (
    BuildGuide,
    BuildStep,
    Component,
    Footprint,
    LibraryPart,
    Project,
)
from app.services import design_summary, footprints, library, pin_safety

# --- classification ----------------------------------------------------------

_SMALL_CHIP = re.compile(r"_(0201|0402|0603|0805|1206)_")
_FINE_PITCH = re.compile(r"QFN|DFN|BGA|LGA|-1EP|_P0\.[3-5]|P0\.65|TSSOP|SSOP|VQFN|WLCSP", re.I)
_POLAR_CAP = re.compile(r"electrolytic|tantalum|polymer|\bCP_\b|CP_Radial|Capacitor_Tantalum", re.I)
_CONNECTOR_CATS = {"connector"}
_POWER_CATS = {"regulator", "charger", "protection"}
_MODULE_HINT = re.compile(r"module|breakout|dev ?board|nano|pico|uno|mega|wroom|wrover|esp32-c3-mini", re.I)

_TOOL_IRON = "Soldering iron (temperature-controlled, fine tip) + 0.5 mm leaded or SAC solder"
_TOOL_TWEEZERS = "Fine tweezers, flux pen, isopropyl alcohol and a lint-free wipe"
_TOOL_HOTAIR = "Hot-air station or hot plate + solder paste (a stencil helps) for the fine-pitch/leadless packages"
_TOOL_CUTTERS = "Flush cutters for through-hole leads"
_TOOL_METER = "Multimeter — continuity and DC volts"
_TOOL_MAGNIFIER = "Magnifier or USB microscope to inspect joints"
_TOOL_USB = "USB cable + a serial monitor for the first power-up"


def _part(mpn: str) -> LibraryPart | None:
    return library.get_part(mpn)


def _footprint_for(comp: Component, project: Project) -> Footprint | None:
    if project.board:
        for pc in project.board.components:
            if pc.refdes == comp.refdes:
                return pc.footprint
    part = _part(comp.mpn)
    if part is None:
        return None
    try:
        return footprints.for_part(part)
    except Exception:  # noqa: BLE001 — a missing footprint must not sink the guide
        return None


def _mount(fp: Footprint | None) -> str:
    if fp is None:
        return "smd"
    return "tht" if any((p.pad_type or "").lower() == "tht" for p in fp.pads) else "smd"


def _area(fp: Footprint | None) -> float:
    if fp is None:
        return 0.0
    w = fp.courtyard_w or fp.width
    h = fp.courtyard_h or fp.height
    return float(w * h)


def _is_fine_pitch(fp: Footprint | None) -> bool:
    if fp is None:
        return False
    if _FINE_PITCH.search(fp.name or ""):
        return True
    xs = sorted({round(p.x, 2) for p in fp.pads})
    ys = sorted({round(p.y, 2) for p in fp.pads})
    gaps = [b - a for a, b in zip(xs, xs[1:])] + [b - a for a, b in zip(ys, ys[1:])]
    gaps = [g for g in gaps if g > 0.05]
    return bool(gaps) and min(gaps) < 0.65


def _polarity_note(comp: Component, part: LibraryPart | None, fp: Footprint | None) -> str | None:
    cat = (part.category if part else "") or ""
    desc = (part.description if part else "") or ""
    name = (fp.name if fp else "") or ""
    if cat in ("diode", "led"):
        return "polarised: the cathode is the band / flat edge / shorter lead — match the silkscreen"
    if cat == "passive" and _POLAR_CAP.search(desc + " " + name):
        return "polarised electrolytic: the stripe on the can is NEGATIVE, the longer lead is +"
    if fp and len(fp.pads) > 2 and cat in ("mcu", "regulator", "charger", "sensor", "interface", "logic", "memory", "audio", "display", "protection"):
        return "pin 1 is the dot / bevelled corner on the package — match the silkscreen dot"
    if cat in _CONNECTOR_CATS:
        return "keyed: check the housing orientation before soldering the first pin"
    return None


def _is_module(comp: Component, part: LibraryPart | None, fp: Footprint | None) -> bool:
    text = " ".join(filter(None, [comp.mpn, part.description if part else "", fp.name if fp else ""]))
    return bool(_MODULE_HINT.search(text)) or bool(part and getattr(part, "onboardPower", False))


# --- skeleton ----------------------------------------------------------------


def _rail_voltage(name: str) -> str | None:
    m = re.search(r"(?<![A-Za-z0-9])(\d+)V(\d+)?", name, re.I)
    if m:
        return f"{m.group(1)}.{m.group(2)}" if m.group(2) else m.group(1)
    if re.search(r"VBUS|USB", name, re.I):
        return "5"
    return None


def skeleton(project: Project) -> BuildGuide:
    ir = project.ir
    comps = list(ir.components)
    info: dict[str, dict] = {}
    for c in comps:
        part = _part(c.mpn)
        fp = _footprint_for(c, project)
        info[c.refdes] = {
            "comp": c,
            "part": part,
            "fp": fp,
            "mount": _mount(fp),
            "area": _area(fp),
            "fine": _is_fine_pitch(fp),
            "polar": _polarity_note(c, part, fp),
            "module": _is_module(c, part, fp),
            "cat": (part.category if part else "") or "",
        }

    summary = design_summary.build(project)
    rails = summary.powerArchitecture

    # Parts that make up the power stage: sources of every regulated rail and
    # the connectors on the input rails. These go down first so the rails can
    # be proven before anything expensive is fed.
    # The summary's rail-source detection misses bucks whose output is the SW
    # node through an inductor (VCC_3V3 with no sources on a buck board), so
    # the power stage is keyed on the parts' CATEGORY, not on rail kinds.
    power_refs: set[str] = {ref for ref, d in info.items() if d["cat"] in _POWER_CATS}
    input_connectors: set[str] = set()
    for r in rails:
        if r.kind == "regulated":
            power_refs.update(r.sources)
        if r.kind == "input":
            for ref in r.sources:
                if info.get(ref, {}).get("cat") in _CONNECTOR_CATS:
                    input_connectors.add(ref)
    power_refs |= input_connectors
    # Their own decoupling: 2-pin passives sharing a net with a power-stage part.
    net_members: dict[str, set[str]] = {}
    for net in ir.nets:
        for pin in net.pins:
            ref = pin[0] if isinstance(pin, (tuple, list)) else getattr(pin, "refdes", None)
            if ref:
                net_members.setdefault(net.name, set()).add(str(ref))
    for net_name, members in net_members.items():
        if power_refs & members and not design_summary._is_gnd_net(net_name):
            for ref in members:
                d = info.get(ref)
                if d and d["cat"] == "passive" and d["fp"] and len(d["fp"].pads) <= 2:
                    power_refs.add(ref)

    def order_key(ref: str) -> tuple:
        d = info[ref]
        return (d["mount"] == "tht", d["module"] or d["cat"] in _CONNECTOR_CATS, d["area"], ref)

    def bucket(refs: list[str]) -> dict[str, list[str]]:
        out = {"smd_small": [], "smd_fine": [], "smd": [], "tht": [], "conn": []}
        for ref in sorted(refs, key=order_key):
            d = info[ref]
            if d["cat"] in _CONNECTOR_CATS or d["module"]:
                out["conn"].append(ref)
            elif d["mount"] == "tht":
                out["tht"].append(ref)
            elif d["fine"]:
                out["smd_fine"].append(ref)
            elif d["fp"] and _SMALL_CHIP.search(d["fp"].name or ""):
                out["smd_small"].append(ref)
            else:
                out["smd"].append(ref)
        return out

    rest = [c.refdes for c in comps if c.refdes not in power_refs]
    power_b = bucket(sorted(power_refs))
    rest_b = bucket(rest)

    def describe(refs: list[str]) -> str:
        parts = []
        for ref in refs:
            d = info[ref]
            if d["cat"] in ("passive", "led", "diode", "crystal"):
                desc = (d["part"].description if d["part"] else "") or d["comp"].mpn
                desc = desc.split(",")[0].split(" — ")[0][:48]
            else:
                desc = d["comp"].mpn
            parts.append(f"{ref} ({desc})")
        return ", ".join(parts)

    steps: list[BuildStep] = []

    def add(title: str, body: str, refs: list[str], check: str | None = None) -> None:
        steps.append(BuildStep(n=len(steps) + 1, title=title, body=body, refdes=refs, check=check))

    add(
        "Inspect the bare board",
        "Check the PCB against the layout: outline, drill holes, and that every pad is where the "
        "placement shows it. Look for solder-mask defects and bridged pads before any part goes on.",
        [],
        "No shorts between VCC and GND on the bare board (continuity test).",
    )

    # -- power stage first
    power_order = power_b["smd_small"] + power_b["smd_fine"] + power_b["smd"] + power_b["tht"] + power_b["conn"]
    if power_order:
        polar = [f"{r}: {info[r]['polar']}" for r in power_order if info[r]["polar"]]
        add(
            "Populate the power stage",
            "Solder the regulator(s), their input/output capacitors and the power input connector "
            f"first so the rails can be tested before anything else is fitted: {describe(power_order)}."
            + (" Watch orientation — " + "; ".join(polar) + "." if polar else ""),
            power_order,
            "Continuity: no short between any rail and GND.",
        )
        checks = []
        for r in rails:
            if r.kind == "input" or design_summary._is_gnd_net(r.rail):
                continue
            v = _rail_voltage(r.rail)
            where = ", ".join(r.sources) if r.sources else (
                f"the supply pin of {r.loads[0]}" if r.loads else "its output capacitor")
            checks.append(
                f"Apply power at the input and measure {r.rail} at {where}"
                + (f": expect about {v} V." if v else ".")
                + " Nothing else is fitted yet, so a wrong reading is the power stage, not a load."
            )
        for r in rails:
            if r.kind == "input" and r.sources:
                v = _rail_voltage(r.rail)
                checks.append(
                    f"Input rail {r.rail} arrives on {', '.join(r.sources)}"
                    + (f" — confirm polarity and about {v} V before connecting." if v else " — confirm polarity before connecting.")
                )
        if checks:
            add("Power-up test #1 — rails only", " ".join(checks), [], "Every regulated rail within 5 % of nominal; the regulator stays cool.")
        power_checks = checks
    else:
        power_checks = []

    # -- everything else, small SMD → fine pitch → other SMD → THT → connectors/modules
    if rest_b["smd_small"]:
        add(
            "Small SMD passives",
            f"Tack one pad, slide the part in, then solder the other pad: {describe(rest_b['smd_small'])}. "
            "Do resistors and capacitors of the same value together so you are not swapping reels.",
            rest_b["smd_small"],
        )
    if rest_b["smd_fine"]:
        polar = [f"{r}: {info[r]['polar']}" for r in rest_b["smd_fine"] if info[r]["polar"]]
        add(
            "Fine-pitch and leadless ICs",
            f"Hot air or hot plate with paste: {describe(rest_b['smd_fine'])}. Align pin 1 before reflow; "
            "a bridged pin is far easier to fix now than after the board is crowded."
            + (" Orientation — " + "; ".join(polar) + "." if polar else ""),
            rest_b["smd_fine"],
            "No bridges under magnification; each IC's power pins read open-circuit to GND (not shorted).",
        )
    if rest_b["smd"]:
        polar = [f"{r}: {info[r]['polar']}" for r in rest_b["smd"] if info[r]["polar"]]
        add(
            "Remaining SMD parts",
            f"{describe(rest_b['smd'])}."
            + (" Orientation — " + "; ".join(polar) + "." if polar else ""),
            rest_b["smd"],
        )
    if rest_b["tht"]:
        polar = [f"{r}: {info[r]['polar']}" for r in rest_b["tht"] if info[r]["polar"]]
        add(
            "Through-hole parts, shortest first",
            f"Insert, bend the leads slightly to hold, solder from the underside, trim: {describe(rest_b['tht'])}. "
            "Shortest parts first so the board sits flat on the bench."
            + (" Orientation — " + "; ".join(polar) + "." if polar else ""),
            rest_b["tht"],
        )
    if rest_b["conn"]:
        polar = [f"{r}: {info[r]['polar']}" for r in rest_b["conn"] if info[r]["polar"]]
        add(
            "Connectors and plug-in modules last",
            f"{describe(rest_b['conn'])}. Solder ONE pin, check the housing is flat and square to the board "
            "edge, then do the rest. Modules on headers: fit the header first, then the module."
            + (" Orientation — " + "; ".join(polar) + "." if polar else ""),
            rest_b["conn"],
        )

    add(
        "Clean and inspect",
        "Remove flux with isopropyl alcohol, then inspect every joint under magnification: shiny, concave "
        "fillets; no bridges; no lifted pads.",
        [],
        "Continuity test again between every rail and GND — a short now is a solder bridge.",
    )

    mcu_refs = sorted(design_summary._mcu_or_host_refdeses(project))
    final_checks = list(power_checks)
    if mcu_refs:
        fam = pin_safety.family_key(info[mcu_refs[0]]["comp"].mpn) if mcu_refs[0] in info else None
        flash = {
            "esp32": "hold BOOT, tap RESET/EN, release BOOT, then flash over USB-serial",
            "rp2040": "hold BOOTSEL while plugging in USB; it mounts as a drive — drop the UF2 on it",
            "avr328": "flash over the USB-serial bootloader (select the right board in the IDE)",
            "avr2560": "flash over the USB-serial bootloader (select the right board in the IDE)",
            "rpi": "boot the Pi from its SD card; the HAT is powered from the 40-pin header",
        }.get(fam or "", "flash with the vendor's usual tool over USB or SWD")
        final_checks.append(
            f"Power up with {', '.join(mcu_refs)} fitted; the board should draw a steady idle current and "
            f"the rails should hold. First program: {flash}. A blink or a serial 'hello' proves the MCU, "
            "the clock and the power tree together."
        )
    add(
        "Power-up test #2 — full board",
        " ".join(final_checks) if final_checks else "Apply power and confirm every rail before connecting anything external.",
        [],
        "Idle current is stable, nothing gets hot, the MCU enumerates / responds.",
    )

    # -- tools
    mounts = {d["mount"] for d in info.values()}
    tools = [_TOOL_IRON, _TOOL_METER, _TOOL_MAGNIFIER]
    if "smd" in mounts:
        tools.insert(1, _TOOL_TWEEZERS)
    if any(d["fine"] for d in info.values()):
        tools.insert(2, _TOOL_HOTAIR)
    if "tht" in mounts:
        tools.append(_TOOL_CUTTERS)
    if mcu_refs:
        tools.append(_TOOL_USB)

    # -- parts to order (from the BOM)
    parts_to_order = []
    for line in project.bom:
        price = ""
        if line.priceSource == "live" and line.unitCost:
            cur = getattr(line, "priceCurrency", None) or "USD"
            price = f" — {line.unitCost:.2f} {cur} each" + (f" ({line.priceVendor})" if line.priceVendor else "")
        elif line.priceSource == "reference" and line.unitCost:
            price = f" — ~{line.unitCost:.2f} USD est."
        parts_to_order.append(f"{line.qty}× {line.mpn or line.refdes}: {line.description}{price}")

    # -- gotchas: polarity, open warnings, curated application notes, RF keep-outs
    gotchas: list[str] = []
    for ref, d in info.items():
        if d["polar"]:
            gotchas.append(f"{ref}: {d['polar']}.")
    acked = {a.issue_id for a in getattr(ir, "acknowledgements", []) or []}
    for iss in project.issues:
        if iss.severity == "warning" and iss.id not in acked and iss.source in ("verifier", "erc", "ipc", "mechanical"):
            gotchas.append(f"Open review note: {iss.message}")
    for a in getattr(ir, "acknowledgements", []) or []:
        gotchas.append(f"Marked intentional ({a.issue_id}): {a.reason}")
    seen: set[str] = set()
    for ref, d in info.items():
        part = d["part"]
        if part and part.typicalApplication and part.mpn not in seen and d["cat"] not in ("passive",):
            seen.add(part.mpn)
            note = part.typicalApplication.strip().split(". ")[0][:220]
            gotchas.append(f"{ref} ({part.mpn}): {note}.")
    if any(re.search(r"ESP32|ESP8266|RF|antenna|LoRa|nRF|BLE|WiFi", d["comp"].mpn, re.I) for d in info.values()):
        gotchas.append("Keep the antenna end of any RF module over the board edge with no copper, metal or "
                       "enclosure wall under it — range drops sharply otherwise.")
    if "smd" in mounts and "tht" in mounts:
        gotchas.append("Mixed assembly: do all SMD work before any through-hole part goes in, or the board "
                       "will not sit flat for reflow.")
    gotchas = gotchas[:14]

    # -- enclosure
    enclosure: list[str] = []
    mech = project.mechanical
    if mech:
        if mech.mountingHoles:
            spec = sorted({h.standoff for h in mech.mountingHoles if h.standoff}) or []
            dia = sorted({round(h.diameter, 1) for h in mech.mountingHoles})
            enclosure.append(
                f"{len(mech.mountingHoles)} mounting holes, Ø{'/'.join(str(x) for x in dia)} mm"
                + (f" for {', '.join(spec)} hardware" if spec else "")
                + f"; standoffs {mech.enclosure.standoff_height:g} mm tall."
            )
        if mech.enclosure and mech.enclosure.kind != "none":
            e = mech.enclosure
            enclosure.append(
                f"{e.kind.replace('_', '-')} enclosure, {e.wall_thickness:g} mm walls, "
                f"{e.clearance_top:g} mm clearance above the tallest part, {e.clearance_bottom:g} mm below."
                + (f" Lid lip {e.lip:g} mm." if e.kind == "snap_fit" else "")
            )
        for c in mech.cutouts:
            enclosure.append(f"Cutout on the {c.edge} wall for {c.refdes}: {c.width:g} × {c.height:g} mm.")
        if mech.enclosure and mech.enclosure.kind != "none":
            enclosure.append("Print or machine the case from the STEP export; fit the board on its standoffs "
                             "BEFORE closing the lid and check every connector reaches its cutout.")

    return BuildGuide(
        summary=f"{len(comps)} parts, {len(steps)} steps"
        + (f", {len(mech.mountingHoles)} mounting holes" if mech and mech.mountingHoles else "")
        + ".",
        tools=tools,
        partsToOrder=parts_to_order,
        steps=steps,
        powerUpChecks=power_checks + final_checks[len(power_checks):],
        gotchas=gotchas,
        enclosure=enclosure,
        model_used=None,
    )


# --- LLM prose over the skeleton ------------------------------------------------

_SYSTEM = """\
You write assembly instructions for an electronics kit. You are given a JSON
skeleton of a build guide whose every fact — part references, order, voltages,
polarities, tool list — was derived from the actual design. Rewrite it so it
reads like an experienced builder wrote it for a beginner.

Rules — these are hard:
- Keep the SAME number of steps in the SAME order with the SAME "n".
- Do not add, remove or rename any part reference (R1, U2, J1 …). Every refdes you
  mention must already appear in that step's "refdes" list or its body.
- Do not invent a number: no voltages, currents, temperatures or values that are
  not already in the skeleton.
- Keep each step body under 90 words. Plain words, active voice, no marketing.
- Return JSON: {"summary": "...", "steps": [{"n": 1, "body": "..."}, ...]}.
"""


def _known_refdes(project: Project) -> set[str]:
    return {c.refdes for c in project.ir.components}


def _apply_prose(guide: BuildGuide, data: dict, known: set[str]) -> BuildGuide:
    bodies = {int(s.get("n", 0)): str(s.get("body", "")) for s in data.get("steps") or [] if isinstance(s, dict)}
    for step in guide.steps:
        body = bodies.get(step.n, "").strip()
        if not body:
            continue
        mentioned = set(re.findall(r"\b([A-Z]{1,3}\d{1,3})\b", body))
        # A refdes the design does not have = the model invented a part. Keep the skeleton text.
        if mentioned - known:
            continue
        step.body = body[:700]
    summary = str(data.get("summary", "")).strip()
    if summary and not (set(re.findall(r"\b([A-Z]{1,3}\d{1,3})\b", summary)) - known):
        guide.summary = summary[:400]
    return guide


def has_api_key(llm=None) -> bool:
    return bool((llm.api_key if llm is not None else None) or os.environ.get("OPENAI_API_KEY"))


def generate(project: Project, llm=None) -> BuildGuide:
    """Skeleton, then (when a key exists) one LLM pass that may only reword."""
    guide = skeleton(project)
    if not has_api_key(llm):
        return guide
    from app.services.agent import _make_openai_client  # noqa: PLC0415

    try:
        client = _make_openai_client(llm)
        model = llm.model if llm is not None else os.environ.get("OPENAI_MODEL", "gpt-4o")
        payload = guide.model_dump(exclude={"partsToOrder", "generated_at", "model_used"})
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=4000,
        )
        data = json.loads(response.choices[0].message.content or "{}")
        guide = _apply_prose(guide, data, _known_refdes(project))
        guide.model_used = model
    except Exception:  # noqa: BLE001 — prose is a nicety; the skeleton is the product
        guide.model_used = None
    guide.generated_at = datetime.now(timezone.utc).isoformat()
    return guide


# --- markdown ---------------------------------------------------------------------


def to_markdown(project: Project) -> str:
    g = project.buildGuide or skeleton(project)
    out = [f"# {project.name} — build guide", "", g.summary, ""]
    if g.tools:
        out += ["## Tools", ""] + [f"- {t}" for t in g.tools] + [""]
    if g.partsToOrder:
        out += ["## Parts to order", ""] + [f"- {p}" for p in g.partsToOrder] + [""]
    out += ["## Assembly", ""]
    for s in g.steps:
        out.append(f"### {s.n}. {s.title}")
        out.append("")
        out.append(s.body)
        if s.refdes:
            out.append("")
            out.append(f"Parts: {', '.join(s.refdes)}")
        if s.check:
            out.append("")
            out.append(f"**Check:** {s.check}")
        out.append("")
    if g.powerUpChecks:
        out += ["## Power-up checks", ""] + [f"- {c}" for c in g.powerUpChecks] + [""]
    if g.gotchas:
        out += ["## Gotchas", ""] + [f"- {x}" for x in g.gotchas] + [""]
    if g.enclosure:
        out += ["## Enclosure", ""] + [f"- {x}" for x in g.enclosure] + [""]
    out.append(f"_Generated by Synqit {g.generated_at[:10]}"
               + (f" · prose by {g.model_used}" if g.model_used else " · deterministic") + "_")
    return "\n".join(out) + "\n"
