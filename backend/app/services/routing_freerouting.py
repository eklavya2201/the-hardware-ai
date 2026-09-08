"""FreeRouting offload — the manufacturing-grade routing path.

The in-house A* router (`routing.py`) is the fast interactive preview. For a
DRC-clean, complete final route we offload to **FreeRouting**, the mature
external autorouter, over the Specctra round-trip — the same "shell out to a
tool, degrade gracefully" pattern as ngspice / kicad-cli:

    project.board (placement) + IR nets
        └─ export.kicad_pcb (unrouted, fab rules)  ──►  .kicad_pcb + .kicad_pro
             └─ kicad_scripts/export_dsn.py         ──►  .dsn
                  └─ freerouting.jar (rip-up + optimise) ──►  .ses
                       └─ kicad_scripts/apply_ses.py ──►  routed tracks/vias (IR coords)
                            └─ rebuild Board.traces / vias

Why this over tuning the grid router: FreeRouting is design-rule-aware and does
rip-up-and-retry, so it routes the dense nets A* gives up on AND respects
clearance/track-width by construction — the four DRC classes the grid router
can't clear on a 0.4 mm grid all go to zero. See docs/ROUTING_KICAD_COMPARISON.

One-way for the IR: we read back only routing *geometry* (a derived Board
artifact), never the IR (components/nets). Everything degrades to `None` (caller
keeps the in-house routes) when Java / FreeRouting / KiCad is absent.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from app.models import Board, RatsLine, Trace, TraceSegment, Via
from app.services import export, fab_profiles, geometry, kicad_bridge

# FreeRouting optimisation effort (max passes). Higher = better routes, slower.
DEFAULT_PASSES = 10
# Hard wall-clock cap, overridable per deployment (FREEROUTING_TIMEOUT seconds).
FREEROUTING_TIMEOUT = int(os.environ.get("FREEROUTING_TIMEOUT", "240"))
# Kill early when this many consecutive passes report identical results —
# on an unroutable board (overlapping placement, DRC-impossible input)
# FreeRouting v2 loops forever instead of giving up, ignoring -mp.
STALL_PASSES = 6

# JVM options for the FreeRouting subprocess. `-Djava.awt.headless=true` runs it
# WITHOUT opening a GUI window — essential both for the server and so a local
# backend doesn't pop a FreeRouting window on every "Finalize". This needs
# FreeRouting **v2.x** (v2.0.0+ is headless-capable and Java-21-compatible; v1.x
# ALWAYS opens the Swing window in batch mode and crashes under headless). Override
# via `FREEROUTING_JAVA_OPTS` (space-separated) if running v1.x with a display.
_DEFAULT_JAVA_OPTS = "-Djava.awt.headless=true"


def _java_opts() -> list[str]:
    return os.environ.get("FREEROUTING_JAVA_OPTS", _DEFAULT_JAVA_OPTS).split()


# "Auto-router pass #12 ... score of 212.40 (86 unrouted and 4 violations)"
# — and, once the violations reach zero, FreeRouting v2 drops the clause:
# "... score of 948.13 (8 unrouted)". The old pattern REQUIRED the violations
# clause, so on prod (review #22's round wearable) 250+ identical passes at
# "8 unrouted" never matched and the watchdog never fired — every Finalize
# ran the full 240 s wall three times over. Violations optional now.
_PASS_RE = re.compile(
    r"pass #(\d+).*?score of ([\d.]+) \((\d+) unrouted(?: and (\d+) violations)?\)",
    re.IGNORECASE,
)


class StallDetector:
    """Watches FreeRouting's per-pass log lines and reports a stall once
    `patience` consecutive passes come back with identical (score, unrouted,
    violations). Pure logic — regression-testable without a JVM."""

    def __init__(self, patience: int = STALL_PASSES):
        self.patience = patience
        self._last: tuple[str, str, str] | None = None
        self._same = 0
        # Last "pass #N ... (U unrouted and V violations)" summary seen — surfaced
        # in the abort message so a timeout on prod is diagnosable from the UI
        # (review #8: a 15-part board hit the 240 s wall with no clue why).
        self.last_pass: str | None = None

    def feed(self, line: str) -> str | None:
        m = _PASS_RE.search(line)
        if not m:
            return None
        pass_no, score, unrouted, violations = m.groups()
        violations = violations or "0"
        self.last_pass = f"pass #{pass_no}: {unrouted} unrouted, {violations} violations"
        stats = (score, unrouted, violations)
        self._same = self._same + 1 if stats == self._last else 0
        self._last = stats
        if self._same >= self.patience and int(unrouted) > 0:
            return (
                f"no progress: still {unrouted} unrouted after "
                f"{pass_no} passes"
            )
        return None


def _run_freerouting(
    cmd: list[str], timeout: float
) -> tuple[int | None, list[str], str | None]:
    """Run FreeRouting, killing it early on timeout or a no-progress stall.
    Returns (returncode, output_lines, abort_reason) — abort_reason is None
    when the process exited by itself."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
    )
    lines: list[str] = []

    def _pump() -> None:
        assert proc.stdout is not None
        for ln in proc.stdout:
            lines.append(ln.rstrip())

    reader = threading.Thread(target=_pump, daemon=True)
    reader.start()

    detector = StallDetector()
    deadline = time.monotonic() + timeout
    seen = 0
    abort: str | None = None
    while proc.poll() is None:
        time.sleep(1.0)
        while seen < len(lines):
            abort = detector.feed(lines[seen])
            seen += 1
            if abort:
                break
        if abort is None and time.monotonic() > deadline:
            abort = f"timed out ({int(timeout)}s)"
            if detector.last_pass:
                abort += f" at {detector.last_pass}"
        if abort:
            proc.kill()
            proc.wait()
            break
    reader.join(timeout=5)
    if abort:
        # Keep the evidence in the server log: the last few FreeRouting lines.
        tail = " | ".join(ln for ln in lines[-6:] if ln.strip())
        print(f"[freerouting] aborted: {abort}. tail: {tail[:600]}", flush=True)
    return proc.returncode, lines, abort


