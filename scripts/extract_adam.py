"""Extraction agent for adam.new — stdlib only, no browser, no API keys.

What it does
------------
1. Pulls every public marketing page on adam.new (server-rendered, so plain
   HTTP is enough) and converts it to readable text.
2. Pulls the whole docs site through its own machine-readable index
   (https://docs.adam.new/llms.txt lists every page as a `.md` URL).
3. Writes one file per page under data/extracted/ plus an index.json with
   title, URL, byte size, fetch time and a content hash — so a re-run shows
   exactly which pages changed since last time (a poor man's competitor
   monitor).
4. Extracts structured facts where the page shape is stable: changelog
   entries (date + headline + tag) parse reliably. Pricing tiers and the API
   rate-limit table are rendered client-side (the pricing page is a 400-byte
   shell; the rate table is a React component that calls their API), so those
   parsers return empty here and the values captured with a real browser on
   2026-09-07 live in data/pricing_snapshot_2026-09-07.json. To automate
   them you need a headless browser (Playwright) — deliberately not a
   dependency of this script.

Usage
-----
    python scripts/extract_adam.py            # full run
    python scripts/extract_adam.py --diff     # only report what changed

Rules it follows: only public pages, polite pacing (0.5 s between requests),
a real User-Agent, and nothing is executed from the fetched content.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "extracted"
INDEX = OUT / "index.json"
UA = "Mozilla/5.0 (research crawler; contact via repo owner)"
PACE = 0.5

MARKETING_PAGES = [
    "https://adam.new/",
    "https://adam.new/copilot",
    "https://adam.new/pricing",
    "https://adam.new/opensource",
    "https://adam.new/cadam",
    "https://adam.new/learn",
    "https://adam.new/install",
    "https://adam.new/changelog",
    "https://adam.new/blog",
    "https://adam.new/blog/bitter-lesson-ai-cad",
    "https://adam.new/blog/seed",
    "https://adam.new/blog/research",
    "https://adam.new/blog/mesh-editor",
    "https://adam.new/blog/sliders",
    "https://adam.new/blog/thesis",
    "https://adam.new/fusion-mcp",
    "https://adam.new/featurescript-mcp",
    "https://adam.new/vs/fusion-mcp",
    "https://adam.new/vs/featurescript-mcp",
    "https://adam.new/careers",
    "https://adam.new/security",
    "https://adam.new/bug-bounty",
    "https://adam.new/privacy-policy",
    "https://adam.new/terms-of-service",
]
DOCS_INDEX = "https://docs.adam.new/llms.txt"


class TextExtractor(HTMLParser):
    """HTML -> text that keeps block structure. Drops script/style/svg."""

    SKIP = {"script", "style", "svg", "noscript", "head"}
    BLOCK = {"p", "div", "section", "article", "li", "h1", "h2", "h3", "h4",
             "h5", "h6", "br", "tr", "table", "ul", "ol", "header", "footer",
             "main", "nav", "blockquote", "pre"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip_depth = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag in {"h1", "h2", "h3"}:
            self.parts.append("#" * int(tag[1]) + " ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip_depth:
            self.skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self.skip_depth:
            return
        self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n\s*\n\s*\n+", "\n\n", raw)
        return raw.strip()


def fetch(url: str) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read(), resp.headers.get_content_type()


def slug(url: str) -> str:
    s = re.sub(r"^https?://", "", url).rstrip("/")
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s)
    return s or "root"


def docs_pages() -> list[str]:
    body, _ = fetch(DOCS_INDEX)
    return re.findall(r"\((https://docs\.adam\.new/[^)\s]+\.md)\)", body.decode("utf-8", "replace"))


def extract_pricing(text: str) -> list[dict]:
    tiers = []
    for m in re.finditer(r"\n(Free|Pro|Max)\n.*?\$(\d+)/ ?month\n(.*?)(?=\n(?:Free|Pro|Max)\n|\n## |\Z)", text, re.S):
        bullets = [b.strip("- ").strip() for b in m.group(3).split("\n") if b.strip().startswith("-")]
        tiers.append({"tier": m.group(1), "usd_per_month": int(m.group(2)), "features": bullets})
    return tiers


def extract_changelog(text: str) -> list[dict]:
    entries = []
    date_re = re.compile(r"^(January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}, \d{4}$")
    current = None
    lines = [l.strip() for l in text.split("\n")]
    for i, line in enumerate(lines):
        if date_re.match(line):
            current = line
            continue
        if line.startswith("### ") and current:
            tag = next((lines[j] for j in range(i + 1, min(i + 4, len(lines))) if lines[j] in {"New", "Fixes", "Improvements", "Security"}), "")
            entries.append({"date": current, "item": line[4:], "tag": tag})
    return entries


def extract_rate_limits(text: str) -> list[dict]:
    rows = []
    for m in re.finditer(r"\|\s*([A-Za-z ]+?)\s*\|\s*([\d,]+/minute)\s*\|\s*([\d,]+/minute)\s*\|", text):
        rows.append({"policy": m.group(1).strip(), "free": m.group(2), "paid": m.group(3)})
    return rows


def run(diff_only: bool) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    previous = json.loads(INDEX.read_text()) if INDEX.exists() else {}
    index: dict[str, dict] = {}
    facts: dict[str, object] = {}
    urls = MARKETING_PAGES + docs_pages()
    changed, failed = [], []
    for url in urls:
        try:
            body, ctype = fetch(url)
        except Exception as exc:  # noqa: BLE001 - report and continue
            failed.append({"url": url, "error": f"{type(exc).__name__}: {exc}"[:160]})
            continue
        if url.endswith(".md") or ctype.startswith("text/plain") or ctype.startswith("text/markdown"):
            text = body.decode("utf-8", "replace")
            title = next((l[2:] for l in text.splitlines() if l.startswith("# ")), url)
        else:
            p = TextExtractor()
            p.feed(body.decode("utf-8", "replace"))
            text, title = p.text(), p.title.strip() or url
        digest = hashlib.sha256(text.encode()).hexdigest()[:16]
        name = slug(url) + ".md"
        entry = {"url": url, "title": title, "bytes": len(text), "sha256_16": digest,
                 "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        index[url] = entry
        if previous.get(url, {}).get("sha256_16") != digest:
            changed.append(url)
        if not diff_only:
            (OUT / name).write_text(f"<!-- source: {url} fetched {entry['fetched']} -->\n\n{text}\n", encoding="utf-8")
        if url.endswith("/pricing"):
            facts["pricing"] = extract_pricing(text)
        elif url.endswith("/changelog"):
            facts["changelog"] = extract_changelog(text)
        elif url.endswith("api-reference/introduction.md"):
            facts["api_rate_limits"] = extract_rate_limits(text)
        time.sleep(PACE)

    if not diff_only:
        INDEX.write_text(json.dumps(index, indent=2), encoding="utf-8")
        (ROOT / "data" / "facts.json").write_text(json.dumps(facts, indent=2), encoding="utf-8")
    print(f"pages fetched {len(index)}  failed {len(failed)}  changed since last run {len(changed)}")
    for u in changed[:40]:
        print("  changed:", u)
    for f in failed:
        print("  failed:", f["url"], f["error"])
    print(f"changelog entries {len(facts.get('changelog', []))}; pricing tiers {len(facts.get('pricing', []))} and "
          f"rate-limit rows {len(facts.get('api_rate_limits', []))} (0 is expected: client-rendered, "
          f"see data/pricing_snapshot_*.json)")
    return 1 if failed and not index else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--diff", action="store_true", help="only report changes, write nothing")
    sys.exit(run(ap.parse_args().diff))
