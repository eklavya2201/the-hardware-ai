"""Curation bot — autonomous library-build pipeline.

The bot reads a coverage matrix (CSV of target MPNs), fetches datasheets,
runs LLM extraction with the richer LibraryPart schema, runs the verifier,
and either auto-merges high-confidence parts or queues low-confidence ones
for human review.

Designed to be conservative: when in doubt, queue rather than auto-merge.
The anti-hallucination promise (LLM cannot invent parts in the agent loop)
depends on the library being clean, so the bot's job is to keep it that way.
"""

from __future__ import annotations

import io
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models import (
    CompanionPart,
    LibraryPart,
    LibraryPin,
    Provenance,
)


BOT_VERSION = "0.1.0"
LIBRARY_DIR = Path(__file__).resolve().parent.parent / "library"
PROPOSED_DIR = LIBRARY_DIR / "proposed_parts"
DATASHEET_DIR = LIBRARY_DIR / "datasheets"   # local PDFs for bot-blocked vendors
COVERAGE_MATRIX = LIBRARY_DIR / "coverage_matrix.csv"
MAX_PDF_BYTES = 32 * 1024 * 1024     # 32 MB cap — big MCU datasheets run 15-30 MB
MAX_PAGES_FOR_EXTRACTION = 14
MAX_TEXT_CHARS = 45_000

# MCUs ship huge family datasheets where the device pinout / pad-map table sits
# well past the front matter (ordering info, block diagrams, memory maps come
# first). A 14-page window misses it, forcing the LLM to infer pins from memory
# — the exact anti-hallucination failure we saw on ATSAMD51J19A. Give MCUs a
# much deeper window so the real pinout table lands in-context.
MCU_MAX_PAGES_FOR_EXTRACTION = 60
MCU_MAX_TEXT_CHARS = 140_000

# Output token budget for the extractor. MCUs/SOMs emit long pin arrays, so a
# 6k cap truncated the JSON mid-string (the RaspberryPi CM4/CM5 failures).
# Reasoning models (gpt-5.x) also burn part of this budget on reasoning tokens,
# which truncated register-heavy display controllers (SH1106/ILI9341/ST7735) at
# 8k, so the non-MCU budget is raised to match.
MAX_OUTPUT_TOKENS = 16_000
MCU_MAX_OUTPUT_TOKENS = 16_000

# Some datasheets (Nordic, NXP, big family docs) put the pin-assignment table
# hundreds of pages in, past any front-of-document window. For MCUs we scan the
# whole doc for the densest "pinout-looking" pages and include them alongside
# the front matter. This regex counts package-pin / GPIO / power-rail tokens.
PINOUT_SCAN_LIMIT = 900   # don't scan beyond this many pages
_PINOUT_RE = re.compile(
    r"\bP\d{1,2}\.\d{1,2}\b|\bGPIO\d+\b|\bVDD\w*\b|\bGND\b|\bVSS\b|"
    r"\b[AU]?QFN\d*\b|\bWLCSP\d*\b|\b[LT]QFP\d*\b|\bDFN\d*\b|\bLGA\d*\b|"
    r"\bPin\s+(?:assignment|number|name|description)\b",
    re.IGNORECASE,
)

# Some datasheet hosts (Sensirion, Plantower, etc.) 403 a bot-looking UA, so we
# present as a normal browser. Kept honest otherwise — we only GET public PDFs.
FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,application/octet-stream,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


# ============================================================================
# Schema-aware system prompt for the extractor
# ============================================================================

