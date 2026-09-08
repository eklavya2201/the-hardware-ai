"""Export to KiCad netlist (.net, S-expression) and JLC-format BOM CSV."""

from __future__ import annotations

import csv
import io
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app.models import Project
from app.services import footprints, kicad_bridge, library


def manufacturability_report(project: Project, *, boards: int = 1) -> dict:
    """Per-component footprint manufacturability + the list of export blockers
    (Phase 5 / #30). A blocker is a *critical* part (MCU, regulator, connector,
    sensor, …) whose footprint is a generic placeholder or is missing pads —
    the parts you cannot fabricate from approximate geometry.

    Completeness gate (LED-matrix review, 2026-08-15): a design that doesn't
    contain the quantities the user asked for (verifier R43, checked fresh here
    so a stale issues list can't fake readiness) is ALSO not ready — a board
    missing 97 of its 100 requested LEDs must never report ready:true to the
    Order panel or the export endpoints."""
    components: list[dict] = []
    blockers: list[dict] = []
    seen: set[str] = set()
    if not project.ir.components:
        # An empty design is not "ready to manufacture" — it is nothing. With no
        # parts there are no footprint blockers and no requirements to fall
        # short of, so every gate below passed vacuously and the Order panel
        # showed a green light on a blank project.
        blockers.append({
            "refdes": "DESIGN",
            "mpn": "empty",
            "kind": "completeness",
            "reason": "There is no design yet — describe the board you want first.",
            "critical": True,
            "blocksExport": True,
        })
    for c in project.ir.components:
        part = library.get_part(c.mpn)
        if part is None or c.mpn in seen:
            continue
        seen.add(c.mpn)
        status = footprints.footprint_status(part)
        status["refdes"] = c.refdes
        components.append(status)
        if status["blocksExport"]:
            blockers.append(status)

    # A board that predates the last schematic edit would ship the OLD layout's
    # Gerbers — exactly the schematic/PCB mismatch beta review #5 hit. Recompute
    # it here (not from project.issues, which only refreshes on run_drc).
    from app.services import pcb_verifier

    for issue in pcb_verifier.board_sync_issues(project):
        blockers.append({
            "refdes": "BOARD",
            "mpn": "out-of-date",
            "kind": "sync",
            "reason": issue.message,
            "critical": True,
            "blocksExport": True,
        })

    # Recompute the requested-quantity check directly (cheap + deterministic)
    # rather than trusting project.issues, which only refreshes on run_drc.
    from app.services import verifier

    for issue in verifier.R43_requested_quantity(project):
        if issue.severity != "error":
            continue  # the over-count warning advises, it doesn't block
        blockers.append({
            # Same key shape the export endpoints format ("refdes (mpn)").
            "refdes": "DESIGN",
            "mpn": "incomplete",
            "kind": "completeness",
            "reason": issue.message,
            "critical": True,
            "blocksExport": True,
        })

    # A board with airwires is not a board a fab can build. The preview router
    # legitimately leaves rats for Finalize (FreeRouting) to close; until then
    # `ready` must say so — three unrouted nets reported ready:true on the
    # smartwatch loop check (2026-09-06). Recomputed from the board, not from
    # project.issues (PCB_R03 only refreshes when the PCB rules run).
    if project.board is not None and project.board.components and project.board.rats:
        n = len({r.net for r in project.board.rats if getattr(r, "net", None)} or project.board.rats)
        blockers.append({
            "refdes": "BOARD",
            "mpn": "unrouted",
            "kind": "routing",
            "reason": (
                f"{n} net{'s' if n != 1 else ''} still unrouted — click Finalize routing "
                "(FreeRouting) or move the crowded parts and re-route. Gerbers of an "
                "unrouted board cannot be built."
            ),
            "critical": True,
            "blocksExport": True,
        })

    # The board must be the product that was asked for. A Ø42 mm watch that
    # came back Ø76 mm shipped ready:true (same loop check): R57 only runs
    # with the schematic rules, which the place/route endpoints don't re-run.
    # Fresh here, like R43 above. Not an export blocker — the Gerbers of an
    # oversize board are still valid Gerbers — but it is not "ready".
    for issue in verifier.R57_form_factor(project):
        if issue.severity != "error":
            continue
        blockers.append({
            "refdes": "BOARD",
            "mpn": "form-factor",
            "kind": "form_factor",
            "reason": issue.message,
            "critical": True,
            "blocksExport": False,
        })

    # And the schematic itself must be clean. Every other gate above is a
    # specific class; this is the general one: any remaining blocking rule
    # error (a rail short, a regulator that cannot regulate, a floating EN)
    # means the design is not ready, whatever the stale issues list says.
    fresh_errors = [
        i for i in verifier.run_all(project)
        if i.severity == "error"
        and not (i.rule or "").startswith(("R43", "R57"))   # already reported above
    ]
    if fresh_errors:
        first = "; ".join(i.message.split(" — ")[0][:110] for i in fresh_errors[:3])
        blockers.append({
            "refdes": "DESIGN",
            "mpn": "errors",
            "kind": "drc",
            "reason": (
                f"{len(fresh_errors)} blocking design error"
                f"{'s' if len(fresh_errors) != 1 else ''} open in Review: {first}"
                + (" …" if len(fresh_errors) > 3 else "")
            ),
            "critical": True,
            "blocksExport": False,
        })

    # An unfinished build blocks fabrication outright. The agent said so in
    # chat ("I ran out of build budget — the design is INCOMPLETE") while Order
    # and Manufacture stayed live; leaving commerce actions enabled on a
    # half-built board is a commercial and reputational exposure, and it makes
    # two indicators on the same screen contradict each other.
    if getattr(project.ir, "buildIncomplete", False):
        blockers.append({
            "refdes": "DESIGN",
            "mpn": "unfinished",
            "kind": "incomplete",
            "reason": (
                "The last build stopped before it finished — the copilot ran out of turns "
                "with work still pending. Ask it to continue, then re-check, before ordering."
            ),
            "critical": True,
            "blocksExport": True,
        })

    # B5: can the BOM be BOUGHT? An EOL or out-of-stock part makes the design
    # not ready, exactly like a missing footprint — but it does not stop a
    # Gerber download (`blocksExport` False: the fab does not need the parts),
    # so the export gates filter through `export_blockers`. "Not checked" is
    # reported as unverified in `sourcing`, never as a blocker: a keyless
    # install must still export, and we did not look is not it cannot be bought.
    from app.services import sourcing as _sourcing

    verdict = _sourcing.assess(project, boards=boards)
    for line in verdict["lines"]:
        if not line["blocking"]:
            continue
        alts = ", ".join(a["mpn"] for a in line["alternatives"][:3])
        blockers.append({
            "refdes": line["refdes"],
            "mpn": line["mpn"],
            "kind": "sourcing",
            "reason": "; ".join(line["reasons"]) + (f" — alternatives: {alts}" if alts else ""),
            "critical": True,
            "blocksExport": False,
        })

    return {
        "components": components,
        "blockers": blockers,
        "ready": not blockers,
        "sourcing": {k: v for k, v in verdict.items() if k != "lines"},
    }


