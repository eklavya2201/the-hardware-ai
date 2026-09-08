"""Build a real .kicad_pcb from the Voltron IR board — task 3.4.

Runs INSIDE KiCad's python (has pcbnew), invoked by
`kicad_bridge.run_script("build_pcb.py", payload)`. Consumes the placed board
(components with real Lib:Name footprints, nets, traces, vias) and emits a valid
`.kicad_pcb` that opens in KiCad / goes to fab.

STRICTLY ONE-WAY: IR -> KiCad. There is NO .kicad_pcb -> IR path, ever. The IR
is the single source of truth (the LLM only mutates it through tools); reading a
board back would let hand-edits bypass that anti-hallucination guarantee. This
script only *writes*.

Coordinate transform: the IR board is mm, origin bottom-left, Y-up. KiCad is mm,
origin top-left, Y-down. Converting is a vertical mirror of the coordinate frame:
    x_k = x,  y_k = board_height - y,  orientation_k = -rotation
which yields an equivalent top-view layout (same layer, same relative geometry).

Payload: {board:{width,height,layer_count}, components:[{refdes,footprint,x,y,
rotation,layer,value}], nets:[{name,pads:[[refdes,pin],...]}],
traces:[{net,layer,width,segments:[[x1,y1,x2,y2],...]}],
vias:[{x,y,drill,diameter,net}]}. Output data: {"pcb": "<.kicad_pcb text>"}.
"""

import glob
import json
import os
import sys
import tempfile


def _out(path, ok, data=None, error=None):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"ok": ok, "data": data or {}, "error": error}, fh)


def _dr_set(ds, field, value_mm, mm):
    """Set a BOARD_DESIGN_SETTINGS member from a mm value, tolerating pcbnew API
    drift — an absent member across KiCad majors is skipped, not fatal."""
    if value_mm is None:
        return
    if hasattr(ds, field):
        setattr(ds, field, mm(float(value_mm)))


# --- footprint library resolution (mirror dump_footprint.py) ------------------
def _footprint_base_dirs():
    dirs = []
    here = os.path.dirname(os.path.abspath(__file__))
    dirs.append(os.path.normpath(os.path.join(here, "..", "..", "library", "footprints")))
    for key, val in os.environ.items():
        if key.startswith("KICAD") and key.endswith("FOOTPRINT_DIR") and val:
            dirs.append(val)
    prefix = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
    dirs.append(os.path.join(prefix, "share", "kicad", "footprints"))
    dirs += ["/usr/share/kicad/footprints", "/usr/local/share/kicad/footprints"]
    dirs += glob.glob(r"C:\Program Files\KiCad\*\share\kicad\footprints")
    seen, out = set(), []
    for d in dirs:
        if d and d not in seen and os.path.isdir(d):
            seen.add(d); out.append(d)
    return out