EXTRACTOR_SYSTEM_PROMPT = """\
You are a hardware engineer extracting structured part data from datasheets
for a curated EDA library. Your output drives an AI-assisted PCB-design tool
where parts cannot be invented at design-time, so the library must be exact.

The user pastes raw text from the first ~14 pages of a datasheet PDF (where
pinouts, package info, electrical specs, and the typical application circuit
typically live). Produce a JSON object matching this schema exactly:

{
  "mpn": "<exact manufacturer part number — match the cover/title exactly>",
  "manufacturer": "<e.g. STMicroelectronics, Bosch Sensortec, Microchip>",
  "description": "<one short sentence, ≤80 chars, mention the key spec>",
  "category": "mcu" | "regulator" | "charger" | "sensor" | "passive" | "led" |
              "switch" | "transistor" | "diode" | "connector" | "crystal" |
              "protection" | "mechanical" | "display" | "actuator" | "memory" |
              "interface" | "audio" | "logic",
  "package": "<package name, e.g. SOIC-8, QFN-32, SOT-23-5, TSSOP-14>",
  "footprint": "<KiCad-style Library:Footprint, best guess from the package>",
  "symbol": "<KiCad-style Library:Symbol, best guess>",
  "pins": [
    {"number": "1", "name": "<exact pin name from pinout table>",
     "side": "left" | "right" | "top" | "bottom",
     "type": "ground" | "power_in" | "power_out" | "input" | "output" |
             "bidirectional" | "passive"}
  ],

  "voltageMin": <float, V — recommended operating min>,
  "voltageMax": <float, V — recommended operating max>,
  "currentTypMa": <float — typical operating current in mA>,
  "currentMaxMa": <float — max rated current in mA>,
  "powerMaxW": <float — max power dissipation in W if specified>,
  "operatingTempMinC": <float — °C>,
  "operatingTempMaxC": <float — °C>,

  "interfaces": ["I2C" | "SPI" | "UART" | "USB" | "CAN" | "LIN" | "I2S" |
                 "PWM" | "Analog" | "BLE" | "WiFi" | "LoRa" | "Ethernet" |
                 "NFC" | "GPIO"],
  "defaultI2cAddress": "<e.g. 0x76>",
  "defaultSpiMode": <0-3>,
  "defaultBaudRate": <int>,

  "companions": [
    {"mpn": "<MPN of recommended companion>", "role": "decoupling" | "pullup" |
     "load_cap" | "crystal_load" | "current_limit" | "level_shift" | "filter",
     "pin": "<pin name of *this* part the companion attaches to>",
     "quantity": <int>, "notes": "<short>"}
  ],
  "typicalApplication": "<one paragraph describing how this part is usually
                         wired up, based on the datasheet's typical-application
                         figure. Be specific about pin-level connections.>",
  "firmwareLibrary": "<PlatformIO lib_deps entry if you know one, else null>",
  "firmwareNotes": "<short hint like 'I2C addr 0x76, 32-byte read'>",

  "alternatives": ["<MPN of pin-compatible alternative>", "..."],

  "_confidence": "high" | "medium" | "low",
  "_notes": ["<things you weren't sure about, missing info, ambiguities>"],
  "_sourcePages": [<page numbers where you found the pinout / specs>]
}

Pin-type mapping rules:
  - VDD/VCC/AVDD/IOVDD/VBUS                                  → power_in
  - GND/VSS/AGND/DGND/PGND/EP (exposed pad if GND)           → ground
  - VOUT/VREF/regulated outputs                              → power_out
  - SDA/SCL/MOSI/MISO/SCK/CSn/IO_*/GPIO/PA*/PB*              → bidirectional
  - RESET/EN/CE/INT/IRQ/NMI (input-only)                     → input
  - PGOOD/ALERT/READY (output-only)                          → output
  - NC/RFU/RESERVED/TEST                                     → passive

Pin-side hint: put power on top, ground on bottom, signals on left/right.
If the chip has many GPIOs, distribute them evenly left/right.

Companion-part guidance:
  - For any digital IC with VDD: add at least one 100 nF (CC0603KRX7R9BB104)
    decoupling cap on VDD-to-GND. For MCUs: also a 10 µF bulk cap.
  - For I2C devices: add 4.7 kΩ pull-ups (RC0603FR-074K7L) on SDA and SCL.
  - For crystal MCUs: add the matched load caps (typically 22 pF NP0).
  - Only suggest companions you can name with an MPN that's typical for the
    JLCPCB/Adafruit ecosystem. If unsure, leave companions empty.

Confidence rules:
  - "high":    clear pinout table, unambiguous MPN, package, complete electrical
  - "medium":  some pins ambiguous OR a few specs missing OR companions guessed
  - "low":     OCR garbled, missing pinout, can't identify the part

If a field genuinely isn't in the datasheet, use null. Don't fabricate values.
Output STRICTLY a JSON object, nothing else.
"""


# ============================================================================
# Data classes for the bot's internal state
# ============================================================================