def export_blockers(report: dict) -> list[dict]:
    """The blockers that stop a FABRICATION export (footprints, sync,
    completeness). Sourcing blockers make the design not-ready but a Gerber
    zip is still valid without the parts in hand."""
    return [b for b in report["blockers"] if b.get("blocksExport", True)]


def kicad_netlist(project: Project) -> str:
    """KiCad 6+ netlist format. Importable into KiCad pcbnew via 'Update PCB from
    Schematic' (after a one-time symbol import) or used by external CAM tools."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out: list[str] = []
    out.append("(export (version E)")
    out.append(f'  (design (source "voltron://{project.id}") (date "{ts}") (tool "voltron 0.1"))')

    # Components
    out.append("  (components")
    for c in project.ir.components:
        part = library.get_part(c.mpn)
        symbol = part.symbol if part else f"voltron:{c.mpn}"
        footprint = c.footprint or (part.footprint if part else "")
        out.append(f'    (comp (ref "{c.refdes}")')
        out.append(f'      (value "{c.value or c.mpn}")')
        out.append(f'      (footprint "{footprint}")')
        if part and part.datasheetUrl:
            out.append(f'      (datasheet "{part.datasheetUrl}")')
        out.append(f'      (libsource (lib "{symbol.split(":")[0]}") (part "{symbol.split(":")[-1]}"))')
        out.append(f'      (property (name "MPN") (value "{c.mpn}")))')
    out.append("  )")

    # Nets
    out.append("  (nets")
    for idx, net in enumerate(project.ir.nets, start=1):
        out.append(f'    (net (code "{idx}") (name "{net.name}")')
        for refdes, pin in net.pins:
            comp = next((c for c in project.ir.components if c.refdes == refdes), None)
            pinfn = "passive"
            if comp:
                pn = next((p for p in comp.pins if p.number == pin or p.name == pin), None)
                if pn:
                    pinfn = pn.type
                    pin = pn.number  # canonicalize on number
            out.append(f'      (node (ref "{refdes}") (pin "{pin}") (pinfunction "{pinfn}"))')
        out.append("    )")
    out.append("  )")
    out.append(")")
    return "\n".join(out) + "\n"


class KicadPcbError(RuntimeError):
    """Raised when the .kicad_pcb export can't be produced (no board / KiCad)."""


