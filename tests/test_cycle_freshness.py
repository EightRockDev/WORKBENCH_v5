"""The continuous-cadence fixes (2026-08-03): don't re-do unchanged work.

The autopilot chains cycles back-to-back on the office server. Re-downloading
~1M municipal records and rebuilding the spine from identical inputs every
cycle saturated the box - the app crawled and Streamlit reruns ghosted stale
elements ("showing lots of information twice"). Both layers now skip
honestly: skips are stamped against their INPUTS (plus a code generation),
never against the clock alone - the listings-freshness lesson, generalized.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

import etl_munidata as em
from core import phase0


# ------------------------------------------------------------- muni pull

def _muni_conn(age_days: float, n: int = 5):
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE muni_records (
        id INTEGER PRIMARY KEY, market TEXT, state TEXT, county TEXT,
        kind TEXT, source_url TEXT, pulled_at TEXT, record TEXT)""")
    stamp = (dt.datetime.now() - dt.timedelta(days=age_days)
             ).isoformat(timespec="seconds")
    conn.executemany(
        "INSERT INTO muni_records (market, state, county, kind, source_url, "
        "pulled_at, record) VALUES (?,?,?,?,?,?,?)",
        [("Norfolk", "VA", "Norfolk", "assessor", "https://f.test/0",
          stamp, "{}")] * n)
    return conn


def _feed(url="https://f.test/0"):
    return em.FeedSpec("Norfolk", "VA", "Norfolk", "assessor", "arcgis", url)


def test_a_recent_pull_skips_without_touching_the_network(monkeypatch):
    conn = _muni_conn(age_days=1)
    monkeypatch.delenv("ER_MUNI_FORCE", raising=False)

    def explode(*a, **k):
        raise AssertionError("a fresh feed must not construct a puller")
    monkeypatch.setattr(em, "puller_for", explode)
    assert em.run_feed(_feed(), conn) == 5      # reports the kept rows


def test_a_stale_pull_repulls(monkeypatch):
    conn = _muni_conn(age_days=em.MUNI_REFRESH_DAYS + 1)
    monkeypatch.delenv("ER_MUNI_FORCE", raising=False)
    assert not em._feed_fresh(conn, _feed())


def test_a_new_feed_url_is_never_fresh(monkeypatch):
    """Discovery replacing a feed (new url / new where) must pull now, not
    in three days."""
    conn = _muni_conn(age_days=0.1)
    monkeypatch.delenv("ER_MUNI_FORCE", raising=False)
    assert not em._feed_fresh(conn, _feed("https://better.test/0"))


def test_force_env_overrides_freshness(monkeypatch):
    conn = _muni_conn(age_days=0.1)
    monkeypatch.setenv("ER_MUNI_FORCE", "1")
    assert not em._feed_fresh(conn, _feed())


def test_an_empty_prior_pull_is_not_fresh(monkeypatch):
    """Zero rows kept for 3 days would be a silently-broken feed."""
    conn = _muni_conn(age_days=0.1, n=0)
    monkeypatch.delenv("ER_MUNI_FORCE", raising=False)
    assert not em._feed_fresh(conn, _feed())


# ---------------------------------------------------------- spine rebuild

def _spine_db(tmp_path):
    db = tmp_path / "wb.db"
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE muni_records (
            id INTEGER PRIMARY KEY, pulled_at TEXT)""")
        conn.execute("INSERT INTO muni_records (pulled_at) VALUES ('t1')")
    return db


def test_fingerprint_moves_with_every_input(tmp_path):
    db = _spine_db(tmp_path)
    base = phase0.spine_input_fingerprint(db)
    # more muni rows -> different
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO muni_records (pulled_at) VALUES ('t2')")
    grew = phase0.spine_input_fingerprint(db)
    assert grew != base
    # learned codes arriving -> different (this is what lets a learned
    # Portsmouth code trigger the very next rebuild)
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE learned_mf_use_codes (
            city TEXT, use_code TEXT, mf_parcels INTEGER, evidence TEXT,
            learned_at TEXT)""")
        conn.execute("INSERT INTO learned_mf_use_codes VALUES "
                     "('Portsmouth', '18', 5, '', 'now')")
    assert phase0.spine_input_fingerprint(db) != grew


