"""Datasheet PDF → LibraryPart pipeline (PRD §7.9).

Stage 1: extract text from the first ~10 pages (where pinout tables and
package outlines typically live).
Stage 2: feed that text plus a strict schema prompt to the LLM, get back a
JSON object matching the LibraryPart shape.
Stage 3: validate, return a proposed part the user can review and commit.

The pipeline is deliberately conservative — it returns a *proposal*, never
auto-commits. PRD §10 calls out that hallucinated part data is a high-risk
failure mode; users review the extracted pinout before it lands in the library.
"""

from __future__ import annotations

import io
import json
import os
from dataclasses import dataclass

from app.models import LibraryPart


MAX_PAGES = 10
MAX_TEXT_CHARS = 35_000   # ~9k tokens; safe for the system+user budget


@dataclass
class IngestionResult:
    success: bool
    proposed: LibraryPart | None
    confidence: str           # "high" | "medium" | "low"
    notes: list[str]
    raw_text_sample: str
    error: str | None


SYSTEM_PROMPT = """\
You are a hardware engineer extracting structured part data from datasheets.

The user will paste raw text extracted from the first ~10 pages of a datasheet
PDF. Your job: produce a JSON object matching this exact schema:

{
  "mpn": "<exact manufacturer part number from the datasheet>",
  "description": "<one short sentence, ≤80 chars, mention key spec>",
  "category": "mcu" | "regulator" | "charger" | "sensor" | "passive" | "led" |
              "switch" | "transistor" | "diode" | "connector" | "crystal" |
              "protection" | "mechanical" | "display" | "actuator" | "memory" |
              "interface" | "audio" | "logic",
  "package": "<package name, e.g. SOIC-8, QFN-32, SOT-23-5>",
  "footprint": "<KiCad-style Library:Footprint, best guess>",
  "symbol": "<KiCad-style Library:Symbol, best guess>",
  "pins": [
    {
      "number": "1",
      "name": "<pin name from the pinout table, e.g. VCC, GND, SDA>",
      "side": "left" | "right" | "top" | "bottom",
      "type": "ground" | "power_in" | "power_out" | "input" | "output" |
              "bidirectional" | "passive"
    }
  ],
  "voltageMin": <float, V — from absolute max ratings or recommended operating>,
  "voltageMax": <float, V>,
  "alternatives": ["<MPN1>", "<MPN2>"],

  "_confidence": "high" | "medium" | "low",
  "_notes": ["<things you weren't sure about, things missing from the text>"]
}

Rules:
- Use EXACT pin names from the datasheet pinout table. Don't translate / rename.
- Map pin function to type:
  - VDD/VCC/AVDD/IOVDD = power_in
  - GND/VSS/AGND = ground
  - regulated outputs (VOUT, VBAT) = power_out
  - SDA/SCL/MOSI/MISO/CLK/IO_*/GPIO* = bidirectional
  - inputs only (RESET, EN, CE, INT_OUT) = input
  - outputs only (PGOOD, ALERT) = output
  - reserved/passive/NC = passive
- Group pins onto sides intuitively: power top/bottom, signals left/right.
- Confidence:
  - "high": clear pinout table, unambiguous MPN, package
  - "medium": some pins ambiguous, made educated guesses
  - "low": text is OCR-garbled, missing pinout, or you can't identify the part
- If you genuinely can't extract a part (corrupted text, irrelevant document),
  set _confidence="low" and put your reason in _notes.
- voltageMin/Max should reflect the recommended operating range, not absolute
  max. If unspecified, use null.
- Don't invent JLC part numbers — leave that to the user. Don't include
  jlcPart or jlcStock fields.

Output STRICTLY a JSON object, nothing else.
"""


