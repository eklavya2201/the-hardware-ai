"""Generate real parametric 3D solids for mechanical parts that have no vendor
STEP model, so the 3D view shows the actual part shape (metal can, barrel lens,
coin motor, encoder shaft, screw terminal, plated ring) instead of a fallback
box. Built from trimesh primitives, mm, Z-up with the base at z=0 (matching the
KiCad-model convention: spec rotation [-90,0,0] maps Z-up -> our Y-up scene).
Wires each into models3d.manifest.json keyed by the part's footprint string.
"""
from __future__ import annotations
import json, math, sys
from pathlib import Path
import numpy as np
import trimesh
from trimesh.creation import cylinder, box, annulus, icosphere, cone

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))
from dotenv import load_dotenv; load_dotenv()
from app.services import library

AUTO = _BACKEND.parent / "frontend" / "public" / "models" / "auto"
MANIFEST = _BACKEND.parent / "frontend" / "src" / "lib" / "models3d.manifest.json"

C = {  # RGBA face colors for a touch of realism
    "metal": [180, 182, 188, 255], "black": [40, 40, 44, 255],
    "gold": [212, 175, 55, 255], "green": [30, 120, 60, 255],
    "silver": [200, 200, 205, 255], "red": [200, 60, 60, 255],
    "blue": [60, 90, 200, 255], "glass": [200, 220, 230, 255],
    "cream": [225, 220, 200, 255], "white": [235, 235, 235, 255],
}


def _c(mesh, color):
    mesh.visual.face_colors = C[color]
    return mesh


def _cyl(r, h, z0, color, sections=48):
    m = cylinder(radius=r, height=h, sections=sections)
    m.apply_translation([0, 0, z0 + h / 2])
    return _c(m, color)


def _box(x, y, h, z0, color, cx=0.0, cy=0.0):
    m = box(extents=[x, y, h]); m.apply_translation([cx, cy, z0 + h / 2])
    return _c(m, color)


def _merge(parts):
    return trimesh.util.concatenate(parts)


# ---- part builders (dims ≈ real datasheet mm) --------------------------------
def mounting_hole(inner_d):
    return _merge([_c(annulus(r_min=inner_d / 2, r_max=inner_d / 2 + 0.8, height=1.6), "gold")])

def test_point(d):
    return _merge([_cyl(d / 2, 0.4, 0, "gold"), _cyl(d / 2 * 0.6, 0.7, 0.4, "gold")])

def coin_motor(d, h):
    return _merge([_cyl(d / 2, h, 0, "metal"), _cyl(d / 6, 0.6, h, "gold")])

def speaker(d):
    return _merge([_cyl(d / 2, 4, 0, "black"),
                   _c(cone(radius=d / 2 * 0.8, height=2.5, sections=48).apply_translation([0, 0, 4]), "cream")])

def mq_sensor():
    base = _cyl(9.5, 3.0, 0, "cream")          # bakelite base
    can = _cyl(4.75, 9.0, 3.0, "metal")        # steel can
    mesh = _c(icosphere(subdivisions=2, radius=4.75), "metal"); mesh.apply_scale([1, 1, 0.4]); mesh.apply_translation([0, 0, 12.0])
    return _merge([base, can, mesh])

def reed_switch():
    tube = cylinder(radius=1.1, height=14, sections=32); tube.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0]))
    tube.apply_translation([0, 0, 1.1]); _c(tube, "glass")
    l1 = cylinder(radius=0.25, height=6, sections=12); l1.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0])); l1.apply_translation([-9.5, 0, 1.1]); _c(l1, "silver")
    l2 = cylinder(radius=0.25, height=6, sections=12); l2.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0])); l2.apply_translation([9.5, 0, 1.1]); _c(l2, "silver")
    return _merge([tube, l1, l2])

def ec11_encoder():
    body = _box(12, 12, 6.5, 0, "silver")
    bush = _cyl(3.5, 1.5, 6.5, "silver")
    shaft = _cyl(3.0, 13, 8.0, "black")
    return _merge([body, bush, shaft])

def tsop_ir():
    body = _box(6, 2.0, 4.5, 1.0, "black", cy=0.4)
    lens = _c(icosphere(subdivisions=2, radius=2.6), "black"); lens.apply_scale([1, 0.5, 0.8]); lens.apply_translation([0, -1.2, 3.0])
    return _merge([body, lens])

