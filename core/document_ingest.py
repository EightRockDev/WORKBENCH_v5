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

Reading a monthly grid (Yardi/RealPage/AppFolio and agent-branded exports):
- If an "ANNUAL TOTALS" block appears, EVERY figure in it is already the
  full year. Use those numbers directly. Do not add up monthly columns, and
  do not take a single month's column as the year.
- These statements usually print a SUMMARY section and then repeat the same
  categories as GL detail further down. Count each category ONCE. If both are
  present, prefer the summary category totals.

Every expense dollar must land somewhere:
- The category names on the statement will not match this schema exactly.
  Map to the closest field rather than dropping a line -- a dropped category
  is the single most common extraction failure.
- A COMBINED line you cannot split (e.g. "Taxes & Insurance") goes entirely
  into the closest field, with the combination called out in
  extraction_notes. Never discard it because it spans two fields.
- Anything with no reasonable home belongs in t12_expenses.other.
- Exclude below-the-line items from totalOpex: debt service, capital
  expenditures, replacement reserves and other non-operating costs are NOT
  operating expenses.
- Before returning, check that your expense fields sum to roughly the
  statement's own Operating Expense total. If they do not, say so in
  extraction_notes with both numbers -- do not silently return a partial set.

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
    # sha256 of the source file's bytes — used to skip re-ingesting an
    # identical file (owner report 2026-08-04: the same doc uploaded several
    # times each re-ran and appended a "0 fields written" row). Set by the
    # ingest_document wrapper; recorded in the _ingestion_log for dedup.
    content_hash: str = ""

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


def _annual_block(rows, sheet_name: str) -> str | None:
    """Resolved full-year totals for a period-grid statement, if it is one.

    Never allowed to break ingestion: a sheet this cannot read still reaches
    the model as the raw grid it always did.
    """
    try:
        from core.t12_grid import normalized_annual_text
        return normalized_annual_text(rows, sheet_name)
    except Exception:
        return None


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
            rows = list(sheet.iter_rows(values_only=True))
            # A monthly grid gets a resolved annual block FIRST, so the model
            # never has to guess which of thirteen columns is the year.
            annual = _annual_block(rows, sheet.title)
            if annual:
                chunks.append("\n" + annual)
            chunks.append(f"\n--- SHEET: {sheet.title} ---\n")
            for row in rows:
                chunks.append("\t".join(str(c) if c is not None else "" for c in row))
        return "\n".join(chunks)

    def _via_xlrd(payload: bytes) -> str:
        import xlrd
        book = xlrd.open_workbook(file_contents=payload)
        chunks = []
        for sheet in book.sheets():
            rows = [[c.value for c in sheet.row(r)] for r in range(sheet.nrows)]
            annual = _annual_block(rows, sheet.name)
            if annual:
                chunks.append("\n" + annual)
            chunks.append(f"\n--- SHEET: {sheet.name} ---\n")
            for row in rows:
                chunks.append("\t".join(str(c) if c != "" else "" for c in row))
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


def file_content_hash(path: Path) -> str:
    """sha256 of a file's bytes, streamed so a 200 MB upload isn't loaded whole.

    Returns '' if the file can't be read — an empty hash never matches, so a
    read failure degrades to "not a duplicate" rather than blocking ingest.
    """
    import hashlib
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def find_prior_ingestion(folder: Path, content_hash: str) -> dict | None:
    """Return the log entry for a previously-ingested identical file, or None.

    Dedup key is the file's content hash: the same bytes ingested before (that
    actually wrote data) need not be re-run — re-running just re-extracts the
    same values and, pre-fix, appended another "0 fields written" row. Only
    entries that wrote at least one field count, so a prior failed/empty run
    doesn't block a real retry.
    """
    if not content_hash:
        return None
    from core.storage import get_storage
    from data.property_io import _rel
    storage = get_storage()
    key = f"{_rel(folder)}/sources.json"
    if not storage.is_file(key):
        return None
    try:
        data = json.loads(storage.read_text(key))
    except (json.JSONDecodeError, OSError):
        return None
    for entry in reversed((data or {}).get("_ingestion_log", []) or []):
        if (entry.get("content_hash") == content_hash
                and (entry.get("fields_written") or 0) > 0):
            return entry
    return None


def ingest_document(
    file_path: Path,
    document_type: str | None = None,
    *,
    max_chars: int = 60_000,
) -> IngestionResult:
    """Extract structured data from one document, stamping the content hash.

    Thin wrapper over ``_ingest_document`` so every return path — success or
    error — carries ``content_hash`` for dedup, without threading it through
    each of the impl's return statements.
    """
    result = _ingest_document(file_path, document_type, max_chars=max_chars)
    if not result.content_hash:
        result.content_hash = file_content_hash(file_path)
    return result


def _ingest_document(
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
        # AC-11.2: an org with ai_enabled off must reach no model at all.
        # Placed on the line that BUILDS the client, so a new surface
        # cannot forget the check and still get one.
        from core import ai_gate
        ai_gate.require_ai(
            'Document extraction',
            'Enter the T-12 / rent-roll figures by hand on the Property Card.',
            ai_gate.current_org_id())
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=4096,
            # temperature=0: extraction must be repeatable. Without it the API
            # default sampling made the same PDF+type return slightly different
            # values (and field counts) run-to-run (owner report 2026-08-04).
            temperature=0,
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
        n_written += _count_data_points(k, wrapped)

    # Add extraction metadata sidecar. content_hash lets a later upload of the
    # same file be recognized as a duplicate (see find_prior_ingestion).
    existing.setdefault("_ingestion_log", []).append({
        "source_doc": result.source_doc,
        "document_type": result.document_type,
        "extracted_at": extracted_at,
        "fields_written": n_written,
        "content_hash": result.content_hash,
        "extraction_notes": result.extraction_notes,
    })

    storage.write_text(key, json.dumps(existing, indent=2, default=str))
    return n_written


def _count_leaves(v: Any) -> int:
    """Count committed data points, not top-level keys.

    The old count incremented once per top-level key — so a whole nested
    block (all the T-12 revenue lines) counted as 1, a null value counted as
    a written field, and a rent-roll counted as 1 (owner report 2026-08-04:
    "6 fields" vs "9 fields" was not comparing like with like). This counts
    real leaves: a wrapped scalar with a non-null value = 1, a nested dict =
    the sum of its leaves, a list = its length, a bare scalar = 1.
    """
    if v is None:
        return 0
    if isinstance(v, dict):
        if "value" in v and "source_doc" in v:      # a wrapped scalar field
            return 0 if v.get("value") is None else 1
        return sum(_count_leaves(x) for x in v.values())
    if isinstance(v, list):
        return len(v)
    return 1


def _count_data_points(key: str, wrapped: Any) -> int:
    """Leaf count for one committed key; rentRoll counts as its unit rows."""
    if key == "rentRoll" and isinstance(wrapped, dict):
        units = wrapped.get("units")
        return len(units) if isinstance(units, list) else 1
    return _count_leaves(wrapped)


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