def extract_text_from_pdf(pdf_bytes: bytes) -> tuple[str, list[str]]:
    """Extract plain text from a PDF blob. Returns (text, page_titles)."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages = []
    for i, page in enumerate(reader.pages[:MAX_PAGES]):
        try:
            txt = page.extract_text() or ""
        except Exception:  # noqa: BLE001
            txt = ""
        pages.append(f"--- PAGE {i + 1} ---\n{txt}")
    full = "\n\n".join(pages)
    if len(full) > MAX_TEXT_CHARS:
        full = full[:MAX_TEXT_CHARS] + "\n[... truncated ...]"
    return full, [f"page {i + 1}" for i in range(min(MAX_PAGES, len(reader.pages)))]


def ingest(pdf_bytes: bytes) -> IngestionResult:
    """Extract a LibraryPart proposal from a PDF blob. Always returns an
    IngestionResult; never raises on the happy path."""
    if not os.environ.get("OPENAI_API_KEY"):
        return IngestionResult(
            success=False,
            proposed=None,
            confidence="low",
            notes=[],
            raw_text_sample="",
            error=(
                "OPENAI_API_KEY not set. Datasheet ingestion needs the LLM to "
                "normalize the pinout table — set the key and try again."
            ),
        )

    try:
        text, _pages = extract_text_from_pdf(pdf_bytes)
    except Exception as exc:  # noqa: BLE001
        return IngestionResult(
            success=False, proposed=None, confidence="low",
            notes=[], raw_text_sample="",
            error=f"PDF text extraction failed: {exc!s}",
        )

    if len(text.strip()) < 200:
        return IngestionResult(
            success=False, proposed=None, confidence="low",
            notes=["Extracted text is suspiciously short — PDF may be scanned/image-only."],
            raw_text_sample=text,
            error="PDF text extraction produced very little text. Image-only "
                  "PDFs need OCR (not yet supported).",
        )

    # Single-shot LLM call.
    from app.services.agent import _make_openai_client

    try:
        client = _make_openai_client()
    except Exception as exc:  # noqa: BLE001
        return IngestionResult(
            success=False, proposed=None, confidence="low",
            notes=[], raw_text_sample=text[:1000],
            error=f"OpenAI client init failed: {exc!s}",
        )

    model = os.environ.get("OPENAI_MODEL", "gpt-4o")

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=4000,
        )
    except Exception as exc:  # noqa: BLE001
        return IngestionResult(
            success=False, proposed=None, confidence="low",
            notes=[], raw_text_sample=text[:1000],
            error=f"OpenAI API call failed: {exc!s}",
        )

    content = response.choices[0].message.content or "{}"
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        return IngestionResult(
            success=False, proposed=None, confidence="low",
            notes=[f"Could not parse LLM response: {exc}"],
            raw_text_sample=text[:1000],
            error="LLM returned malformed JSON.",
        )

    confidence = data.pop("_confidence", "medium")
    notes = data.pop("_notes", []) or []
    if not isinstance(notes, list):
        notes = [str(notes)]

    # Drop any fields the LLM might have invented — we control jlc data.
    for key in ("jlcPart", "jlcStock", "unitCost"):
        data.pop(key, None)

    # Make sure required fields exist with sane defaults.
    data.setdefault("alternatives", [])
    if not data.get("pins"):
        return IngestionResult(
            success=False, proposed=None, confidence="low",
            notes=notes + ["LLM did not return any pins."],
            raw_text_sample=text[:1000],
            error="No pinout extracted.",
        )

    # Normalize pin sides — LLM might use uppercase or alternate spellings.
    for pin in data.get("pins", []):
        if isinstance(pin, dict):
            side = (pin.get("side") or "left").lower()
            if side not in {"left", "right", "top", "bottom"}:
                side = "left"
            pin["side"] = side
            ptype = (pin.get("type") or "passive").lower().replace("-", "_")
            if ptype not in {
                "ground", "power_in", "power_out", "input", "output",
                "bidirectional", "passive", "unspecified",
            }:
                ptype = "passive"
            pin["type"] = ptype

    try:
        part = LibraryPart.model_validate(data)
    except Exception as exc:  # noqa: BLE001
        return IngestionResult(
            success=False, proposed=None, confidence=confidence,
            notes=notes + [f"Schema validation failed: {exc}"],
            raw_text_sample=text[:1000],
            error="Extracted part doesn't match the LibraryPart schema.",
        )

    return IngestionResult(
        success=True,
        proposed=part,
        confidence=confidence if confidence in {"high", "medium", "low"} else "medium",
        notes=notes,
        raw_text_sample=text[:1500],
        error=None,
    )
