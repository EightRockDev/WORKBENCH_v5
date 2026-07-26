"""AI-driven document ingestion for T-12, rent roll, and OM uploads.

Drop a PDF / XLSX / CSV into the property folder; Claude extracts structured
fields (revenue, expenses, unit mix, lease distribution) with per-field
provenance (source_doc, page, confidence) and writes them into
``sources.json`` using the workbench's existing schema.

Eliminates the bug class where extracted data never makes it to
sources.json — every successful extraction is committed atomically.

Schema written into ``sources.json``:

    {
      "totalRevenue":  {"value": 1234567, "source_doc": "T12.pdf", "page": 1,
                        "confidence": 0.95, "extracted_at": "2026-05-26T...",
                        "label": "Total Revenue"},
      "totalOpex":     {"value": 567890, ...},
      "t12_revenue":   {"grossPotentialRent": {...}, "vacancy": {...}, ...},
      "t12_expenses":  {"realEstateTaxes": {...}, "insurance": {...}, ...},
      "unitMix":       [{"floorplan": "1BR", "count": 40, ...}, ...],
      ...
    }

Each leaf value is either a raw number (for backward compat with existing
sources.json files) or a provenance dict ``{"value": ..., "source_doc": ..., ...}``.
Loaders handle both shapes via ``.get("value")`` fallback.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Extraction prompts — one per document type
# ---------------------------------------------------------------------------

_T12_PROMPT = """\
You are extracting a trailing-12-month operating statement from a real
estate document. Return ONLY valid JSON (no markdown fences, no other text)
matching this exact schema:

{
  "totalRevenue": <int dollars or null>,
  "totalOpex": <int dollars or null>,
  "noi": <int dollars or null>,
  "t12_revenue": {
    "grossPotentialRent": <int or null>,
    "vacancy": <int or null>,
    "concessions": <int or null>,
    "badDebt": <int or null>,
    "rubsRecovery": <int or null>,
    "otherIncome": <int or null>
  },
  "t12_fixedCharges": {
    "realEstateTaxes": <int or null>,
    "insurance": <int or null>
  },
  "t12_expenses": {
    "payroll": <int or null>,
    "marketing": <int or null>,
    "repairsMaintenance": <int or null>,
    "utilities": <int or null>,
    "managementFee": <int or null>,
    "contractServices": <int or null>,
    "administrative": <int or null>,
    "other": <int or null>
  },
  "extraction_notes": "<short note: which page each major figure came from, any rough estimates>"
}

Rules:
- Use null if a line isn't present (don't make up zeros)
- Treat vacancy/concessions/badDebt as POSITIVE losses (subtract from rent)
- Round to whole dollars
- If multiple periods shown (e.g. T-12 + budget), use the T-12 actuals
- If only a P&L summary is shown (no line detail), populate totalRevenue/
  totalOpex/noi and leave detail breakouts null

Document content follows:

"""


_RENT_ROLL_PROMPT = """\
You are extracting unit-mix and rent-roll summary from a rental property
rent roll. Return ONLY valid JSON matching this schema:

{
  "totalUnits": <int>,
  "occupiedUnits": <int>,
  "occupancyPct": <float 0.0-1.0>,
  "unitMix": [
    {"floorplan": "<e.g. 'A1', '1BR/1BA'>", "bedrooms": <int>,
     "count": <int>, "avgSqft": <int or null>,
     "avgRent": <float>, "marketRent": <float or null>}
  ],
  "leaseExpirationDistribution": [
    {"month": "<YYYY-MM>", "expiring_count": <int>}
  ],
  "delinquencySummary": {
    "currentDelinquentBalance": <float or null>,
    "delinquentUnitCount": <int or null>
  },
  "rentSummary": {
    "avgEffectiveRent": <float or null>,
    "marketRentAvg": <float or null>,
    "lossToLeasePct": <float 0.0-1.0 or null>
  },
  "extraction_notes": "<short note explaining any inferred values>"
}

Rules:
- bedrooms=0 means studio/efficiency
- avgEffectiveRent is actual rent paid (not asking); leave null if not derivable
- For leaseExpirationDistribution, return up to 18 months of expirations
- If the rent roll has 100+ units, sample the unitMix categories (don't
  enumerate every unit) — get one row per FLOORPLAN type

Document content follows:

"""


_OM_PROMPT = """\
You are extracting a multifamily Offering Memorandum executive summary.
Return ONLY valid JSON matching this schema:

{
  "askingPrice": <int dollars or null>,
  "askingCapRate": <float 0.0-1.0 or null>,
  "pricePerUnit": <int or null>,
  "totalUnits": <int or null>,
  "yearBuilt": <int or null>,
  "occupancyAtPresent": <float 0.0-1.0 or null>,
  "seller_proforma_noi": <int or null>,
  "in_place_noi": <int or null>,
  "narrative_summary": "<3-5 sentence executive summary written in 40-yr-experience underwriter voice>"
}