def screw_terminal(n, pitch=5.0):
    parts = []
    x0 = -(n - 1) * pitch / 2
    for i in range(n):
        x = x0 + i * pitch
        parts.append(_box(pitch - 0.3, 8.0, 9.0, 0, "blue", cx=x))          # housing
        parts.append(_cyl(1.7, 1.0, 9.0, "silver").apply_translation([x, -1.5, 0]))  # screw head
    return _merge(parts)

def led_3mm(color="white"):
    """3 mm through-hole LED on KiCad's LED_D3.0mm: pin 1 (K) at the origin,
    pin 2 (A) at +2.54, body centred on the pad field (1.27, 0) — the model
    is built in the footprint frame so it lands on its pads. Flange + 3 mm
    cylinder + dome (5.3 mm total, the curated bodyHeightMm) + two leads."""
    cx = 1.27
    lens = _cyl(1.5, 3.8, 1.0, color); lens.apply_translation([cx, 0, 0])
    dome = icosphere(subdivisions=3, radius=1.5); dome.apply_translation([cx, 0, 4.8])
    dome = dome.slice_plane([cx, 0, 4.8], [0, 0, 1]); _c(dome, color)
    flange = _cyl(1.9, 1.0, 0, color); flange.apply_translation([cx, 0, 0])
    lead1 = _cyl(0.25, 3.0, -3.0, "silver"); lead1.apply_translation([0.0, 0, 0])
    lead2 = _cyl(0.25, 3.0, -3.0, "silver"); lead2.apply_translation([2.54, 0, 0])
    return _merge([flange, lens, dome, lead1, lead2])


def led_5mm(color="white"):
    """5 mm through-hole LED on KiCad's LED_D5.0mm: same frame as led_3mm
    (pin 1 K at the origin, pin 2 A at +2.54, body centred on the pad field) —
    2.5 mm-radius lens, 8.6 mm curated height, flange, two sunk leads."""
    cx = 1.27
    lens = _cyl(2.5, 6.1, 1.0, color); lens.apply_translation([cx, 0, 0])
    dome = icosphere(subdivisions=3, radius=2.5); dome.apply_translation([cx, 0, 7.1])
    dome = dome.slice_plane([cx, 0, 7.1], [0, 0, 1]); _c(dome, color)
    flange = _cyl(2.9, 1.0, 0, color); flange.apply_translation([cx, 0, 0])
    lead1 = _cyl(0.25, 3.0, -3.0, "silver")
    lead2 = _cyl(0.25, 3.0, -3.0, "silver"); lead2.apply_translation([2.54, 0, 0])
    return _merge([flange, lens, dome, lead1, lead2])


def r_axial():
    """Axial THT resistor lying flat on R_Axial_DIN0207 (10.16 mm pitch):
    tan body 6.3 x 2.5 centred between the pads, bent leads sunk at 0/10.16."""
    cx = 10.16 / 2
    body = _cyl(1.25, 6.3, 0.0, "cream")
    body.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [0, 1, 0]))
    body.apply_translation([cx, 0, 1.25 + 0.1])
    l1 = _cyl(0.3, 4.0, -3.0, "silver")
    l2 = _cyl(0.3, 4.0, -3.0, "silver"); l2.apply_translation([10.16, 0, 0])
    h1 = _cyl(0.3, 2.0, 1.0, "silver")
    h1.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [0, 1, 0]))
    h1.apply_translation([1.0, 0, 1.35])
    h2 = h1.copy(); h2.apply_translation([10.16 - 2 * 1.0, 0, 0])
    return _merge([body, l1, l2, h1, h2])


def u_fl():
    """Hirose U.FL receptacle: 2.6 x 2.6 x 1.25 mm metal can with the coax
    barrel, origin-centred (the KiCad footprint is centre-anchored)."""
    base = _box(2.6, 2.6, 0.4, 0.0, "gold")
    barrel = _cyl(1.0, 0.85, 0.4, "silver")
    pin = _cyl(0.25, 0.85, 0.4, "gold")
    return _merge([base, barrel, pin])


