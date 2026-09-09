"""Guard: the agent's QUERIES (services/queries.py) — its eyes — and the
guardrails around them (founder's bar, 2026-09-08):

  1. PURITY    every query is read-only: the Project's JSON is byte-identical
                after calling EVERY query with valid, invalid and edge inputs,
                and no query opens a socket, calls the LLM client, or opens a
                file outside the library / footprint cache.
  2. SIZE      every answer <= queries.MAX_RESULT_CHARS (one constant); lists
                are pages (offset/limit) reporting total / truncated /
                next_offset — checked on a synthetic 200-part board too.
  3. TYPED     pydantic-validated arguments: wrong type / missing field /
                unknown field answer {"error", "expected"}; an unknown refdes,
                net, hole or query answers {"error": "no such ...", "known"}.
  4. LOOP      at most QUERY_BUDGET_PER_TURN queries per agent turn; past it
                the tool answers "query budget exhausted" (fake tool loop).
  5. CLAIMS    a reply claiming "no errors" / "all connected" / "fully routed"
                / "fits" with no backing query that turn is flagged as an
                LLM_unverified_status WARNING; the text is never rewritten.
  6. AUTH      /q/* reads through _readable_project: owner or share token,
                another user / anonymous / wrong token -> 404 (two users, on a
                scratch SQLite — never the real DB).
  7. BUDGET    the prompt text this round added is < 2,500 chars, and the
                SYNQIT_AGENT_QUERIES=0 switch hides tools + rule cleanly.
  8. UNTOUCHED verifier*, placement*, routing*, export*, mechanical_bridge,
                pricing* carry no diff against cad-sso (skipped without git).

Builds a small board in memory (deterministic stub agent, no LLM, no KiCad).
Run from backend/:  python -m scripts.regression_agent_queries
"""
from __future__ import annotations

import builtins
import io
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

# The DB engine is created at import time from DATABASE_URL: point it at a
# scratch file BEFORE any app import so the auth checks never touch voltron.db.
_SCRATCH_DB = _BACKEND_ROOT / "scratch_queries.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_SCRATCH_DB.as_posix()}"
os.environ.pop("OPENAI_API_KEY", None)      # force the deterministic stub
os.environ.pop("SYNQIT_AGENT_QUERIES", None)

_failures: list[str] = []
_count = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global _count
    _count += 1
    line = f"  [{'PASS' if ok else 'FAIL'}] {label}"
    if detail:
        line += f"  ({detail})"
    print(line)
    if not ok:
        _failures.append(label)


def _size(obj) -> int:
    return len(json.dumps(obj, default=str))


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _stub_board():
    from app.models import Project
    from app.services import agent, mechanical_tools, mechanical_verifier, pcb_verifier, placement, routing

    project = Project(id="q-guard", name="q-guard", path="/q-guard")
    for _ in agent.run(project, "ESP32 sensor board"):
        pass
    project.board = placement.auto_place(project)
    project.board = routing.auto_route(project)
    pcb_verifier.merge_into_project(project)
    mechanical_tools.dispatch(project, "derive_from_board", {})
    mechanical_verifier.merge_into_project(project)
    return project