def kicad_pcb(project: Project, design_rules: dict | None = None) -> str:
    """Generate a real `.kicad_pcb` from the IR board (task 3.4) via the headless
    KiCad bridge. STRICTLY one-way — IR -> KiCad; the board is never read back
    (the IR stays the single source of truth). Raises KicadPcbError if there is
    no placed board or KiCad is unavailable.

    Consumes `project.board` (placed components with real Lib:Name footprints,
    traces, vias) plus `project.ir.nets` for pad-net assignment, and returns the
    `.kicad_pcb` S-expression text. `design_rules` (from `fab_profiles`) stamps
    the board's DRC constraints so the industry DRC (Phase 2) checks against a
    chosen fab's limits."""
    board = project.board
    if board is None or not board.components:
        raise KicadPcbError(
            "No placed board to export — generate the PCB layout first."
        )

    values = {c.refdes: (c.value or c.mpn) for c in project.ir.components}
    payload = {
        "board": {
            "width": board.width,
            "height": board.height,
            "layer_count": board.layer_count,
            "shape": board.shape,
            "planes": [p.model_dump() for p in (getattr(board, "planes", None) or [])],
        },
        "design_rules": design_rules or {},
        "components": [
            {
                "refdes": pc.refdes,
                "footprint": pc.footprint.name,
                "x": pc.x,
                "y": pc.y,
                "rotation": pc.rotation,
                "layer": pc.layer,
                "value": values.get(pc.refdes, pc.mpn),
            }
            for pc in board.components
        ],
        "nets": [
            {"name": n.name, "pads": [[r, p] for (r, p) in n.pins]}
            for n in project.ir.nets
        ],
        "traces": [
            {
                "net": t.net,
                "layer": t.layer,
                "width": t.width,
                "segments": [[s.x1, s.y1, s.x2, s.y2] for s in t.segments],
            }
            for t in board.traces
        ],
        "vias": [
            {"x": v.x, "y": v.y, "drill": v.drill, "diameter": v.diameter, "net": v.net}
            for v in board.vias
        ],
    }

    result = kicad_bridge.run_script("build_pcb.py", payload, timeout=90)
    if not result.success:
        raise KicadPcbError(result.error or "KiCad board build failed.")
    return result.data.get("pcb", "")


# Gerber layer set for a standard 2-layer board (task 4.1). Extra copper layers
# are appended for >2-layer boards. Untranslated KiCad layer names.
_GERBER_BASE_LAYERS = [
    "F.Cu",
    "B.Cu",
    "F.Paste",
    "B.Paste",
    "F.SilkS",
    "B.SilkS",
    "F.Mask",
    "B.Mask",
    "Edge.Cuts",
]