def _find_pretty(lib, bases):
    for base in bases:
        cand = os.path.join(base, lib + ".pretty")
        if os.path.isdir(cand):
            return cand
    return None


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

    mm = pcbnew.FromMM
    bases = _footprint_base_dirs()
    board = pcbnew.BOARD()

    # --- fab-profile design rules (task 5 / engineering-rules Phase 2) ---
    # When the caller passes a `design_rules` dict (from fab_profiles), apply it
    # in TWO places so it governs the whole toolchain consistently:
    #   1. the default NET CLASS (clearance / track width / via size / drill) —
    #      this is what BOTH `kicad-cli pcb drc` checks against AND what
    #      ExportSpecctraDSN writes into the DSN, so an external router
    #      (FreeRouting) routes to the exact same rules KiCad then verifies.
    #   2. the BOARD_DESIGN_SETTINGS minimums (the DRC floor).
    # Setting only the minimums (the old bug) left the net class at KiCad's
    # defaults, so DRC used 0.2 mm clearance / 0.3 mm min-hole regardless of the
    # profile — flagging standard part footprints (USB-C 0.15 mm pad gaps,
    # 0.2 mm mounting holes) that the chosen fab actually accepts. Version-
    # tolerant: every call is guarded so pcbnew API drift can't break the build.
    warnings = []
    dr = payload.get("design_rules") or {}
    if dr:
        try:
            ds = board.GetDesignSettings()
            clearance = dr.get("min_clearance")
            track = dr.get("min_track_width")
            via_d = dr.get("min_via_diameter")
            drill = dr.get("min_through_drill")

            # 1. Default net class — drives routing (DSN) + DRC clearance.
            try:
                nc = ds.m_NetSettings.GetDefaultNetclass()
                if clearance is not None:
                    nc.SetClearance(mm(float(clearance)))
                if track is not None:
                    # Route a touch wider than the bare minimum for margin.
                    nc.SetTrackWidth(mm(max(float(track), 0.2)))
                if via_d is not None:
                    nc.SetViaDiameter(mm(float(via_d)))
                if drill is not None:
                    nc.SetViaDrill(mm(float(drill)))
            except Exception as exc:
                warnings.append("netclass: %s" % exc)

            # 2. Board minimums (the DRC floor).
            _dr_set(ds, "m_MinClearance", clearance, mm)
            _dr_set(ds, "m_TrackMinWidth", track, mm)
            _dr_set(ds, "m_ViasMinSize", via_d, mm)
            _dr_set(ds, "m_MinThroughDrill", drill, mm)
            _dr_set(ds, "m_ViasMinAnnularWidth", dr.get("min_annular"), mm)
            _dr_set(ds, "m_HoleToHoleMin", dr.get("hole_to_hole"), mm)
            _dr_set(ds, "m_CopperEdgeClearance", dr.get("edge_clearance"), mm)
        except Exception as exc:
            warnings.append("design_rules: %s" % exc)

    bh = float(payload.get("board", {}).get("height", 40.0))
    bw = float(payload.get("board", {}).get("width", 50.0))

    def yk(y):
        return bh - float(y)   # IR Y-up -> KiCad Y-down

    # --- board outline on Edge.Cuts (rectangle, or a circle for round boards) ---
    if payload.get("board", {}).get("shape") == "round":
        circ = pcbnew.PCB_SHAPE(board)
        circ.SetShape(pcbnew.SHAPE_T_CIRCLE)
        # Centre + a point on the rim define the circle; IR centre is (w/2, w/2).
        circ.SetCenter(pcbnew.VECTOR2I(mm(bw / 2), mm(yk(bw / 2))))
        circ.SetEnd(pcbnew.VECTOR2I(mm(bw), mm(yk(bw / 2))))
        circ.SetLayer(pcbnew.Edge_Cuts)
        circ.SetWidth(mm(0.15))
        board.Add(circ)
    else:
        corners = [(0, 0), (bw, 0), (bw, bh), (0, bh)]
        for i in range(4):
            x1, y1 = corners[i]; x2, y2 = corners[(i + 1) % 4]
            seg = pcbnew.PCB_SHAPE(board)
            seg.SetShape(pcbnew.SHAPE_T_SEGMENT)
            seg.SetStart(pcbnew.VECTOR2I(mm(x1), mm(yk(y1))))
            seg.SetEnd(pcbnew.VECTOR2I(mm(x2), mm(yk(y2))))
            seg.SetLayer(pcbnew.Edge_Cuts)
            seg.SetWidth(mm(0.15))
            board.Add(seg)

    # --- nets ---
    netmap = {}
    for i, net in enumerate(payload.get("nets", []), start=1):
        name = net["name"]
        try:
            ni = pcbnew.NETINFO_ITEM(board, name, i)
            board.Add(ni)
            netmap[name] = ni
        except Exception as exc:
            warnings.append("net %s: %s" % (name, exc))
    # (refdes, pin) -> net name, for pad assignment
    pad_net = {}
    for net in payload.get("nets", []):
        for pair in net.get("pads", []):
            pad_net[(pair[0], str(pair[1]))] = net["name"]

    # --- components ---
    placed = 0
    for c in payload.get("components", []):
        fpid = c.get("footprint") or ""
        if ":" not in fpid:
            warnings.append("%s: no Lib:Name footprint (%r)" % (c.get("refdes"), fpid)); continue
        lib, name = fpid.split(":", 1)
        pretty = _find_pretty(lib, bases)
        if pretty is None:
            warnings.append("%s: lib %s not found" % (c.get("refdes"), lib)); continue
        try:
            fp = pcbnew.FootprintLoad(pretty, name)
        except Exception as exc:
            warnings.append("%s: load %s failed: %s" % (c.get("refdes"), fpid, exc)); continue
        if fp is None:
            warnings.append("%s: footprint %s not in %s" % (c.get("refdes"), name, lib)); continue
        board.Add(fp)
        fp.SetReference(c.get("refdes", "?"))
        try:
            fp.SetValue(c.get("value") or c.get("refdes", ""))
        except Exception:
            pass
        fp.SetPosition(pcbnew.VECTOR2I(mm(float(c.get("x", 0))), mm(yk(c.get("y", 0)))))
        try:
            fp.SetOrientationDegrees(-float(c.get("rotation", 0)))
        except Exception:
            pass
        if c.get("layer") == "bottom":
            try:
                fp.Flip(fp.GetPosition(), False)
            except Exception:
                pass
        # assign nets to pads
        for pad in fp.Pads():
            nm = pad_net.get((c.get("refdes"), pad.GetNumber()))
            if nm and nm in netmap:
                pad.SetNet(netmap[nm])
        placed += 1

    # --- traces ---
    LAYER = {"top": pcbnew.F_Cu, "bottom": pcbnew.B_Cu}
    for tr in payload.get("traces", []):
        layer = LAYER.get(tr.get("layer", "top"), pcbnew.F_Cu)
        width = mm(float(tr.get("width", 0.2)))
        netobj = netmap.get(tr.get("net"))
        for s in tr.get("segments", []):
            t = pcbnew.PCB_TRACK(board)
            t.SetStart(pcbnew.VECTOR2I(mm(float(s[0])), mm(yk(s[1]))))
            t.SetEnd(pcbnew.VECTOR2I(mm(float(s[2])), mm(yk(s[3]))))
            t.SetWidth(width)
            t.SetLayer(layer)
            if netobj is not None:
                t.SetNet(netobj)
            board.Add(t)

    # --- vias ---
    for v in payload.get("vias", []):
        try:
            via = pcbnew.PCB_VIA(board)
            via.SetPosition(pcbnew.VECTOR2I(mm(float(v.get("x", 0))), mm(yk(v.get("y", 0)))))
            via.SetDrill(mm(float(v.get("drill", 0.3))))
            via.SetWidth(mm(float(v.get("diameter", 0.6))))
            netobj = netmap.get(v.get("net"))
            if netobj is not None:
                via.SetNet(netobj)
            board.Add(via)
        except Exception as exc:
            warnings.append("via: %s" % exc)

    # --- 4-layer: copper layer count + one solid zone per inner-layer plane ---
    # (signal / GND / power / signal). Each plane is a zone over the whole
    # outline on In1.Cu / In2.Cu carrying its net; pads of that net connect
    # through the vias/plated holes the router already placed. The zone's fill
    # is COMPUTED HERE (`SetFilledPolysList`): pcbnew's ZONE_FILLER segfaults
    # in a headless KiCad 9 python (verified on the prod image), and the only
    # copper on an inner layer besides the plane is vias + plated holes, so the
    # fill is the outline minus a clearance void around every foreign
    # via/through-hole. Version-tolerant, every call guarded.
    layer_count = int(payload.get("board", {}).get("layer_count", 2) or 2)
    planes = payload.get("board", {}).get("planes") or []
    if layer_count > 2:
        import math as _m

        try:
            board.SetCopperLayerCount(layer_count)
        except Exception as exc:
            warnings.append("layer_count: %s" % exc)
        inner = {"In1": getattr(pcbnew, "In1_Cu", None), "In2": getattr(pcbnew, "In2_Cu", None)}
        is_round = payload.get("board", {}).get("shape") == "round"
        inset = 0.5  # keep the plane inside the copper-to-edge clearance
        try:
            plane_clear = mm(max(0.25, float((payload.get("design_rules") or {}).get("min_clearance") or 0.2) + 0.05))
        except Exception:
            plane_clear = mm(0.25)

        def _outline_pts():
            if is_round:
                r = bw / 2.0 - inset
                pts = [(mm(bw / 2 + r * _m.cos(2 * _m.pi * k / 72)), mm(yk(bw / 2 + r * _m.sin(2 * _m.pi * k / 72))))
                       for k in range(72)]
            else:
                pts = [(mm(px), mm(yk(py))) for (px, py) in
                       ((inset, inset), (bw - inset, inset), (bw - inset, bh - inset), (inset, bh - inset))]
            # The Y flip reverses winding; a negative-area outline reads as a
            # hole to the polygon booleans (subtracting the voids then LEFT
            # only the voids). Keep the outline positive.
            area = 0
            for i in range(len(pts)):
                x1, y1 = pts[i]
                x2, y2 = pts[(i + 1) % len(pts)]
                area += x1 * y2 - x2 * y1
            return pts if area > 0 else list(reversed(pts))

        def _circle(cx, cy, r, n=32):
            poly = pcbnew.SHAPE_POLY_SET()
            poly.NewOutline()
            rr = r / _m.cos(_m.pi / n)  # circumscribed: the void never dips under r
            for k in range(n):
                a = 2 * _m.pi * k / n
                poly.Append(int(cx + rr * _m.cos(a)), int(cy + rr * _m.sin(a)))
            return poly

        def _keep_main_outline(poly):
            # Overlapping voids (a module's thermal-via array) leave copper
            # slivers enclosed inside the void cluster — isolated islands the
            # DRC rightly rejects. Keep only the largest outline (the plane).
            try:
                n = poly.OutlineCount()
                if n <= 1:
                    return
                areas = [abs(poly.Outline(i).Area()) for i in range(n)]
                keep = areas.index(max(areas))
                for i in range(n - 1, -1, -1):
                    if i != keep:
                        poly.DeletePolygon(i)
            except Exception:
                pass

        def _poly_op(poly, other, op):
            fn = getattr(poly, op)
            try:
                fn(other, pcbnew.SHAPE_POLY_SET.PM_FAST)  # KiCad <= 8
            except Exception:
                fn(other)  # KiCad 9 dropped the mode argument

        def _fracture(poly):
            # The file format stores fills fractured (no holes: each void is
            # joined to the outline by a zero-width slit); an unfractured
            # polygon-with-holes saved as `filled_polygon` loses its outline.
            try:
                poly.Fracture(pcbnew.SHAPE_POLY_SET.PM_FAST)
            except Exception:
                try:
                    poly.Fracture()
                except Exception:
                    pass

        def _via_width(v):
            try:
                return v.GetWidth(pcbnew.F_Cu)
            except Exception:
                return v.GetWidth()

        # Foreign copper that pierces the inner layers: vias + through-hole pads
        # (a void per item, net-agnostic for NPTH). Collected once, reused per plane.
        holes = []  # (x, y, radius, netname or None)
        try:
            for tr in board.Tracks():
                if tr.GetClass() == "PCB_VIA":
                    pos = tr.GetPosition()
                    holes.append((pos.x, pos.y, _via_width(tr) // 2, tr.GetNetname()))
            for fp in board.GetFootprints():
                for pad in fp.Pads():
                    try:
                        drill = pad.GetDrillSize()
                        if drill.x <= 0:
                            continue  # SMD — outer layers only
                        size = pad.GetSize()
                        rad = max(size.x, size.y) // 2
                        pos = pad.GetPosition()
                        holes.append((pos.x, pos.y, rad, pad.GetNetname() or None))
                    except Exception:
                        continue
        except Exception as exc:
            warnings.append("plane voids: %s" % exc)

        # NOTE: footprint copper-pour keep-outs (the ESP32 antenna) are NOT
        # subtracted from the plane. FreeRouting never sees keep-outs (the
        # DSN strips them so a compact board can finalize at all) and drops
        # plane vias wherever the plane OUTLINE is — a void here would leave
        # those vias dangling. KiCad DRC `items_not_allowed` still reports
        # copper under the antenna, which is the honest signal.
        for pl in planes:
            try:
                lay = inner.get(pl.get("layer"))
                netobj = netmap.get(pl.get("net"))
                if lay is None or netobj is None:
                    warnings.append("plane %s skipped (layer/net)" % pl)
                    continue
                zone = pcbnew.ZONE(board)
                zone.SetLayer(lay)
                try:
                    ls = pcbnew.LSET()
                    ls.AddLayer(lay)
                    zone.SetLayerSet(ls)  # KiCad 9: SetLayer alone left the zone on F.Cu
                except Exception:
                    pass
                zone.SetNet(netobj)
                try:
                    zone.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
                except Exception:
                    pass
                pts = _outline_pts()
                outline = zone.Outline()
                outline.NewOutline()
                for (x, y) in pts:
                    outline.Append(x, y)
                fill = pcbnew.SHAPE_POLY_SET()
                fill.NewOutline()
                for (x, y) in pts:
                    fill.Append(x, y)
                # Union all voids first, then ONE subtraction: subtracting
                # overlapping circles one at a time (an ESP32's thermal-via
                # array) left only slivers between them.
                voids = pcbnew.SHAPE_POLY_SET()
                any_void = False
                for (hx, hy, hr, hnet) in holes:
                    if hnet == pl.get("net"):
                        continue  # same net: solid connection to the plane
                    _poly_op(voids, _circle(hx, hy, hr + plane_clear), "BooleanAdd")
                    any_void = True
                if any_void:
                    _poly_op(fill, voids, "BooleanSubtract")
                    _keep_main_outline(fill)
                _fracture(fill)
                zone.SetFilledPolysList(lay, fill)
                try:
                    zone.SetLocalClearance(plane_clear)  # DRC checks the void we cut, not the 0.5 default
                except Exception:
                    pass
                zone.SetIsFilled(True)
                try:
                    zone.SetNeedRefill(False)
                except Exception:
                    pass
                board.Add(zone)
            except Exception as exc:
                warnings.append("plane %s: %s" % (pl.get("net"), exc))

    # --- save + read back the text ---
    with tempfile.TemporaryDirectory(prefix="voltron_pcb_") as tmp:
        pcb_path = os.path.join(tmp, "board.kicad_pcb")
        pcbnew.SaveBoard(pcb_path, board)
        with open(pcb_path, encoding="utf-8") as fh:
            text = fh.read()

    _out(out_path, True, data={
        "pcb": text,
        "placed": placed,
        "components": len(payload.get("components", [])),
        "warnings": warnings[:40],
    })


if __name__ == "__main__":
    main()
