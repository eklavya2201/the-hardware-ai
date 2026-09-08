"""Export a .kicad_pcb to a Specctra DSN file — task 4.3 (routing offload probe).

Runs INSIDE KiCad's python (has pcbnew). KiCad has NO built-in/headless
autorouter; the only way to offload routing is the Specctra round-trip:
    pcbnew.ExportSpecctraDSN(board, board.dsn)   # <- here
    freerouting.jar board.dsn -> board.ses       # external tool
    pcbnew.ImportSpecctraSES(board, board.ses)   # bring routes back
This script implements only the *export* half — the concrete handoff artifact —
so the comparison harness can prove the offload path is wired without requiring
FreeRouting to be installed. STRICTLY one-way from our IR (we never read board
geometry back into the IR; a routed .ses would be applied to a .kicad_pcb only).

Payload: {"pcb": "<.kicad_pcb text>"}. Output data: {"dsn": "<specctra text>"}.
"""

import json
import os
import sys
import tempfile


def _out(path, ok, data=None, error=None):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"ok": ok, "data": data or {}, "error": error}, fh)


def main():
    out_path = sys.argv[2] if len(sys.argv) > 2 else "out.json"
    try:
        payload = json.load(open(sys.argv[1], encoding="utf-8"))
    except Exception as exc:
        _out(out_path, False, error="bad payload: %s" % exc); return

    try:
        import pcbnew
    except Exception as exc:
        _out(out_path, False, error="pcbnew import failed: %s" % exc); return

    try:
        import wx
        wx.DisableAsserts()  # no modal assert dialogs in a headless run
    except Exception:
        pass

    pcb_text = payload.get("pcb") or ""
    if not pcb_text:
        _out(out_path, False, error="no pcb text in payload"); return

    with tempfile.TemporaryDirectory(prefix="voltron_dsn_") as tmp:
        pcb_path = os.path.join(tmp, "board.kicad_pcb")
        dsn_path = os.path.join(tmp, "board.dsn")
        with open(pcb_path, "w", encoding="utf-8") as fh:
            fh.write(pcb_text)
        # A sibling .kicad_pro makes the loaded board carry the fab net-class
        # rules (KiCad 6+ keeps them in the project), so the exported DSN tells
        # FreeRouting the right clearance/width to route to.
        pro = payload.get("kicad_pro")
        if pro:
            with open(os.path.join(tmp, "board.kicad_pro"), "w", encoding="utf-8") as fh:
                fh.write(pro if isinstance(pro, str) else json.dumps(pro))
        try:
            board = pcbnew.LoadBoard(pcb_path)
        except Exception as exc:
            _out(out_path, False, error="LoadBoard failed: %s" % exc); return
        try:
            ok = pcbnew.ExportSpecctraDSN(board, dsn_path)
        except Exception as exc:
            _out(out_path, False, error="ExportSpecctraDSN failed: %s" % exc); return
        if not os.path.exists(dsn_path):
            _out(out_path, False, error="DSN not written (ExportSpecctraDSN=%r)" % ok); return
        with open(dsn_path, encoding="utf-8", errors="replace") as fh:
            dsn = fh.read()

    _out(out_path, True, data={"dsn": dsn, "bytes": len(dsn)})


if __name__ == "__main__":
    main()