def sma_edge():
    """Amphenol 132289 edge-launch SMA: 9.5 mm hex-ish body block + threaded
    barrel pointing off-board along -Y (the mating direction), centre pin."""
    block = _box(7.9, 5.0, 6.35, 0.0, "gold", cy=2.5)
    barrel = _cyl(3.1, 7.0, 0.0, "gold")
    barrel.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [1, 0, 0]))
    barrel.apply_translation([0, -3.5, 3.17])
    pin = _cyl(0.35, 9.0, 0.0, "silver")
    pin.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [1, 0, 0]))
    pin.apply_translation([0, -4.5, 3.17])
    return _merge([block, barrel, pin])


def dip(n, row=7.62, pitch=2.54):
    """Through-hole DIP-N on KiCad's Package_DIP:DIP-N_W7.62mm, built in the
    FOOTPRINT frame: pin 1 at the origin, pins run toward -Y, the second row at
    x=+row. Body = 6.35 wide (JEDEC MS-001), (N/2)*pitch long, 3.3 tall on
    0.5 mm standoff feet; pin-1 notch; N leads sunk through the board. A
    DIP-20 rendered with the DIP-8 stock model (the old class fallback) was
    12 mm short of its own pads."""
    half = n // 2
    length = half * pitch
    cx = row / 2
    cy = -(half - 1) * pitch / 2
    body = _box(6.35, length, 3.3, 0.5, "black", cx=cx, cy=cy)
    notch = _cyl(0.9, 0.6, 3.8 - 0.3, "black").apply_translation([cx, cy + length / 2, 0])
    parts = [body, notch]
    for i in range(half):
        y = -i * pitch
        for x in (0.0, row):
            lead = _box(0.5, 0.25, 3.5, -3.0, "silver", cx=x, cy=y)
            parts.append(lead)
    return _merge(parts)


def cp_radial(d, h, pitch):
    """Radial electrolytic on KiCad's CP_Radial_D{d}mm_P{pitch}mm: pin 1 at the
    origin, pin 2 at +pitch, can centred between them. Aluminium can + dark
    sleeve stripe + two sunk leads."""
    cx = pitch / 2
    can = _cyl(d / 2, h, 0.5, "black"); can.apply_translation([cx, 0, 0])
    top = _cyl(d / 2 * 0.98, 0.3, 0.5 + h, "silver"); top.apply_translation([cx, 0, 0])
    l1 = _cyl(0.3, 3.5, -3.0, "silver")
    l2 = _cyl(0.3, 3.5, -3.0, "silver"); l2.apply_translation([pitch, 0, 0])
    return _merge([can, top, l1, l2])


def trimpot_3296w():
    """Bourns 3296W: 9.53 x 4.83 x 10 mm blue body over 3 inline pins at 2.54,
    body centred on pin 2; brass adjuster screw on top."""
    body = _box(9.53, 4.83, 10.0, 0.0, "blue", cx=2.54)
    screw = _cyl(1.1, 0.4, 10.0, "gold").apply_translation([2.54 - 3.2, 0, 0])
    leads = [_box(0.5, 0.5, 3.5, -3.0, "silver", cx=i * 2.54) for i in range(3)]
    return _merge([body, screw] + leads)


def relay_srd():
    """Songle SRD-05VDC-SL-C: 19 x 15.5 x 15.3 mm blue block. KiCad
    Relay_SPDT_SANYOU_SRD_Series_Form_C: pin 1 (coil) at the origin, pads at
    (0,0) (0,-12.2)?? — the cached dump is the truth: build the block centred
    on the cached pad field."""
    import json as _json
    cache = _json.loads((_BACKEND / "app" / "library" / "footprint_cache.json").read_text(encoding="utf-8"))
    pads = cache["Relay_THT:Relay_SPDT_SANYOU_SRD_Series_Form_C"]["pads"]
    xs = [p["x"] for p in pads]; ys = [p["y"] for p in pads]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    body = _box(19.0, 15.5, 15.3, 0.0, "blue", cx=cx, cy=cy)
    leads = [_box(0.8, 0.5, 3.5, -3.0, "silver", cx=p["x"], cy=p["y"]) for p in pads]
    return _merge([body] + leads)


