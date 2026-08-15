"""Reader/writer for the existing `Properties/<folder>/` per-property files.

Schema is determined by the legacy HTML workbench — `deal.json` keys all
start with `s-` (slider state), values are stored as **percentages** (0-50)
not fractions (0.0-0.50). This module preserves that on-disk shape for full
backward compatibility, and exposes fraction-valued properties for downstream
math.

Two new fields per Brian's 2026-05-06 conventions:
  - `raise_amount` (LP equity raise, dollars; defaults to pp × dp/100 if absent)
  - `vacancy_source` ('record' if seeded from record occupancy, 'user' if overridden)

Older property folders predate these fields — Pydantic defaults kick in on load.

Parsing of T-12 / Rent Roll xlsx is **out of scope** — `sources.json` is
trusted as-is. The legacy server.py + wb_parsers.py owns that workflow.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import tempfile
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Properties/ lives at workbench root, sibling of python_workbench/
# Layout: <root>/python_workbench/data/property_io.py → root.parent.parent.parent
#
# `PROPERTIES_ROOT` stays as a local-absolute Path for backward compat with
# callers that compose paths off it (e.g. ``PROPERTIES_ROOT / "_broker_crm.json"``).
# All ACTUAL IO routes through ``core.storage.get_storage()`` via the
# ``_rel`` helper below — that's what makes the cloud-mode Graph backend
# work transparently. In local mode the storage layer just translates back
# to the same absolute path PROPERTIES_ROOT points at, so behavior is
# identical to the pre-storage-abstraction code.
# `ER_PROPERTIES_ROOT` overrides the location outright, so the deal folders
# can live anywhere on disk (second drive, NAS mount) independent of where
# the app folder sits. Unset -> the classic sibling layout.
_props_override = os.environ.get("ER_PROPERTIES_ROOT", "").strip()


# ONE fixed location, always (owner directive, 2026-08-15: "Don't choose
# anything - pick a folder - in the directory I told you - and write to it.
# Every Time. Don't guess.").
#
# Deal folders live in <app>/Properties - C:\WORKBENCH_V5\Properties. No
# discovery, no scoring, no fallbacks. Every previous variant of this line
# tried to be clever about WHERE the folders were and every one of them
# failed differently: a rule relative to the app broke when the app moved, a
# first-match search picked a decoy folder over the real one. A constant
# cannot do either. It is created on import so a write never fails for want
# of a directory.
#
# ER_PROPERTIES_ROOT still overrides it - that is explicit configuration, not
# a guess - but nothing is inferred when it is unset.
PROPERTIES_ROOT = (
    Path(_props_override).expanduser()
    if _props_override
    else Path(__file__).resolve().parent.parent / "Properties"
)
try:
    PROPERTIES_ROOT.mkdir(parents=True, exist_ok=True)
except OSError:
    pass

# Workbench root (parent of Properties/) — used to compute storage-relative paths.
_WB_ROOT = PROPERTIES_ROOT.parent


def _local_storage_root() -> Path | None:
    """The directory the local backend resolves relative keys against.

    None in graph mode (no local root) or if storage is unavailable.
    """
    try:
        from core.storage import get_storage

        root = getattr(get_storage(), "root", None)
        return Path(root) if root is not None else None
    except Exception:
        return None


def _rel(path: Path | str) -> str:
    """Translate an absolute filesystem path into a storage key.

    The key is made relative to whatever the storage backend will resolve it
    against — NOT to this module's own idea of the workbench root. Those two
    were allowed to disagree until 2026-08-15, and when they did (the local
    backend rooted one level above the app), every ``Properties/...`` key
    silently resolved into a directory that did not exist. Nothing raised;
    folder discovery just returned nothing, forever.

    So: relative to the local backend's root when there is one, else
    relative to ``_WB_ROOT`` (graph mode, where keys are drive-relative),
    else absolute. Absolute is always safe in local mode - ``_resolve()``
    trusts absolute keys - and is what makes deal folders outside the app
    (``ER_PROPERTIES_ROOT`` on another drive, a test ``tmp_path``) work
    instead of being silently rewritten to a path under the app root.
    """
    p = Path(path).resolve()

    root = _local_storage_root()
    if root is not None:
        try:
            return str(p.relative_to(root)).replace("\\", "/")
        except ValueError:
            # Outside the backend's root - hand it the absolute path rather
            # than a relative key it would resolve somewhere else entirely.
            return str(p).replace("\\", "/")

    try:
        return str(p.relative_to(_WB_ROOT)).replace("\\", "/")
    except ValueError:
        return str(p).replace("\\", "/")


# ---------------------------------------------------------------------------
# DealState — slider state on disk (deal.json), one per property folder
# ---------------------------------------------------------------------------

class DealState(BaseModel):
    """The deal dial state persisted at `<folder>/deal.json`.

    Field aliases match the legacy `s-*` keys so existing files load as-is.
    Values are stored as **percentages**, not fractions — `dp=30` means 30%.
    Use the helper properties (`down_payment_frac`, `interest_rate`, etc.)
    when feeding math into `core.calc` / `core.waterfall`.
    """

    model_config = ConfigDict(populate_by_name=True)

    # --- legacy slider fields ---
    pp: float = Field(..., alias="s-pp", description="Purchase price (dollars)")
    noi: float = Field(..., alias="s-noi", description="NOI (annual dollars)")
    dp: float = Field(..., alias="s-dp", ge=10, le=50,
                      description="Down payment (percent 10-50)")
    ir: float = Field(..., alias="s-ir", ge=3, le=12,
                      description="Interest rate (percent 3-12)")
    vac: float = Field(..., alias="s-vac", ge=0, le=25,
                       description="Vacancy (percent 0-25)")
    rg: float = Field(..., alias="s-rg", ge=0, le=8,
                      description="Rent growth (percent 0-8)")
    eg: float = Field(..., alias="s-eg", ge=0, le=6,
                      description="Expense growth (percent 0-6)")
    xc: float = Field(..., alias="s-xc", ge=4, le=12,
                      description="Exit cap rate (percent 4-12)")
    hp: int = Field(..., alias="s-hp", ge=3, le=10,
                    description="Hold period (years)")
    am: int = Field(25, alias="s-am",
                    description="Amortization (years; locked at 25 by Eight Rock convention)")
    io: int = Field(0, alias="s-io", ge=0, le=10,
                    description="IO years (0-10)")
    amf: float = Field(4.0, alias="s-amf", ge=0, le=5,
                       description="AM fee (percent of GPR, 0-5; default 4)")

    # --- new fields (added 2026-05-06; older deal.json files won't have them) ---
    raise_amount: float | None = Field(
        None,
        description=(
            "LP equity raise (dollars). Defaults to pp × dp/100 if absent. "
            "Brian can raise extra for closing/capex/reserves."
        ),
    )
    # Whether raise_amount is a DELIBERATE override (owner decision
    # 2026-08-13: "track the dials until I override it"). Before this flag,
    # `raise_amount is not None` was the only signal - and the dial widget
    # silently wrote a value on the first dial move, pinning the raise
    # forever. That is what made slider A->B->A return a DIFFERENT IRR:
    # the dials came back, the denominator did not.
    raise_is_custom: bool = Field(
        False, alias="s-raise-custom",
        description=("True only when the analyst typed an LP raise. False = "
                     "the raise tracks the dials (down payment + closing "
                     "costs)."))
    # --- one-time uses at close (2026-08-13, owner items 6 + 7) ---------
    # Both are EXCLUDED from NOI, cap rate and loan sizing - they are
    # capital uses, not operations.
    gp_fee: float = Field(
        0.0, alias="s-gpfee", ge=0,
        description=("One-time GP / sponsor acquisition fee (dollars) paid "
                     "at close. Owner decision 2026-08-13: charged to the "
                     "PROJECT but excluded from LP invested capital - it "
                     "depresses project IRR and return-on-cost, not LP IRR, "
                     "equity multiple or CoC."))
    closing_costs: float = Field(
        0.0, alias="s-closing", ge=0,
        description=("One-time closing costs (dollars). Owner decision "
                     "2026-08-13: funded by the equity raise, so they raise "
                     "LP invested capital and depress LP IRR / EM / CoC."))
    vacancy_source: str = Field(
        "record",
        description=(
            "'record' if vacancy was seeded from the property record's occupancy, "
            "'user' if Brian overrode it. Files written before the Phase-0 "
            "de-identification carry a vendor label here; anything that is not "
            "'user' is treated as record-seeded."
        ),
    )
    # --- post-sale expense adjustments (added 2026-05-07; per Beardsley) ---
    tax_reassessment_on: bool = Field(
        True,
        description=(
            "Auto-bump property tax line by ~20% post-sale (default ON). "
            "Captures Virginia's reassessment-on-sale practice. Most "
            "underwriters miss this until closing — Beardsley flags as "
            "common cause of yr-1 NOI miss."
        ),
    )
    insurance_escalator_on: bool = Field(
        False,
        description=(
            "Add $50/unit/yr insurance premium for agency debt (Fannie/"
            "Freddie). Default OFF — only applies if you plan to close with "
            "Fannie/Freddie rather than a local bank or life co."
        ),
    )
    # --- post-sale ramp / reposition disruption (added 2026-05-08; B3 + B4) ---
    vac_spike_pp: float = Field(
        10.0,
        ge=0, le=25,
        description=(
            "Going-in vacancy spike (percentage points) added to the dialed "
            "vacancy rate during the first ~6 months post-close. Captures "
            "NTVs/evictions/skips during reposition (Beardsley B3). Default 10pp."
        ),
    )
    stabilization_months: int = Field(
        18,
        ge=0, le=27,
        description=(
            "Months for revenue to ramp from going-in (vac+spike) back to "
            "stabilized vacancy. Default 18 (Beardsley B4 mid-range; "
            "smaller value-add: 12 mo · heavy reposition: 24-27 mo)."
        ),
    )
    # --- B1 21-lever value-add menu selections (added 2026-05-08) ---
    # List of lever IDs (matching `ui.value_add.VALUE_ADD_LEVERS[*]['id']`)
    # the analyst has toggled ON for this deal. Persists per-property so
    # toggling levers on Property A doesn't carry over to Property B and
    # tweaking the price slider doesn't reset selections.
    selected_levers: list[str] = Field(
        default_factory=list,
        description=(
            "IDs of value-add levers the analyst has toggled ON for this "
            "property. Cumulative NOI lift in the Underwriting tab is the "
            "sum of these. Persists in deal.json."
        ),
    )
    # --- optimistic concurrency (added 2026-07-31; spec FR-9.3.1) ---
    # deal.json is shared state the moment two people open the same property.
    # These mirror the `row_version` / audit columns the Postgres tables carry
    # (§9.3) so the file store enforces the same compare-and-set rule.
    # Files written before this default to 0, which is the "unversioned" case
    # save_deal() accepts on a first write.
    row_version: int = Field(
        0,
        ge=0,
        description=(
            "Monotonic save counter (FR-9.3.1). save_deal() refuses to write "
            "when the on-disk value has moved past the one the editor loaded."
        ),
    )
    updated_by: str | None = Field(
        None,
        description="Display name of whoever last saved (FR-9.3.2 conflict dialog).",
    )
    updated_at: str | None = Field(
        None,
        description="ISO-8601 UTC timestamp of the last save (FR-9.3.2).",
    )

    @model_validator(mode="before")
    @classmethod
    def _migrate_stale_amf(cls, data: Any) -> Any:
        """Migration shim: legacy `s-amf` was a $0-50,000 dollar value before
        the slider was converted to 0-5%. Reset stale values to the locked
        4% default per `feedback_underwriting_conventions.md`.

        Runs in `mode="before"` so the rewrite happens BEFORE field validation
        (which would otherwise reject 15000 against the `le=5` constraint).
        """
        if isinstance(data, dict):
            for key in ("s-amf", "amf"):
                if key in data:
                    try:
                        if float(data[key]) > 5:
                            data[key] = 4.0
                    except (TypeError, ValueError):
                        pass
        return data

    # --- fraction helpers for math ---
    @property
    def down_payment_frac(self) -> float:
        """Down payment as a fraction (e.g., 0.30 for 30%)."""
        return self.dp / 100.0

    @property
    def interest_rate(self) -> float:
        """Interest rate as a fraction (e.g., 0.065 for 6.5%)."""
        return self.ir / 100.0

    @property
    def vacancy_frac(self) -> float:
        return self.vac / 100.0

    @property
    def rent_growth(self) -> float:
        return self.rg / 100.0

    @property
    def expense_growth(self) -> float:
        return self.eg / 100.0

    @property
    def exit_cap(self) -> float:
        return self.xc / 100.0

    @property
    def am_fee_pct(self) -> float:
        return self.amf / 100.0

    @property
    def loan_amount(self) -> float:
        return self.pp * (1.0 - self.down_payment_frac)

    @property
    def down_payment_dollars(self) -> float:
        """The down payment itself — price × dp%, no fees."""
        return self.pp * self.down_payment_frac

    @property
    def tracked_raise(self) -> float:
        """What the LP raise is when it TRACKS the dials: the down payment
        plus the closing costs the equity has to fund (owner decision
        2026-08-13). A pure function of the dials - that is the property
        that makes A->B->A reproduce."""
        return self.down_payment_dollars + self.closing_costs

    @property
    def equity_raise(self) -> float:
        """LP invested capital — the denominator for LP IRR, equity
        multiple and cash-on-cash.

        Per Brian's 2026-05-06 convention this is NOT the down payment
        alone. As of 2026-08-13 it tracks the dials (down payment +
        closing costs) UNLESS the analyst explicitly overrode it, which
        `raise_is_custom` records. The GP fee is deliberately absent: the
        owner's 2026-08-13 call is that the sponsor's acquisition fee sits
        outside LP invested capital.
        """
        if self.raise_is_custom and self.raise_amount and self.raise_amount > 0:
            return self.raise_amount
        return self.tracked_raise

    @property
    def total_uses(self) -> float:
        """All-in capitalisation at close: price + closing costs + GP fee.
        The basis for return-on-cost. NOT the loan basis - debt is still
        sized off the purchase price alone."""
        return self.pp + self.closing_costs + self.gp_fee

    @property
    def project_equity(self) -> float:
        """Total equity deployed at close, including the GP fee. This is
        the year-0 outflow for PROJECT IRR; LP-level metrics use
        `equity_raise` instead."""
        return self.equity_raise + self.gp_fee

    @model_validator(mode="after")
    def _migrate_legacy_raise(self):
        """Classify a pre-2026-08-13 `raise_amount` as tracking or custom.

        Old files carry no `raise_is_custom`, and most non-null values were
        written by the pinning bug rather than typed by a human. Marking
        them all custom would launder that corruption into permanence. A
        value that matches what the dials imply (within $1k or 1%) is
        therefore treated as a pinned copy and released back to tracking;
        anything materially different was a real human decision and is
        kept as an override.
        """
        if self.raise_is_custom or not self.raise_amount:
            return self
        implied = self.pp * self.down_payment_frac + self.closing_costs
        tol = max(1_000.0, implied * 0.01)
        if abs(self.raise_amount - implied) > tol:
            object.__setattr__(self, "raise_is_custom", True)
        return self


# ---------------------------------------------------------------------------
# PropertyFolder — pointer to one property's on-disk folder
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PropertyFolder:
    path: Path
    folder_name: str
    has_deal: bool
    has_sources: bool
    has_sales: bool
    has_notes: bool

    @classmethod
    def from_path(cls, path: Path) -> "PropertyFolder":
        """Build a PropertyFolder from a folder path.

        Routes through ``core.storage`` for the existence checks so the same
        constructor works against local-disk and Graph-backed OneDrive folders.
        """
        from core.storage import get_storage
        storage = get_storage()
        key = _rel(path)
        return cls(
            path=path,
            folder_name=path.name,
            has_deal=storage.is_file(f"{key}/deal.json"),
            has_sources=storage.is_file(f"{key}/sources.json"),
            has_sales=storage.is_file(f"{key}/sales.json"),
            has_notes=storage.is_file(f"{key}/notes.txt"),
        )


# ---------------------------------------------------------------------------
# Discovery + load + save
# ---------------------------------------------------------------------------

def discover_property_folders(
    properties_root: Path = PROPERTIES_ROOT,
) -> list[PropertyFolder]:
    """Walk `Properties/` and return one entry per subfolder.

    Skips top-level config files (`_custom_props.json`, `_favorites.json`)
    and any files at root. Hidden directories (leading `.`) are also skipped.

    Routes through ``core.storage`` so the discovery works against either
    a local OneDrive folder or a Graph-API-backed OneDrive in cloud mode.
    """
    from core.storage import get_storage
    storage = get_storage()
    root_key = _rel(properties_root)
    if not storage.is_dir(root_key):
        return []

    names = sorted(storage.list_dir(root_key))
    folders: list[PropertyFolder] = []
    for name in names:
        if name.startswith("."):
            continue
        subkey = f"{root_key}/{name}"
        if not storage.is_dir(subkey):
            continue
        # PropertyFolder.path stays as an absolute local Path for compat with
        # callers that pass it to other functions; IO routes back through
        # storage (which translates back to absolute or graph as needed).
        folder_path = properties_root / name
        folders.append(PropertyFolder(
            path=folder_path,
            folder_name=name,
            has_deal=storage.is_file(f"{subkey}/deal.json"),
            has_sources=storage.is_file(f"{subkey}/sources.json"),
            has_sales=storage.is_file(f"{subkey}/sales.json"),
            has_notes=storage.is_file(f"{subkey}/notes.txt"),
        ))
    return folders


def load_deal(folder: Path) -> DealState | None:
    """Load and validate `deal.json`. Returns None if the file is missing.

    Pydantic raises `ValidationError` if the file exists but is malformed —
    callers may want to catch and surface that to the UI. We do **not**
    swallow validation errors here.
    """
    from core.storage import get_storage
    storage = get_storage()
    key = f"{_rel(folder)}/deal.json"
    if not storage.is_file(key):
        return None
    raw = json.loads(storage.read_text(key))
    return DealState.model_validate(raw)


@dataclass(frozen=True)
class SaveResult:
    """Outcome of a `save_deal` call (FR-9.3.1 / FR-9.3.2).

    `ok` False means nothing was written and the caller is holding stale
    state. `their_deal` is what is actually on disk, so the UI can offer a
    side-by-side instead of a bare "try again".
    """
    ok: bool
    version: int
    conflict_by: str | None = None
    conflict_at: str | None = None
    their_deal: "DealState | None" = None


def _disk_version(storage, key: str) -> tuple[int, str | None, str | None]:
    """Read just the concurrency stamp off the stored file.

    A malformed or half-written file must not block a save — it reports
    version 0, which only matters against an explicit expected_version.
    """
    if not storage.is_file(key):
        return 0, None, None
    try:
        raw = json.loads(storage.read_text(key))
    except (json.JSONDecodeError, OSError):
        return 0, None, None
    if not isinstance(raw, dict):
        return 0, None, None
    v = raw.get("row_version") or 0
    return (int(v) if isinstance(v, (int, float)) else 0,
            raw.get("updated_by"), raw.get("updated_at"))


def save_deal(
    folder: Path,
    deal: DealState,
    expected_version: int | None = None,
    actor: str | None = None,
) -> SaveResult:
    """Write `deal.json` with stable key ordering and `s-*` aliases.

    Legacy keys (`s-pp`, `s-noi`, ...) are emitted via Pydantic aliases so the
    file stays compatible with the legacy HTML workbench.

    Concurrency (FR-9.3.1): pass `expected_version` — the `row_version` the
    editor loaded — to get compare-and-set. If the on-disk version has moved,
    nothing is written and the returned `SaveResult` carries who changed it
    and their copy, for the FR-9.3.2 dialog. Omitting `expected_version`
    keeps the historical last-writer-wins behaviour, which is what the
    single-user desktop path and the test-suite fixtures rely on.

    Honest bound: the version check and the write are two operations against
    a blob store, not one transaction, so a collision inside that millisecond
    window can still slip through. The Postgres soft lock (FR-9.3.3) is what
    keeps two editors off the same record in the first place; this is the
    backstop that makes a lost update visible instead of silent. Records that
    need true atomicity live in Postgres and go through
    `data.concurrency.optimistic_update`.
    """
    from core.storage import get_storage
    storage = get_storage()
    key = f"{_rel(folder)}/deal.json"

    disk_v, disk_by, disk_at = _disk_version(storage, key)
    if expected_version is not None and disk_v != expected_version:
        their = None
        try:
            their = load_deal(folder)
        except Exception:            # a corrupt file must not mask the conflict
            their = None
        return SaveResult(ok=False, version=disk_v, conflict_by=disk_by,
                          conflict_at=disk_at, their_deal=their)

    deal = deal.model_copy(update={
        "row_version": disk_v + 1,
        "updated_by": actor or deal.updated_by,
        "updated_at": _dt.datetime.now(_dt.timezone.utc)
                         .replace(microsecond=0).isoformat(),
    })
    out = deal.model_dump(by_alias=True)

    # Stable ordering: legacy slider keys first (in canonical order),
    # then the new fields, so file diffs stay clean.
    canonical_order = (
        "s-pp", "s-noi", "s-dp", "s-ir", "s-vac", "s-rg", "s-eg",
        "s-xc", "s-hp", "s-am", "s-io", "s-amf",
        "raise_amount", "vacancy_source",
    )
    ordered = {k: out[k] for k in canonical_order if k in out}
    for k in out:
        if k not in ordered:
            ordered[k] = out[k]

    payload = json.dumps(ordered, indent=2) + "\n"
    storage.write_text(key, payload)
    return SaveResult(ok=True, version=deal.row_version)


def load_sources(folder: Path) -> dict[str, Any] | None:
    """Load `sources.json` raw — schema is the legacy workbench's shape.

    Validation is intentionally loose: T-12 keys vary across older property
    folders, and we don't want strict validation to break loading. Streamlit
    code reads via `.get()` to tolerate missing keys.

    Returns None if the file is missing.
    """
    from core.storage import get_storage
    storage = get_storage()
    key = f"{_rel(folder)}/sources.json"
    if not storage.is_file(key):
        return None
    return json.loads(storage.read_text(key))


def merge_sources(folder: Path, new_blocks: dict[str, Any]) -> None:
    """Merge top-level keys from `new_blocks` into `sources.json`.

    Used by the upload parser (`data.parsers`) to write parsed `rentRoll` /
    `t12_*` blocks without disturbing other keys (e.g. `assessmentHistory`).
    Each key in `new_blocks` replaces any existing key of the same name.
    No-op when `new_blocks` is empty.
    """
    if not new_blocks:
        return
    from core.storage import get_storage
    storage = get_storage()
    key = f"{_rel(folder)}/sources.json"
    if storage.is_file(key):
        try:
            existing = json.loads(storage.read_text(key))
            if not isinstance(existing, dict):
                existing = {}
        except json.JSONDecodeError:
            existing = {}
    else:
        existing = {}

    existing.update(new_blocks)
    storage.write_text(key, json.dumps(existing, indent=2, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Tax Assessment History (Phase 2 — structured storage)
# ---------------------------------------------------------------------------
#
# Schema (`sources.json` → `assessmentHistory` key):
#   {
#     "source": "City Assessor",   # data origin
#     "city": "Norfolk",            # which assessor's office
#     "parcel_id": "41127600",      # city's parcel number
#     "gpin": "1540015241",         # VA's Geographic Parcel ID Number
#     "pull_date": "2026-05-08",    # when this data was last refreshed
#     "records": [
#       {"fiscal_year": 2019, "assessed_value": 6463000, "land_value": null,
#        "building_value": null, "note": ""},
#       ...
#     ]
#   }
#
# Records are sorted ascending by fiscal_year. Phase 3's ETL puller will
# auto-populate this block from each city's open-data portal. Until Phase 3
# lands, the migration script seeds this from the embedded `sales.json` notes.

def load_assessment_history(folder: Path) -> dict[str, Any] | None:
    """Return the structured `assessmentHistory` block from `sources.json`,
    or None if not yet populated for this property."""
    sources = load_sources(folder)
    if not sources or not isinstance(sources, dict):
        return None
    block = sources.get("assessmentHistory")
    if not isinstance(block, dict) or not block.get("records"):
        return None
    return block


def save_assessment_history(
    folder: Path,
    history: dict[str, Any],
) -> None:
    """Upsert the `assessmentHistory` block into `sources.json` (creating
    the file if missing). Preserves all other keys in `sources.json`.

    `history` shape:
      {
        "source": str,
        "city": str | None,
        "parcel_id": str | None,
        "gpin": str | None,
        "pull_date": str,
        "records": list[{"fiscal_year": int, "assessed_value": int, ...}],
      }
    """
    from core.storage import get_storage
    storage = get_storage()
    key = f"{_rel(folder)}/sources.json"
    if storage.is_file(key):
        try:
            existing = json.loads(storage.read_text(key))
            if not isinstance(existing, dict):
                existing = {}
        except json.JSONDecodeError:
            existing = {}
    else:
        existing = {}

    # Sort records ascending by fiscal_year for canonical storage
    if isinstance(history.get("records"), list):
        history = {
            **history,
            "records": sorted(history["records"], key=lambda r: r.get("fiscal_year", 0)),
        }

    existing["assessmentHistory"] = history
    storage.write_text(key, json.dumps(existing, indent=2, ensure_ascii=False))


def load_sales(folder: Path) -> dict[str, Any] | list[dict[str, Any]] | None:
    """Load `sales.json` raw.

    Shape varies:
      - Legacy/manual: a list of `{date, price, grantor, grantee, notes}` dicts.
      - Auto-pulled (e.g., from VA Beach ArcGIS): a metadata dict with keys like
        `property`, `pulled_on`, `parcel_lookup`, `last_3_apartment_sales`, etc.

    Caller decides which shape it has. Returns None if file is missing.
    """
    from core.storage import get_storage
    storage = get_storage()
    key = f"{_rel(folder)}/sales.json"
    if not storage.is_file(key):
        return None
    return json.loads(storage.read_text(key))


def load_notes(folder: Path) -> str:
    """Read `notes.txt` (free-form analyst notes). Returns '' if missing."""
    from core.storage import get_storage
    storage = get_storage()
    key = f"{_rel(folder)}/notes.txt"
    if not storage.is_file(key):
        return ""
    return storage.read_text(key)


def save_notes(folder: Path, text: str) -> None:
    """Write `notes.txt`."""
    from core.storage import get_storage
    storage = get_storage()
    storage.write_text(f"{_rel(folder)}/notes.txt", text)


# ---------------------------------------------------------------------------
# Property photo (one canonical image per folder, named `photo.<ext>`)
# ---------------------------------------------------------------------------

# Browser-supported image formats. Streamlit's `st.image` reads these natively
# and most browsers render them inline without conversion.
_PHOTO_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def find_property_photo(folder: Path) -> Path | None:
    """Return the path to the property's hero photo, or None if missing.

    Convention: a single canonical photo named `photo.<ext>` in the folder.
    Returns an absolute Path (in local mode this is the real file on disk;
    in graph mode it's a synthesized local path — callers that need to feed
    Streamlit an image should read bytes via ``read_property_photo_bytes()``
    instead of relying on the Path being directly readable).
    """
    from core.storage import get_storage
    storage = get_storage()
    folder_key = _rel(folder)
    for ext in _PHOTO_EXTENSIONS:
        key = f"{folder_key}/photo{ext}"
        if storage.is_file(key):
            # Local mode: return real Path. Graph mode: same shape but the
            # Path isn't directly readable — UI should call
            # read_property_photo_bytes() instead.
            return folder / f"photo{ext}"
    return None


def read_property_photo_bytes(folder: Path) -> tuple[bytes, str] | None:
    """Return (bytes, extension) for the property's photo, or None if absent.

    UI code should prefer this over `find_property_photo` when displaying
    the image, so it works the same in local-disk and Graph-API modes.
    """
    from core.storage import get_storage
    storage = get_storage()
    folder_key = _rel(folder)
    for ext in _PHOTO_EXTENSIONS:
        key = f"{folder_key}/photo{ext}"
        if storage.is_file(key):
            return storage.read_bytes(key), ext
    return None


def save_property_photo(
    folder: Path,
    image_bytes: bytes,
    original_filename: str,
) -> Path:
    """Save uploaded image bytes as `photo.<ext>`. Returns the saved path.

    Replaces any existing photo (different extensions included) so there's
    only ever one canonical photo per property.

    Raises ValueError if the original filename's extension isn't a supported
    image format — the upload widget should pre-filter, but defend in depth.
    """
    from core.storage import get_storage
    storage = get_storage()
    folder_key = _rel(folder)

    suffix = Path(original_filename).suffix.lower()
    if suffix not in _PHOTO_EXTENSIONS:
        raise ValueError(
            f"Unsupported image extension {suffix!r}. "
            f"Allowed: {', '.join(_PHOTO_EXTENSIONS)}"
        )

    # Remove stale photo variants of different extensions
    for ext in _PHOTO_EXTENSIONS:
        if ext == suffix:
            continue
        stale_key = f"{folder_key}/photo{ext}"
        if storage.is_file(stale_key):
            try:
                storage.delete(stale_key)
            except Exception:
                pass  # non-fatal; the new save will still succeed

    storage.write_bytes(f"{folder_key}/photo{suffix}", image_bytes)
    return folder / f"photo{suffix}"


# ---------------------------------------------------------------------------
# Root-level config helpers (custom props, favorites)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Custom properties — user-added properties not in the property records
# ---------------------------------------------------------------------------

# Legacy F-dictionary positions used by the HTML workbench's _custom_props.json.
# Each custom property was a flat array indexed by these positions.
# (id, nm, ad, ct, zp, co, un, yr, oc, sf, rt, rf, cl, mg, tp, mk, la, ln,
#  ow, oa, op, af, ps, ph, ws, em, cm, lt, rm, sm, tg, ix, st)
_LEGACY_POSITIONS = {
    0: "legacy_id",
    1: "name",
    2: "address",
    3: "city",
    4: "zip",
    5: "county",
    6: "units",
    7: "year_built",
    8: "occupancy_pct_legacy",  # 0-100 in legacy file; convert to 0.0-1.0
    9: "avg_sqft",
    10: "avg_rent",
    11: "rent_per_sqft",
    12: "asset_class",
    13: "management_company",
    14: "property_type",
    15: "market",
    16: "latitude",
    17: "longitude",
    18: "owner",
    19: "owner_address",
    20: "owner_phone",
    21: "asset_or_fee",
    22: "pm_software",
    23: "property_phone",
    24: "website",
    25: "email",
    26: "manager",
    27: "lease_terms",
    28: "last_remodel",
    29: "submarket",
    30: "tags",
    32: "status",
}


def _legacy_array_to_dict(arr: list) -> dict[str, Any]:
    """Convert a legacy F-dictionary positional array → property dict."""
    out: dict[str, Any] = {}
    for pos, key in _LEGACY_POSITIONS.items():
        if pos < len(arr):
            out[key] = arr[pos]
    # Convert legacy occupancy 0-100 → fraction 0.0-1.0 to match the schema
    if "occupancy_pct_legacy" in out:
        v = out.pop("occupancy_pct_legacy")
        if v not in (None, ""):
            try:
                f = float(v)
                out["occupancy_pct"] = f / 100.0 if f > 1.0 else f
            except (TypeError, ValueError):
                out["occupancy_pct"] = None
    # Custom props use -1 as the legacy_id sentinel — clear it
    if str(out.get("legacy_id", "")) == "-1":
        out["legacy_id"] = None
    # Synthesize a property_id if the entry is custom (no legacy_id)
    if not out.get("legacy_id"):
        out["property_id"] = f"custom-{uuid.uuid5(uuid.NAMESPACE_OID, json.dumps(arr, default=str))}"
    return out


def load_custom_props(
    properties_root: Path = PROPERTIES_ROOT,
) -> list[dict[str, Any]]:
    """Load `Properties/_custom_props.json` — user-added properties not in the property records.

    Returns a list of dicts. Handles both the legacy list-of-arrays format
    (HTML workbench) and the new list-of-dicts format on a per-entry basis,
    so any mix of old + new entries loads cleanly.
    """
    from core.storage import get_storage
    storage = get_storage()
    key = f"{_rel(properties_root)}/_custom_props.json"
    if not storage.is_file(key):
        return []
    raw = json.loads(storage.read_text(key))
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in raw:
        if isinstance(entry, list):
            out.append(_legacy_array_to_dict(entry))
        elif isinstance(entry, dict):
            out.append(entry)
    return out


def add_custom_property(
    prop: dict[str, Any],
    properties_root: Path = PROPERTIES_ROOT,
) -> str:
    """Append a new custom property to `_custom_props.json`. Returns property_id.

    Generates a unique `property_id` if not provided. Caller is responsible
    for ALSO upserting into SQLite so the sidebar list picks up the new
    entry immediately (see `data.db`).
    """
    from core.storage import get_storage
    storage = get_storage()

    if not prop.get("property_id"):
        prop["property_id"] = f"custom-{uuid.uuid4()}"
    if "pull_date" not in prop:
        import datetime as dt
        prop["pull_date"] = dt.date.today().isoformat()

    existing = load_custom_props(properties_root)
    existing.append(prop)
    storage.write_text(
        f"{_rel(properties_root)}/_custom_props.json",
        json.dumps(existing, indent=2),
    )
    return prop["property_id"]


def load_favorites(
    properties_root: Path = PROPERTIES_ROOT,
) -> set[str]:
    """Load `Properties/_favorites.json` as a set of string IDs.

    Legacy HTML workbench stored legacy numeric IDs as ints (e.g. 134263).
    Modern entries use property_id (UUIDs). We normalize all to strings on
    load so the matching logic works for both.
    """
    from core.storage import get_storage
    storage = get_storage()
    key = f"{_rel(properties_root)}/_favorites.json"
    if not storage.is_file(key):
        return set()
    try:
        raw = json.loads(storage.read_text(key))
    except json.JSONDecodeError:
        return set()
    if not isinstance(raw, list):
        return set()
    return {str(x) for x in raw}


def save_favorites(
    favs: set[str] | list[str],
    properties_root: Path = PROPERTIES_ROOT,
) -> None:
    """Write `_favorites.json` — sorted for stable diffs."""
    from core.storage import get_storage
    storage = get_storage()
    out = sorted({str(x) for x in favs})
    storage.write_text(
        f"{_rel(properties_root)}/_favorites.json",
        json.dumps(out, indent=2),
    )


# ---------------------------------------------------------------------------
# Saved searches (named filter presets — used by Inventory tab, etc.)
# ---------------------------------------------------------------------------
#
# Stored at `Properties/_saved_searches.json` as a two-level dict:
#   { "<section_id>": { "<search_name>": { <widget_key>: <value>, ... } } }
#
# `section_id` partitions the file by where the search lives in the UI
# (e.g. "inventory_browse"), so different tabs can have independent saved
# searches without collision. The inner dict is just the widget-key →
# value map that should be restored when the user picks the named search.

def load_saved_searches(
    section_id: str,
    properties_root: Path = PROPERTIES_ROOT,
) -> dict[str, dict[str, Any]]:
    """Return all saved searches for `section_id` as { name → state }.

    Empty dict on first run / missing file / parse error — callers treat
    "no saved searches" as a normal state.
    """
    from core.storage import get_storage
    storage = get_storage()
    key = f"{_rel(properties_root)}/_saved_searches.json"
    if not storage.is_file(key):
        return {}
    try:
        all_data = json.loads(storage.read_text(key))
    except json.JSONDecodeError:
        return {}
    if not isinstance(all_data, dict):
        return {}
    section = all_data.get(section_id) or {}
    return section if isinstance(section, dict) else {}


def save_search(
    section_id: str,
    name: str,
    state: dict[str, Any],
    properties_root: Path = PROPERTIES_ROOT,
) -> None:
    """Persist a named filter state under `section_id`. Overwrites if the
    name already exists."""
    from core.storage import get_storage
    storage = get_storage()
    key = f"{_rel(properties_root)}/_saved_searches.json"
    if storage.is_file(key):
        try:
            all_data = json.loads(storage.read_text(key))
        except json.JSONDecodeError:
            all_data = {}
        if not isinstance(all_data, dict):
            all_data = {}
    else:
        all_data = {}
    all_data.setdefault(section_id, {})[name] = state
    storage.write_text(key, json.dumps(all_data, indent=2, sort_keys=True))


def delete_saved_search(
    section_id: str,
    name: str,
    properties_root: Path = PROPERTIES_ROOT,
) -> bool:
    """Remove a saved search by name. Returns True if a delete happened."""
    from core.storage import get_storage
    storage = get_storage()
    key = f"{_rel(properties_root)}/_saved_searches.json"
    if not storage.is_file(key):
        return False
    try:
        all_data = json.loads(storage.read_text(key))
    except json.JSONDecodeError:
        return False
    section = all_data.get(section_id) or {}
    if name not in section:
        return False
    del section[name]
    if not section:
        all_data.pop(section_id, None)
    storage.write_text(key, json.dumps(all_data, indent=2, sort_keys=True))
    return True


# Synthesized property_ids look like "<source-slug>-<numeric id>". The slug
# changed in the Phase-0 de-identification, so `_favorites.json` can still
# hold entries written under the old one. Comparing on the numeric tail keeps
# those favorites matching. UUID, "8R-..." and "custom-<uuid>" ids never match
# this shape, so they compare byte-for-byte exactly as before.
_SYNTHETIC_ID_RE = re.compile(r"^[a-z]+-(\d+)$")


def _fav_key(value: str) -> str:
    """Normalize one favorite/property id for comparison."""
    m = _SYNTHETIC_ID_RE.match(value)
    return m.group(1) if m else value


def _fav_keys(prop: dict[str, Any]) -> set[str]:
    """Every normalized id under which `prop` might be stored as a favorite."""
    return {
        _fav_key(str(prop.get(k) or ""))
        for k in ("property_id", "legacy_id")
        if prop.get(k)
    }


def is_favorite(
    prop: dict[str, Any],
    favs: set[str] | None = None,
    properties_root: Path = PROPERTIES_ROOT,
) -> bool:
    """Check whether a property is favorited.

    Pass `favs` to avoid hitting disk repeatedly when filtering a list.
    Matches on either `property_id` (modern) or `legacy_id` (legacy numeric),
    normalizing synthesized ids so entries written by older builds still hit.
    """
    if favs is None:
        favs = load_favorites(properties_root)
    keys = _fav_keys(prop)
    if not keys:
        return False
    return bool(keys & {_fav_key(f) for f in favs})


def toggle_favorite(
    prop: dict[str, Any],
    properties_root: Path = PROPERTIES_ROOT,
) -> bool:
    """Toggle favorite state for `prop`. Returns the new state (True=favorited).

    Adds using `property_id` (preferred). If the property was favorited under
    a legacy numeric `legacy_id` — or under an older synthesized id prefix —
    removes that entry too, so toggling off clears every match.
    """
    favs = load_favorites(properties_root)
    pid = str(prop.get("property_id") or "")
    lid = str(prop.get("legacy_id") or "")
    keys = _fav_keys(prop)

    was_fav = bool(keys) and bool(keys & {_fav_key(f) for f in favs})
    if was_fav:
        favs = {f for f in favs if _fav_key(f) not in keys}
        new_state = False
    else:
        favs.add(pid or lid)  # prefer property_id
        new_state = True

    save_favorites(favs, properties_root)
    return new_state


# ---------------------------------------------------------------------------
# Folder matching — link an property record to its on-disk folder
# ---------------------------------------------------------------------------

# Suffixes the data provider attaches to property names that folder names usually
# omit. Stripped before matching so "Dove Landing Apartments" → folder
# "Dove-Landing-316-Virginia-Beach" connects properly.
# Strip ONLY truly noise suffixes that don't distinguish a property from
# its neighbors. "Townhomes"/"Condos"/"Homes" are meaningful descriptors
# (folder names keep them — see `make_folder_name`), so dropping them from
# the search token set causes wrong-folder matches when multiple properties
# share a base name (e.g. "Crossroads Townhomes" matched
# "Crossroads-Landing-104-Norfolk" before this fix).
_NAME_SUFFIXES_TO_STRIP = (
    " apartments", " apt", " apts", " flats",
)


def _name_words(s: str) -> set[str]:
    """Lowercase tokens (alnum runs) extracted from a string for fuzzy match."""
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def make_folder_name(prop: dict[str, Any]) -> str:
    """Build a folder name from a property dict, following Eight Rock convention.

    Format: ``<Name>-<Units>-<City>`` with dashes replacing spaces. Common
    suffixes ("Apartments", "Townhomes", etc.) are stripped from the property
    name to match Brian's existing folder names.

    Examples:
      "Dove Landing Apartments" + 316u + "Virginia Beach"
        → "Dove-Landing-316-Virginia-Beach"
      "Crossroads Townhomes" + 29u + "Norfolk"
        → "Crossroads-Townhomes-29-Norfolk"  *(suffix kept since it's
                                              a meaningful descriptor)*
    """
    name = (prop.get("name") or "Property").strip()
    # Strip "Apartments" but NOT "Townhomes" — the latter shows up in
    # canonical folder names like Crossroads-Townhomes-29-Norfolk.
    apartment_suffixes = (" apartments", " apt", " apts", " flats")
    for suffix in apartment_suffixes:
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)].strip()
            break

    units = prop.get("units") or 0
    city = (prop.get("city") or "").strip()

    name_slug = re.sub(r"\s+", "-", name)
    name_slug = re.sub(r"[^\w-]", "", name_slug)
    city_slug = re.sub(r"\s+", "-", city)
    city_slug = re.sub(r"[^\w-]", "", city_slug)

    parts = [name_slug]
    if units:
        try:
            parts.append(str(int(units)))
        except (TypeError, ValueError):
            pass
    if city_slug:
        parts.append(city_slug)
    return "-".join(p for p in parts if p) or "Property"


def ensure_property_folder(
    prop: dict[str, Any],
    properties_root: Path = PROPERTIES_ROOT,
) -> PropertyFolder:
    """Return the property's on-disk folder, creating it if it doesn't exist.

    First tries fuzzy match via `find_folder_for_property` to avoid creating
    a duplicate when the folder already exists under a slightly different
    name. If no match, creates a fresh folder using `make_folder_name`.
    """
    from core.storage import get_storage
    storage = get_storage()
    folders = discover_property_folders(properties_root)
    existing = find_folder_for_property(prop, folders)
    if existing is not None:
        return existing

    folder_name = make_folder_name(prop)
    target = properties_root / folder_name
    storage.mkdir(f"{_rel(properties_root)}/{folder_name}", parents=True, exist_ok=True)
    return PropertyFolder.from_path(target)


def find_folder_for_property(
    prop: dict[str, Any],
    folders: Iterable[PropertyFolder] | None = None,
) -> PropertyFolder | None:
    """Heuristic match an property record to its on-disk Properties/ folder.

    Strategy: tokenize the property name (after stripping pure-noise
    suffixes like 'Apartments') AND the unit count (when known), then find
    a folder whose token set is a superset of all required tokens. The unit
    count is decisive when the same base name has multiple folders — e.g.
    "Crossroads Townhomes" with 26 units must match `Crossroads-Townhomes-26-Norfolk`,
    not `Crossroads-Townhomes-29-Norfolk` or `Crossroads-Landing-104-Norfolk`.

    Returns the first folder that matches, or None if no match found.
    Pass an explicit `folders` list to avoid re-walking the filesystem when
    looking up many properties in a row.
    """
    if not prop.get("name"):
        return None
    if folders is None:
        folders = list(discover_property_folders())
    else:
        folders = list(folders)

    # Strip noise suffixes ("Apartments", "Apts", etc.) but keep meaningful
    # descriptors ("Townhomes", "Condos", etc.) — they distinguish products
    # at the same base address.
    name = prop["name"].lower()
    for suffix in _NAME_SUFFIXES_TO_STRIP:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break

    name_set = _name_words(name)
    if not name_set:
        return None

    # Add the unit count as a required token IF we know it. The folder
    # convention `<Name>-<Units>-<City>` always embeds the unit count, so
    # this disambiguates same-name buildings reliably.
    units_token: str | None = None
    units = prop.get("units")
    if units:
        try:
            units_token = str(int(units))
        except (TypeError, ValueError):
            units_token = None

    # First pass: name + units must both match (most specific). If no folder
    # has the unit count, fall back to name-only match for backward compat.
    if units_token is not None:
        required = name_set | {units_token}
        for folder in folders:
            folder_set = _name_words(folder.folder_name)
            if required.issubset(folder_set):
                return folder

    # Fallback: name-only match (previous behavior). Used when units missing
    # from the property record, or when a folder predates the units-in-name
    # convention.
    for folder in folders:
        folder_set = _name_words(folder.folder_name)
        if name_set.issubset(folder_set):
            return folder
    return None
