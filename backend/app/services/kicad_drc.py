"""KiCad industry DRC — task 4.2.

Runs KiCad's *own* Design Rules Check engine (via `kicad-cli pcb drc`) on a
`.kicad_pcb` generated from the IR board, and maps the violations into the same
`VerifierIssue` shape the Review UI already renders. These run **alongside** the
in-house schematic rules (verifier.py `R01`–`R23`) and board rules
(pcb_verifier.py `PCB_R01`–`PCB_R07`) — they do NOT replace them. KiCad catches
manufacturing-geometry defects our grid router doesn't model (copper clearance,
annular ring / drill range, courtyard/soldermask bridges, edge clearance), so
surfacing both gives a fuller picture than either alone.

One-way, like everything KiCad here: we *write* an IR-derived board and *read*
only the diagnostic report — never the board geometry back into the IR.

Issues carry a `KICAD_<type>` rule id and `source="kicad"` so the Review panel
can badge them distinctly from the deterministic in-house rules.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from app.models import Project, VerifierIssue
from app.services import export, fab_profiles, kicad_bridge

# KiCad DRC can emit hundreds of rows on an unoptimised auto-routed board (every
# tight-clearance segment is its own violation). Emit at most this many
# individual issues, then a single rollup, so the Review panel stays scannable.
MAX_ISSUES = 60

# KiCad severities -> our three-level scale. "exclusion" = a rule the user
# explicitly excluded; we show it as info so it's visible but not alarming.
_SEV = {"error": "error", "warning": "warning", "exclusion": "info", "info": "info"}

# Violation types that, when they involve only pads/edge (no routed Track/Via),
# are properties of a part's footprint or placement rather than routing defects —
# a connector's tight pad pitch, a module's small mounting holes, an edge-mounted
# part's pads near the cut. Standard, fab-accepted; surfaced as info, not errors.
_FOOTPRINT_TYPES = {
    "drill_out_of_range",
    "copper_edge_clearance",
    "clearance",
    "hole_to_hole",
    "annular_width",
}

# "... of U3 on F.Cu", "Pad 2 [GND] of U1 ..." -> refdes for row-click focus.
_REFDES_RE = re.compile(r"\bof ([A-Z]{1,4}\d+)\b")
# "[GND]" / "[VBUS]" inside an item description -> net name.
_NET_RE = re.compile(r"\[([^\]]+)\]")


def _first(items: list[dict], pattern: re.Pattern) -> str | None:
    for it in items:
        m = pattern.search(it.get("description", ""))
        if m:
            return m.group(1)
    return None


def run(
    project: Project, profile_key: str | None = None
) -> tuple[list[VerifierIssue], str | None]:
    """Run KiCad DRC on the project's board against a fab-capability profile
    (Phase 2 — the profile's min clearance/track/drill/via/annular are stamped
    into the board so DRC enforces THAT fab's limits, not KiCad's generic
    defaults). Returns `(issues, error)`: `error` is a human-readable reason when
    DRC couldn't run (no board, KiCad absent, CLI failure); `issues` is empty in
    that case. Never raises."""
    board = project.board
    if board is None or not board.components:
        return [], "No placed board — run auto-place/route first."
    if not kicad_bridge.cli_available():
        return [], (
            "kicad-cli not found — install KiCad to run the industry DRC engine."
        )

    profile = fab_profiles.get(profile_key)
    try:
        pcb_text = export.kicad_pcb(project, design_rules=profile.as_design_rules())
    except export.KicadPcbError as exc:
        return [], str(exc)

    with tempfile.TemporaryDirectory(prefix="voltron_drc_") as tmp:
        tmp_path = Path(tmp)
        pcb_path = tmp_path / f"{project.id}.kicad_pcb"
        report_path = tmp_path / "drc.json"
        pcb_path.write_text(pcb_text, encoding="utf-8")
        # Sibling .kicad_pro so DRC checks against THIS fab's net-class rules
        # (KiCad 6+ keeps them in the project, not the standalone board) — else
        # DRC flags fab-compatible part footprints against generic defaults.
        pro_path = tmp_path / f"{project.id}.kicad_pro"
        pro_path.write_text(json.dumps(profile.as_kicad_pro()), encoding="utf-8")

        res = kicad_bridge.run_cli(
            [
                "pcb", "drc",
                "--format", "json",
                "--severity-all",
                "--units", "mm",
                "-o", str(report_path),
                str(pcb_path),
            ],
            timeout=120,
        )
        # kicad-cli returns non-zero only with --exit-code-violations (we don't
        # pass it), so a failure here is a real CLI/tool problem. But guard on the
        # report existing regardless, so a benign non-zero doesn't lose results.
        if not report_path.exists():
            return [], res.error or "KiCad DRC produced no report."

        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            return [], f"KiCad DRC returned unreadable JSON: {exc}"

    return _map_report(report), None


def _map_report(report: dict) -> list[VerifierIssue]:
    """Flatten KiCad's `violations` + `unconnected_items` + `schematic_parity`
    into VerifierIssues, capped with a rollup so the UI stays scannable."""
    rows: list[dict] = []
    for v in report.get("violations", []):
        rows.append(v)
    # Unconnected items use the same shape but have no top-level `type`.
    for v in report.get("unconnected_items", []):
        rows.append({**v, "type": v.get("type", "unconnected_items")})
    for v in report.get("schematic_parity", []):
        rows.append({**v, "type": v.get("type", "schematic_parity")})

    issues: list[VerifierIssue] = []
    for i, v in enumerate(rows[:MAX_ISSUES]):
        vtype = v.get("type", "drc")
        sev = _SEV.get(v.get("severity", "error"), "warning")
        items = v.get("items", []) or []
        refdes = _first(items, _REFDES_RE)
        net = _first(items, _NET_RE)
        desc = v.get("description") or vtype.replace("_", " ")

        # Footprint/placement-inherent vs routing defect: a violation that
        # touches NO routed object (Track/Via) — only component pads / board
        # edge — is a property of the *parts* (a USB-C connector's tight pad
        # gaps, a module's small mounting holes) or their placement, identical
        # under any router and standard-fab-accepted. Tag it `KICAD_FP_*` and
        # downgrade to info so it doesn't read as a routing bug. Routing defects
        # (clearance/short/width involving a Track/Via) keep their severity.
        touches_routing = any(
            (it.get("description", "") or "").lstrip().startswith(("Track", "Via"))
            for it in items
        )
        footprint_inherent = not touches_routing and vtype in _FOOTPRINT_TYPES
        rule = f"KICAD_{'FP_' if footprint_inherent else ''}{vtype}"
        if footprint_inherent:
            sev = "info"
            desc += " — inherent to the part footprint/placement, not routing"

        issues.append(
            VerifierIssue(
                id=f"{rule}:{i}",
                severity=sev,  # type: ignore[arg-type]
                message=f"KiCad DRC — {desc}",
                refdes=refdes,
                net=net,
                rule=rule,
                source="kicad",
            )
        )

    remaining = len(rows) - MAX_ISSUES
    if remaining > 0:
        issues.append(
            VerifierIssue(
                id="KICAD_more",
                severity="warning",
                message=(
                    f"KiCad DRC — {remaining} more violation(s) not shown "
                    f"(showing the first {MAX_ISSUES}). Export the board and open "
                    f"it in KiCad for the full report."
                ),
                rule="KICAD_summary",
                source="kicad",
            )
        )
    return issues


def merge_into_project(
    project: Project, profile_key: str | None = None
) -> tuple[str | None, int]:
    """Run KiCad DRC against a fab profile and merge its results into
    `project.issues`, replacing any prior `KICAD_*` rows (so successive runs
    don't accumulate stale ones) while leaving the in-house `R*` / `PCB_*`
    diagnostics untouched. Returns `(error, count)`."""
    issues, error = run(project, profile_key)
    project.issues = [
        i for i in project.issues if not (i.rule and i.rule.startswith("KICAD_"))
    ]
    project.issues.extend(issues)
    return error, len(issues)
