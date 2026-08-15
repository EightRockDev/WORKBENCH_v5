"""Per-property sale history from the assessor data we already pull.

The municipal assessor feeds ingested nightly into ``muni_records`` carry
last-sale fields (price / date / buyer) and, for some localities, deed
book+page. Phase 0 stores the raw record but the spine deliberately IGNORES the
sale fields (they're in ``_IGNORED_KEYS``), so the data is on hand yet never
surfaced. This module reads it back out — READ-ONLY, no change to the nightly
spine build — and returns the ``{date, price, grantor, grantee}`` shape the
existing Sale History card already renders.

Matching a displayed property to its assessor record reuses phase 0's proven
``normalize_record`` (APN first, normalized address as fallback) so we don't
reinvent the parcel matcher. Sale-field extraction is tolerant of the many
spellings different county feeds use.

Everything here is defensive: any failure returns an empty list so the Sale
History card simply falls back to "No sale history available" rather than
breaking the page.
"""

from __future__ import annotations

import sqlite3
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

# Raw-key spellings seen across VA assessor feeds (Chesapeake ArcGIS uses
# last_sale_*; Socrata/VGIN rolls use saleprice/saledate; deeds carry bk/pg).
# The 'assessor+sales' registry feeds add: Wake TOTSALPRICE, Forsyth
# LASTQUALIFIEDSALEPRICE/-DATE, Nashville OwnDate (etl_munidata.MUNI_FEEDS
# notes; TOTSALPRICE confirmed against the live DB 2026-08-06 — its absence
# here made 1.38M assessor+sales rows extract price=None).
_PRICE_KEYS = ("lastsaleprice", "last_sale_price", "saleprice", "sale_price",
               "saleamount", "sale_amount", "saleamt", "salesprice",
               "saleprice1", "price", "considerationamount", "consideration",
               "totsalprice", "lastqualifiedsaleprice")
_DATE_KEYS = ("lastsaledate", "last_sale_date", "saledate", "sale_date",
              "salesdate", "transferdate", "transfer_date", "deeddate",
              "recordeddate", "recorddate", "saledate1", "owndate",
              "lastqualifiedsaledate",
              # LAST on purpose (earlier keys win): Chesapeake's parcels layer
              # names its transfer date just "TRANSFER" (2026-08-11).
              "transfer")
_BUYER_KEYS = ("last_sale_buyer", "lastsalebuyer", "grantee", "buyer",
               "buyername", "granteename", "newowner",
               # Chesapeake LandBook: owner-of-record after the transfer.
               "currentowner")
_SELLER_KEYS = ("grantor", "seller", "sellername", "grantorname", "prevowner",
                "previousowner")
_DEED_BOOK_KEYS = ("deedbk", "deedbook", "deed_book", "book", "instrumentbook")
_DEED_PAGE_KEYS = ("deedpg", "deedpage", "deed_page", "page", "instrumentpage")

_KEY_JUNK = re.compile(r"[^a-z0-9]")


def _nk(key: str) -> str:
    """Loosely normalize a raw attribute key: lowercase, strip non-alnum."""
    return _KEY_JUNK.sub("", str(key or "").lower())


