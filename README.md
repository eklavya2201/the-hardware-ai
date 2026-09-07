# adam-teardown

A private research repo answering one question: **what does adam.new do, how
is it built, and can Synqit do it?** Assembled 2026-09-07.

## Layout

| Path | What it is |
|---|---|
| `research/01-adam-product-teardown.md` | The product: positioning, architecture as they describe it, CAD features shipped in 2026, pricing, public API and MCP |
| `research/02-cadam-architecture.md` | How the open-source CADAM app works, down to the two agent tools and the screenshot loop |
| `research/03-synqit-feasibility.md` | What we can legally reuse, what Synqit already has, an 8-day plan for a CadQuery-plus-screenshot enclosure agent, and what not to do |
| `cadam/` | Verbatim copy of github.com/Adam-CAD/CADAM at the commit in `data/cadam_upstream_commit.txt`. **GPL-3.0.** Read it, run it, do not paste it into a closed-source product |
| `scripts/extract_adam.py` | The extraction agent: stdlib-only crawler for adam.new + docs.adam.new, writes `data/extracted/` and `data/facts.json`; `--diff` reports what changed since the last run |
| `data/extracted/` | One text file per public page (78 pages on first run) with an `index.json` of hashes |
| `data/facts.json` | Structured changelog entries (57). Pricing and API rate limits are client-rendered, so they are in `data/pricing_snapshot_2026-09-07.json`, captured with a browser |

## Verified on 2026-09-07

- CADAM: `npm ci` (958 packages), `tsc -b --noEmit`, and `npm run build`
  (Vite + Nitro, prerenders `/cadam`) all succeed on Node 22.14 / Windows.
  Running it needs Supabase CLI plus an LLM key; see `cadam/README.md`.
- Extractor: 78 pages fetched, 0 failures, 57 changelog entries parsed.

## Licence notice

Everything under `cadam/` is © Adam-CAD contributors, GPL-3.0 (with GPL-2.0+
OpenSCAD-WASM binaries; source offer in
`cadam/src/vendor/openscad-wasm/SOURCE-OFFER.txt`). Everything else in this
repo is internal research. Text quoted from adam.new is reproduced only as
short factual excerpts for analysis.

## Refresh

```
python scripts/extract_adam.py          # re-pull everything
python scripts/extract_adam.py --diff   # what changed since last pull
```