def _big_board(n_parts: int = 200):
    """A synthetic 200-part project with a hand-built board: 100 LEDs + 100
    resistors in matrices, every pad paired by a rats line, 30 holes, 25
    keep-outs, 25 cutouts, and DRC run — the worst case every query must
    still answer under the cap."""
    from app.models import Board, PlacedComponent, Project, RatsLine, VerifierIssue
    from app.services import footprints, library, mechanical_tools, tools

    led = next((p.mpn for p in library.all_parts() if p.mpn.startswith("LED_3MM_")), None) or "LED_3MM_R"
    res = "RC0603FR-07330RL"
    project = Project(id="q-big", name="q-big", path="/q-big")
    half = n_parts // 2
    tools.dispatch(project, "add_component_array", {"mpn": led, "count": half, "rows": 10, "cols": half // 10})
    tools.dispatch(project, "add_component_array", {"mpn": res, "count": half})
    lp = library.get_part(led).pins
    rp = library.get_part(res).pins
    tools.dispatch(project, "connect_matrix", {"refdes_prefix": "D", "rows": 10, "cols": half // 10,
                                               "row_pin": lp[0].number, "col_pin": lp[1].number})
    tools.dispatch(project, "connect_matrix", {"refdes_prefix": "R", "rows": 10, "cols": half // 10,
                                               "row_pin": rp[0].number, "col_pin": rp[1].number,
                                               "row_net_prefix": "RR", "col_net_prefix": "RC"})
    board = Board(width=200.0, height=200.0)
    for i, c in enumerate(project.ir.components):
        board.components.append(PlacedComponent(
            refdes=c.refdes, mpn=c.mpn, x=5.0 + (i % 20) * 9.5, y=5.0 + (i // 20) * 9.5,
            footprint=footprints.for_mpn(c.mpn)))
    pads = [(pc.x + pad.x, pc.y + pad.y) for pc in board.components for pad in pc.footprint.pads]
    for i in range(0, len(pads) - 1, 2):
        (x1, y1), (x2, y2) = pads[i], pads[i + 1]
        board.rats.append(RatsLine(net=f"N{i}", x1=x1, y1=y1, x2=x2, y2=y2))
    board.auto_placed = board.auto_routed = True
    project.board = board
    for i in range(30):
        mechanical_tools.dispatch(project, "add_mounting_hole", {"x": 3.0 + i * 6, "y": 3.0})
    for i in range(25):
        mechanical_tools.dispatch(project, "add_keepout", {"x": i * 7.0, "y": 100.0, "width": 5.0, "height": 5.0,
                                                           "reason": f"synthetic keep-out {i}"})
    for i in range(25):
        mechanical_tools.dispatch(project, "add_connector_cutout", {"x": i * 7.0, "y": 0.0, "width": 6.0,
                                                                    "height": 4.0, "edge": "bottom",
                                                                    "refdes": f"D{i + 1}"})
    tools.run_drc(project)
    project.issues.extend(VerifierIssue(id=f"PCB_R03:N{i}", severity="error", rule="PCB_R03_unrouted_nets",
                                        message=f"Net N{i} could not be fully routed", net=f"N{i}", source="pcb")
                          for i in range(0, 300, 2))
    return project


# Arguments every query is swept with: valid, invalid and edge cases.
def _arg_sets() -> list[dict]:
    return [
        {}, {"refdes": "U1"}, {"refdes": "R1"}, {"refdes": "ZZ9"}, {"name": "GND"}, {"name": "NOPE"},
        {"refdes": "U1", "pin": "1"}, {"refdes": "U1", "pin": "GND"}, {"refdes": "U1", "pin": "IO99"},
        {"id": "nope"}, {"severity": "error"}, {"rule": "PCB_"}, {"severity": "bogus"},
        {"offset": 0, "limit": 1}, {"offset": 10_000, "limit": 200}, {"offset": -1}, {"limit": 0},
        {"limit": 10_000}, {"limit": "abc"}, {"refdes": 12}, {"bogus": 1}, {"refdes": ""},
        {"refdes": "U1", "offset": "2", "limit": "3"},
    ]


class _NoNetwork:
    """Any socket use inside a query is a failure."""

    def __init__(self) -> None:
        self.hits = 0

    def __enter__(self):
        self._connect, self._create = socket.socket.connect, socket.create_connection

        def boom(*a, **k):
            self.hits += 1
            raise RuntimeError("network call inside a query")

        socket.socket.connect = boom          # type: ignore[assignment]
        socket.create_connection = boom       # type: ignore[assignment]
        return self

    def __exit__(self, *exc):
        socket.socket.connect, socket.create_connection = self._connect, self._create


class _WatchOpen:
    """Record every file a query opens; only the library / caches are allowed."""

    def __init__(self) -> None:
        self.paths: list[str] = []

    def __enter__(self):
        self._open = builtins.open

        def rec(file, *a, **k):
            if isinstance(file, (str, os.PathLike)):
                self.paths.append(os.fspath(file))
            return self._open(file, *a, **k)

        builtins.open = rec       # type: ignore[assignment]
        io.open = rec             # type: ignore[assignment]
        return self

    def __exit__(self, *exc):
        builtins.open = self._open
        io.open = self._open


def main() -> int:
    from fastapi import HTTPException
    from starlette.requests import Request

    from app.models import ChatMessage, Project, ToolCallSummary
    from app.services import agent, geometry, mechanical_tools, mechanical_verifier, pcb_verifier, queries, tools

    project = _stub_board()
    n_comp, n_net = len(project.ir.components), len(project.ir.nets)
    print(f"Fixture: {n_comp} parts, {n_net} nets, {len(project.board.rats)} rats, "
          f"{len(project.issues)} issues; cap {queries.MAX_RESULT_CHARS} chars, "
          f"budget {queries.QUERY_BUDGET_PER_TURN}/turn\n")
    CAP = queries.MAX_RESULT_CHARS

    # ---- registration --------------------------------------------------------
    print("-- registration")
    names = set(queries.QUERIES)
    expected = {"get_component", "get_pin_net", "list_unconnected_pins", "list_components", "get_net",
                "list_nets", "list_issues", "list_unrouted_ratlines", "get_board_summary",
                "get_part_envelope", "list_holes", "get_hole", "list_cutouts", "list_keepouts"}
    check("all expected queries registered", expected == names, ", ".join(sorted(expected ^ names)) or "ok")
    schema_names = {s["function"]["name"] for s in agent.TOOL_SCHEMAS}
    check("every query has a tool schema", names <= schema_names, ", ".join(sorted(names - schema_names)) or "ok")
    check("query schemas are labelled QUERY (read-only)", all(
        s["function"]["description"].startswith("QUERY (read-only")
        for s in agent.TOOL_SCHEMAS if s["function"]["name"] in names))
    check("no query name collides with a verb", not (names & set(tools.TOOLS)))
    res, diff = tools.dispatch(project, "get_board_summary", {})
    check("tools.dispatch routes queries and returns no diff", diff is None and res["board"] is not None)

    # ---- shapes + identifiers (direct calls) ----------------------------------
    print("-- shapes")
    u1 = queries.run(project, "get_component", {"refdes": "U1"})
    check("get_component(U1): identity, pins [number,name,type,net], placement, dims",
          u1["refdes"] == "U1" and u1["pin_fields"] == ["number", "name", "type", "net"]
          and u1["total"] == u1["pin_count"] and any(r[3] == "GND" for r in u1["pins"])
          and u1["placed"]["side"] == "top" and len(u1["body_mm"]) == 2 and u1["height_mm"] > 0)
    check("get_component pages its pins (truncated/next_offset agree)",
          u1["truncated"] == (u1["next_offset"] is not None)
          and (not u1["truncated"] or u1["next_offset"] == u1["offset"] + len(u1["pins"])))
    if u1["truncated"]:
        rest = queries.run(project, "get_component", {"refdes": "U1", "offset": u1["next_offset"]})
        check("get_component second page completes the pin table",
              len(u1["pins"]) + len(rest["pins"]) == u1["pin_count"] or rest["truncated"])
    pn = queries.run(project, "get_pin_net", {"refdes": "U1", "pin": "GND"})
    check("get_pin_net(U1, GND) resolves the alias and answers the net",
          pn["net"] == "GND" and pn["net_role"] == "gnd" and pn["pin"])
    ok = all(queries.run(project, "get_pin_net", {"refdes": r, "pin": p})["net"] == net.name
             for net in project.ir.nets for r, p in net.pins[:3])
    check("get_pin_net agrees with the nets for every sampled pin", ok)
    un = queries.run(project, "list_unconnected_pins", {})
    pins_true = {(r, p) for n in project.ir.nets for r, p in n.pins}
    check("list_unconnected_pins: none on a net, power/ground first, by_refdes",
          not ({tuple(row[0].split(".", 1)) for row in un["pins"]} & pins_true)
          and all(row[2] in ("power_in", "power_out", "ground") for row in un["pins"][: un["unwired_power_or_ground"]])
          and isinstance(un["by_refdes"], dict) and un["total"] == un["count"])
    lc = queries.run(project, "list_components", {"limit": 5})
    lc2 = queries.run(project, "list_components", {"offset": lc["next_offset"], "limit": 200})
    check("list_components paginates and the second page completes the roster",
          lc["total"] == n_comp and len(lc["components"]) == 5 and lc["truncated"] and lc["next_offset"] == 5
          and len(lc2["components"]) == n_comp - 5 and lc2["next_offset"] is None and not lc2["truncated"])
    ln = queries.run(project, "list_nets", {})
    check("list_nets: [name, role, pins, routed]", ln["total"] == n_net and all(len(r) == 4 for r in ln["nets"]))
    gnd = queries.run(project, "get_net", {"name": "GND"})
    check("get_net(GND): role gnd, pins, routed, counts",
          gnd["role"] == "gnd" and gnd["pin_count"] == gnd["total"] and gnd["routed"] is True
          and gnd["pin_fields"] == ["refdes", "pin", "name", "type"])
    sig = next((n.name for n in project.ir.nets if n.name.startswith("I2C")), None)
    if sig:
        gs = queries.run(project, "get_net", {"name": sig})
        check(f"get_net({sig}) reaches an MCU pin", bool(gs.get("mcu_pin")), str(gs.get("mcu_pin")))
    li = queries.run(project, "list_issues", {})
    check("list_issues: counts over all + compact rows with ids, errors first, one-line",
          set(li["counts"]) == {"error", "warning", "info"} and sum(li["counts"].values()) == len(project.issues)
          and all({"id", "sev", "rule", "msg"} <= set(r) for r in li["issues"])
          and [r["sev"] for r in li["issues"]] == sorted((r["sev"] for r in li["issues"]),
                                                          key=lambda s: {"error": 0, "warning": 1, "info": 2}[s])
          and all("\n" not in r["msg"] and len(r["msg"]) <= 140 for r in li["issues"]))
    errs = queries.run(project, "list_issues", {"severity": "error"})
    pcb = queries.run(project, "list_issues", {"rule": "PCB_"})
    check("list_issues filters (severity, rule prefix) keep the totals",
          all(r["sev"] == "error" for r in errs["issues"]) and errs["counts"] == li["counts"]
          and all(r["rule"].startswith("PCB_") for r in pcb["issues"]) and pcb["total"] >= 1)
    bs = queries.run(project, "get_board_summary", {})
    check("get_board_summary: outline/layers/thickness, parts per side, bbox, tallest, holes, routing, enclosure",
          bs["board"]["shape"] == project.board.shape and bs["board"]["thickness"] == 1.6
          and bs["parts"]["total"] == len(project.board.components) and len(bs["parts_bbox"]) == 4
          and bs["tallest_top"]["refdes"] and bs["holes"] and all("id" in h for h in bs["holes"])
          and bs["routing"]["rats"] == len(project.board.rats) and bs["enclosure"]["kind"] == "box")
    bare = Project(id="bare", name="bare", path="/bare")
    check("get_board_summary / list_unrouted_ratlines degrade with no board",
          queries.run(bare, "get_board_summary", {})["board"] is None
          and queries.run(bare, "list_unrouted_ratlines", {})["board"] is None)
    rats = queries.run(project, "list_unrouted_ratlines", {})
    check("list_unrouted_ratlines: longest first, named by pads, id net:pads",
          rats["count"] == len(project.board.rats)
          and [r["mm"] for r in rats["rats"]] == sorted((r["mm"] for r in rats["rats"]), reverse=True)
          and all(isinstance(r["from"], str) and "." in r["from"] and r["id"] == f"{r['net']}:{r['from']}-{r['to']}"
                  for r in rats["rats"]), json.dumps(rats["rats"][:1]))
    env = queries.run(project, "get_part_envelope", {"refdes": "U1"})
    pc = next(c for c in project.board.components if c.refdes == "U1")
    truth = [[round(v, 2) for v in rect] for rect in geometry.solid_rects(pc)]
    check("get_part_envelope(U1) == geometry.solid_rects (the STEP export's boxes)",
          env["placed"] and env["rects"] == truth and env["height_mm"] > 0)
    sch_only = Project(id="s", name="s", path="/s")
    for _ in agent.run(sch_only, "ESP32 sensor board"):
        pass
    check("get_part_envelope(unplaced) answers footprint + height",
          queries.run(sch_only, "get_part_envelope", {"refdes": "U1"})["placed"] is False)
    holes = queries.run(project, "list_holes", {})
    hole = queries.run(project, "get_hole", {"id": holes["holes"][0]["id"]})
    check("list_holes / get_hole: ids, nearest part clearance",
          holes["total"] == len(project.mechanical.mountingHoles) and hole["id"] == holes["holes"][0]["id"]
          and hole["nearest_part"] and hole["nearest_part"]["clearance_mm"] >= 0)
    cuts = queries.run(project, "list_cutouts", {})
    check("list_cutouts: ids + edges", cuts["total"] == len(project.mechanical.cutouts)
          and all(c["id"] and c["edge"] for c in cuts["cutouts"]))
    mechanical_tools.dispatch(project, "add_keepout", {"x": 1, "y": 1, "width": 5, "height": 5, "reason": "test"})
    ko = queries.run(project, "list_keepouts", {})
    check("list_keepouts sees a keep-out with its id", ko["total"] == 1 and ko["keepouts"][0]["id"].startswith("ko-"))
    mechanical_tools.dispatch(project, "delete_keepout", {"id": ko["keepouts"][0]["id"]})
    sp, _ = tools.dispatch(project, "search_parts", {"query": "esp32", "limit": 5})
    check("search_parts rows are compact (mpn/category/package/one-line description)",
          all({"mpn", "category", "package", "description"} <= set(r) for r in sp["parts"])
          and all(len(r["description"]) <= 90 for r in sp["parts"]))
    dr, _ = tools.dispatch(project, "run_drc", {})
    check("run_drc keeps issue_count/errors/warnings and returns the compact issue view",
          {"issue_count", "errors", "warnings", "issues", "counts"} <= set(dr)
          and all({"id", "sev", "rule", "msg"} <= set(r) for r in dr["issues"]))
    pcb_verifier.merge_into_project(project)
    mechanical_verifier.merge_into_project(project)

    # ---- 3. typed inputs + graceful errors ------------------------------------
    print("-- typed inputs")
    bad = queries.run(project, "get_component", {"refdes": "ZZ9"})
    check("unknown refdes -> {error: no such component, known[:20]}",
          bad["error"].startswith("no such component") and "U1" in bad["known"] and len(bad["known"]) <= 20)
    check("unknown net -> no such net + known", queries.run(project, "get_net", {"name": "NOPE"})["error"].startswith("no such net")
          and "GND" in queries.run(project, "get_net", {"name": "NOPE"})["known"])
    check("unknown hole -> no such hole + known", queries.run(project, "get_hole", {"id": "mh-nope"})["error"].startswith("no such hole")
          and queries.run(project, "get_hole", {"id": "mh-nope"})["known"])
    check("unknown pin -> error + known pins", "error" in queries.run(project, "get_pin_net", {"refdes": "U1", "pin": "IO99"})
          and queries.run(project, "get_pin_net", {"refdes": "U1", "pin": "IO99"})["known"])
    check("unknown query -> error + known queries", "known" in queries.run(project, "nope", {}))
    missing = queries.run(project, "get_component", {})
    check("missing required field -> {error, expected}", "error" in missing and "refdes" in missing["expected"])
    wrong = queries.run(project, "list_components", {"limit": "abc"})
    check("wrong type -> {error, expected}", "error" in wrong and "limit" in wrong["expected"])
    check("unknown field -> {error, expected}", "expected" in queries.run(project, "get_board_summary", {"bogus": 1}))
    check("out-of-range page args -> error (limit 0 / offset -1)",
          "expected" in queries.run(project, "list_nets", {"limit": 0}) and "expected" in queries.run(project, "list_nets", {"offset": -1}))
    check("bad severity enum -> error", "expected" in queries.run(project, "list_issues", {"severity": "bogus"}))
    coerced = queries.run(project, "list_components", {"limit": "3", "offset": "1"})
    check("string ints coerce (HTTP path)", coerced["offset"] == 1 and len(coerced["components"]) == 3)
    check("queries.run never raises on any swept argument set",
          all(isinstance(queries.run(project, n, a), dict) for n in names for a in _arg_sets()))

    # ---- 2. size caps ---------------------------------------------------------
    print("-- size caps")
    over = [(n, a, _size(queries.run(project, n, a))) for n in names for a in _arg_sets()]
    worst = max(over, key=lambda t: t[2])
    check(f"every answer on the stub board <= {CAP} chars", all(t[2] <= CAP for t in over), f"worst {worst[0]}{worst[1]} = {worst[2]}")
    big = _big_board()
    over_big = [(n, a, _size(queries.run(big, n, a))) for n in names for a in _arg_sets()]
    worst = max(over_big, key=lambda t: t[2])
    check(f"every answer on a {len(big.ir.components)}-part board <= {CAP} chars",
          all(t[2] <= CAP for t in over_big), f"worst {worst[0]}{worst[1]} = {worst[2]}")
    cut = queries.run(big, "list_issues", {"limit": 200})
    check("a cut list says truncated:true, total, next_offset",
          cut["truncated"] is True and cut["total"] > len(cut["issues"]) and cut["next_offset"] == len(cut["issues"]))
    walked, offset, guard = 0, 0, 0
    while offset is not None and guard < 50:
        page = queries.run(big, "list_unrouted_ratlines", {"offset": offset, "limit": 200})
        walked += len(page["rats"])
        offset = page["next_offset"]
        guard += 1
    check("walking next_offset visits every rats line exactly once", walked == len(big.board.rats), f"{walked}/{len(big.board.rats)}")
    fit = queries._fit({"rows": list(range(2000)), "note": "x"})
    check("_fit trims an unpaged oversize list and marks it", _size(fit) <= CAP and fit["truncated"] and fit["total"] == 2000)

    # ---- 1. purity -----------------------------------------------------------
    print("-- purity")
    llm_calls = []
    real_client = agent._make_openai_client
    agent._make_openai_client = lambda llm=None: llm_calls.append(1)  # type: ignore[assignment]
    try:
        snap = project.model_dump_json()
        snap_big = big.model_dump_json()
        with _NoNetwork() as net, _WatchOpen() as files:
            for n in names:
                for a in _arg_sets():
                    queries.run(project, n, a)
                    queries.run(big, n, a)
    finally:
        agent._make_openai_client = real_client
    check("queries never mutate the project (stub board, every query x every arg set)",
          project.model_dump_json() == snap)
    check("queries never mutate the project (200-part board)", big.model_dump_json() == snap_big)
    check("queries open no socket", net.hits == 0)
    check("queries never touch the LLM client", not llm_calls)
    lib_dir = (_BACKEND_ROOT / "app" / "library").resolve()
    stray = [p for p in files.paths if not Path(p).resolve().is_relative_to(lib_dir)]
    check("queries open no file outside app/library (the library / footprint cache)", not stray, ", ".join(stray[:3]))

    # ---- 4. loop guard --------------------------------------------------------
    print("-- loop guard")
    b = queries.QueryBudget()
    took = [b.take("get_board_summary") for _ in range(queries.QUERY_BUDGET_PER_TURN + 3)]
    check(f"QueryBudget allows {queries.QUERY_BUDGET_PER_TURN} then answers 'query budget exhausted'",
          all(t is None for t in took[: queries.QUERY_BUDGET_PER_TURN])
          and all(t and t["error"] == "query budget exhausted" and "hint" in t for t in took[queries.QUERY_BUDGET_PER_TURN:]))
    check("verbs never consume the query budget", queries.QueryBudget().take("connect") is None)

    class _Fn:
        def __init__(self, name, args):
            self.name, self.arguments = name, json.dumps(args)

    class _Call:
        def __init__(self, i, name, args):
            self.id, self.function = f"call_{i}", _Fn(name, args)

    class _Msg:
        def __init__(self, content, calls):
            self.content, self.tool_calls = content, calls

    class _Usage:
        prompt_tokens, completion_tokens = 1000, 50
        prompt_tokens_details = type("D", (), {"cached_tokens": 400})()

    class _Completion:
        def __init__(self, msg):
            self.choices = [type("C", (), {"message": msg})()]
            self.usage = _Usage()

    class _FakeClient:
        """Turn 1: 15 queries + one verb in one message; turn 2: final text."""
        def __init__(self):
            self.n = 0
            self.chat = type("Chat", (), {})()
            self.chat.completions = type("Comp", (), {})()
            self.chat.completions.create = self.create

        def create(self, **kw):
            self.n += 1
            self.tools_seen = kw["tools"]
            if self.n == 1:
                calls = [_Call(i, "get_board_summary", {}) for i in range(15)]
                calls.append(_Call(99, "set_value", {"refdes": "R1", "value": "10k"}))
                return _Completion(_Msg("checking the board", calls))
            return _Completion(_Msg("done: 0 errors and everything is connected", None))

    fake = _FakeClient()
    agent._make_openai_client = lambda llm=None: fake  # type: ignore[assignment]
    try:
        agent.reset_usage()
        loop_project = Project(**json.loads(snap))
        events = list(agent._run_openai(loop_project, "check the board"))
    finally:
        agent._make_openai_client = real_client
    results = [e.payload["result"] for e in events if e.kind == "tool_use"]
    denied = [r for r in results if isinstance(r, dict) and r.get("error") == "query budget exhausted"]
    check(f"fake tool loop: 15 queries -> {15 - queries.QUERY_BUDGET_PER_TURN} denied, the verb still ran",
          len(denied) == 15 - queries.QUERY_BUDGET_PER_TURN and any("old" in r for r in results))
    u = agent.USAGE
    check("usage hook sums prompt/completion/cached tokens and tool stats",
          u["llm_calls"] == 2 and u["prompt_tokens"] == 2000 and u["completion_tokens"] == 100
          and u["cached_tokens"] == 800 and u["tool_calls"] == 16 and u["queries"] == 15
          and u["queries_denied"] == 3 and u["tool_output_chars"] > 0)
    check("the fake loop offered the query schemas to the model",
          any(t["function"]["name"] == "list_issues" for t in fake.tools_seen))

    # ---- 5. unverified status claims -----------------------------------------
    print("-- unverified claims")
    claimed = "Done. 0 errors, everything is connected, the board is fully routed and fits in the 60 mm case."

    def _chat_project(text, calls):
        p = Project(id="c", name="c", path="/c")
        p.chat.append(ChatMessage(id="u", role="user", text="build"))
        p.chat.append(ChatMessage(id="a", role="assistant", text=text,
                                  toolCalls=[ToolCallSummary(tool=t, args={}) for t in calls]))
        return p

    p1 = _chat_project(claimed, ["add_component", "run_drc"])
    agent._flag_unverified_status_claims(p1, 0)
    flagged = {i.id for i in p1.issues}
    check("four unbacked status claims -> four LLM_status warnings",
          flagged == {"LLM_status:errors", "LLM_status:routing", "LLM_status:connections", "LLM_status:fit"}
          and all(i.severity == "warning" and i.source == "llm" for i in p1.issues), ", ".join(sorted(flagged)))
    check("the assistant text is not rewritten", p1.chat[-1].text == claimed)
    p2 = _chat_project(claimed, ["list_issues", "list_unconnected_pins", "list_unrouted_ratlines", "get_board_summary"])
    agent._flag_unverified_status_claims(p2, 0)
    check("the same claims backed by queries this turn -> no warning", not p2.issues)
    p3 = _chat_project("I added U1 and wired its power pins; run DRC when ready.", ["add_component"])
    agent._flag_unverified_status_claims(p3, 0)
    check("ordinary prose without a status claim -> no warning", not p3.issues)
    p4 = _chat_project("There are 0 errors.", ["run_drc"])
    agent._flag_unverified_status_claims(p4, 0)
    agent._flag_unverified_status_claims(p4, 0)
    check("run_drc alone does not back a '0 errors' claim; re-check does not duplicate", [i.id for i in p4.issues] == ["LLM_status:errors"])
    check("stub path never flags (no queries exist there)", not any(i.rule == "LLM_unverified_status" for i in project.issues))

    # ---- 6. endpoints: owner or share token, else 404 --------------------------
    print("-- endpoints (scratch DB)")
    from app import db
    from app.api.projects import create_share, list_queries, run_query
    from app.models_db import UserRow
    from app.services import store

    db.init_db()
    with db.SessionLocal() as s:
        for uid, email in (("q-owner", "q-owner@example.test"), ("q-other", "q-other@example.test")):
            if s.get(UserRow, uid) is None:
                s.add(UserRow(id=uid, email=email, name=uid, password_hash="x"))
        s.commit()
        owner, other = s.get(UserRow, "q-owner"), s.get(UserRow, "q-other")
    saved = store.create_project("q-endpoint", user_id="q-owner")
    saved.ir = project.ir
    saved.board = project.board
    store.upsert(saved)
    pid = saved.id

    def req(qs: str) -> Request:
        return Request({"type": "http", "method": "GET", "query_string": qs.encode(), "headers": []})

    def is_404(fn) -> bool:
        try:
            fn()
        except HTTPException as exc:
            return exc.status_code == 404
        return False

    check("owner: GET /q lists the queries", set(list_queries(pid, None, owner)["queries"]) == names)
    got = run_query(pid, "get_component", req("refdes=U1"), None, owner)
    check("owner: GET /q/get_component?refdes=U1 answers", got.get("refdes") == "U1")
    check("owner: string page args coerce over HTTP", run_query(pid, "list_components", req("limit=2&offset=1"), None, owner)["offset"] == 1)
    check("owner: unknown query is a JSON error, not a 4xx", "known" in run_query(pid, "nope", req(""), None, owner))
    check("another user -> 404", is_404(lambda: run_query(pid, "list_components", req(""), None, other)))
    check("anonymous -> 404", is_404(lambda: run_query(pid, "list_components", req(""), None, None)))
    check("anonymous + wrong token -> 404", is_404(lambda: run_query(pid, "list_components", req(""), "bad-token", None)))
    tok = create_share(pid, owner).token or ""
    check("anonymous + valid share token reads (same rule as GET /projects/{id})",
          run_query(pid, "list_components", req(f"share={tok}"), tok, None)["total"] == n_comp)
    check("share= is not passed to the query as an argument",
          "error" not in run_query(pid, "get_board_summary", req(f"share={tok}"), tok, None))
    check("another user + token reads too", run_query(pid, "list_nets", req(""), tok, other)["total"] == n_net)
    check("other user still cannot see a project id they do not own (no leak)",
          is_404(lambda: list_queries(pid, None, other)))
    store.delete_project(pid, user_id="q-owner")

    # ---- 7. budget + switch ---------------------------------------------------
    print("-- budget")
    rule = agent.query_rule_text()
    fix_added = "Check a pin with get_pin_net before moving it. After your fixes, call run_drc, then list_issues to read what is left. "
    fixup = agent._build_fixup_prompt([], 1)
    added = len(rule) + (len(fix_added) if fix_added.strip() in fixup else 0)
    check("added prompt text < 2,500 chars (rule block + fix-up hint)", added < 2500, f"{added} chars")
    check("the rule block carries both halves (ask, don't guess / no unverified claims)",
          "ASK, DON'T GUESS" in rule and "NO UNVERIFIED STATUS CLAIMS" in rule)
    from app.services import library
    prompt = agent.SYSTEM_PROMPT + "\n\n" + agent._library_index_for_prompt()
    check("system prompt + core index <= 120k chars (regression_prompt_budget's bar)", len(prompt) <= 120_000, f"{len(prompt):,}")
    os.environ[agent.QUERIES_ENV] = "0"
    off_tools, off_prompt = agent.active_tool_schemas(), agent.active_system_prompt()
    os.environ[agent.QUERIES_ENV] = "1"
    on_tools, on_prompt = agent.active_tool_schemas(), agent.active_system_prompt()
    os.environ.pop(agent.QUERIES_ENV, None)
    check("SYNQIT_AGENT_QUERIES=0 hides every query tool and the rule; =1 restores them",
          not ({t["function"]["name"] for t in off_tools} & names) and "ASK, DON'T GUESS" not in off_prompt
          and names <= {t["function"]["name"] for t in on_tools} and on_prompt == agent.SYSTEM_PROMPT
          and len(agent.active_tool_schemas()) == len(on_tools))
    # ---- the state-guess counter (what a read-tool experiment measures) --------
    agent.reset_usage()
    agent._record_tool_error("Pin U1.5 is already on net 'VCC_3V3'; a pin can be on ONE net only (R21).")
    agent._record_tool_error("Pin U2.1 is already on net VBAT (power). Adding it to GND would short the rails.")
    agent._record_tool_error("Component 'U9' does not exist.")
    agent._record_tool_error("rows and cols must both be >= 1.")
    check("every tool error is counted", agent.USAGE["tool_errors"] == 4, f"{agent.USAGE[chr(34)+chr(34)] if False else agent.USAGE['tool_errors']}")
    check("only false beliefs about state count as a guess",
          agent.USAGE["state_guesses"] == 3,
          "a bad argument is not a guess")
    agent.reset_usage()
    check("query tool schemas <= 8k chars (~2k tokens)", _size(agent.QUERY_SCHEMAS) <= 8000, f"{_size(agent.QUERY_SCHEMAS)}")
    del library

    # ---- 8. untouched ------------------------------------------------------------
    print("-- untouched")
    guarded = ["backend/app/services/verifier.py", "backend/app/services/pcb_verifier.py",
               "backend/app/services/mechanical_verifier.py", "backend/app/services/placement.py",
               "backend/app/services/routing.py", "backend/app/services/routing_freerouting.py",
               "backend/app/services/export.py", "backend/app/services/export_sch.py",
               "backend/app/services/mechanical_bridge.py", "backend/app/services/pricing.py"]
    try:
        out = subprocess.run(["git", "diff", "--stat", "cad-sso..HEAD", "--", *guarded],
                             cwd=_BACKEND_ROOT.parent, capture_output=True, text=True, timeout=30)
        if out.returncode == 0:
            check("no diff against cad-sso under verifier*/placement*/routing*/export*/mechanical_bridge/pricing*",
                  out.stdout.strip() == "", out.stdout.strip()[:120] or "clean")
        else:
            print("  [SKIP] git diff unavailable (no cad-sso ref here)")
    except (OSError, subprocess.TimeoutExpired):
        print("  [SKIP] git not available")

    # ---- cleanup -----------------------------------------------------------------
    try:
        db.engine.dispose()
        _SCRATCH_DB.unlink(missing_ok=True)
    except OSError:
        pass

    print(f"\nRESULT: {'all assertions passed' if not _failures else str(len(_failures)) + ' FAILED'} "
          f"({_count - len(_failures)}/{_count} checks)")
    for f in _failures:
        print("  -", f)
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