def pololu_module():
    """A4988/DRV8825 StepStick on Module:Pololu_Breakout-16: two 1x8 rows at
    x=0 and x=15.24, pins toward -Y. Green PCB 15.24 x 20.3 x 1.6 on 8.5 mm
    header standoff, black IC + heatsink-sized block on top."""
    cy = -(7 * 2.54) / 2
    cx = 15.24 / 2
    pcb = _box(15.24 + 0.6, 20.3, 1.6, 8.5, "green", cx=cx, cy=cy)
    ic = _box(6.0, 6.0, 1.0, 10.1, "black", cx=cx, cy=cy)
    hs = _box(9.0, 9.0, 4.0, 11.1, "silver", cx=cx, cy=cy)
    pins = []
    for r in (0.0, 15.24):
        for i in range(8):
            pins.append(_box(0.64, 0.64, 11.5, -3.0, "gold", cx=r, cy=-i * 2.54))
    hdr = [_box(2.54, 8 * 2.54, 8.5, 0.0, "black", cx=r, cy=cy) for r in (0.0, 15.24)]
    return _merge([pcb, ic, hs] + pins + hdr)


def promicro_rp2040():
    """SparkFun Pro Micro RP2040 on Synqit_ProMicro_RP2040_Module: two 1x12
    rows at x=0/15.24, pins toward -Y, board 17.78 x 33.02 x 0.8 on 8.5 mm
    header standoff. USB-C shell at the pin-1 end (the board overhangs the
    pin row by 3.81 mm there), RP2040 + flash in the middle."""
    cx = 15.24 / 2
    cy = (3.81 - 29.21) / 2          # body centre: +3.81 (USB end) .. -29.21
    pcb = _box(17.78, 33.02, 0.8, 8.5, "green", cx=cx, cy=cy)
    rp = _box(7.0, 7.0, 0.9, 9.3, "black", cx=cx, cy=cy - 2.0)
    flash = _box(5.0, 6.0, 1.0, 9.3, "black", cx=cx, cy=cy - 10.0)
    usb = _box(9.0, 7.3, 3.2, 9.3, "silver", cx=cx, cy=3.81 - 7.3 / 2 + 1.27)
    pins = []
    for r in (0.0, 15.24):
        for i in range(12):
            pins.append(_box(0.64, 0.64, 11.5, -3.0, "gold", cx=r, cy=-i * 2.54))
    hdr = [_box(2.54, 12 * 2.54, 8.5, 0.0, "black", cx=r, cy=-(11 * 2.54) / 2)
           for r in (0.0, 15.24)]
    return _merge([pcb, rp, flash, usb] + pins + hdr)


def mx_switch():
    """Cherry MX keyswitch on Synqit_MX_Switch_PCB (origin-centred): 14x14
    black base, sloped upper housing, cross-stem, two contact legs sunk at
    the datasheet positions ((-3.81, 2.54) and (2.54, 5.08) y-up) plus the
    centre boss and fixation pins."""
    base = _box(14.0, 14.0, 5.0, 0.5, "black")
    upper = _box(11.0, 11.0, 5.0, 5.5, "black")
    stem = _box(4.1, 1.2, 3.6, 10.5, "cream")
    stem2 = _box(1.2, 4.1, 3.6, 10.5, "cream")
    boss = _cyl(1.9, 3.0, -3.0, "black")
    f1 = _cyl(0.8, 3.0, -3.0, "black"); f1.apply_translation([-5.08, 0, 0])
    f2 = _cyl(0.8, 3.0, -3.0, "black"); f2.apply_translation([5.08, 0, 0])
    l1 = _cyl(0.6, 3.3, -3.3, "gold"); l1.apply_translation([-3.81, 2.54, 0])
    l2 = _cyl(0.6, 3.3, -3.3, "gold"); l2.apply_translation([2.54, 5.08, 0])
    return _merge([base, upper, stem, stem2, boss, f1, f2, l1, l2])


def lcd1602():
    """16x2 character LCD module standing on its 1x16 header (pins toward -Y):
    80 x 36 x 1.6 PCB with the 71.5 x 26.5 bezel, offset so pin 1 sits at
    the module's pin-1 corner (header along the top edge of the module)."""
    cy = -(15 * 2.54) / 2          # header centre along the pins
    # module extends away from the header (in +X here: pins run along Y, module
    # body sits beside the header row)
    pcb = _box(36.0, 80.0, 1.6, 8.5, "green", cx=18.0 - 2.5, cy=cy)
    bezel = _box(26.5, 71.5, 9.0, 10.1, "black", cx=18.0 - 2.5 + 3.0, cy=cy)
    glass = _box(23.0, 65.0, 0.4, 19.1, "glass", cx=18.0 - 2.5 + 3.0, cy=cy)
    pins = [_box(0.64, 0.64, 11.5, -3.0, "gold", cy=-i * 2.54) for i in range(16)]
    hdr = _box(2.54, 16 * 2.54, 8.5, 0.0, "black", cy=cy)
    return _merge([pcb, bezel, glass, hdr] + pins)