def test_fingerprint_moves_with_the_code_generation(tmp_path, monkeypatch):
    """An unchanged-inputs skip must not pin an old spine under new code -
    the listings PULL_GENERATION lesson, generalized."""
    db = _spine_db(tmp_path)
    a = phase0.spine_input_fingerprint(db)
    monkeypatch.setattr(phase0, "SPINE_BUILD_GENERATION",
                        phase0.SPINE_BUILD_GENERATION + 1)
    assert phase0.spine_input_fingerprint(db) != a


def test_spine_meta_round_trips(tmp_path):
    db = _spine_db(tmp_path)
    assert phase0.load_spine_meta(db, "fingerprint") is None
    phase0.save_spine_meta(db, "fingerprint", "abc123")
    phase0.save_spine_meta(db, "last_report", "the report text")
    assert phase0.load_spine_meta(db, "fingerprint") == "abc123"
    assert phase0.load_spine_meta(db, "last_report") == "the report text"
    phase0.save_spine_meta(db, "fingerprint", "def456")   # upsert, not dup
    assert phase0.load_spine_meta(db, "fingerprint") == "def456"


# --------------------------------------------------- passcode remember-me

class _Ctx:
    def __init__(self, cookies): self.cookies = dict(cookies or {})


class _FakeSt:
    """The minimum streamlit surface require_passcode touches."""

    def __init__(self, query_params=None, cookies=None):
        self.session_state: dict = {}
        self.query_params = dict(query_params or {})
        self.context = _Ctx(cookies)
        self.stopped = False

    def markdown(self, *a, **k): ...
    def caption(self, *a, **k): ...
    def error(self, *a, **k): ...

    def form(self, *_a, **_k):
        import contextlib
        return contextlib.nullcontext()

    def text_input(self, *a, **k):
        return ""

    def form_submit_button(self, *a, **k):
        return False

    def stop(self):
        self.stopped = True
        raise RuntimeError("st.stop")


def _token(passcode: str) -> str:
    import hashlib
    import hmac
    return hmac.new(passcode.encode(), b"8r-device-v1",
                    hashlib.sha256).hexdigest()[:20]


def test_a_remembered_device_skips_the_prompt(monkeypatch):
    from core.session import require_passcode

    monkeypatch.setenv("ER_APP_PASSCODE", "granite")
    st = _FakeSt(query_params={"k": _token("granite")})
    require_passcode(st)                       # no stop -> app renders
    assert st.session_state.get("_passcode_ok")


def test_a_wrong_or_stale_token_still_gates(monkeypatch):
    """Changing the passcode must invalidate every remembered device."""
    import pytest
    from core.session import require_passcode

    monkeypatch.setenv("ER_APP_PASSCODE", "granite")
    st = _FakeSt(query_params={"k": _token("old-passcode")})
    with pytest.raises(RuntimeError, match="st.stop"):
        require_passcode(st)
    assert not st.session_state.get("_passcode_ok")


def test_the_url_token_is_not_the_passcode(monkeypatch):
    """The token must be a derivation - the secret never rides in the URL."""
    assert _token("granite") != "granite"
    assert "granite" not in _token("granite")


def test_a_cookie_from_a_prior_visit_keeps_the_device_signed_in(monkeypatch):
    """The durable path: a real cookie survives new tabs and fresh ?prop=
    URLs, which the query-param token did not."""
    from core import session

    monkeypatch.setenv("ER_APP_PASSCODE", "granite")
    tok = session.passcode_device_token("granite")
    st = _FakeSt(cookies={session.PASSCODE_COOKIE: tok})
    session.require_passcode(st)
    assert st.session_state.get("_passcode_ok")


def test_a_cookie_for_the_old_passcode_is_rejected(monkeypatch):
    import pytest
    from core import session

    monkeypatch.setenv("ER_APP_PASSCODE", "granite")
    stale = session.passcode_device_token("old-passcode")
    st = _FakeSt(cookies={session.PASSCODE_COOKIE: stale})
    with pytest.raises(RuntimeError, match="st.stop"):
        session.require_passcode(st)
    assert not st.session_state.get("_passcode_ok")


def test_the_cookie_value_is_the_token_not_the_passcode():
    from core import session
    tok = session.passcode_device_token("granite")
    assert tok != "granite" and "granite" not in tok