def kicad_project(project: Project) -> tuple[bytes, list[str]]:
    """Full, openable KiCad project as a zip — the "clean handoff" the market
    asked for (item #5). Returns (zip bytes, notes).

    Always includes the schematic (`.kicad_sch`, generated in pure Python by
    `export_sch`), the project file (`.kicad_pro`, fab DRC rules) and the
    netlist. The board (`.kicad_pcb`) is included when the design is placed AND
    KiCad is available — its absence degrades to a note in the README rather
    than a failed export, so a KiCad-less server still hands over a usable
    project. One-way, like every KiCad writer here."""
    import json

    from app.services import export_sch, fab_profiles

    notes: list[str] = []
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in project.name) or "synqit"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{safe}/{safe}.kicad_sch", export_sch.kicad_sch(project))
        zf.writestr(
            f"{safe}/{safe}.kicad_pro",
            json.dumps(fab_profiles.get(None).as_kicad_pro(), indent=2),
        )
        zf.writestr(f"{safe}/{safe}.net", kicad_netlist(project))
        zf.writestr(f"{safe}/bom.csv", bom_csv(project))

        if project.board and project.board.components:
            try:
                zf.writestr(f"{safe}/{safe}.kicad_pcb", kicad_pcb(project))
            except KicadPcbError as exc:
                notes.append(f"Board not included: {exc}")
        else:
            notes.append("Board not included: the PCB has not been placed yet.")

        if project.buildGuide:
            from app.services import build_guide as _bg  # noqa: PLC0415

            zf.writestr(f"{safe}/BUILD_GUIDE.md", _bg.to_markdown(project))
        zf.writestr(f"{safe}/README.txt", _project_readme(project, notes))
    return buf.getvalue(), notes


def _project_readme(project: Project, notes: list[str]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"{project.name} — KiCad project exported by Synqit",
        f"Generated {stamp}",
        "",
        "Open the .kicad_pro in KiCad 7 or newer.",
        "",
        "Contents",
        "  *.kicad_sch  Schematic. Symbols carry real pins, footprints and MPNs;",
        "               nets connect by global label (netlist-derived convention).",
        "  *.kicad_pro  Project file with the fab DRC/net-class rules.",
        "  *.net        KiCad netlist (S-expression).",
        "  bom.csv      Bill of materials.",
        "  *.kicad_pcb  Placed/routed board, when the layout exists.",
        "",
        "Notes",
        "  - Symbols are generated rectangles with correct pins and electrical",
        "    types — they are electrically right, not pretty. Re-annotate or swap",
        "    to your preferred library symbols if you want a publication drawing.",
        "  - Footprint fields reference standard KiCad libraries where possible.",
        "  - This export is ONE-WAY. Edits you make in KiCad do not flow back to",
        "    Synqit; keep designing in Synqit or take ownership in KiCad.",
    ]
    if notes:
        lines += ["", "This export"] + [f"  - {n}" for n in notes]
    return "\n".join(lines) + "\n"