def fiducial():
    return _merge([_cyl(0.5, 0.05, 0.0, "gold")])


def l_radial(d, h, pitch):
    """Radial power inductor (Bourns RLB0914 class): black drum Ø d, height h,
    centred between the two leads at 0 and +pitch (pin-1 origin), leads sunk."""
    cx = pitch / 2
    drum = _cyl(d / 2, h, 0.5, "black"); drum.apply_translation([cx, 0, 0])
    band = _cyl(d / 2 * 1.02, 0.6, 0.5 + h * 0.75, "silver"); band.apply_translation([cx, 0, 0])
    l1 = _cyl(0.35, 3.5, -3.0, "silver")
    l2 = _cyl(0.35, 3.5, -3.0, "silver"); l2.apply_translation([pitch, 0, 0])
    return _merge([drum, band, l1, l2])


def holder_18650():
    """Keystone 1042 1x18650 SMD holder: 77.7 x 20.4 x 15 mm open cradle centred
    on the origin (pads at x=±39.69), with a cell rendered inside."""
    base = _box(77.7, 20.4, 2.0, 0.0, "black")
    e1 = _box(4.0, 20.4, 15.0, 0.0, "black", cx=-36.8)
    e2 = _box(4.0, 20.4, 15.0, 0.0, "black", cx=36.8)
    cell = cylinder(radius=9.0, height=65.0, sections=48)
    cell.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0]))
    cell.apply_translation([0, 0, 2.0 + 9.0]); _c(cell, "blue")
    return _merge([base, e1, e2, cell])


def seven_segment():
    """LTS-6760 0.56\" single-digit 7-segment: 12.7 x 19 x 8 mm grey body over
    two 5-pin rows at x=0 and x=15.24 (pins toward -Y, pitch 2.54); the body
    is centred on the pad field. Red segment face on top."""
    cx, cy = 15.24 / 2, -10.16 / 2
    body = _box(12.7, 19.0, 8.0, 0.0, "black", cx=cx, cy=cy)
    face = _box(11.5, 17.5, 0.2, 8.0, "red", cx=cx, cy=cy)
    leads = [_box(0.5, 0.5, 3.5, -3.0, "silver", cx=x, cy=-i * 2.54)
             for x in (0.0, 15.24) for i in range(5)]
    return _merge([body, face] + leads)


def touch_pad_12mm():
    return _merge([_cyl(6.0, 0.05, 0.0, "gold")])


def tcrt5000():
    """Vishay TCRT5000 reflective sensor: 10.2 x 5.8 x 7 mm black housing over
    2x2 pins (x 0/5.5, y 0/-2.54); two lens domes (emitter/detector) on top."""
    cx, cy = 5.5 / 2, -2.54 / 2
    body = _box(10.2, 5.8, 7.0, 0.0, "black", cx=cx, cy=cy)
    d1 = _c(icosphere(subdivisions=2, radius=1.5), "blue"); d1.apply_translation([cx - 2.6, cy, 7.0])
    d2 = _c(icosphere(subdivisions=2, radius=1.5), "glass"); d2.apply_translation([cx + 2.6, cy, 7.0])
    leads = [_box(0.5, 0.5, 3.5, -3.0, "silver", cx=x, cy=y) for x in (0.0, 5.5) for y in (0.0, -2.54)]
    return _merge([body, d1, d2] + leads)


def rocker_switch():
    body = _box(13, 8, 8, 0, "black")
    pad = box(extents=[10, 6, 2])
    pad.apply_transform(trimesh.transformations.rotation_matrix(np.radians(12), [0, 1, 0]))
    pad.apply_translation([0, 0, 8.5]); _c(pad, "red")
    return _merge([body, pad])


