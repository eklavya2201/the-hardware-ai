"""Apply a FreeRouting Specctra .ses session back onto a board and dump the
routed tracks/vias in IR coordinates — engineering-rules / routing offload.

Runs INSIDE KiCad's python (has pcbnew). The FreeRouting round-trip is:
    IR -> .kicad_pcb -> ExportSpecctraDSN -> .dsn
    freerouting.jar .dsn -> .ses               (external autorouter)
    ImportSpecctraSES(.ses) -> routed board    <- HERE
This script does the final leg: load the (unrouted) board, import the .ses, and
return the resulting copper as plain track/via geometry in the IR's coordinate
frame (Y-up, origin bottom-left) so the service can rebuild `Board.traces/vias`.

STRICTLY one-way for the IR: we only read back *routing geometry* (traces/vias),
which is a derived Board artifact — NOT the IR (components/nets). The IR stays
the single source of truth; FreeRouting is just a better trace generator for the
nets the IR already defines.

Payload: {"pcb": "<unrouted .kicad_pcb text>", "ses": "<.ses text>",
"board_height": H}. Output data: {"traces": [{net,layer,width,segments:
[[x1,y1,x2,y2],...]}], "vias": [{x,y,drill,diameter,net}]}.
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

    # Suppress wxWidgets debug-assert dialogs — a modal popup would hang this
    # headless subprocess (e.g. deprecated PCB_VIA.GetWidth() asserts).
    try:
        import wx
        wx.DisableAsserts()
    except Exception:
        pass

    pcb_text = payload.get("pcb") or ""
    ses_text = payload.get("ses") or ""
    bh = float(payload.get("board_height", 0.0))
    if not pcb_text or not ses_text:
        _out(out_path, False, error="pcb and ses required"); return

    def yk(y_kicad):
        return bh - y_kicad  # KiCad Y-down -> IR Y-up

    with tempfile.TemporaryDirectory(prefix="voltron_ses_") as tmp:
        pcb_path = os.path.join(tmp, "board.kicad_pcb")
        ses_path = os.path.join(tmp, "board.ses")
        with open(pcb_path, "w", encoding="utf-8") as fh:
            fh.write(pcb_text)
        with open(ses_path, "w", encoding="utf-8") as fh:
            fh.write(ses_text)
        try:
            board = pcbnew.LoadBoard(pcb_path)
        except Exception as exc:
            _out(out_path, False, error="LoadBoard failed: %s" % exc); return
        try:
            pcbnew.ImportSpecctraSES(board, ses_path)
        except Exception as exc:
            _out(out_path, False, error="ImportSpecctraSES failed: %s" % exc); return

        to_mm = pcbnew.ToMM
        F_Cu, B_Cu = pcbnew.F_Cu, pcbnew.B_Cu
        traces = []
        vias = []
        for t in board.GetTracks():
            klass = t.GetClass()
            netname = t.GetNetname() or ""
            if klass == "PCB_VIA":
                pos = t.GetPosition()
                # KiCad 10's PCB_VIA.GetWidth() asserts (and pops a modal dialog
                # that would hang a headless run) unless given a layer — vias can
                # be per-layer sized. Read the diameter on the via's top copper
                # layer.
                try:
                    diameter = to_mm(t.GetWidth(t.TopLayer()))
                except Exception:
                    diameter = to_mm(t.GetWidth(F_Cu))
                vias.append({
                    "x": to_mm(pos.x),
                    "y": yk(to_mm(pos.y)),
                    "drill": to_mm(t.GetDrillValue()),
                    "diameter": diameter,
                    "net": netname,
                })
            elif klass in ("PCB_TRACK", "PCB_ARC"):
                if t.GetLayer() not in (F_Cu, B_Cu):
                    # An inner-layer track (4-layer board): the Board model has
                    # no inner signal layers — the planes are the only inner
                    # copper — so never mislabel it as bottom; the net's plane
                    # via/pad connectivity is what carries it.
                    continue
                layer = "top" if t.GetLayer() == F_Cu else "bottom"
                s, e = t.GetStart(), t.GetEnd()
                # An arc (from FreeRouting's optimiser) must NOT be dropped — that
                # would split the net. Our Trace model is straight segments, so
                # approximate the arc as two chords through its midpoint: keeps
                # connectivity and roughly the shape. Straight tracks stay one seg.
                if klass == "PCB_ARC":
                    try:
                        m = t.GetMid()
                        pts = [s, m, e]
                    except Exception:
                        pts = [s, e]
                else:
                    pts = [s, e]
                segments = [
                    [to_mm(pts[k].x), yk(to_mm(pts[k].y)),
                     to_mm(pts[k + 1].x), yk(to_mm(pts[k + 1].y))]
                    for k in range(len(pts) - 1)
                ]
                traces.append({
                    "net": netname,
                    "layer": layer,
                    "width": to_mm(t.GetWidth()),
                    "segments": segments,
                })

    _out(out_path, True, data={"traces": traces, "vias": vias})


if __name__ == "__main__":
    main()