def fab_package(project: Project) -> bytes:
    """Real, fab-ready manufacturing package (task 4.1): Gerbers + Excellon drill
    + pick-and-place (position) files, zipped. Generated by `kicad-cli` from the
    IR board — STRICTLY one-way (IR -> KiCad, never read back).

    Pipeline: build the `.kicad_pcb` from the IR (same path as `kicad_pcb`), write
    it to a temp file, then shell out to `kicad-cli pcb export {gerbers,drill,pos}`
    and collect every produced file into a single zip. Raises KicadPcbError if
    there's no placed board or KiCad/kicad-cli is unavailable."""
    board = project.board
    if board is None or not board.components:
        raise KicadPcbError(
            "No placed board to export — generate the PCB layout first."
        )
    if not kicad_bridge.cli_available():
        raise KicadPcbError(
            "kicad-cli not found — install KiCad to generate Gerbers/drill/"
            "pick-and-place files."
        )

    # 1. Build the board text via pcbnew (raises KicadPcbError on failure).
    pcb_text = kicad_pcb(project)

    layers = list(_GERBER_BASE_LAYERS)
    inner = max(0, int(getattr(board, "layer_count", 2)) - 2)
    for i in range(1, inner + 1):
        layers.insert(1 + i, f"In{i}.Cu")  # keep copper grouped after F.Cu

    with tempfile.TemporaryDirectory(prefix="voltron_fab_") as tmp:
        tmp_path = Path(tmp)
        pcb_path = tmp_path / f"{project.id}.kicad_pcb"
        pcb_path.write_text(pcb_text, encoding="utf-8")

        gerber_dir = tmp_path / "gerbers"
        gerber_dir.mkdir()

        steps = [
            (
                "gerbers",
                [
                    "pcb", "export", "gerbers",
                    "--output", str(gerber_dir),
                    "--layers", ",".join(layers),
                    "--no-protel-ext",  # KiCad .gbr extensions, portable
                    str(pcb_path),
                ],
            ),
            (
                "drill",
                [
                    "pcb", "export", "drill",
                    "--output", str(gerber_dir) + "/",  # drill wants a dir
                    "--format", "excellon",
                    "--excellon-units", "mm",
                    "--generate-map",
                    str(pcb_path),
                ],
            ),
            (
                "pos",
                [
                    "pcb", "export", "pos",
                    "--output", str(gerber_dir / f"{project.id}-pos.csv"),
                    "--format", "csv",
                    "--units", "mm",
                    "--side", "both",
                    str(pcb_path),
                ],
            ),
        ]
        errors: list[str] = []
        for name, args in steps:
            res = kicad_bridge.run_cli(args, timeout=120)
            if not res.success:
                errors.append(f"{name}: {res.error}")

        produced = [p for p in gerber_dir.rglob("*") if p.is_file()]
        if not produced:
            raise KicadPcbError(
                "kicad-cli produced no fab files. " + " | ".join(errors)
            )

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(produced):
                zf.write(p, arcname=p.name)
            # A README so the downloaded zip is self-describing.
            readme = _fab_readme(project, [p.name for p in sorted(produced)], errors)
            zf.writestr("README.txt", readme)
        return buf.getvalue()


def _fab_readme(project: Project, files: list[str], errors: list[str]) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"Synqit fabrication package — {project.name} ({project.id})",
        f"Generated {ts}",
        "",
        "Contents (RS-274X Gerbers, Excellon drill, CSV pick-and-place):",
    ]
    lines += [f"  - {f}" for f in files]
    lines += [
        "",
        "Upload the Gerber + drill files to any fab (JLCPCB, PCBWay, OSH Park).",
        "The -pos.csv file is the pick-and-place / centroid file for assembly.",
        "Always review footprints and run DRC before ordering hardware.",
    ]
    if errors:
        lines += ["", "NOTE — some export steps reported issues:"] + [
            f"  ! {e}" for e in errors
        ]
    return "\n".join(lines) + "\n"


def _price_cells(mpn: str, qty: int) -> tuple[str, str, str, str, str]:
    """(unit, currency, source, as_of, ext) for a CSV — the SAME provenance
    the Inspector shows (B2). A reference cost is labelled an estimate, an
    unknown price is a BLANK cell (never 0.0000 — "$0.00 reads as free"), and
    a live price is the break for THIS quantity with its date and vendor."""
    from app.services import pricing

    live, stale = pricing.cached_price_any(mpn)
    if live is not None:
        unit = pricing.price_for_qty(live, qty)
        src = f"live ({live.vendor}{', stale' if stale else ''})"
        as_of = datetime.fromtimestamp(live.fetchedAt, timezone.utc).strftime("%Y-%m-%d")
        return f"{unit:.4f}", live.currency, src, as_of, f"{unit * qty:.4f}"
    part = library.get_part(mpn)
    if part and part.unitCost > 0:
        return f"{part.unitCost:.4f}", "USD", "estimate", "", f"{part.unitCost * qty:.4f}"
    return "", "", "none", "", ""


