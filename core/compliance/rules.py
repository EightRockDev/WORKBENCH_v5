"""The compliance gate — C1-C7 evaluated as pipeline rules (spec §4.4).

``evaluate()`` is the single chokepoint every outbound touch must pass. It
returns a :class:`Decision` carrying ``allowed`` plus a full **rule trace**
(which checks ran, passed/failed, and why) — the trace is persisted with the
touch to satisfy AC-B2 ("rule-evaluation trace, audit-exportable").

Rules implemented:
  C1  DNC scrub  — federal + six state registries (IN, LA, MO, PA, TX, WY);
                   scrub must be <=31 days old; internal DNC list honored.
  C2  Litigator  — professional-plaintiff suppression.
  C3  Channel    — live agent-initiated calls OK with a scrub; prerecorded /
                   AI-voice / ringless voicemail to a CELL and SMS require prior
                   express written consent (hard block otherwise).
  C4  Time & freq— quiet hours 8:00-21:00 called-party LOCAL time (derived from
                   number geography, else address state, else the conservative
                   all-timezone intersection); per-person frequency caps incl.
                   the Oregon 3/day overlay.
  C5  Revocation — any opt-out, any channel, honored across all channels.
  C6  FCRA       — acquisition/marketing purpose only; screening purposes are
                   refused outright (structural firewall).
  C7  Licensing  — tracing for the tenant's own acquisition purpose is the
                   supported pattern; managed "we trace for you" is out of scope.

Deterministic and LLM-free (Section 11).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from core.compliance import ledger

# C1 — the six state DNC registries the spec calls out for separate scrubs.
STATE_DNC_REGISTRIES = ("IN", "LA", "MO", "PA", "TX", "WY")

# C4 — quiet hours in the CALLED PARTY's local time.
QUIET_START_HOUR = 8       # 8:00 AM
QUIET_END_HOUR = 21        # 9:00 PM
DEFAULT_DAILY_CAP = 3      # per person per day, across all campaign types
STATE_DAILY_CAPS = {"OR": 3}   # Oregon: 3 calls/consumer/day from Jan 2026

# Channels whose *subtypes* are prerecorded/artificial-voice or RVM (C3).
CONSENT_REQUIRED_SUBTYPES = {"prerecorded", "ai_voice", "voice_clone", "rvm",
                             "ringless", "artificial_voice"}

# Minimal NANP area-code -> UTC offset (standard time) map for the markets the
# Workbench covers, plus broad US coverage. Unknown codes fall back to the
# address state, then to the conservative all-US intersection.
_AREA_TZ = {
    # Eastern (UTC-5)
    "757": -5, "804": -5, "703": -5, "571": -5, "540": -5, "434": -5, "276": -5,
    "252": -5, "919": -5, "704": -5, "336": -5, "828": -5, "910": -5, "984": -5,
    "803": -5, "843": -5, "864": -5, "404": -5, "470": -5, "678": -5, "770": -5,
    "912": -5, "478": -5, "706": -5, "212": -5, "718": -5, "917": -5, "646": -5,
    "215": -5, "267": -5, "412": -5, "301": -5, "410": -5, "240": -5, "443": -5,
    "202": -5, "305": -5, "786": -5, "813": -5, "407": -5, "904": -5, "561": -5,
    "617": -5, "857": -5, "203": -5, "860": -5, "614": -5, "216": -5, "513": -5,
    # Central (UTC-6)
    "615": -6, "901": -6, "731": -6, "214": -6, "469": -6, "972": -6, "713": -6,
    "281": -6, "832": -6, "210": -6, "512": -6, "817": -6, "504": -6, "225": -6,
    "314": -6, "816": -6, "312": -6, "773": -6, "205": -6, "251": -6, "256": -6,
    "317": -6, "463": -6, "414": -6, "612": -6, "402": -6, "501": -6, "405": -6,
    # Mountain (UTC-7)
    "303": -7, "720": -7, "970": -7, "801": -7, "385": -7, "505": -7, "406": -7,
    "307": -7, "208": -7, "602": -7, "480": -7, "623": -7,
    # Pacific (UTC-8)
    "213": -8, "310": -8, "323": -8, "415": -8, "510": -8, "619": -8, "714": -8,
    "760": -8, "805": -8, "818": -8, "916": -8, "949": -8, "206": -8, "253": -8,
    "425": -8, "503": -8, "971": -8, "702": -8, "775": -8,
}
_STATE_TZ = {
    "VA": -5, "NC": -5, "SC": -5, "GA": -5, "FL": -5, "MD": -5, "DC": -5, "NY": -5,
    "PA": -5, "NJ": -5, "MA": -5, "CT": -5, "OH": -5, "MI": -5, "IN": -5, "WV": -5,
    "TN": -6, "TX": -6, "LA": -6, "MO": -6, "IL": -6, "AL": -6, "MS": -6, "AR": -6,
    "OK": -6, "KS": -6, "IA": -6, "MN": -6, "WI": -6, "NE": -6,
    "CO": -7, "UT": -7, "NM": -7, "MT": -7, "WY": -7, "ID": -7, "AZ": -7,
    "CA": -8, "WA": -8, "OR": -8, "NV": -8,
}
# Conservative fallback: satisfy 8-21 local in BOTH the earliest (UTC-8) and the
# latest (UTC-5) US zone -> 11:00-21:00 Eastern.
_FALLBACK_OFFSETS = (-5, -8)


@dataclass
class RuleResult:
    rule: str
    passed: bool
    detail: str = ""

    def as_dict(self) -> dict:
        return {"rule": self.rule, "passed": self.passed, "detail": self.detail}


@dataclass
class Decision:
    allowed: bool
    trace: list[RuleResult] = field(default_factory=list)

    @property
    def reasons(self) -> list[str]:
        return [f"{r.rule}: {r.detail}" for r in self.trace if not r.passed]

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "ok"

    def trace_json(self) -> list[dict]:
        return [r.as_dict() for r in self.trace]


def _area_code(e164: str | None) -> str | None:
    if not e164:
        return None
    digits = "".join(c for c in e164 if c.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits[:3] if len(digits) == 10 else None


def local_now(e164: str | None, state: str | None,
              now_utc: dt.datetime | None = None) -> tuple[dt.datetime | None, str]:
    """Called-party local time and how it was derived (C4). Returns (None, why)
    when geography is unknown, so the caller applies the conservative window."""
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    ac = _area_code(e164)
    if ac and ac in _AREA_TZ:
        off = _AREA_TZ[ac]
        return now_utc + dt.timedelta(hours=off), f"area code {ac} (UTC{off})"
    if state and state.upper() in _STATE_TZ:
        off = _STATE_TZ[state.upper()]
        return now_utc + dt.timedelta(hours=off), f"address state {state.upper()} (UTC{off})"
    return None, "geography unknown"


def within_quiet_hours(e164: str | None, state: str | None,
                       now_utc: dt.datetime | None = None) -> tuple[bool, str]:
    """True when the call is inside the permitted 8:00-21:00 local window (C4)."""
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    local, how = local_now(e164, state, now_utc)
    if local is not None:
        ok = QUIET_START_HOUR <= local.hour < QUIET_END_HOUR
        return ok, f"{local:%H:%M} local via {how}"
    # Unknown geography: require the window to hold in every US zone.
    hours = [(now_utc + dt.timedelta(hours=o)).hour for o in _FALLBACK_OFFSETS]
    ok = all(QUIET_START_HOUR <= h < QUIET_END_HOUR for h in hours)
    return ok, f"{how}; conservative all-zone window ({hours[0]}h ET / {hours[1]}h PT)"


def evaluate(org_id: str, *, channel: str, e164: str | None = None,
             email: str | None = None, subtype: str = "manual_dial",
             state: str | None = None, phone_record: dict | None = None,
             purpose: str = "acquisition", managed_service: bool = False,
             now_utc: dt.datetime | None = None,
             daily_cap: int | None = None) -> Decision:
    """Run the full C1-C7 gate for one intended touch.

    ``phone_record`` is the §4.5 phone dict from Module A (grade, litigator, dnc
    stamp). Passing it lets the gate reuse the stamp captured at resolution time;
    the ledger is still consulted for revocations and internal DNC.
    """
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    trace: list[RuleResult] = []
    ok = True

    def add(rule: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        trace.append(RuleResult(rule, passed, detail))
        if not passed:
            ok = False

    # --- C6 FCRA FIREWALL (refuse non-acquisition purposes outright) --------
    if purpose != "acquisition":
        add("C6-FCRA", False,
            f"purpose '{purpose}' is not acquisition marketing/owner location; "
            "skip-trace data must never feed tenant screening, credit or employment")
        return Decision(False, trace)
    add("C6-FCRA", True, "acquisition purpose")

    # --- C7 LICENSING ------------------------------------------------------
    if managed_service:
        add("C7-LICENSING", False,
            "managed 'we trace for you' concierge is out of scope pending "
            "state-by-state PI licensing review")
    else:
        add("C7-LICENSING", True, "tenant's own acquisition purpose")

    # Mail has no TCPA/DNC exposure; it only needs the revocation check.
    is_phone_channel = channel in ("call", "voicemail", "sms")

    # --- C5 REVOCATION (any channel -> all channels) -----------------------
    revoked = ledger.is_revoked(org_id, e164=e164, email=email, channel=channel)
    add("C5-REVOCATION", not revoked,
        "contact opted out (honored across all channels)" if revoked else "no opt-out on file")

    if channel == "mail":
        return Decision(ok, trace)

    if channel == "email":
        add("C3-CHANNEL", True, "email permitted (consent-aware sequences)")
        return Decision(ok, trace)

    if not e164:
        add("C1-DNC", False, "no phone number supplied")
        return Decision(False, trace)

    # --- C1 DNC: internal list, scrub freshness, federal + state registries --
    if ledger.on_internal_dnc(org_id, e164):
        add("C1-INTERNAL-DNC", False, "number is on the tenant's internal do-not-call list")
    else:
        add("C1-INTERNAL-DNC", True, "not on internal DNC")

    stamp = (phone_record or {}).get("dnc") or {}
    scrub = ledger.latest_scrub(org_id, e164)
    scrubbed_at = stamp.get("scrubbed_at")
    expires_at = stamp.get("expires_at")
    fresh = False
    if expires_at:
        try:
            fresh = dt.datetime.fromisoformat(str(expires_at)) > now_utc
        except ValueError:
            fresh = False
    if not fresh:
        fresh = ledger.scrub_is_fresh(scrub, now_utc)
    add("C1-SCRUB-FRESH", fresh,
        "no valid scrub within 31 days - re-scrub required before use"
        if not fresh else f"scrub valid (stamped {scrubbed_at or scrub.get('scrubbed_at')})")

    fed = bool(stamp.get("federal") or (scrub or {}).get("federal"))
    add("C1-FEDERAL-DNC", not fed,
        "on the National DNC Registry" if fed else "not on federal DNC")

    st_list = list(stamp.get("state") or (scrub or {}).get("states") or [])
    hit_states = [s for s in st_list if s in STATE_DNC_REGISTRIES]
    add("C1-STATE-DNC", not hit_states,
        f"on state DNC registry: {','.join(hit_states)}" if hit_states
        else "clear of the six state registries")

    # --- C2 LITIGATOR ------------------------------------------------------
    lit = bool((phone_record or {}).get("litigator") or (scrub or {}).get("litigator"))
    add("C2-LITIGATOR", not lit,
        "known TCPA litigator - suppressed" if lit else "no litigator flag")

    # --- C3 CHANNEL RULES ---------------------------------------------------
    line_type = (phone_record or {}).get("line_type", "unknown")
    sub = (subtype or "").lower()
    needs_consent = sub in CONSENT_REQUIRED_SUBTYPES or channel == "sms"
    if needs_consent:
        is_cell = line_type in ("mobile", "unknown")   # unknown treated as cell (conservative)
        if channel == "sms" or is_cell:
            consented = ledger.has_consent(org_id, e164, "sms" if channel == "sms" else "voice")
            add("C3-CHANNEL", consented,
                (f"'{sub or channel}' to a cell requires prior express WRITTEN consent - "
                 "hard-blocked (this is a deliberate product stance; the unsafe "
                 "button does not exist)") if not consented
                else "prior express written consent on file")
        else:
            add("C3-CHANNEL", True, f"'{sub}' to a {line_type} - not a cell")
    else:
        add("C3-CHANNEL", True, f"live agent-initiated '{sub or channel}' permitted with DNC scrub")

    # --- C4 TIME & FREQUENCY ------------------------------------------------
    in_window, how = within_quiet_hours(e164, state, now_utc)
    add("C4-QUIET-HOURS", in_window,
        f"outside 8:00-21:00 called-party local time ({how})" if not in_window
        else f"within 8:00-21:00 local ({how})")

    cap = daily_cap if daily_cap is not None else STATE_DAILY_CAPS.get(
        (state or "").upper(), DEFAULT_DAILY_CAP)
    used = ledger.touches_today(org_id, e164)
    add("C4-FREQUENCY", used < cap,
        f"daily cap reached ({used}/{cap} touches to this person today)"
        if used >= cap else f"{used}/{cap} touches today")

    return Decision(ok, trace)