Document content follows:

"""


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ExtractedField:
    """One extracted value with provenance."""
    key: str
    value: Any
    source_doc: str
    page: int | None = None
    confidence: float = 0.85
    extracted_at: str = field(default_factory=lambda: dt.datetime.now().isoformat(timespec="seconds"))
    label: str = ""

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "source_doc": self.source_doc,
            "page": self.page,
            "confidence": self.confidence,
            "extracted_at": self.extracted_at,
            "label": self.label or self.key,
        }


@dataclass
class IngestionResult:
    """Outcome of one ingest call. Caller writes `extracted` to sources.json
    via ``commit_to_sources_json``."""
    document_type: str           # "t12" | "rent_roll" | "om"
    source_doc: str
    extracted: dict[str, Any]
    confidence: float
    extraction_notes: str = ""
    error: str | None = None

    @property
    def is_success(self) -> bool:
        return self.error is None and bool(self.extracted)


# ---------------------------------------------------------------------------
# Document type classification
# ---------------------------------------------------------------------------

def classify_document(filename: str, first_page_text: str = "") -> str:
    """Guess the document type from filename + first page content.

    Returns one of: "t12", "rent_roll", "om", "unknown".
    """
    fn = filename.lower()
    text = first_page_text.lower()

    # Filename heuristics first (most reliable)
    if any(kw in fn for kw in ("t-12", "t12", "trailing", "p&l", "income statement", "operating statement")):
        return "t12"
    if any(kw in fn for kw in ("rent roll", "rentroll", "rr_", "rr-")):
        return "rent_roll"
    if any(kw in fn for kw in ("om", "offering", "investment summary", "marketing package")):
        return "om"

    # Content heuristics
    if "trailing twelve" in text or "trailing 12" in text or "t-12" in text:
        return "t12"
    if "rent roll" in text or ("unit" in text and "lease end" in text):
        return "rent_roll"
    if "offering memorandum" in text or "investment opportunity" in text:
        return "om"

    return "unknown"


# ---------------------------------------------------------------------------
# Document text extraction (PDF / XLSX / CSV)
# ---------------------------------------------------------------------------

def extract_text_from_pdf(pdf_path: Path, max_pages: int = 30) -> list[tuple[int, str]]:
    """Return list of (page_num, text) for each page. Best-effort; returns
    empty list if no PDF parser is available."""
    try:
        import pypdf
    except ImportError:
        try:
            # Try pdfplumber as alternative
            import pdfplumber
            with pdfplumber.open(pdf_path) as pdf:
                return [
                    (i + 1, page.extract_text() or "")
                    for i, page in enumerate(pdf.pages[:max_pages])
                ]
        except ImportError:
            return []

    reader = pypdf.PdfReader(str(pdf_path))
    out = []
    for i, page in enumerate(reader.pages[:max_pages]):
        out.append((i + 1, page.extract_text() or ""))
    return out


def extract_text_from_xlsx(xlsx_path: Path) -> str:
    """Concat all sheet contents to plain text.

    openpyxl reads modern .xlsx/.xlsm; legacy .xls (BIFF) goes through xlrd.
    Some systems export .xls bytes under an .xlsx name (and vice versa), so on
    a format error the other reader is tried before giving up.
    """
    def _via_openpyxl(payload: bytes) -> str:
        # BytesIO, not the path: openpyxl rejects a ".xls" FILENAME even when
        # the bytes are a perfectly good .xlsx (mislabeled PM-system exports).
        import io
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(payload), read_only=True,
                                    data_only=True)
        chunks = []
        for sheet in wb.worksheets:
            chunks.append(f"\n--- SHEET: {sheet.title} ---\n")
            for row in sheet.iter_rows(values_only=True):
                chunks.append("\t".join(str(c) if c is not None else "" for c in row))
        return "\n".join(chunks)

    def _via_xlrd(payload: bytes) -> str:
        import xlrd
        book = xlrd.open_workbook(file_contents=payload)
        chunks = []
        for sheet in book.sheets():
            chunks.append(f"\n--- SHEET: {sheet.name} ---\n")
            for r in range(sheet.nrows):
                chunks.append("\t".join(str(c.value) if c.value != "" else ""
                                         for c in sheet.row(r)))
        return "\n".join(chunks)

    payload = xlsx_path.read_bytes()
    # Route by CONTENT, not extension (mislabeled exports are common):
    # xlsx = zip ("PK"), legacy xls = OLE2 compound file (D0 CF 11 E0).
    if payload[:4] == b"\xd0\xcf\x11\xe0":
        readers = [_via_xlrd, _via_openpyxl]
    else:
        readers = [_via_openpyxl, _via_xlrd]
    last_err: Exception | None = None
    for reader in readers:
        try:
            return reader(payload)
        except Exception as e:      # wrong format for this reader - try the other
            last_err = e
    raise last_err if last_err else RuntimeError("no spreadsheet reader available")


def extract_text_from_csv(csv_path: Path) -> str:
    return csv_path.read_text(encoding="utf-8", errors="replace")


def extract_document_text(file_path: Path) -> str:
    """Detect file type from suffix and extract text. Returns "" on failure."""
    suffix = file_path.suffix.lower()
    try:
        if suffix == ".pdf":
            pages = extract_text_from_pdf(file_path)
            return "\n\n".join(f"[Page {p}]\n{t}" for p, t in pages)
        if suffix in (".xlsx", ".xls", ".xlsm"):
            return extract_text_from_xlsx(file_path)
        if suffix == ".csv":
            return extract_text_from_csv(file_path)
        if suffix == ".txt":
            return file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"[extraction error: {e}]"
    return ""


# ---------------------------------------------------------------------------
# AI extraction
# ---------------------------------------------------------------------------

_PROMPTS = {
    "t12": _T12_PROMPT,
    "rent_roll": _RENT_ROLL_PROMPT,
    "om": _OM_PROMPT,
}


def ingest_document(
    file_path: Path,
    document_type: str | None = None,
    *,
    max_chars: int = 60_000,
) -> IngestionResult:
    """Extract structured data from one document.

    ``document_type`` overrides auto-classification. Default is auto.

    Tabular files (XLSX/CSV) are parsed DETERMINISTICALLY first - a rent roll
    or T-12 in Excel is structured data and needs no model and no API key
    (spec 11: the core runs with AI off). The model handles PDFs and layouts
    the parser can't recognize.
    """
    try:
        size = file_path.stat().st_size
    except OSError:
        size = 0
    if size == 0:
        return IngestionResult(
            document_type="unknown",
            source_doc=file_path.name,
            extracted={},
            confidence=0.0,
            error=("EMPTY_FILE: the uploaded file contained 0 bytes. This "
                   "usually means it is a cloud-only OneDrive/SharePoint "
                   "placeholder or was dragged straight from an email "
                   "preview. Open it once on this computer (or File > Save "
                   "As to the Desktop), then upload that copy."),
        )

    text = extract_document_text(file_path)
    if not text or text.startswith("[extraction error"):
        cause = text[len("[extraction error: "):-1] if text else "the file produced no readable text"
        return IngestionResult(
            document_type="unknown",
            source_doc=file_path.name,
            extracted={},
            confidence=0.0,
            error=(f"could not read {file_path.name} - {cause}. If this is a "
                   "very old .xls export, re-save it as .xlsx and upload again."),
        )

    if document_type is None:
        document_type = classify_document(file_path.name, text[:2000])

    # ---- Deterministic path for tabular files (no API key required) ------
    tabular = file_path.suffix.lower() in (".xlsx", ".xlsm", ".xls", ".csv")
    if tabular and document_type in ("rent_roll", "t12", "unknown"):
        from core import rent_roll_parser as rrp
        if document_type in ("rent_roll", "unknown"):
            block = rrp.parse_rent_roll(file_path)
            if block is not None:
                n = block["summary"]["totalUnits"]
                return IngestionResult(
                    document_type="rent_roll",
                    source_doc=file_path.name,
                    extracted={"rentRoll": block,
                               "totalUnits": n,
                               "occupiedUnits": block["summary"]["occupiedUnits"],
                               "occupancyPct": block["summary"]["occupancyPct"]},
                    confidence=rrp.CONFIDENCE,
                    extraction_notes=(f"Parsed deterministically from the "
                                      f"spreadsheet ({n} unit rows) - no AI used."),
                )
        if document_type in ("t12", "unknown"):
            t12 = rrp.parse_t12(file_path)
            if t12 is not None:
                return IngestionResult(
                    document_type="t12",
                    source_doc=file_path.name,
                    extracted=t12,
                    confidence=rrp.CONFIDENCE,
                    extraction_notes=("Parsed deterministically from the "
                                      "spreadsheet (12-month totals column) - "
                                      "no AI used."),
                )

    if document_type == "unknown":
        return IngestionResult(
            document_type="unknown",
            source_doc=file_path.name,
            extracted={},
            confidence=0.0,
            error="could not classify document — please rename file to include 't12', 'rent roll', or 'om'",
        )

    prompt = _PROMPTS.get(document_type)
    if prompt is None:
        return IngestionResult(
            document_type=document_type,
            source_doc=file_path.name,
            extracted={},
            confidence=0.0,
            error=f"no extractor for document_type={document_type}",
        )

    truncated = text[:max_chars]
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        if tabular:
            hint = ("the spreadsheet layout was not recognized by the "
                    "built-in parser, and AI extraction needs an Anthropic "
                    "API key")
        else:
            hint = "AI extraction of PDFs needs an Anthropic API key"
        return IngestionResult(
            document_type=document_type,
            source_doc=file_path.name,
            extracted={},
            confidence=0.0,
            error=f"NEEDS_API_KEY: {hint}",
        )

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt + truncated}],
        )
        content = msg.content[0].text if msg.content else ""
        # Strip code-fence wrappers if present
        import re
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.MULTILINE)
        data = json.loads(content)
        if not isinstance(data, dict):
            return IngestionResult(
                document_type=document_type,
                source_doc=file_path.name,
                extracted={},
                confidence=0.0,
                error="Claude returned non-dict JSON",
            )
        notes = data.pop("extraction_notes", "") or ""
        return IngestionResult(
            document_type=document_type,
            source_doc=file_path.name,
            extracted=data,
            confidence=0.85,
            extraction_notes=notes,
        )
    except json.JSONDecodeError as e:
        return IngestionResult(
            document_type=document_type,
            source_doc=file_path.name,
            extracted={},
            confidence=0.0,
            error=f"Claude returned invalid JSON: {e}",
        )
    except Exception as e:
        return IngestionResult(
            document_type=document_type,
            source_doc=file_path.name,
            extracted={},
            confidence=0.0,
            error=f"{type(e).__name__}: {e}",
        )


# ---------------------------------------------------------------------------
# Commit to sources.json with provenance
# ---------------------------------------------------------------------------

def commit_to_sources_json(
    folder: Path,
    result: IngestionResult,
    overwrite: bool = False,
) -> int:
    """Merge `result.extracted` into the property's sources.json.

    Each leaf value is wrapped as ``{value, source_doc, page, confidence,
    extracted_at, label}`` so the UI can show provenance + Brian can override.

    Returns the count of fields written. If ``overwrite=False`` and a field
    already exists in sources.json, it's preserved (no clobber). Set
    ``overwrite=True`` to replace.
    """
    if not result.is_success:
        return 0

    from core.storage import get_storage
    from data.property_io import _rel
    storage = get_storage()
    key = f"{_rel(folder)}/sources.json"

    existing: dict[str, Any] = {}
    if storage.is_file(key):
        try:
            existing = json.loads(storage.read_text(key))
            if not isinstance(existing, dict):
                existing = {}
        except json.JSONDecodeError:
            existing = {}

    n_written = 0
    extracted_at = dt.datetime.now().isoformat(timespec="seconds")

    def wrap_field(k: str, v: Any) -> dict | Any:
        # Don't wrap None or lists (lists like unitMix stay as-is)
        if v is None or isinstance(v, (list, dict)):
            return v
        return {
            "value": v,
            "source_doc": result.source_doc,
            "confidence": result.confidence,
            "extracted_at": extracted_at,
            "label": k,
        }

    # Walk extracted dict and merge
    for k, v in result.extracted.items():
        if k == "rentRoll":
            # Canonical block read RAW by ui/rent_roll.py and the anomaly
            # detectors (summary values, units list). It carries its own
            # provenance (file/date) - wrapping its leaves would break every
            # reader, so it is stored exactly as parsed.
            wrapped = v
        elif isinstance(v, dict):
            wrapped = _wrap_nested(v, result.source_doc, extracted_at, result.confidence)
        else:
            wrapped = wrap_field(k, v)

        if k in existing and not overwrite:
            continue
        existing[k] = wrapped
        n_written += 1

    # Add extraction metadata sidecar
    existing.setdefault("_ingestion_log", []).append({
        "source_doc": result.source_doc,
        "document_type": result.document_type,
        "extracted_at": extracted_at,
        "fields_written": n_written,
        "extraction_notes": result.extraction_notes,
    })

    storage.write_text(key, json.dumps(existing, indent=2, default=str))
    return n_written


def _wrap_nested(d: dict, source_doc: str, extracted_at: str, confidence: float) -> dict:
    """Recursively wrap leaf values in a nested dict."""
    out = {}
    for k, v in d.items():
        if v is None:
            out[k] = None
        elif isinstance(v, dict):
            out[k] = _wrap_nested(v, source_doc, extracted_at, confidence)
        elif isinstance(v, list):
            out[k] = v   # lists kept as-is (e.g. unitMix)
        else:
            out[k] = {
                "value": v,
                "source_doc": source_doc,
                "confidence": confidence,
                "extracted_at": extracted_at,
                "label": k,
            }
    return out