def bom_csv(project: Project, *, boards: int = 1) -> str:
    """JLC-style BOM: Comment, Designator, Footprint, JLCPCB Part #, Value, Qty,
    then the price WITH its provenance — the Inspector's honesty used to die at
    export, where every estimate went out under a column headed "Unit Cost USD"
    and unknown parts as 0.0000 (B2)."""
    boards = max(1, int(boards or 1))
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Comment", "Designator", "Footprint", "JLCPCB Part #", "Value", "Qty",
                     "Unit Price", "Currency", "Price Source", "Price As Of", "Ext Price"])

    for ln in _grouped_lines(project, boards=boards):
        part = ln["part"]
        mpn = ln["mpn"]
        comment = part.description if part else mpn
        footprint = part.footprint if part else ""
        jlc = part.jlcPart or "" if part else ""
        # Use the value of the first occurrence; fallback to MPN.
        first = next(c for c in project.ir.components if c.refdes == ln["refdeses"][0])
        value = first.value or (part.description.split(",")[0] if part else mpn)
        unit, cur, src, as_of, ext = _price_cells(mpn, ln["qty"])
        writer.writerow([comment, ln["refs"], footprint, jlc, value, ln["qty"],
                         unit, cur, src, as_of, ext])

    return buf.getvalue()


def _grouped_lines(project: Project, *, boards: int = 1) -> list[dict]:
    """One BOM line per distinct MPN: the library part (if any), refdes list,
    qty (x boards) and the cached live quote. Shared by every BOM export."""
    from app.services import pricing

    boards = max(1, int(boards or 1))
    grouped: dict[str, list[str]] = {}
    for c in project.ir.components:
        grouped.setdefault(c.mpn, []).append(c.refdes)
    lines: list[dict] = []
    for mpn, refdeses in grouped.items():
        part = library.get_part(mpn)
        live, _stale = pricing.cached_price_any(mpn)
        lines.append(
            {
                "mpn": mpn,
                "part": part,
                "refdeses": refdeses,
                "refs": ",".join(sorted(refdeses, key=_refdes_sort_key)),
                "qty": len(refdeses) * boards,
                "description": part.description if part else mpn,
                "live": live,
            }
        )
    return lines


def _sku(ln: dict, vendor: str, stored: str | None) -> str:
    """The distributor's part number for a line: the library's stored SKU, else
    the one the live quote came back with (only from THAT vendor — a Mouser
    SKU in a DigiKey column would match nothing)."""
    if stored:
        return stored
    live = ln.get("live")
    if live is not None and live.vendor == vendor and live.sku:
        return live.sku
    return ""


def digikey_bom_csv(project: Project, *, boards: int = 1) -> str:
    """CSV in the column layout DigiKey's myLists / BOM upload wizard auto-maps.
    Uses the stored DigiKey SKU when the library has one, else the SKU a live
    DigiKey quote returned; the MPN column alone is enough for their matcher
    otherwise."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["Digi-Key Part Number", "Manufacturer Part Number", "Quantity", "Customer Reference", "Description"]
    )
    for ln in _grouped_lines(project, boards=boards):
        part = ln["part"]
        writer.writerow(
            [_sku(ln, "DigiKey", part.digikeyPart if part else None),
             ln["mpn"], ln["qty"], ln["refs"], ln["description"]]
        )
    return buf.getvalue()


def mouser_bom_csv(project: Project, *, boards: int = 1) -> str:
    """CSV in the column layout Mouser's BOM tool auto-maps (mouser.com/bom)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["Mouser Part Number", "Mfr Part Number", "Quantity", "Customer Part Number", "Description"]
    )
    for ln in _grouped_lines(project, boards=boards):
        part = ln["part"]
        writer.writerow(
            [_sku(ln, "Mouser", part.mouserPart if part else None),
             ln["mpn"], ln["qty"], ln["refs"], ln["description"]]
        )
    return buf.getvalue()


def _refdes_sort_key(ref: str) -> tuple:
    head = "".join(c for c in ref if c.isalpha())
    tail = "".join(c for c in ref if c.isdigit())
    return (head, int(tail) if tail else 0)