@dataclass
class CurationTarget:
    """A row in the coverage matrix — one MPN we want to curate."""
    mpn: str
    category: str
    tier: int                      # 1 (highest priority) → 3
    archetype: str                 # e.g. "us_student_air_quality"
    datasheet_url: str | None = None
    notes: str = ""


@dataclass
class CurationOutcome:
    target: CurationTarget
    success: bool
    proposed_part: LibraryPart | None = None
    confidence: str = "low"
    confidence_score: float = 0.0
    auto_mergeable: bool = False
    verifier_checks: dict[str, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    error: str | None = None
    elapsed_seconds: float = 0.0


# ============================================================================
# Datasheet fetching
# ============================================================================


def fetch_pdf(url: str, timeout: int = 120, retries: int = 3) -> bytes:
    """Download a PDF, with browser headers and retry/backoff for flaky hosts.

    Retries transient failures (timeouts, dropped connections, 5xx). Bails
    immediately on permanent ones (404/410 dead URL, or exceeding the size cap)
    since retrying won't help.

    `local:<filename>` reads from DATASHEET_DIR instead of the network — the
    escape hatch for vendors that block automated fetches (ST, Microchip, etc.):
    download the PDF once in a browser, drop it in app/library/datasheets/."""
    if url.startswith("local:"):
        path = DATASHEET_DIR / url[len("local:"):]
        if not path.exists():
            raise RuntimeError(
                f"local datasheet not found: {path.name} — download it into "
                f"{DATASHEET_DIR} first"
            )
        data = path.read_bytes()
        if not data[:1024].lstrip().startswith(b"%PDF"):
            raise RuntimeError(f"{path.name} is not a valid PDF")
        return data

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=FETCH_HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:    # noqa: S310
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_PDF_BYTES:
                        raise RuntimeError(
                            f"Datasheet exceeds {MAX_PDF_BYTES // (1024 * 1024)} MB cap"
                        )
                    chunks.append(chunk)
                data = b"".join(chunks)
                # Some hosts answer 200 with an HTML interstitial / login wall
                # instead of the file. Detect it here so the failure is a clear
                # message rather than a cryptic pypdf "invalid pdf header".
                if not data[:1024].lstrip().startswith(b"%PDF"):
                    raise RuntimeError(
                        "server returned a non-PDF response (HTML/redirect "
                        "page — likely a bot-block); needs a different mirror"
                    )
                return data
        except RuntimeError:
            raise  # size cap / non-PDF — permanent, don't retry
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410):
                raise RuntimeError(f"HTTP {exc.code} {exc.reason} (dead URL)") from exc
            last_err = RuntimeError(f"HTTP {exc.code} {exc.reason}")
        except Exception as exc:    # noqa: BLE001 — timeouts, resets, DNS, etc.
            last_err = exc
        if attempt < retries - 1:
            time.sleep(1.5 * (attempt + 1))  # linear backoff: 1.5s, 3s
    raise last_err if last_err else RuntimeError("PDF fetch failed")


