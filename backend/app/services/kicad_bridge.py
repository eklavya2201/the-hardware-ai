"""Headless KiCad bridge — Week-1 KiCad foundation task 3.1.

`pcbnew` is a Python module that lives *inside KiCad's own interpreter*, not a
wheel we can install into `backend/.venv`. On the Linux deploy box `apt install
kicad` provides a system `python3` that can `import pcbnew` plus the `kicad-cli`
binary; on a Windows dev box KiCad ships its own `python.exe` under
`C:\\Program Files\\KiCad\\<ver>\\bin\\`. So we never import pcbnew here — we
shell out to a small generator script (in `kicad_scripts/`) run *by KiCad's
python*, exchanging JSON files. This mirrors the ngspice pattern in
`spice.py::find_ngspice` / `run_simulation`: locate the tool, run it in a
tempdir, and **always degrade gracefully** (never raise) so stub mode and the
offline demo keep working when KiCad is absent.

Downstream tasks build on this:
- 3.2 (real footprint geometry) runs `dump_footprint.py` through `run_script`.
- 3.4 (IR -> .kicad_pcb) runs `build_pcb.py` through `run_script`.
- 3.3 (per-part STEP/3D) uses `find_kicad_cli()` for `kicad-cli` exports.

Nothing here reads a .kicad_pcb back into the IR — the IR stays the single
source of truth (IR -> KiCad only, never the reverse).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from glob import glob
from pathlib import Path
import tempfile

# Directory holding the standalone scripts executed by KiCad's python.
SCRIPTS_DIR = Path(__file__).resolve().parent / "kicad_scripts"

# How long a single KiCad subprocess may run before we give up.
DEFAULT_TIMEOUT = 30


class _Unset:
    """Sentinel so `None` (resolved-but-absent) is distinct from not-yet-resolved."""


_UNSET = _Unset()

# Module-level caches. Resolving the interpreter probes subprocesses, which is
# slow; footprint strings and the toolchain location are stable within a run.
# Seeded with the sentinel so the first lookup actually probes.
_kicad_python_cache: str | None | _Unset = _UNSET
_probe_cache: dict | None | _Unset = _UNSET


@dataclass
class KicadResult:
    """Uniform result for every KiCad subprocess call. `success=False` (with a
    human-readable `error`) whenever KiCad is missing, the script failed, or it
    timed out — callers never have to catch exceptions."""

    success: bool
    data: dict = field(default_factory=dict)
    error: str | None = None
    raw_log: str = ""


# --------------------------------------------------------------------------- #
# Toolchain discovery
# --------------------------------------------------------------------------- #
def _can_import_pcbnew(python_bin: str) -> bool:
    """True if `python_bin` is a KiCad-flavoured interpreter that has pcbnew."""
    try:
        proc = subprocess.run(
            [python_bin, "-c", "import pcbnew"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _candidate_pythons() -> list[str]:
    """Ordered candidate interpreters likely to expose pcbnew."""
    candidates: list[str] = []

    explicit = os.environ.get("KICAD_PYTHON_BIN")
    if explicit:
        candidates.append(explicit)

    if sys.platform.startswith("win"):
        # KiCad bundles its own python next to pcbnew.dll.
        candidates += sorted(
            glob(r"C:\Program Files\KiCad\*\bin\python.exe"), reverse=True
        )
        candidates += sorted(
            glob(r"C:\Program Files (x86)\KiCad\*\bin\python.exe"), reverse=True
        )
    else:
        # On Debian/Ubuntu (the deploy box) `apt install kicad` builds pcbnew
        # against the *system* python3 at /usr/bin/python3 — NOT the
        # python:3.x-slim interpreter at /usr/local/bin. Prefer the system one.
        candidates.append("/usr/bin/python3")
        which = shutil.which("python3")
        if which:
            candidates.append(which)

    # De-dup while preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            ordered.append(c)
    return ordered


def find_kicad_python() -> str | None:
    """Locate a python interpreter that can `import pcbnew`, or None. Cached."""
    global _kicad_python_cache
    if not isinstance(_kicad_python_cache, _Unset):
        return _kicad_python_cache  # type: ignore[return-value]

    resolved: str | None = None
    for candidate in _candidate_pythons():
        if _can_import_pcbnew(candidate):
            resolved = candidate
            break
    _kicad_python_cache = resolved
    return resolved


def find_kicad_cli() -> str | None:
    """Locate the `kicad-cli` binary (used by 3.3 for STEP/3D exports), or None.

    Not required for the pcbnew scripting path; kept here so the whole KiCad
    toolchain is discovered in one place."""
    explicit = os.environ.get("KICAD_CLI_BIN")
    if explicit and Path(explicit).exists():
        return explicit

    found = shutil.which("kicad-cli")
    if found:
        return found

    if sys.platform.startswith("win"):
        for candidate in sorted(
            glob(r"C:\Program Files\KiCad\*\bin\kicad-cli.exe"), reverse=True
        ):
            if Path(candidate).exists():
                return candidate
    return None


def reset_cache() -> None:
    """Forget cached toolchain discovery (tests / after installing KiCad)."""
    global _kicad_python_cache, _probe_cache
    _kicad_python_cache = _UNSET
    _probe_cache = _UNSET


# --------------------------------------------------------------------------- #
# Generic script runner
# --------------------------------------------------------------------------- #
def run_script(
    script_name: str,
    payload: dict | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> KicadResult:
    """Run `kicad_scripts/<script_name>` under KiCad's python, passing `payload`
    as JSON and returning its JSON output. Always returns a KicadResult; never
    raises for a missing/failed/slow KiCad.

    The convention (see `probe.py`): the script is invoked as
    `python <script> <in.json> <out.json>`, reads the payload from `in.json`,
    and writes a `{ "ok": bool, "data"?: {...}, "error"?: str }` object to
    `out.json`.
    """
    python_bin = find_kicad_python()
    if python_bin is None:
        return KicadResult(
            success=False,
            error=(
                "KiCad (pcbnew) not found. Install KiCad "
                "(https://www.kicad.org/) — on Linux `apt install kicad`, on "
                "Windows the standard installer — or set KICAD_PYTHON_BIN to a "
                "python that can `import pcbnew`."
            ),
        )

    script_path = SCRIPTS_DIR / script_name
    if not script_path.exists():
        return KicadResult(
            success=False,
            error=f"KiCad script not found: {script_path}",
        )

    with tempfile.TemporaryDirectory(prefix="voltron_kicad_") as tmp:
        tmp_path = Path(tmp)
        in_path = tmp_path / "in.json"
        out_path = tmp_path / "out.json"
        in_path.write_text(json.dumps(payload or {}), encoding="utf-8")

        try:
            proc = subprocess.run(
                [python_bin, str(script_path), str(in_path), str(out_path)],
                cwd=str(tmp_path),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return KicadResult(
                success=False,
                error=f"KiCad script '{script_name}' timed out ({timeout}s).",
            )

        log = (proc.stdout or "") + "\n" + (proc.stderr or "")

        if not out_path.exists():
            return KicadResult(
                success=False,
                error=(
                    f"KiCad script '{script_name}' produced no output "
                    f"(exit {proc.returncode}). See raw log."
                ),
                raw_log=log,
            )

        try:
            result = json.loads(out_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            return KicadResult(
                success=False,
                error=f"KiCad script '{script_name}' returned bad JSON: {exc}",
                raw_log=log,
            )

        if not result.get("ok"):
            return KicadResult(
                success=False,
                data=result.get("data", {}) or {},
                error=result.get("error") or "KiCad script reported failure.",
                raw_log=log,
            )

        return KicadResult(
            success=True,
            data=result.get("data", {}) or {},
            raw_log=log,
        )


# --------------------------------------------------------------------------- #
# kicad-cli runner (fab exports + industry DRC — tasks 4.1 / 4.2)
# --------------------------------------------------------------------------- #
def run_cli(args: list[str], timeout: int = 90) -> KicadResult:
    """Run `kicad-cli <args>` and return a KicadResult. Never raises; a missing
    binary / non-zero exit / timeout all come back as `success=False` with a
    human-readable `error`, mirroring `run_script`.

    Unlike `run_script` (which drives *pcbnew* inside KiCad's python), this drives
    the standalone `kicad-cli` binary — the version-stable path for Gerber/drill/
    position exports (4.1) and the industry DRC engine (4.2). `data["stdout"]`
    holds the CLI's stdout for callers that parse it."""
    cli = find_kicad_cli()
    if cli is None:
        return KicadResult(
            success=False,
            error=(
                "kicad-cli not found. Install KiCad (https://www.kicad.org/) or "
                "set KICAD_CLI_BIN to the kicad-cli executable."
            ),
        )
    try:
        proc = subprocess.run(
            [cli, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return KicadResult(
            success=False,
            error=f"kicad-cli timed out ({timeout}s): {' '.join(args[:3])}…",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return KicadResult(success=False, error=f"kicad-cli failed to launch: {exc}")

    log = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if proc.returncode != 0:
        return KicadResult(
            success=False,
            data={"stdout": proc.stdout or "", "returncode": proc.returncode},
            error=(
                f"kicad-cli exited {proc.returncode}: "
                f"{(proc.stderr or proc.stdout or '').strip()[:400]}"
            ),
            raw_log=log,
        )
    return KicadResult(
        success=True,
        data={"stdout": proc.stdout or "", "returncode": 0},
        raw_log=log,
    )


def cli_available() -> bool:
    """True if the `kicad-cli` binary is present (fab exports / industry DRC)."""
    return find_kicad_cli() is not None


# --------------------------------------------------------------------------- #
# FreeRouting discovery (external autorouter — the routing offload)
# --------------------------------------------------------------------------- #
def find_java() -> str | None:
    """Locate a Java runtime for FreeRouting, or None."""
    explicit = os.environ.get("JAVA_BIN")
    if explicit and Path(explicit).exists():
        return explicit
    found = shutil.which("java")
    if found:
        return found
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        cand = Path(java_home) / "bin" / ("java.exe" if sys.platform.startswith("win") else "java")
        if cand.exists():
            return str(cand)
    return None


def find_freerouting() -> str | None:
    """Locate the freerouting jar (env `FREEROUTING_JAR` → common paths), or
    None. FreeRouting is an external GPL autorouter run as a subprocess (like
    ngspice); absent ⇒ callers fall back to the in-house A* router."""
    explicit = os.environ.get("FREEROUTING_JAR")
    if explicit and Path(explicit).exists():
        return explicit
    candidates: list[str] = []
    if sys.platform.startswith("win"):
        candidates += sorted(
            glob(r"C:\Program Files\freerouting*\*.jar"), reverse=True
        )
    candidates += [
        "/usr/share/freerouting/freerouting.jar",
        "/opt/freerouting/freerouting.jar",
        "/usr/local/share/freerouting/freerouting.jar",
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    return None


def freerouting_available() -> bool:
    """True if both Java and the freerouting jar are present."""
    return find_java() is not None and find_freerouting() is not None


# --------------------------------------------------------------------------- #
# Probe / availability (health endpoint)
# --------------------------------------------------------------------------- #
def probe() -> dict | None:
    """Return `{version, python, ...}` if KiCad/pcbnew is usable, else None.
    Cached — safe to call from the health endpoint on every request."""
    global _probe_cache
    if not isinstance(_probe_cache, _Unset):
        return _probe_cache  # type: ignore[return-value]

    result = run_script("probe.py", timeout=20)
    _probe_cache = result.data if result.success else None
    return _probe_cache


def is_available() -> bool:
    """True if the pcbnew scripting path is usable end-to-end."""
    return probe() is not None


def kicad_status() -> str:
    """'available' | 'absent' — for /api/health, mirroring agent_mode."""
    return "available" if is_available() else "absent"