def _first(raw_lower: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        v = raw_lower.get(k)
        if v not in (None, "", " "):
            return v
    return None


def _coerce_price(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if v else None
    s = re.sub(r"[^0-9.]", "", str(v))
    if not s or s == ".":
        return None
    try:
        n = float(s)
    except ValueError:
        return None
    return n or None


def _coerce_date(v: Any) -> str | None:
    """Return an ISO 'YYYY-MM-DD' string (or the trimmed original if we can't
    parse it). Handles unix-ms epochs (ArcGIS), ISO, and M/D/Y."""
    if v in (None, "", " "):
        return None
    # Epoch milliseconds (ArcGIS date fields) — 10-14 digit integers.
    if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
        try:
            n = int(v)
        except (TypeError, ValueError):
            n = None
        if n is not None and n <= 0:
            # 0 is the assessors' "never sold / unknown" sentinel; without this
            # it fell through the epoch bands and came back as the phantom
            # date string "0".
            return None
        if n and 19000101 <= n <= 21991231:
            # YYYYMMDD integers sit inside the epoch-seconds band (they'd parse
            # as an August-1970 timestamp) — try the calendar reading first.
            # Real epoch-seconds sale dates are ~1.7e9, far above this range.
            try:
                return dt.datetime.strptime(str(n), "%Y%m%d").date().isoformat()
            except ValueError:
                pass
        if n and n > 10_000_000_000:          # ms since epoch
            try:
                return dt.datetime.fromtimestamp(n / 1000, dt.timezone.utc).date().isoformat()
            except (OverflowError, OSError, ValueError):
                return None
        if n and 10_000_000 < n < 10_000_000_000:   # seconds since epoch
            try:
                return dt.datetime.fromtimestamp(n, dt.timezone.utc).date().isoformat()
            except (OverflowError, OSError, ValueError):
                return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y", "%m-%d-%Y", "%Y%m%d"):
        try:
            return dt.datetime.strptime(s[:len(fmt) + 4], fmt).date().isoformat()
        except ValueError:
            continue
    return s[:10] if s else None


def extract_sale_records(raw: dict) -> list[dict]:
    """Pull the sale event(s) out of ONE raw assessor record.

    Assessor rolls almost always carry only the most-recent sale, so this
    returns 0 or 1 record. Emits a record only when there's at least a price or
    a date — a bare buyer name is the current owner, not a dated sale.
    """
    if not isinstance(raw, dict) or not raw:
        return []
    low = {_nk(k): v for k, v in raw.items()}
    price = _coerce_price(_first(low, _PRICE_KEYS))
    date = _coerce_date(_first(low, _DATE_KEYS))
    if price is None and date is None:
        return []
    grantee = _first(low, _BUYER_KEYS)
    grantor = _first(low, _SELLER_KEYS)
    book = _first(low, _DEED_BOOK_KEYS)
    page = _first(low, _DEED_PAGE_KEYS)
    notes = ""
    if book or page:
        notes = f"Deed {book or '?'}/{page or '?'}"
    return [{
        "date": date,
        "price": price,
        "grantor": (str(grantor).strip() if grantor else ""),
        "grantee": (str(grantee).strip() if grantee else ""),
        "notes": notes,
        "source": "assessor transfer record",
    }]


# --------------------------------------------------------------- matching

_ADDR_JUNK = re.compile(r"[^a-z0-9 ]")
_WS = re.compile(r"\s+")


def _norm_addr(s: Any) -> str:
    # Prefer parity's matcher (collapses Street→St, drops unit designators,
    # keys ranges on the first number): the exact-equality fallback here dies
    # on "2110 Richmond Street" vs "2110 Richmond St" without it.
    try:
        from core.phase0_parity import normalize_address
        return normalize_address(str(s) if s is not None else "")
    except Exception:
        t = _ADDR_JUNK.sub(" ", str(s or "").lower())
        return _WS.sub(" ", t).strip()


def _norm_apn(s: Any) -> str:
    return _KEY_JUNK.sub("", str(s or "").lower())


def _muni_db_path(db_path: Path | None) -> Path | None:
    if db_path is not None:
        return Path(db_path)
    from core import phase0
    # phase0 exposes `find_workbench_db()` (→ Path | None), NOT `workbench_db()`.
    # The old name raised AttributeError on every call, which the broad
    # `except` in sale_history_for swallowed — so EVERY property silently read
    # "No sale history available." Locating the DB can also legitimately return
    # None (no muni DB on this box); callers must handle None.
    return phase0.find_workbench_db()


# One page view scans the market's muni rows twice (Sale History card + radar
# tenure), and Streamlit reruns the whole script on every widget interaction —
# memoize per property identity, invalidated when workbench.db changes (the
# nightly pull rewrites it, bumping mtime).
_HIST_CACHE: dict[tuple, list[dict]] = {}
_HIST_CACHE_MAX = 512


def _cache_key(prop: dict, db_path: Path | None) -> tuple | None:
    path = _muni_db_path(db_path)
    if path is None or not Path(path).exists():
        return None
    try:
        stamp = Path(path).stat().st_mtime_ns
    except OSError:
        return None
    return (_norm_apn(prop.get("apn")), _norm_addr(prop.get("address")),
            (prop.get("city") or "").strip().lower(),
            (prop.get("market") or "").strip().lower(), str(path), stamp)


def sale_history_for(prop: dict, *, db_path: Path | None = None) -> list[dict]:
    """Sale-history records for one property, from its assessor muni record(s).

    Read-only. Returns a list of ``{date, price, grantor, grantee, notes,
    source}`` newest-first, or ``[]`` if nothing matches / on any error.
    """
    try:
        key = _cache_key(prop, db_path)
        if key is not None:
            hit = _HIST_CACHE.get(key)
            if hit is not None:
                return [dict(r) for r in hit]
        out = _sale_history_for(prop, db_path)
        if key is not None:
            if len(_HIST_CACHE) >= _HIST_CACHE_MAX:
                _HIST_CACHE.clear()
            _HIST_CACHE[key] = [dict(r) for r in out]
        return out
    except Exception:
        return []


def last_sale_year_for(prop: dict, *, db_path: Path | None = None) -> int | None:
    """Year of the property's most recent assessor-recorded sale.

    Feeds radar v2's tenure signal: the vendor ``last_sold_year`` column only
    exists on the legacy read path, so 8r properties otherwise read "No deed
    record on file" forever. Returns None when no dated sale is on record —
    tenure stays *unknown*, never scored as 0.
    """
    for rec in sale_history_for(prop, db_path=db_path):
        d = str(rec.get("date") or "")
        if len(d) >= 4 and d[:4].isdigit():
            year = int(d[:4])
            if 1800 <= year <= dt.date.today().year + 1:
                return year
    return None


_DIRECTIONS = {"n", "s", "e", "w", "ne", "nw", "se", "sw",
               "north", "south", "east", "west"}
_STREET_TYPES = {"st", "street", "ave", "avenue", "rd", "road", "dr", "drive",
                 "blvd", "boulevard", "ln", "lane", "ct", "court", "cir",
                 "circle", "pl", "place", "ter", "terrace", "way", "pkwy",
                 "parkway", "hwy", "highway", "trl", "trail", "run", "loop"}


def _addr_dir(address: str | None) -> str:
    """The directional prefix of an address, or "" when it has none.

    Dropping this was a real defect (owner, 2026-08-15: "not accurate").
    "3000 S. Cape Henry" and "3000 N. Cape Henry" are DIFFERENT STREETS in
    Norfolk, and a key that ignored the direction happily matched one to the
    other - producing a 26-unit apartment building whose sale history was a
    house changing hands between two couples for $313,500.
    """
    norm = _norm_addr(address)
    parts = norm.split()
    if len(parts) >= 2 and parts[0].isdigit() and parts[1] in _DIRECTIONS:
        return parts[1][0]        # n/s/e/w - "north" and "n" must agree
    return ""


def _dirs_compatible(a: str | None, b: str | None) -> bool:
    """True when two addresses do not CONTRADICT each other on direction.

    One side omitting the direction is normal (the assessor often does); the
    two naming DIFFERENT directions is a different street and must never
    match.
    """
    da, db = _addr_dir(a), _addr_dir(b)
    return not (da and db and da != db)


def _units_compatible(a: object, b: object) -> bool:
    """True unless two unit counts say these are plainly different buildings.

    The strongest corroboration available for free: a 26-unit apartment does
    not share a parcel with a 1-unit house. Only judges when BOTH sides know
    their unit count - an unknown never blocks a match.
    """
    try:
        ua = int(float(a)) if a is not None else None
        ub = int(float(b)) if b is not None else None
    except (TypeError, ValueError):
        return True
    if not ua or not ub:
        return True
    lo, hi = sorted((ua, ub))
    return hi <= max(lo * 2, lo + 2)


def _addr_core(address: str | None) -> str:
    """House number + the distinctive street word, and nothing else.

    Exact address equality is too strict to be the only fallback. A vendor
    writes "3000 S. Cape Henry"; the assessor writes "3000 CAPE HENRY AVE".
    Both normalize to different strings, so the sale card renders empty while
    the sale sits in the index. This key keeps the two parts that actually
    identify a property - the number and the street's own name - and drops
    the parts the two sources disagree about: leading direction and trailing
    street type.

    "3000 S Cape Henry Ave" and "3000 Cape Henry" both -> "3000|cape".
    Returns "" when there is no house number, because without one this is far
    too loose to be safe.
    """
    norm = _norm_addr(address)
    if not norm:
        return ""
    parts = norm.split()
    if not parts or not parts[0].isdigit():
        return ""
    number = parts[0]
    for tok in parts[1:]:
        if tok in _DIRECTIONS or tok in _STREET_TYPES or tok.isdigit():
            continue
        return f"{number}|{tok}"
    return ""


def _apn_via_address(prop: dict, path: Path) -> str:
    """Parcel id for a property the crosswalk does not know, via its address.

    `property_crosswalk` only holds what Phase 0 parity has matched - a few
    hundred rows - so it answers for almost nothing. But the backbone knows
    every parcel in the county BY ADDRESS and carries the parcel id, and
    Norfolk's sales are keyed on that id. So: address -> backbone row ->
    parcel id -> sales. Two hops, no crosswalk required, and it works for any
    property that has an address at all.

    Matching uses the house-number + street-name key, scoped to the property's
    own city, and REFUSES when more than one distinct parcel id answers -
    an ambiguous match would attach another building's sale history to this
    one, which is worse than showing nothing.
    """
    core = _addr_core(prop.get("address"))
    city = (prop.get("city") or "").strip()
    if not core or not city:
        return ""
    number = core.split("|", 1)[0]
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return ""
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT apn, address, units FROM properties_8r "
            " WHERE COALESCE(r8_market, city) = ? AND apn IS NOT NULL "
            "   AND address LIKE ?", (city, f"{number} %")).fetchall()
    except sqlite3.Error:
        return ""
    finally:
        conn.close()
    want_units = prop.get("units")
    hits = set()
    for r in rows:
        addr = r["address"]
        if _addr_core(addr) != core:
            continue
        if not _dirs_compatible(prop.get("address"), addr):
            continue           # "3000 S Cape Henry" is not "3000 N Cape Henry"
        if not _units_compatible(want_units, r["units"]):
            continue           # a 26-unit building is not a 1-unit house
        a = _norm_apn(r["apn"])
        if a:
            hits.add(a)
    return hits.pop() if len(hits) == 1 else ""


def _apn_via_crosswalk(prop: dict, path: Path) -> str:
    """Parcel id for a LEGACY property row, resolved through the crosswalk.

    The read layer serves the licensed vendor table until the Phase 0 gates
    hold, and that table has NO parcel column - `property_id` is a provider
    UUID and `legacy_id` a provider integer. So `prop["apn"]` is always None
    on the legacy path, the apn branch of the lookup below is structurally
    dead, and EVERY property falls back to matching a marketing address
    ("3000 S. Cape Henry") against an assessor situs address. When those
    differ by an abbreviation or a unit designator, the card reads "No sale
    history available" even though the sales are sitting in the index.

    That is not a Norfolk problem or a data problem - it is every property in
    every market, and it lasts until the cutover. `property_crosswalk` is the
    bridge phase0 parity already builds (legacy id -> backbone id), and the
    backbone row carries the real apn. Use it.

    Returns "" when the property is not matched yet, which is honest: the
    caller then falls back to address matching exactly as before.
    """
    legacy_id = prop.get("property_id")
    if not legacy_id:
        return ""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return ""
    try:
        row = conn.execute(
            "SELECT p.apn FROM property_crosswalk x "
            "  JOIN properties_8r p ON p.property_id = x.r8_property_id "
            " WHERE x.legacy_property_id = ?", (str(legacy_id),)).fetchone()
    except sqlite3.Error:
        return ""          # crosswalk or backbone absent - pre-Phase 0 box
    finally:
        conn.close()
    return _norm_apn(row[0]) if row and row[0] else ""


def _sale_history_for(prop: dict, db_path: Path | None) -> list[dict]:
    import sqlite3

    from core import phase0

    city = (prop.get("city") or "").strip()
    state = (prop.get("state") or "VA").strip() or "VA"
    market = (prop.get("market") or city or "").strip()
    want_apn = _norm_apn(prop.get("apn"))
    want_addr = _norm_addr(prop.get("address"))

    path = _muni_db_path(db_path)
    if path is None or not Path(path).exists():
        return []

    # No apn means the legacy read path (its table has no parcel column).
    # Resolve one through the crosswalk before giving up on the strong key -
    # address matching alone is why so many cards read "no sale history".
    bridged = ""
    if not want_apn:
        want_apn = _apn_via_crosswalk(prop, Path(path))
        if not want_apn:
            want_apn = _apn_via_address(prop, Path(path))
            bridged = want_apn          # matched by address, not by its own id
    if not (want_apn or want_addr):
        return []

    # Indexed path first (owner report 2026-08-09 "too slow"): the autopilot
    # pre-extracts every sale into sale_records, so this is a millisecond
    # lookup instead of a ~355K-row scan on big markets. None = index not
    # built yet on this box -> fall through to the live scan below.
    from core import sale_index
    indexed = sale_index.lookup(Path(path), apn_norm=want_apn,
                                addr_norm=want_addr,
                                addr_core=_addr_core(prop.get("address")),
                                market=(city or market))
    if indexed is not None:
        if bridged:
            # Say HOW this was matched. The property carries no parcel id of
            # its own, so the tie to these sales was inferred from its
            # address - and after showing a house's sale history on a 26-unit
            # building, the owner is owed the parcel number to check against.
            for rec in indexed:
                rec["matched_parcel"] = bridged
        return indexed

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        # Scope to the property's market/city assessor rows so we scan a few
        # thousand records, not a million. `market` on muni_records is the
        # locality name the feed was filed under (e.g. "Norfolk") — note
        # prop["market"] is "Hampton Roads" on the 8r read path, so only the
        # city leg can realistically hit; NOCASE so "NORFOLK" still matches.
        rows = conn.execute(
            "SELECT state, record, source_url FROM muni_records "
            "WHERE kind LIKE 'assessor%' AND "
            "(market = ? COLLATE NOCASE OR market = ? COLLATE NOCASE)",
            (market, city)).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()

    out: list[dict] = []
    seen: set[tuple] = set()
    for r in rows:
        try:
            raw = json.loads(r["record"]) if r["record"] else {}
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(raw, dict):
            continue
        norm = phase0.normalize_record(city or "", r["state"] or state, raw)
        if want_apn and _norm_apn(norm.get("apn")) == want_apn:
            matched = True
        elif want_addr and _norm_addr(norm.get("address")) == want_addr:
            matched = True
        else:
            matched = False
        if not matched:
            continue
        for rec in extract_sale_records(raw):
            key = (rec["date"], rec["price"])
            if key in seen:
                continue
            seen.add(key)
            rec["source_url"] = r["source_url"] or ""
            out.append(rec)

    out.sort(key=lambda x: (x.get("date") or ""), reverse=True)
    return out


def explain_no_sales(prop: dict, *, db_path: Path | None = None) -> str:
    """Why this property shows no sales - in the owner's words, not ours.

    Three different situations produced one identical dead-end caption, and
    they need three different actions: wait for a feed, fix a match, or
    accept that the parcel genuinely never sold. Never raises; the caller
    renders whatever comes back.
    """
    default = "No sale history available."
    city = (prop.get("city") or "").strip()
    try:
        path = _muni_db_path(db_path)
        if path is None or not Path(path).exists():
            return default
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            try:
                n_city = conn.execute(
                    "SELECT COUNT(*) FROM sale_records WHERE market = ?",
                    (city,)).fetchone()[0]
            except sqlite3.Error:
                return default          # index not built on this box
            if not n_city:
                return (f"No sales loaded for {city or 'this city'} yet — "
                        "the nightly data pull has not landed this locality's "
                        "transfer records.")
            has_key = bool(_norm_apn(prop.get("apn"))
                           or _apn_via_crosswalk(prop, Path(path))
                           or _apn_via_address(prop, Path(path)))
            if not has_key:
                return (f"{n_city:,} recorded sales loaded for {city}, but "
                        "this property is not yet tied to a county parcel, "
                        "so none can be matched to it.")
            return (f"No recorded sale for this parcel in {city}'s "
                    f"{n_city:,} transfer records — it may not have sold in "
                    "the period covered.")
        finally:
            conn.close()
    except Exception:
        return default
