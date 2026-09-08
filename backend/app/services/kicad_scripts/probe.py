"""KiCad-python probe — runs INSIDE KiCad's interpreter, not backend/.venv.

Executed by `kicad_bridge.run_script("probe.py")` to confirm pcbnew is usable
and report its version for /api/health. Follows the bridge convention:

    python probe.py <in.json> <out.json>

reads the (unused) payload from in.json and writes
`{"ok": true, "data": {...}}` — or `{"ok": false, "error": "..."}` — to
out.json. Must stay dependency-free (KiCad's python is not our venv): stdlib +
pcbnew only.
"""

import json
import sys


def main() -> None:
    out_path = sys.argv[2] if len(sys.argv) > 2 else "out.json"
    try:
        import pcbnew  # noqa: F401  (import is the point of the probe)

        data = {
            "version": pcbnew.GetBuildVersion(),
            "python": sys.version.split()[0],
        }
        # SETTINGS_MANAGER exposes the stock footprint-library table location,
        # which 3.2 will need; surface it here so health can confirm libs exist.
        try:
            data["semantic_version"] = pcbnew.GetSemanticVersion()
        except Exception:
            pass

        result = {"ok": True, "data": data}
    except Exception as exc:  # pragma: no cover - depends on host KiCad
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh)


if __name__ == "__main__":
    main()