def shield_headers():
    """The four female headers an Arduino shield presents to the base board.

    Built in the FOOTPRINT frame from the footprint's OWN strip rectangles and
    pad positions, so the solid can never drift from the copper: each strip is
    a 8.5 mm black nylon body (the standard stacking-header height) with a gold
    contact showing at every pad and a lead sunk through the board.

    A shield used to render as a whole Arduino Uno board, because its part
    pointed at KiCad's `Module:Arduino_UNO_R3` — the footprint for placing an
    Uno ON a carrier, not for the headers a shield carries (2026-08-20).
    """
    from app.services import footprints as _fp

    fp = _fp.for_part(library.get_part("Arduino_Shield_Headers"))
    parts = []
    for x0, y0, x1, y1 in fp.bodyRects:
        parts.append(_box(x1 - x0, y1 - y0, 8.5, 0.0, "black",
                          cx=(x0 + x1) / 2, cy=(y0 + y1) / 2))
    for pad in fp.pads:
        # Contact mouth at the top of the body + the lead through the board.
        parts.append(_box(1.4, 1.4, 0.6, 7.9, "gold", cx=pad.x, cy=pad.y))
        lead = _cyl(0.32, 3.2, -3.2, "silver")
        lead.apply_translation([pad.x, pad.y, 0])
        parts.append(lead)
    return _merge(parts)


BUILDERS = {
    "MountingHole:MountingHole_2.2mm_M2": lambda: mounting_hole(2.2),
    "MountingHole:MountingHole_2.7mm_M2.5": lambda: mounting_hole(2.7),
    "MountingHole:MountingHole_3.2mm_M3": lambda: mounting_hole(3.2),
    "TestPoint:TestPoint_Pad_1.0x1.0mm": lambda: test_point(1.0),
    "TestPoint:TestPoint_THTPad_1.5x1.5mm_Drill0.7mm": lambda: test_point(1.5),
    "Synqit:Synqit_Vibration_Motor_8mm": lambda: coin_motor(8, 3),
    "Synqit:Synqit_Coil_Vibration_4mm": lambda: coin_motor(4, 3),
    "Synqit:Synqit_Speaker_8ohm_SMD": lambda: speaker(15),
    "Synqit:Synqit_MK4_Reed": reed_switch,
    "Synqit:Synqit_Rotary_Encoder_EC11": ec11_encoder,
    "Synqit:Synqit_TSOP38238": tsop_ir,
    "Synqit:Synqit_Rocker_SPST": rocker_switch,
    "LED_THT:LED_D3.0mm": led_3mm,
    "LED_THT:LED_D5.0mm": led_5mm,
    "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal": r_axial,
    "Connector_Coaxial:U.FL_Hirose_U.FL-R-SMT-1_Vertical": u_fl,
    "Connector_Coaxial:SMA_Amphenol_132289_EdgeMount": sma_edge,
    "Package_DIP:DIP-8_W7.62mm": lambda: dip(8),
    "Package_DIP:DIP-16_W7.62mm": lambda: dip(16),
    "Package_DIP:DIP-20_W7.62mm": lambda: dip(20),
    "Package_DIP:DIP-24_W7.62mm": lambda: dip(24),
    "Package_DIP:DIP-4_W7.62mm": lambda: dip(4),
    "Package_DIP:DIP-6_W7.62mm": lambda: dip(6),
    "Capacitor_THT:CP_Radial_D5.0mm_P2.00mm": lambda: cp_radial(5.0, 11.0, 2.0),
    "Capacitor_THT:CP_Radial_D6.3mm_P2.50mm": lambda: cp_radial(6.3, 11.0, 2.5),
    "Capacitor_THT:CP_Radial_D8.0mm_P3.50mm": lambda: cp_radial(8.0, 11.5, 3.5),
    "Capacitor_THT:CP_Radial_D10.0mm_P5.00mm": lambda: cp_radial(10.0, 20.0, 5.0),
    "Potentiometer_THT:Potentiometer_Bourns_3296W_Vertical": trimpot_3296w,
    "Relay_THT:Relay_SPDT_SANYOU_SRD_Series_Form_C": relay_srd,
    "Module:Pololu_Breakout-16_15.2x20.3mm": pololu_module,
    "Synqit:Synqit_ProMicro_RP2040_Module": promicro_rp2040,
    "Synqit:Synqit_MX_Switch_PCB": mx_switch,
    "Connector_PinHeader_2.54mm:PinHeader_1x16_P2.54mm_Vertical": lcd1602,
    "Fiducial:Fiducial_1mm_Mask2mm": fiducial,
    "Battery:BatteryHolder_Keystone_1042_1x18650": holder_18650,
    "Display_7Segment:7SegmentLED_LTS6760_LTS6780": seven_segment,
    "Synqit:TouchPad_D12mm": touch_pad_12mm,
    "OptoDevice:Vishay_TCRT5000": tcrt5000,
    "Inductor_THT:L_Radial_D9.5mm_P5.00mm_Fastron_09HCP": lambda: l_radial(9.5, 12.0, 5.0),
    "Synqit:Synqit_Arduino_Shield_Headers": shield_headers,
}
# THT-lead builders (leads below z=0 sink through the board — never lifted).
THT_KEYS = ("LED_THT:", "Package_DIP:", "Capacitor_THT:", "Potentiometer_THT:", "Relay_THT:", "Module:Pololu",
            "Synqit:Synqit_ProMicro_RP2040_Module", "Synqit:Synqit_MX_Switch_PCB", "Resistor_THT:",
            "Connector_PinHeader_2.54mm:PinHeader_1x16", "Display_7Segment:", "OptoDevice:", "Inductor_THT:",
            "Synqit:Synqit_Arduino_Shield_Headers")
