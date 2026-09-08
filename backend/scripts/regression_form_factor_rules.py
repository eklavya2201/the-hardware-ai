"""Regression — R69 (standard outline) + R70 (oversized board) + the
deterministic board-size extractor, from the 2026-08-20 review round.

Three of five reviewed boards were the wrong SIZE, and the worst — a "2 x 1
inch" ESP32-S3 dev board built at 112.2 x 44.5 mm — shipped with **0 errors**,
because R57 only speaks when a form factor was recorded and recording it was
left to the model. Guards:

  1. `agent.declared_board_size` parses a size the user stated (mm or inch,
     rect or round) and REFUSES to parse a mounting-hole pitch or a part's
     size ("a 1.28 inch display").
  2. `form_factors.apply` records the standard's outline as the form factor, so
     R57 guards HAT/shield boards.
  3. `R69` errors when a board carrying a standard host header is not the
     standard outline, and warns when that header is not locked.
  4. `R70` reports a board whose parts occupy under 10 % of it, and escalates
     to an error when a size was asked for.
  5. Neither rule fires on the known-good stub design.

Offline: no LLM, no network, no DB.
    python -m scripts.regression_form_factor_rules
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.models import Project, SchematicIR  # noqa: E402
from app.services import agent, form_factors, placement, verifier  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def _ids(issues, prefix: str) -> list[str]:
    return [i.id for i in issues if i.id.startswith(prefix)]


def main() -> int:
    # ---- 1. the size extractor ------------------------------------------
    d = agent.declared_board_size
    cases: list[tuple[str, dict | None]] = [
        ("Custom ESP32-S3 development board, 2 x 1 inch, USB-C for programming",
         {"shape": "rect", "width_mm": 50.8, "height_mm": 25.4}),
        ("make a board that fits a 50.8 x 25.4 mm outline",
         {"shape": "rect", "width_mm": 50.8, "height_mm": 25.4}),
        ("a wearable with a 36 mm round board", {"shape": "round", "diameter_mm": 36.0}),
        ("board 68.58 x 53.34 mm please. Also add an LED.",
         {"shape": "rect", "width_mm": 68.58, "height_mm": 53.34}),
        # NOT a board size:
        ("ESP32 drone carrier on the 30.5 x 30.5 mm mounting pattern", None),
        ("a smart watch with a round 1.28 inch display", None),
        ("use a 2.54 mm pitch header on the board", None),
        ("Arduino Uno shield with an L298N motor driver", None),
        ("supports a 4S LiPo up to 16.8 V", None),
    ]
    for text, want in cases:
        got = d(text)
        ok = (want is None and got is None) or (
            want is not None and got is not None
            and all(got.get(k) == v for k, v in want.items())
        )
        check(f"size extractor: {text[:46]}…", ok, f"got {got}")

    # ---- 2. form_factors.apply records the spec --------------------------
    p = Project(id="ff", name="ff", path="/tmp/ff", ir=SchematicIR())
    form_factors.apply(p, "arduino_shield")
    spec = p.ir.form_factor
    ff = form_factors.get("arduino_shield")
    check("apply(arduino_shield) records a form factor", spec is not None)
    check(
        "recorded spec is the standard outline",
        spec is not None and spec.shape == "rect"
        and abs((spec.width_mm or 0) - ff.board_w) < 0.01
        and abs((spec.height_mm or 0) - ff.board_h) < 0.01,
        f"got {spec}",
    )
    check("R57 is silent on the freshly applied form factor",
          not _ids(verifier.R57_form_factor(p), "R57"),
          f"{[i.message for i in verifier.R57_form_factor(p)]}")

    # ---- 3. R69 fires when the outline drifts ----------------------------
    check("R69 silent on the spec-sized shield", not _ids(verifier.R69_standard_form_factor_outline(p), "R69"))
    p.board.width = 94.5   # what the reviewer's build actually shipped
    p.board.height = 100.0
    issues = verifier.R69_standard_form_factor_outline(p)
    check("R69 ERRORS on a 94.5 x 100 mm 'Uno shield'",
          any(i.severity == "error" and "68.58" in i.message for i in issues),
          f"got {[(i.id, i.severity) for i in issues]}")
    check("R69 names the fix (apply_template)",
          any("apply_template" in i.message for i in issues))
    p.board.width, p.board.height = ff.board_w, ff.board_h
    anchor = next(pc for pc in p.board.components if pc.mpn == ff.anchor_mpn)
    anchor.locked = False
    check("R69 warns when the host header is not locked",
          any(i.severity == "warning" and i.id.endswith(":anchor")
              for i in verifier.R69_standard_form_factor_outline(p)))
    anchor.locked = True

    # ---- 4. R70 on an oversized board ------------------------------------
    p2 = Project(id="big", name="big", path="/tmp/big", ir=SchematicIR())
    for _ in agent._run_stub(p2, "esp32"):
        pass
    p2.board = placement.auto_place(p2)
    check("R70 silent on the auto-placed stub board",
          not _ids(verifier.R70_board_oversized_for_content(p2), "R70"),
          f"{[i.message for i in verifier.R70_board_oversized_for_content(p2)]}")
    p2.board.width *= 3.0     # the same parts on a board 9x the area
    p2.board.height *= 3.0
    issues = verifier.R70_board_oversized_for_content(p2)
    check("R70 reports a board 9x too big (3.5 % body fill)", len(issues) == 1, f"got {issues}")
    check("R70 is a warning when no size was requested",
          issues and issues[0].severity == "warning")
    from app.services import tools
    tools.set_form_factor(p2, shape="rect", width_mm=40, height_mm=30)
    issues = verifier.R70_board_oversized_for_content(p2)
    check("R70 ERRORS once a size was requested",
          issues and issues[0].severity == "error", f"got {issues}")

    # ---- 5. no false positives on the known-good stub --------------------
    p3 = Project(id="stub", name="stub", path="/tmp/stub", ir=SchematicIR())
    for _ in agent._run_stub(p3, "esp32"):
        pass
    p3.board = placement.auto_place(p3)
    new = [i for i in verifier.run_all(p3)
           if i.rule in ("R69_standard_form_factor_outline", "R70_board_oversized_for_content")]
    check("neither rule fires on the stub design", not new, f"got {[(i.id, i.message) for i in new]}")


    # ---- 6. R71: the HAT ID EEPROM -------------------------------------
    from app.services import tools as _tools

    def _hat(with_eeprom=True, on_id_pins=True, extra_on_id=False, wp=True) -> Project:
        pr = Project(id="hat", name="hat", path="/tmp/hat", ir=SchematicIR())
        form_factors.apply(pr, "rpi_hat")
        hdr = next(c.refdes for c in pr.ir.components if c.mpn == "RPi_HAT_Header_40P")
        _tools.add_component(pr, mpn="BME280")
        bme = next(c.refdes for c in pr.ir.components if c.mpn == "BME280")
        if with_eeprom:
            _tools.add_component(pr, mpn="24LC256-I/SN")
            eep = next(c.refdes for c in pr.ir.components if "24LC256" in c.mpn)
            if on_id_pins:
                sda = [[hdr, "27"], [eep, "SDA"]] + ([[bme, "SDI"]] if extra_on_id else [])
                _tools.connect(pr, net_name="ID_SD", pins=sda)
                _tools.connect(pr, net_name="ID_SC", pins=[[hdr, "28"], [eep, "SCL"]])
            else:
                _tools.connect(pr, net_name="I2C_SDA", pins=[[hdr, "3"], [eep, "SDA"]])
                _tools.connect(pr, net_name="I2C_SCL", pins=[[hdr, "5"], [eep, "SCL"]])
            if wp:
                _tools.connect(pr, net_name="GND", pins=[[hdr, "6"], [eep, "WP"]])
        return pr

    check("R71 silent on a correctly wired HAT ID EEPROM",
          not verifier.R71_hat_id_eeprom(_hat()),
          f"{[i.id for i in verifier.R71_hat_id_eeprom(_hat())]}")
    check("R71 ERRORS when the HAT has no ID EEPROM",
          any(i.id == "R71:missing" and i.severity == "error"
              for i in verifier.R71_hat_id_eeprom(_hat(with_eeprom=False))))
    check("R71 ERRORS when the EEPROM is on the normal I2C bus",
          any(i.id == "R71:miswired" and i.severity == "error"
              for i in verifier.R71_hat_id_eeprom(_hat(on_id_pins=False))))
    check("R71 ERRORS when something else shares the ID bus",
          any(i.id.endswith(":shared") and i.severity == "error"
              for i in verifier.R71_hat_id_eeprom(_hat(extra_on_id=True))))
    check("R71 warns on a floating write-protect pin",
          any(i.id.endswith(":wp") and i.severity == "warning"
              for i in verifier.R71_hat_id_eeprom(_hat(wp=False))))
    check("R71 silent on a design with no HAT header",
          not verifier.R71_hat_id_eeprom(p3))

    # ---- 7. 4-layer for a dense THT host header --------------------------
    # A realistic HAT: header + sensor + EEPROM + decoupling + pull-ups. Below
    # FOUR_LAYER_MIN_PARTS a board stays 2-layer however big its header is.
    hat_board = _hat()
    for mpn in ("CC0603KRX7R9BB104", "CC0603KRX7R9BB104", "CC0603KRX7R9BB104",
                "RC0603FR-074K7L", "RC0603FR-074K7L"):
        _tools.add_component(hat_board, mpn=mpn)
    layers, planes = placement.choose_stackup(hat_board)
    # Two planes need two plane-worthy nets; this fixture wires GND + VCC_3V3
    # only through the EEPROM/sensor, so one plane (GND) is the honest result.
    check("a Pi HAT (dense THT header) gets a 4-layer stack-up",
          layers == 4 and len(planes) >= 1,
          f"got {layers} layers, {planes}")
    check("the dense-header detector names the header",
          placement._dense_tht_header(hat_board) is not None)
    check("a 4-part HAT stays 2-layer (complexity floor)",
          placement.choose_stackup(_hat())[0] == 2)
    # An MCU module with 30 THT pins is NOT a host header, and a small design
    # stays 2-layer: an Arduino Nano buzzer board must not become 4-layer.
    nano = Project(id="nano", name="nano", path="/tmp/nano", ir=SchematicIR())
    _tools.add_component(nano, mpn="Arduino_Nano_V3")
    _tools.add_component(nano, mpn="Buzzer_Active_9mm")
    check("an Arduino Nano board stays 2-layer",
          placement.choose_stackup(nano)[0] == 2,
          f"got {placement.choose_stackup(nano)}")

    # ---- 8. R72: a module with no body ----------------------------------
    def _one(mpn: str) -> Project:
        pr = Project(id="m", name="m", path="/tmp/m", ir=SchematicIR())
        _tools.add_component(pr, mpn=mpn)
        return pr

    check("R72 reports a module still on a bare pin header",
          any(i.id == "R72:DHT22" for i in verifier.R72_module_without_body(_one("DHT22"))))
    check("R72 is silent on a module with a real body (L298N)",
          not verifier.R72_module_without_body(_one("L298N_MOD")))
    check("R72 is silent on a module with a real body (MPU6050)",
          not verifier.R72_module_without_body(_one("MPU6050_MOD")))
    check("R72 is silent on a plain IC",
          not verifier.R72_module_without_body(_one("BME280")))
    # An OFF-BOARD device reaches the PCB through a connector: the header IS
    # the whole board footprint, so there is no body to miss. R72 was calling a
    # servo and a stepper motor hard ERRORS for this, and R72 errors feed the
    # agent's fix-up loop — it would have burned turns on a correct design.
    for mpn in ("SG90", "28BYJ-48", "Keypad_4x4_Membrane", "Limit_Switch_KW12"):
        check(f"R72 is silent on {mpn} (off-board, reached by a connector)",
              not verifier.R72_module_without_body(_one(mpn)))
    check("but a DHT22 is still flagged — it solders to the board and has a body",
          any(i.id == "R72:DHT22" for i in verifier.R72_module_without_body(_one("DHT22"))))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURES: {FAILURES}")
        return 1
    print("All form-factor rule checks green.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