def is_available() -> bool:
    """True if the whole offload chain is usable (KiCad + Java + FreeRouting)."""
    return kicad_bridge.is_available() and kicad_bridge.freerouting_available()


def status() -> str:
    return "available" if is_available() else "absent"


def route(
    project, profile_key: str | None = None, passes: int = DEFAULT_PASSES
) -> tuple[Board | None, str | None]:
    """Route the project's board with FreeRouting. Returns `(board, error)`:
    a new `Board` (same placement, FreeRouting's traces/vias) on success, else
    `(None, reason)`. Never raises — a missing tool / failure is a graceful
    `None` and the caller keeps the in-house routes."""
    board = project.board
    if board is None or not board.components:
        return None, "No placed board — run auto-place first."
    if not kicad_bridge.is_available():
        return None, "KiCad (pcbnew) not installed — needed for the DSN/SES round-trip."
    java = kicad_bridge.find_java()
    jar = kicad_bridge.find_freerouting()
    if java is None or jar is None:
        return None, (
            "FreeRouting not found — set FREEROUTING_JAR (and install a JRE) to "
            "enable manufacturing-grade routing. The in-house router is used "
            "meanwhile."
        )

    profile = fab_profiles.get(profile_key)
    pro_json = json.dumps(profile.as_kicad_pro())

    # 1. Unrouted board: placement + nets + fab rules, NO in-house traces
    #    (FreeRouting does all the routing).
    unrouted = project.model_copy(deep=True)
    unrouted.board.traces = []
    unrouted.board.vias = []
    try:
        pcb_text = export.kicad_pcb(unrouted, design_rules=profile.as_design_rules())
    except export.KicadPcbError as exc:
        return None, str(exc)

    # 2. IR -> DSN (with fab net-class rules via the sibling .kicad_pro).
    dsn_res = kicad_bridge.run_script(
        "export_dsn.py", {"pcb": pcb_text, "kicad_pro": pro_json}, timeout=60
    )
    if not dsn_res.success:
        return None, dsn_res.error or "DSN export failed."
    dsn_text = dsn_res.data.get("dsn", "")
    if not dsn_text:
        return None, "DSN export produced no output."
    # 4-layer boards need NO special handling here: KiCad exports the inner
    # planes as `(plane NET (polygon In1.Cu ...))` and FreeRouting routes the
    # plane nets by dropping vias into them (verified: 20 wires + 10 vias,
    # every wire on F.Cu). Do NOT retype the inner layers `(type power)` — that
    # made FreeRouting route NOTHING (10/10 unrouted, no progress).
    dsn_text = _strip_footprint_keepouts(dsn_text)

    # 3. FreeRouting: .dsn -> .ses. ignore_cleanup_errors: after a proc.kill()
    # on Windows a still-open handle can make the rmtree raise — never let
    # cleanup turn a graceful failure into a 500.
    with tempfile.TemporaryDirectory(
        prefix="voltron_fr_", ignore_cleanup_errors=True
    ) as tmp:
        tmp_path = Path(tmp)
        dsn_path = tmp_path / "board.dsn"
        ses_path = tmp_path / "board.ses"
        try:
            dsn_path.write_text(dsn_text, encoding="utf-8")
        except OSError as exc:
            return None, f"Could not stage the DSN file: {exc}"
        cmd = [
            java,
            *_java_opts(),          # -Djava.awt.headless=true → no GUI window
            "-jar", jar,
            "-de", str(dsn_path),
            "-do", str(ses_path),
            "-mp", str(passes),
        ]
        try:
            rc, out_lines, abort = _run_freerouting(cmd, FREEROUTING_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as exc:
            return None, f"FreeRouting failed to launch: {exc}"
        if abort and abort.startswith("no progress"):
            return None, (
                f"FreeRouting made {abort} — the board as placed can't be "
                "routed DRC-clean (overlapping or too-congested parts are the "
                "usual cause). Adjust the placement and retry; the in-house "
                "routes are kept."
            )
        if abort:
            return None, f"FreeRouting {abort}."
        if not ses_path.exists():
            tail = "\n".join(out_lines).strip()[-400:]
            return None, f"FreeRouting produced no .ses (exit {rc}). {tail}"
        try:
            ses_text = ses_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return None, f"Could not read FreeRouting's .ses: {exc}"

    # 4. .ses -> routed geometry in IR coords.
    ses_res = kicad_bridge.run_script(
        "apply_ses.py",
        {"pcb": pcb_text, "ses": ses_text, "board_height": board.height},
        timeout=60,
    )
    if not ses_res.success:
        return None, ses_res.error or "Applying the .ses failed."

    # 5. Rebuild a Board with FreeRouting's traces/vias (keep placement/outline).
    # Wrapped: malformed apply_ses output (missing keys, short segment tuples,
    # a bad field) must degrade to the graceful (None, reason), not a 500.
    routed = board.model_copy(deep=True)
    try:
        routed.traces = [
            Trace(
                net=t["net"],
                layer=t.get("layer", "top"),
                width=t.get("width", 0.2),
                segments=[
                    TraceSegment(x1=s[0], y1=s[1], x2=s[2], y2=s[3])
                    for s in t.get("segments", [])
                ],
            )
            for t in ses_res.data.get("traces", [])
        ]
        routed.vias = [
            Via(
                x=v["x"], y=v["y"],
                drill=v.get("drill", 0.3),
                diameter=v.get("diameter", 0.6),
                net=v.get("net", ""),
            )
            for v in ses_res.data.get("vias", [])
        ]
    except Exception as exc:  # noqa: BLE001 — route() must never raise
        return None, f"FreeRouting returned malformed geometry: {exc}"

    # FreeRouting silently leaves an unroutable net as an airwire — the .ses
    # simply lacks its wiring. Report those honestly as rats instead of the old
    # hardcoded `rats = []`, which made a partially-routed board claim complete.
    routed.rats = _unrouted_rats(project, routed)
    return routed, None


_KEEPOUT_LINE_RE = re.compile(r"^[ \t]*\(keepout\b[^\n]*\)[ \t]*\n", re.MULTILINE)


def _strip_footprint_keepouts(dsn_text: str) -> str:
    """Drop footprint keep-out rule areas from the DSN before FreeRouting sees
    them. The ESP32-WROOM-32's footprint carries a 48 x 21 mm antenna keep-out
    on every copper layer; on a compact board (a Ø44 watch) other parts' pads
    inevitably sit inside it, FreeRouting treats the area as a hard obstacle
    and leaves those nets unrouted FOREVER ("no progress: still 8 unrouted"
    — the reviewer's stuck Finalize). The in-house preview router ignores
    keep-outs too; KiCad DRC (`items_not_allowed`) stays the honest judge of
    copper under the antenna."""
    return _KEEPOUT_LINE_RE.sub("", dsn_text)


def _unrouted_rats(project, routed: Board) -> list[RatsLine]:
    """Rats for every multi-pin net the routed board carries no copper for —
    one airline per missing pin hop, from real (rotation-aware) pad positions."""
    covered = {t.net for t in routed.traces if t.segments}
    pad_pos: dict[tuple[str, str], tuple[float, float]] = {}
    for pc in routed.components:
        for pad in pc.footprint.pads:
            pad_pos[(pc.refdes, pad.pin_number)] = geometry.pad_world(pc, pad)
    rats: list[RatsLine] = []
    for net in project.ir.nets:
        if len(net.pins) < 2 or net.name in covered:
            continue
        pts = [p for p in (pad_pos.get(tuple(pin)) for pin in net.pins) if p]
        for a, b in zip(pts, pts[1:]):
            rats.append(RatsLine(net=net.name, x1=a[0], y1=a[1], x2=b[0], y2=b[1]))
    return rats