# Parts that reuse a stock KiCad 3D model (STEP) rather than a generated solid.
STOCK_STEP = {
    "Synqit:Synqit_LED_RGB_5050":
        r"C:/Program Files/KiCad/10.0/share/kicad/3dmodels/LED_SMD.3dshapes/LED_RGB_5050-6.step",
}
for mpn in ("MQ-2", "MQ-3", "MQ-5", "MQ-7", "MQ-135"):
    p = library.get_part(mpn)
    if p:
        BUILDERS[p.footprint] = mq_sensor
for mpn, n in (("ScrewTerminal_2P_5mm", 2), ("ScrewTerminal_3P_5mm", 3)):
    p = library.get_part(mpn)
    if p:
        BUILDERS[p.footprint] = (lambda nn: (lambda: screw_terminal(nn)))(n)


def main():
    # `--only KEY[,KEY]` regenerates just those footprints (the committed GLBs
    # are meshopt-packed; a blanket regen would replace them with raw output).
    only = None
    if len(sys.argv) > 2 and sys.argv[1] == "--only":
        only = set(sys.argv[2].split(","))
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for fpkey, build in BUILDERS.items():
        if only is not None and fpkey not in only:
            continue
        m = build()
        safe = fpkey.replace(":", "__").replace("/", "_").replace(".", "_")
        out = AUTO / f"{safe}.glb"
        m.export(str(out))
        # THT leads below z=0 sink through the board (never lift a THT model —
        # see the 3D-geometric-truth notes); only lift SMD/surface bodies.
        minz = float(m.vertices[:, 2].min())
        tht = fpkey.startswith(THT_KEYS)
        lift = 0.0 if tht else round(-minz, 3)
        man[fpkey] = {"url": f"/models/auto/{safe}.glb", "scale": 1.0,
                      "rotation": [-90, 0, 0], "offset": [0, lift, 0]}
        print(f"  {fpkey:60s} {out.stat().st_size/1024:5.0f}KB")
    # Stock-STEP-backed parts (LED etc.) — convert the real KiCad solid.
    try:
        import cascadio  # noqa: E402
    except ImportError:
        cascadio = None
        print("  (cascadio not installed — skipping stock-STEP conversions)")
    for fpkey, step in (STOCK_STEP.items() if cascadio and only is None else []):
        if not Path(step).exists():
            print(f"  (skip stock {fpkey}: {step} missing)"); continue
        safe = fpkey.replace(":", "__").replace("/", "_").replace(".", "_")
        out = AUTO / f"{safe}.glb"
        cascadio.step_to_glb(step, str(out), tol_linear=0.02, tol_angular=0.2)
        lm = trimesh.load(str(out), force="mesh")
        scale = 1000.0 if float(max(lm.extents)) < 0.1 else 1.0
        man[fpkey] = {"url": f"/models/auto/{safe}.glb", "scale": scale,
                      "rotation": [-90, 0, 0],
                      "offset": [0, round(-float(lm.vertices[:, 2].min()) * scale, 3), 0]}
        print(f"  {fpkey:60s} {out.stat().st_size/1024:5.0f}KB (stock STEP)")
    MANIFEST.write_text(json.dumps(man, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\ngenerated {len(BUILDERS)} parametric + {len(STOCK_STEP)} stock models")


if __name__ == "__main__":
    main()