def extract_text_from_pdf(
    pdf_bytes: bytes,
    max_pages: int = MAX_PAGES_FOR_EXTRACTION,
    max_chars: int = MAX_TEXT_CHARS,
    seek_pinout: bool = False,
) -> tuple[str, int]:
    """Pull datasheet text, capped at `max_chars`. Returns (text, pages_used).

    Default: the first `max_pages` pages. With `seek_pinout` (used for MCUs),
    also locate the densest pin-assignment pages anywhere in the document and
    include them alongside the front matter — Nordic/NXP family datasheets bury
    the pinout table hundreds of pages in, past any front-of-document window."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    n = len(reader.pages)

    def page_text(i: int) -> str:
        try:
            return reader.pages[i].extract_text() or ""
        except Exception:    # noqa: BLE001
            return ""

    if not seek_pinout or n <= max_pages:
        pages = list(range(min(max_pages, n)))
    else:
        front = min(12, n)
        scan_n = min(n, PINOUT_SCAN_LIMIT)
        texts = [page_text(i) for i in range(scan_n)]
        scores = [len(_PINOUT_RE.findall(t)) for t in texts]
        # Densest pinout page beyond the front matter, then a window around it
        # to capture the whole multi-page pin table.
        best = max(range(front, scan_n), key=lambda i: scores[i]) if scan_n > front else 0
        window = range(max(front, best - 4), min(scan_n, best + 14))
        pages = sorted(set(range(front)) | set(window))

    out = [f"--- PAGE {i + 1} ---\n{page_text(i)}" for i in pages]
    # pypdf can emit lone surrogates for undecodable glyphs; they blow up every
    # downstream encode (the HTTP request body, the proposal JSON write).
    full = "\n\n".join(out).encode("utf-8", "ignore").decode("utf-8", "ignore")
    if len(full) > max_chars:
        full = full[:max_chars] + "\n[... truncated ...]"
    return full, len(pages)


# ============================================================================
# LLM extraction with the rich schema
# ============================================================================


def _make_openai_client():
    """Pulled into a helper so tests can monkey-patch."""
    from app.services.agent import _make_openai_client as agent_client
    return agent_client()


def llm_extract(text: str, target: CurationTarget) -> tuple[dict[str, Any], str]:
    """Run the LLM extraction. Returns (parsed_json, model_id)."""
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY not set — curation requires LLM access. "
            "Set it in your env and rerun."
        )

    client = _make_openai_client()
    model = os.environ.get("CURATION_MODEL", os.environ.get("OPENAI_MODEL", "gpt-4o"))
    is_mcu = (target.category or "").strip().lower() == "mcu"
    max_out = MCU_MAX_OUTPUT_TOKENS if is_mcu else MAX_OUTPUT_TOKENS

    user_msg = (
        f"Target MPN: {target.mpn}\n"
        f"Expected category: {target.category}\n"
        f"Archetype hint: {target.archetype}\n\n"
        f"---DATASHEET TEXT---\n{text}"
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": EXTRACTOR_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
        max_completion_tokens=max_out,
        temperature=0.1,
    )
    raw = response.choices[0].message.content or "{}"
    try:
        return json.loads(raw), model
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"LLM returned malformed JSON: {exc}") from exc


# ============================================================================
# Verifier — applied to a candidate part before merging
# ============================================================================


# Lookup: package name → expected pin count. Used as a soft check.
PACKAGE_PIN_COUNTS: dict[str, int] = {
    "SOT-23": 3, "SOT-23-3": 3, "SOT-23-5": 5, "SOT-23-6": 6,
    "SOT-89": 3, "SOT-223": 4,
    "TO-92": 3, "TO-220": 3, "TO-220-5": 5, "TO-252": 3, "TO-263": 3,
    "SOIC-8": 8, "SOIC-14": 14, "SOIC-16": 16, "SOIC-20": 20, "SOIC-28": 28,
    "TSSOP-8": 8, "TSSOP-14": 14, "TSSOP-16": 16, "TSSOP-20": 20, "TSSOP-24": 24,
    "MSOP-8": 8, "MSOP-10": 10,
    "DFN-6": 6, "DFN-8": 8, "DFN-10": 10,
    "QFN-16": 16, "QFN-20": 20, "QFN-24": 24, "QFN-28": 28, "QFN-32": 32,
    "QFN-40": 40, "QFN-48": 48, "QFN-56": 56, "QFN-64": 64,
    "LQFP-32": 32, "LQFP-48": 48, "LQFP-64": 64, "LQFP-100": 100, "LQFP-144": 144,
    "UQFN-20": 20, "UFQFPN-20": 20, "UFQFPN-28": 28, "UFQFPN-32": 32,
    "WLCSP-16": 16, "WLCSP-25": 25, "WLCSP-36": 36,
    "0402": 2, "0603": 2, "0805": 2, "1206": 2, "1210": 2,
}


def _normalise_pkg(pkg: str) -> str:
    """Strip whitespace, uppercase, collapse separators for lookup."""
    return re.sub(r"[\s_]+", "-", pkg.strip().upper())


def run_verifier(part: LibraryPart) -> dict[str, bool]:
    """Return a dict of check-name → pass/fail. Names of checks should be
    stable so we can track which ones are failing across the library."""
    checks: dict[str, bool] = {}

    # 1. Required-field presence.
    # Purely-mechanical parts (mounting holes, heatsinks) have no electrical
    # pins, so the pin checks below are exempted for them rather than forcing a
    # dummy pin into the data.
    is_mechanical = part.category == "mechanical"

    checks["has_mpn"] = bool(part.mpn and part.mpn.strip())
    checks["has_description"] = bool(part.description and part.description.strip())
    checks["has_category"] = bool(part.category and part.category.strip())
    checks["has_package"] = bool(part.package and part.package.strip())
    checks["has_footprint"] = bool(part.footprint and ":" in part.footprint)
    checks["has_symbol"] = bool(part.symbol and ":" in part.symbol)
    checks["has_pins"] = bool(part.pins) or is_mechanical

    # 2. Pin sanity. If pins are present we still validate them even for
    # mechanical parts; only the "missing pins" case is exempted.
    if part.pins:
        seen_numbers: set[str] = set()
        dup = False
        for p in part.pins:
            if p.number in seen_numbers:
                dup = True
                break
            seen_numbers.add(p.number)
        checks["pins_unique"] = not dup
        checks["pins_all_typed"] = all(p.type for p in part.pins)
        checks["pins_all_named"] = all(p.name and p.name.strip() for p in part.pins)
    elif is_mechanical:
        checks["pins_unique"] = True
        checks["pins_all_typed"] = True
        checks["pins_all_named"] = True
    else:
        checks["pins_unique"] = False
        checks["pins_all_typed"] = False
        checks["pins_all_named"] = False

    # 3. Pin-count plausibility vs the package.
    #
    # The library intentionally models only the pins a user actually wires —
    # NC / unused / auxiliary pads are omitted (an MPU6050 in QFN-24 is modelled
    # with its 6 usable pins; an RP2040 QFN-56 with the essential rails and a
    # handful of IO). So we do NOT require every package pin to be present. What
    # we DO flag is a count that *exceeds* the package — that's physically
    # impossible and signals a wrong package name, duplicated pins, or an LLM
    # extracting the wrong pinout (e.g. an eval-board, not the chip).
    pkg_key = _normalise_pkg(part.package or "")
    expected = PACKAGE_PIN_COUNTS.get(pkg_key)
    if expected is not None:
        # +1 tolerates QFN/DFN exposed pads modelled as an extra pin.
        checks["pin_count_matches_package"] = len(part.pins) <= expected + 1
    else:
        # Unknown package — pass by default but flag in notes elsewhere.
        checks["pin_count_matches_package"] = True

    # 3b. Gross under-count guard. A chip with almost no pins is a broken
    # extraction (the 2-pin nRF52840 pulled from a 3-page product brief). Only
    # ICs — passives / connectors / mechanical legitimately have few pins. We
    # still tolerate the library's intentional subset modelling (RP2040 ~21%,
    # MPU6050 ~25% of the package) and only flag truly degenerate counts.
    if part.category in {
        "mcu", "sensor", "regulator", "charger", "memory",
        "interface", "logic", "audio",
    }:
        floor = 3 if expected is None else max(3, int(expected * 0.10))
        checks["pin_count_plausible"] = len(part.pins) >= floor
    else:
        checks["pin_count_plausible"] = True

    # 4. Electrical sanity.
    if part.voltageMin is not None and part.voltageMax is not None:
        # Power devices legitimately rate negative (P-channel MOSFETs) or well
        # above 100 V (rectifiers / TVS), so the 0–100 V logic-rail cap wrongly
        # fails them. Exempt those categories and just require min <= max.
        if part.category in {"diode", "transistor", "protection"}:
            checks["voltage_range_sane"] = part.voltageMin <= part.voltageMax
        else:
            checks["voltage_range_sane"] = (
                0.0 <= part.voltageMin <= part.voltageMax <= 100.0
            )
    else:
        checks["voltage_range_sane"] = True

    # 5. Power/ground sanity for ICs. Floating regulators (LM317-style) have a
    # power pin but no GND by design, so a regulator only needs *a* power pin;
    # every other IC category needs both a power_in and a ground.
    if part.category == "regulator":
        checks["has_power_and_ground"] = any(
            p.type in {"power_in", "power_out"} for p in part.pins
        )
    elif part.category == "sensor":
        # Digital sensors (I2C/SPI — they expose bidirectional bus pins) must
        # have a power and a ground pin. Analog/resistive sensors (gas sensors,
        # thermistors) connect via heater + sense electrodes with no
        # conventional VCC/GND pair, so they're exempt from the ground rule.
        if any(p.type == "bidirectional" for p in part.pins):
            checks["has_power_and_ground"] = (
                any(p.type == "power_in" for p in part.pins)
                and any(p.type == "ground" for p in part.pins)
            )
        else:
            checks["has_power_and_ground"] = True
    elif part.category in {"mcu", "memory", "interface", "logic"}:
        has_pwr = any(p.type == "power_in" for p in part.pins)
        has_gnd = any(p.type == "ground" for p in part.pins)
        checks["has_power_and_ground"] = has_pwr and has_gnd
    else:
        checks["has_power_and_ground"] = True

    # 5c. Pin-integrity guard (2026-07 audit follow-up). A part modelled with a
    # truncated pinout silently breaks the schematic (the LLM can't wire a pin
    # that isn't there) — cheap insurance so a future part can't be added that
    # way. A LINEAR regulator MUST expose a power_out pin (its output is on-chip;
    # a *switching* regulator legitimately has none — the output is the external
    # SW-node → inductor, so it's exempt). Description-based detection to avoid a
    # verifier import cycle.
    if part.category == "regulator":
        d = (part.description or "").lower()
        is_switching = any(
            k in d for k in ("switching", "buck", "boost", "dc-dc", "dc/dc",
                             "smps", "step-down", "step-up", "sw ")
        )
        if is_switching:
            checks["regulator_has_output"] = True
        else:
            checks["regulator_has_output"] = any(
                p.type == "power_out" for p in part.pins
            )
    else:
        checks["regulator_has_output"] = True

    # 5b. Pin-type meaningfulness (engineering-rules Phase 0). The ERC pin-type
    # matrix (R24) is only as trustworthy as pin typing: an IC modelled with
    # every pin `passive` or every pin `unspecified` gives the matrix nothing to
    # reason about (passive↔anything and unspec↔anything never error), so a real
    # driver-contention / power-to-ground short would slip through. Flag ICs
    # whose pins are ALL passive or ALL unspecified. Passives / connectors /
    # mechanical legitimately are all-passive, so they're exempt.
    ic_categories = {
        "mcu", "regulator", "charger", "sensor", "memory",
        "interface", "logic", "audio", "actuator", "display",
    }
    if part.pins and part.category in ic_categories:
        types = {p.type for p in part.pins}
        all_passive = types <= {"passive"}
        all_unspec = types <= {"unspecified"}
        checks["pin_types_meaningful"] = not (all_passive or all_unspec)
    else:
        checks["pin_types_meaningful"] = True

    # 6. Companion-part references aren't dangling.
    # (Checked at library-write time against the merged library; here just
    # check structural validity.)
    checks["companions_well_formed"] = all(
        c.mpn and c.role for c in part.companions
    )

    # 7. Footprint pad/pin agreement (Phase 5 / #29). The footprint geometry is
    # generated from the package; verify every electrical pin the part exposes
    # gets a pad, and that the geometry isn't a generic placeholder for an
    # electrical part. Advisory — surfaces footprint gaps without blocking the
    # whole library on parametric geometry estimates.
    if part.pins and not is_mechanical:
        from app.services import footprints  # local import avoids a cycle

        status = footprints.footprint_status(part)
        checks["footprint_pads_match_pins"] = not status["missingPads"]
    else:
        checks["footprint_pads_match_pins"] = True

    return checks


def score_confidence(part: LibraryPart, checks: dict[str, bool],
                     llm_confidence: str) -> tuple[float, bool]:
    """Aggregate verifier checks + LLM self-reported confidence into a 0–1
    score and an auto-merge decision."""
    weights = {
        "has_mpn": 0.10,
        "has_description": 0.05,
        "has_category": 0.05,
        "has_package": 0.05,
        "has_footprint": 0.05,
        "has_symbol": 0.05,
        "has_pins": 0.10,
        "pins_unique": 0.10,
        "pins_all_typed": 0.05,
        "pins_all_named": 0.05,
        "pin_count_matches_package": 0.08,
        "pin_count_plausible": 0.07,
        "voltage_range_sane": 0.05,
        "has_power_and_ground": 0.10,
        "companions_well_formed": 0.05,
    }
    score = sum(w for k, w in weights.items() if checks.get(k))

    # Penalise low self-reported LLM confidence.
    if llm_confidence == "low":
        score *= 0.4
    elif llm_confidence == "medium":
        score *= 0.75

    # Auto-merge threshold: 0.90 and high LLM confidence. Conservative on
    # purpose — false positives erode trust faster than queued reviews do.
    auto_merge = score >= 0.90 and llm_confidence == "high"
    return round(score, 3), auto_merge


# ============================================================================
# Coverage matrix loader
# ============================================================================


def load_coverage_matrix(path: Path = COVERAGE_MATRIX) -> list[CurationTarget]:
    """Read the coverage matrix CSV. Missing file → empty list."""
    if not path.exists():
        return []
    import csv

    out: list[CurationTarget] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            mpn = (row.get("mpn") or "").strip()
            if not mpn or mpn.startswith("#"):
                continue
            try:
                tier = int(row.get("tier", "3"))
            except ValueError:
                tier = 3
            out.append(CurationTarget(
                mpn=mpn,
                category=(row.get("category") or "").strip(),
                tier=tier,
                archetype=(row.get("archetype") or "").strip(),
                datasheet_url=(row.get("datasheet_url") or "").strip() or None,
                notes=(row.get("notes") or "").strip(),
            ))
    return out


def filter_targets(
    targets: list[CurationTarget],
    *,
    only_tier: int | None = None,
    only_category: str | None = None,
    only_mpn: str | None = None,
    skip_existing_mpns: set[str] | None = None,
) -> list[CurationTarget]:
    skip_existing_mpns = skip_existing_mpns or set()
    out: list[CurationTarget] = []
    for t in targets:
        if only_mpn and t.mpn != only_mpn:
            continue
        if only_tier is not None and t.tier != only_tier:
            continue
        if only_category and t.category != only_category:
            continue
        if t.mpn in skip_existing_mpns:
            continue
        out.append(t)
    # Tier 1 first, then category alpha — stable sort.
    out.sort(key=lambda x: (x.tier, x.category, x.mpn))
    return out


# ============================================================================
# Main curation pipeline for a single target
# ============================================================================


def curate_one(target: CurationTarget) -> CurationOutcome:
    """Run the full extract → verify → score pipeline for one target.
    Always returns an outcome — never raises on the happy path."""
    started = time.time()
    notes: list[str] = []

    if not target.datasheet_url:
        return CurationOutcome(
            target=target, success=False,
            error="No datasheet_url in coverage matrix.",
            elapsed_seconds=time.time() - started,
        )

    # 1. Fetch.
    try:
        pdf_bytes = fetch_pdf(target.datasheet_url)
    except Exception as exc:    # noqa: BLE001
        return CurationOutcome(
            target=target, success=False,
            error=f"PDF fetch failed: {exc}",
            elapsed_seconds=time.time() - started,
        )

    # 2. Text extract. MCUs get a deeper window so the pinout table — which
    # sits well past the front matter in big family datasheets — is in-context.
    is_mcu = (target.category or "").strip().lower() == "mcu"
    max_pages = MCU_MAX_PAGES_FOR_EXTRACTION if is_mcu else MAX_PAGES_FOR_EXTRACTION
    max_chars = MCU_MAX_TEXT_CHARS if is_mcu else MAX_TEXT_CHARS
    try:
        text, pages_used = extract_text_from_pdf(
            pdf_bytes, max_pages, max_chars, seek_pinout=is_mcu
        )
    except Exception as exc:    # noqa: BLE001
        return CurationOutcome(
            target=target, success=False,
            error=f"PDF parse failed: {exc}",
            elapsed_seconds=time.time() - started,
        )
    if len(text.strip()) < 200:
        return CurationOutcome(
            target=target, success=False,
            error="PDF appears to be image-only; OCR fallback not yet wired.",
            elapsed_seconds=time.time() - started,
        )

    # 3. LLM extract.
    try:
        raw, model_id = llm_extract(text, target)
    except Exception as exc:    # noqa: BLE001
        return CurationOutcome(
            target=target, success=False,
            error=f"LLM extraction failed: {exc}",
            elapsed_seconds=time.time() - started,
        )

    llm_confidence = (raw.pop("_confidence", "medium") or "medium").lower()
    llm_notes = raw.pop("_notes", []) or []
    source_pages = raw.pop("_sourcePages", []) or []
    if not isinstance(llm_notes, list):
        llm_notes = [str(llm_notes)]
    notes.extend(str(n) for n in llm_notes)

    # 4. Coerce into our schema.
    raw.setdefault("alternatives", [])
    raw.setdefault("companions", [])
    raw.setdefault("interfaces", [])
    # Normalize pin shapes.
    for pin in raw.get("pins", []) or []:
        if not isinstance(pin, dict):
            continue
        side = (pin.get("side") or "left").lower()
        if side not in {"left", "right", "top", "bottom"}:
            side = "left"
        pin["side"] = side
        ptype = (pin.get("type") or "passive").lower().replace("-", "_")
        if ptype not in {"ground", "power_in", "power_out", "input", "output",
                         "bidirectional", "passive", "unspecified"}:
            ptype = "passive"
        pin["type"] = ptype
    # Force the MPN to match the target — LLM occasionally drifts.
    if raw.get("mpn") != target.mpn:
        notes.append(f"LLM returned MPN '{raw.get('mpn')}'; forced to '{target.mpn}'.")
        raw["mpn"] = target.mpn
    # Force category to the matrix's category — also a common drift.
    if target.category and raw.get("category") != target.category:
        notes.append(
            f"LLM returned category '{raw.get('category')}'; "
            f"matrix expected '{target.category}'."
        )
        raw["category"] = target.category

    # 5. Validate against Pydantic.
    try:
        part = LibraryPart.model_validate(raw)
    except Exception as exc:    # noqa: BLE001
        return CurationOutcome(
            target=target, success=False,
            error=f"Schema validation failed: {exc}",
            notes=notes + [str(exc)],
            elapsed_seconds=time.time() - started,
        )

    # 6. Stamp datasheet URL.
    if not part.datasheetUrl:
        part.datasheetUrl = target.datasheet_url

    # 7. Verify.
    checks = run_verifier(part)
    score, auto_merge = score_confidence(part, checks, llm_confidence)

    # 8. Attach provenance.
    part.provenance = Provenance(
        datasheetUrl=target.datasheet_url,
        extractedAt=datetime.now(timezone.utc).isoformat(),
        extractorVersion=BOT_VERSION,
        extractorModel=model_id,
        sourcePages=[int(p) for p in source_pages if isinstance(p, (int, str))
                     and str(p).isdigit()],
        confidenceScore=score,
        verifierChecks=checks,
        notes=notes,
    )
    part.curationStatus = "auto_merged" if auto_merge else "needs_review"

    return CurationOutcome(
        target=target, success=True, proposed_part=part,
        confidence=llm_confidence, confidence_score=score,
        auto_mergeable=auto_merge, verifier_checks=checks, notes=notes,
        elapsed_seconds=time.time() - started,
    )


# ============================================================================
# Proposed-part queue I/O
# ============================================================================


def proposed_path(mpn: str) -> Path:
    """Where a queued / auto-merged proposal lives on disk."""
    PROPOSED_DIR.mkdir(parents=True, exist_ok=True)
    # Sanitise MPN for filesystem.
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", mpn)
    return PROPOSED_DIR / f"{safe}.json"


def write_proposed(part: LibraryPart) -> Path:
    """Atomically write a proposed part to disk."""
    out = proposed_path(part.mpn)
    payload = json.dumps(part.model_dump(exclude_none=True), indent=2)
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(out)
    return out


def list_proposed() -> list[LibraryPart]:
    if not PROPOSED_DIR.exists():
        return []
    out: list[LibraryPart] = []
    for f in sorted(PROPOSED_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            out.append(LibraryPart.model_validate(data))
        except Exception:    # noqa: BLE001
            continue
    return out


def load_proposed(mpn: str) -> LibraryPart | None:
    p = proposed_path(mpn)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return LibraryPart.model_validate(data)
    except Exception:    # noqa: BLE001
        return None


def delete_proposed(mpn: str) -> bool:
    p = proposed_path(mpn)
    if p.exists():
        p.unlink()
        return True
    return False


# ============================================================================
# Library merge
# ============================================================================


def merge_into_user_parts(part: LibraryPart) -> None:
    """Promote a proposed part into the live user_parts.json layer. We don't
    touch parts.json (the hand-curated layer) — the bot's output sits on top."""
    from app.services import library as lib

    # Mark as auto_merged if it wasn't already.
    if part.curationStatus == "needs_review":
        part.curationStatus = "human_verified"
    lib.add_user_part(part)
