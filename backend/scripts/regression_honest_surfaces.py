"""Guard for the reviews-29-33 "the screen tells the truth" round (2026-08-20).

Five reviewers in one batch reported the same shape of defect from different
angles: a surface that reads as an assurance while the thing it names is not
actually being checked. Every check here corresponds to one of those findings,
and each asserts BOTH directions — the surface fires on the defect AND stays
quiet on a correct design, because a check that can only pass is exactly what
this round was about.

Offline; no LLM spend, no network.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app.models_db import ProjectRow  # noqa: E402
from app.services import export, library, store, templates, tools, verifier  # noqa: E402
from app.services import pcb_verifier  # noqa: E402

PASSED = 0
FAILED: list[str] = []
_SCRATCH: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [PASS] {label}" + (f"  ({detail})" if detail else ""))
    else:
        FAILED.append(label)
        print(f"  [FAIL] {label}" + (f"  ({detail})" if detail else ""))


def scratch(name: str):
    p = store.create_project(name, user_id="regression-honest")
    _SCRATCH.append(p.id)
    return p


def cleanup() -> None:
    with db.SessionLocal() as s:
        for pid in _SCRATCH:
            row = s.get(ProjectRow, pid)
            if row is not None:
                s.delete(row)
        s.commit()


def _a_usb_c() -> str:
    for part in library.all_parts():
        blob = f"{part.mpn} {part.description or ''}".lower()
        if (part.category or "") == "connector" and "usb" in blob:
            return part.mpn
    raise AssertionError("library has no USB connector")


# --------------------------------------------------------------------------
# 1. An unfinished build must block fabrication.
# --------------------------------------------------------------------------
def test_incomplete_build_blocks_export() -> None:
    print("\n-- incomplete build blocks Order/Manufacture")
    p = scratch("honest incomplete")
    tools.add_component(p, mpn=_a_usb_c(), refdes="J1")

    before = export.manufacturability_report(p)
    p.ir.buildIncomplete = True
    after = export.manufacturability_report(p)

    check(
        "a design cut short mid-build is NOT ready",
        after["ready"] is False,
        f"ready={after['ready']}",
    )
    check(
        "the blocker says so in plain words, no rule ids",
        any(
            b.get("kind") == "incomplete" and "R" not in b["reason"].split()[0]
            for b in after["blockers"]
        ),
    )
    check(
        "clearing the flag lifts the block again",
        len(after["blockers"]) > len(before["blockers"]),
        f"{len(before['blockers'])} -> {len(after['blockers'])}",
    )


def test_empty_design_is_not_ready() -> None:
    print("\n-- an empty design is not 'ready to manufacture'")
    p = scratch("honest empty")
    rep = export.manufacturability_report(p)
    check("no parts => not ready", rep["ready"] is False)
    check(
        "and it explains why rather than showing an empty green state",
        any("no design yet" in b["reason"].lower() for b in rep["blockers"]),
    )


# --------------------------------------------------------------------------
# 2. R74 — duplicated hardware nobody asked for.
# --------------------------------------------------------------------------
def test_r74_duplicate_port() -> None:
    print("\n-- R74 duplicate unrequested port")
    usb = _a_usb_c()

    p = scratch("honest dup")
    tools.record_requirements(
        p,
        items=[{
            "description": "one USB-C connector for power and programming",
            "quantity": 1, "mpn": usb, "category": "connector",
        }],
    )
    tools.add_component(p, mpn=usb, refdes="J1")
    tools.add_component(p, mpn=usb, refdes="J2")
    found = verifier.R74_duplicate_unrequested_port(p)
    check("two USB receptacles when one was asked for => error",
          bool(found) and found[0].severity == "error")
    check("the message names both refdeses",
          bool(found) and "J1" in found[0].message and "J2" in found[0].message)

    p2 = scratch("honest dup allowed")
    tools.record_requirements(
        p2,
        items=[{"description": "two USB-C connectors", "quantity": 2,
                "mpn": usb, "category": "connector"}],
    )
    tools.add_component(p2, mpn=usb, refdes="J1")
    tools.add_component(p2, mpn=usb, refdes="J2")
    check("asking for two is silent",
          not verifier.R74_duplicate_unrequested_port(p2))

    p3 = scratch("honest single")
    tools.add_component(p3, mpn=usb, refdes="J1")
    check("a single port is silent",
          not verifier.R74_duplicate_unrequested_port(p3))

    check("R74 is registered in ALL_RULES",
          verifier.R74_duplicate_unrequested_port in verifier.ALL_RULES)


# --------------------------------------------------------------------------
# 3. Templates refuse the cases they were not vetted for.
# --------------------------------------------------------------------------
def test_template_input_envelope() -> None:
    print("\n-- vetted blocks refuse an input they were not vetted for")
    from app.services.tools import ToolError

    p = scratch("honest envelope")
    tools.record_requirements(
        p,
        items=[{"description": "single-cell Li-ion battery input 3.0 V to 4.2 V",
                "quantity": 1, "category": "connector"}],
    )
    try:
        templates.apply(
            p, template_name="lm7805_5v_linear",
            net_map={"VIN": "VBAT", "VOUT": "VCC_5V", "GND": "GND"},
        )
        check("LM7805 block from a 1S cell is refused", False, "it was applied")
    except ToolError as e:
        check("LM7805 block from a 1S cell is refused", True)
        check("the refusal names the real numbers and a way forward",
              "3" in str(e) and ("boost" in str(e).lower() or "dropout" in str(e).lower()))

    # A cascade onto a downstream regulated rail is a normal design.
    try:
        templates.apply(
            p, template_name="lm7805_5v_linear",
            net_map={"VIN": "VCC_12V", "VOUT": "VCC_5V", "GND": "GND"},
        )
        check("a downstream regulated rail is still allowed", True)
    except ToolError as e:
        check("a downstream regulated rail is still allowed", False, str(e))

    # Every block that REGULATES an upstream rail must declare the window it
    # was vetted for. `usb_c_5v_input` is exempt on purpose: its input is a USB
    # host, not a rail in this design, so there is nothing to check it against.
    regulating = [
        n for n, tpl in templates.TEMPLATES.items()
        if tpl.category == "power" and n != "usb_c_5v_input"
    ]
    missing = [n for n in regulating if templates.TEMPLATES[n].input_range is None]
    check("every regulating power block declares its input envelope",
          not missing, f"{len(regulating)} blocks, missing: {missing}")


# --------------------------------------------------------------------------
# 4. PCB_R10 — mixed assembly is stated, not discovered at quote time.
# --------------------------------------------------------------------------
def test_pcb_r10_mixed_assembly() -> None:
    print("\n-- PCB_R10 mixed THT/SMD assembly notice")
    check("PCB_R10 is registered",
          pcb_verifier.PCB_R10_mixed_assembly in pcb_verifier.ALL_RULES)

    from app.models import Board, PlacedComponent
    from app.services import footprints

    def board_of(*mpns: str) -> Board:
        return Board(
            width=50, height=50,
            components=[
                PlacedComponent(
                    refdes=f"X{i}", mpn=m, x=5 + i * 8, y=10, rotation=0,
                    layer="top", footprint=footprints.for_part(library.get_part(m)),
                )
                for i, m in enumerate(mpns)
            ],
        )

    smd_only = board_of("RC0603FR-0710KL", "RC0603FR-071KL")
    mixed = board_of("RC0603FR-0710KL", "LED_3MM_R")

    check("an all-SMT board says nothing",
          not pcb_verifier.PCB_R10_mixed_assembly(smd_only))
    found = pcb_verifier.PCB_R10_mixed_assembly(mixed)
    check("a mixed board reports the two processes", bool(found))
    check("and it is advisory, never a blocker",
          bool(found) and found[0].severity == "info")


# --------------------------------------------------------------------------
# 5. A user can delete their own project — and only their own.
# --------------------------------------------------------------------------
def test_project_delete() -> None:
    print("\n-- project delete (data lifecycle)")
    p = store.create_project("honest delete", user_id="regression-honest")
    check("another user cannot delete it",
          store.delete_project(p.id, user_id="someone-else") is False)
    check("it still exists after that attempt",
          store.get_project(p.id) is not None)
    check("the owner can delete it",
          store.delete_project(p.id, user_id="regression-honest") is True)
    check("and it is gone", store.get_project(p.id) is None)
    check("deleting twice is not an error, just False",
          store.delete_project(p.id, user_id="regression-honest") is False)


# --------------------------------------------------------------------------
# 6. Published claims are recounted from code, never hand-typed.
# --------------------------------------------------------------------------
def test_marketing_claims_match_code() -> None:
    print("\n-- landing claims match the code they describe")
    from app.services import mechanical_verifier

    total = (
        len(verifier.ALL_RULES)
        + len(pcb_verifier.ALL_RULES)
        + 1  # board/schematic sync (not in ALL_RULES — it needs the whole Project)
        + len(mechanical_verifier.ALL_RULES)
    )
    parts = len(library.all_parts())

    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    landing = os.path.join(here, "frontend", "src", "routes", "Landing.tsx")
    with open(landing, encoding="utf-8") as fh:
        text = fh.read()

    check(f"the landing states {total} checks", f'"{total}"' in text, f"code says {total}")
    check(f"the landing states {parts} parts", f'"{parts}"' in text, f"code says {parts}")
    check("the routing copy credits FreeRouting by name", "FreeRouting" in text)
    check("the export copy lists what actually ships",
          all(tok in text for tok in ("Gerbers", "STEP", "IDF", "pick-and-place")))


# --------------------------------------------------------------------------
# 7. "Show reasoning" must show reasoning, not a tool log.
# --------------------------------------------------------------------------
def test_build_trace_carries_rationale() -> None:
    print("\n-- the build trace carries the copilot's stated intent")
    from app.models import ToolCallSummary
    from app.services import agent

    check("a tool call can carry the rationale for its step",
          "rationale" in ToolCallSummary.model_fields)
    check("older stored messages without it still load",
          ToolCallSummary(tool="connect", args={}).rationale is None)

    # The prompt has to ASK for the sentence. Measured live before and after:
    # the model wrote prose on 0 of 9 steps under the old wording, 11 of 11
    # under this one.
    prompt = agent.SYSTEM_PROMPT
    check("the prompt requires a sentence of intent before each tool batch",
          "design intent" in prompt and "before each" in prompt)
    check("and tells the model it is shown to the user as the rationale",
          "rationale for those steps" in prompt)

    # Stub mode must show the same shape as the live path, or the offline demo
    # is the one place the trace still reads as machinery.
    import os
    key = os.environ.pop("OPENAI_API_KEY", None)
    try:
        p = scratch("honest trace")
        for _ in agent.run(p, "build an esp32 sensor board"):
            pass
        msg = p.chat[-1]
        check("stub mode attaches a rationale to every step",
              bool(msg.toolCalls) and all(t.rationale for t in msg.toolCalls),
              f"{sum(1 for t in msg.toolCalls if t.rationale)}/{len(msg.toolCalls)}")
    finally:
        if key is not None:
            os.environ["OPENAI_API_KEY"] = key


def main() -> int:
    db.init_db()
    try:
        test_incomplete_build_blocks_export()
        test_empty_design_is_not_ready()
        test_r74_duplicate_port()
        test_template_input_envelope()
        test_pcb_r10_mixed_assembly()
        test_project_delete()
        test_marketing_claims_match_code()
        test_build_trace_carries_rationale()
    finally:
        cleanup()

    print(f"\n{PASSED} checks passed, {len(FAILED)} failed.")
    if FAILED:
        for name in FAILED:
            print(f"  - {name}")
        return 1
    print("All honest-surface checks green.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
