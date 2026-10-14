"""Open the real app in a real browser and look at the Sale History card.

The owner's question on 2026-08-15, and it was a fair one: *"Why don't you
test these things after you make updates? After all, you have browser
control no problem."*

Five releases were shipped against a screenshot of an empty Sale History
card. Every one passed a green unit suite. None of them was ever opened in a
browser, and the bug — deal folders resolving to `C:\\Properties` instead of
`C:\\WORKBENCH_V5\\Properties` — lived in the seam BETWEEN the units, where
each half was locally correct and nothing compared them.

This test does what should have been done first: writes a `sales.json` the
way the owner does, starts the app, clicks to the Subject tab, and asserts
the sale rows are on the page. It fails against the pre-V5.59 code.

Skips cleanly when Playwright or the bundled Chromium is not installed, so a
machine without a browser still gets a green suite — but on any machine that
CAN run it, it runs by default rather than hiding behind a marker.
"""

from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CHROMIUM_CANDIDATES = (
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/opt/pw-browsers/chromium/chrome-linux/chrome",
)

PROP_ID = "8R-E2E-SALEHIST"
FOLDER = "Grand-Hampton-at-Langley-136-Hampton"
SALES = [
    {"date": "2019-06-04", "price": 21500000,
     "grantor": "LANGLEY GARDENS ASSOCIATES LP",
     "grantee": "GRAND HAMPTON OWNER LLC",
     "notes": "Recorded deed, Hampton Circuit Court"},
    {"date": "2004-11-19", "price": 9250000,
     "grantor": "MICHIGAN DRIVE PROPERTIES INC",
     "grantee": "LANGLEY GARDENS ASSOCIATES LP", "notes": ""},
]


def _chromium() -> str | None:
    for c in CHROMIUM_CANDIDATES:
        if Path(c).is_file():
            return c
    return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


pytestmark = pytest.mark.skipif(
    _chromium() is None or sys.platform == "win32",
    reason="needs the bundled Chromium; not run on the owner's Windows box",
)


# Where the app subprocess must read and write, and nowhere else. Each ER_*
# var below is read once at import by the module named (ER_RECENT_VIEWS_FILE:
# on every call), so it must be in the child's env before `streamlit run`
# starts. load_dotenv() never overrides a key that is already set, so a
# developer's .env cannot point any of these back at live data.
#   ER_DB_PATH          read ONLY by data/db.py (DB_PATH: the properties
#                       table) and core/calibration.py (WORKBENCH_DB_PATH:
#                       its tables share that file). Deliberately not
#                       ER_WORKBENCH_DB, which phase0 users set to a
#                       different file.
#   ER_WORKBENCH_DB     core/phase0.py find_workbench_db(), the muni_records /
#                       sale_records lookup - here the same sandbox file
#   ER_PROPERTIES_ROOT  data/property_io.py PROPERTIES_ROOT (deal folders)
#   ER_RECENT_VIEWS_FILE  ui/v2_theme_05292026.py's recently-viewed list,
#                       whose default sits OUTSIDE the repo
#   ER_LEGACY_DATA_DIR  data/legacy_loader.py: an EMPTY dir, so
#                       ensure_db_synced() finds no licensed export to
#                       rebuild the tmp DB from
#   ER_STORAGE_BACKEND  local disk, never a cloud drive a .env might name
#   DATABASE_URL=""     see below; it is NOT enough on its own
#   HOME                an EMPTY sandbox dir: ~/.streamlit/secrets.toml and
#                       config.toml, and data/property_seed.py's scan of ~
#                       for deal folders, all see nothing real
# plus the CLI flag --secrets.files=<sandbox>/secrets.toml (an empty file).
# data/pg.py database_url() consults st.secrets["postgres"]["url"] BEFORE
# $DATABASE_URL, and Streamlit's default secrets.files includes
# <repo>/.streamlit/secrets.toml, which an empty HOME cannot hide - so
# without the flag a developer's real [postgres] url switches Postgres back
# on. The flag REPLACES that default list, and a command-line value beats
# any STREAMLIT_SECRETS_FILES a developer's own env might carry.

# Proves, in a fresh interpreter with the app's env, cwd and `streamlit run`
# argv, what the app is about to resolve. Streamlit config cannot be pointed
# at a file from the env in a plain interpreter, so step 1 replays exactly
# what `streamlit run <argv>` does before it imports the app: the same click
# parser over the same argv (env-var defaults included), then the same two
# steps streamlit.web.cli._main_run takes. st.secrets - and therefore
# pg.database_url() - then reads the same files the app will. If the flag
# is ever dropped from the command line, the probe sees it too.
_PROBE = r"""
import json, os, sys
from pathlib import Path

from streamlit import config as st_config
from streamlit.web import bootstrap, cli

ctx = cli.main_run.make_context("run", json.loads(sys.argv[1]))
flags = dict(ctx.params)
target = flags.pop("target")
flags.pop("args")
st_config._main_script_path = os.path.abspath(target)
bootstrap.load_config_options(flag_options=flags)

from core import calibration, phase0
from data import db, pg, property_io
from ui import v2_theme_05292026 as v2

wb = phase0.find_workbench_db()
print("PROBE=" + json.dumps({
    "paths": {
        "Path.home()": str(Path.home()),
        "data.db.DB_PATH": str(db.DB_PATH),
        "core.calibration.WORKBENCH_DB_PATH": str(calibration.WORKBENCH_DB_PATH),
        "core.phase0.find_workbench_db()": str(wb) if wb else None,
        "data.property_io.PROPERTIES_ROOT": str(property_io.PROPERTIES_ROOT),
        "recent-views file": str(v2._recent_views_path()),
    },
    "secrets.files": [str(f) for f in st_config.get_option("secrets.files")],
    # A bool, never the URL itself: a failure message must not print a
    # credential.
    "pg.database_url() set": bool(pg.database_url()),
    "pg.is_configured()": pg.is_configured(),
}))
"""


def _probe(env: dict[str, str], streamlit_argv: list[str]) -> dict:
    """What a fresh interpreter with `env`, launched as `streamlit run
    *streamlit_argv` would be, resolves - i.e. what the app is about to use."""
    out = subprocess.run(
        [sys.executable, "-c", _PROBE, json.dumps(streamlit_argv)],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    line = next(ln for ln in out.stdout.splitlines() if ln.startswith("PROBE="))
    return json.loads(line[len("PROBE="):])


def _log_tail(log_path: Path) -> str:
    try:
        return log_path.read_text(errors="replace")[-4000:]
    except OSError as exc:
        return f"(no server log: {exc})"


def _stop(proc: subprocess.Popen) -> None:
    """Kill the server and reap it. Never raises: this runs in a finally,
    where an exception would mask the failure that got us there."""
    if proc.poll() is None:
        proc.kill()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        warnings.warn(f"streamlit server pid {proc.pid} ignored SIGKILL for 30s",
                      stacklevel=2)


@pytest.fixture(scope="module")
def app_url(tmp_path_factory):
    """The app, running, with one property and one curated sale history.

    Every WRITE is sandboxed under a pytest tmp dir: its own SQLite file,
    deal-folder root, recent-views file, HOME and (empty) secrets file, and
    no Postgres. One read is not: on a machine that has the Hampton Roads
    ETL file, core/etl_db.py still finds it ($ER_ETL_DB only adds a search
    location ahead of the defaults), and market/rent panels read it.
    An earlier version of this fixture wrote straight into data/workbench.db
    and the real Properties/ folder - harmless only on a machine where they
    happened to be empty, and an edit to live data on any developer box
    where they are not (see tests/pgguard.py for how that class of test once
    ended).
    """
    pytest.importorskip("playwright.sync_api")

    import etl_munidata
    from core import sale_index
    from data import db as db_mod

    sandbox = tmp_path_factory.mktemp("sale_history_e2e")
    db_path = sandbox / "workbench.db"
    props_root = sandbox / "Properties"
    no_exports = sandbox / "no-licensed-exports"
    home = sandbox / "home"
    secrets_file = sandbox / "secrets.toml"
    recents_file = sandbox / "recent_views.json"
    props_root.mkdir()
    no_exports.mkdir()
    home.mkdir()
    secrets_file.write_text("", encoding="utf-8")

    # --- the property record, as the owner's screenshot shows it ---------
    # Schema straight from the repo's own source (data/schema.sql), the same
    # call the app makes on a box with no licensed export.
    db_mod.init_empty_db(db_path, db_mod.SCHEMA_PATH)
    # 192 units on the record, 136 in the folder name: a real mismatch that
    # must not prevent the folder from matching.
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO properties
           (property_id, name, address, city, state, zip, units, year_built,
            asset_class, asset_type, market, status, source_file)
           VALUES (?, 'Grand Hampton at Langley', '611 Michigan Dr',
                   'Hampton', 'VA', '23669', 192, 1967, 'C', 'Multifamily',
                   'Hampton Roads', 'Active', 'E2E-TEST')""",
        (PROP_ID,))
    conn.commit()
    # --- county data: present, but nothing for Hampton --------------------
    # muni_records as the nightly pull creates it, then sale_records built
    # from it by the autopilot's own builder: both empty. Without these
    # tables core/sale_history.explain_no_sales() hits a missing table and
    # says a generic "No sale history available." even when sales.json is
    # never read - so the "nightly data pull" assertion below could not
    # fail. With them, that is exactly the caption a skipped sales.json
    # produces.
    etl_munidata._ensure_schema(conn)
    conn.close()
    built = sale_index.build(db_path)
    assert built.get("sales") == 0, built

    # --- the curated sale history, written exactly as the owner has it ---
    folder = props_root / FOLDER
    folder.mkdir()
    (folder / "sales.json").write_text(json.dumps(SALES), encoding="utf-8")

    port = _free_port()
    env = {**os.environ}
    # Empty, not absent: app.py calls load_dotenv(), which would otherwise
    # put the developer's real DATABASE_URL back and gate the app behind a
    # Postgres that is not running here. python-dotenv leaves a key alone
    # when it is already present in the environment, so "" wins. Necessary
    # but not sufficient: see --secrets.files above.
    env["DATABASE_URL"] = ""
    env["ER_NO_AUTOPILOT"] = "1"
    env["ER_DB_PATH"] = str(db_path)
    env["ER_WORKBENCH_DB"] = str(db_path)
    env["ER_PROPERTIES_ROOT"] = str(props_root)
    env["ER_RECENT_VIEWS_FILE"] = str(recents_file)
    env["ER_LEGACY_DATA_DIR"] = str(no_exports)
    env["ER_STORAGE_BACKEND"] = "local"
    # The app process only. Playwright and Chromium run in THIS process,
    # whose HOME is untouched.
    env["HOME"] = str(home)

    streamlit_argv = [
        "app.py",
        f"--server.port={port}", "--server.address=127.0.0.1",
        "--server.headless=true",
        f"--secrets.files={secrets_file}",
    ]

    # Refuse to start the app unless it will resolve the sandbox. If an
    # override is ever dropped, this fails here - before the app has opened,
    # migrated or seeded anything real - instead of as a confusing UI miss.
    probe = _probe(env, streamlit_argv)
    allowed = {
        "Path.home()": {home},
        "data.db.DB_PATH": {db_path},
        "core.calibration.WORKBENCH_DB_PATH": {db_path},
        # None = "no muni DB on this box", which reads nothing: also safe.
        "core.phase0.find_workbench_db()": {db_path, None},
        "data.property_io.PROPERTIES_ROOT": {props_root},
        "recent-views file": {recents_file},
    }
    resolved = {k: (Path(v) if v is not None else None)
                for k, v in probe["paths"].items()}
    outside = [f"{k} -> {resolved.get(k, '(not probed)')}"
               for k in allowed if resolved.get(k, "?") not in allowed[k]]
    outside += [f"{k} -> (unchecked)" for k in resolved if k not in allowed]
    assert not outside, (
        "the app would read/write outside its sandbox: " + ", ".join(outside))
    assert probe["secrets.files"] == [str(secrets_file)], probe["secrets.files"]
    assert probe["pg.database_url() set"] is False, (
        "a Postgres URL is still reachable from the app's env/secrets")
    assert probe["pg.is_configured()"] is False

    # A file, not a PIPE: nothing reads the pipe, so a chatty server would
    # block on a full buffer mid-test. The log also explains a failed start.
    log_path = sandbox / "streamlit.log"
    log = log_path.open("wb")
    proc = None
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "streamlit", "run", *streamlit_argv],
            cwd=str(ROOT), env=env,
            stdout=log, stderr=subprocess.STDOUT)

        url = f"http://127.0.0.1:{port}"
        deadline = time.time() + 90
        import urllib.error
        import urllib.request
        while time.time() < deadline:
            try:
                urllib.request.urlopen(url, timeout=2)
                break
            except (urllib.error.URLError, OSError):
                if proc.poll() is not None:
                    break
                time.sleep(1)
        else:
            _stop(proc)
            pytest.fail("the app never came up:\n" + _log_tail(log_path))
        if proc.poll() is not None:
            pytest.fail(f"the app exited ({proc.returncode}) before serving:\n"
                        + _log_tail(log_path))

        yield url
    finally:
        # Always: on a normal teardown, a failed start, or any exception in
        # between. Nothing to clean up outside the sandbox; pytest owns it.
        try:
            if proc is not None:
                _stop(proc)
        finally:
            log.close()


def _subject_tab_text(url: str) -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=_chromium())
        try:
            page = browser.new_page(viewport={"width": 1400, "height": 1600})
            page.goto(f"{url}/?prop={PROP_ID}", wait_until="networkidle",
                      timeout=90_000)
            page.wait_for_timeout(5_000)
            page.locator(
                "button:has-text('Subject'), [role=tab]:has-text('Subject'), "
                "p:text-is('Subject')").first.click()
            page.wait_for_selector("text=Sale History", timeout=30_000)
            page.wait_for_timeout(3_000)
            return page.inner_text("body")
        finally:
            browser.close()


# `st.dataframe` paints its cells into a <canvas> and exposes no ARIA grid
# roles, so the prices themselves are genuinely unreadable from the DOM -
# verified by probing for gridcell/cell/row/grid/table, all zero. The three
# assertions below are the DOM-visible facts that separate "rendered the
# owner's sale rows" from every failure mode, which is what this needs to
# prove; asserting on a screenshot's pixels would be worse, not stronger.


def test_the_curated_sale_history_renders(app_url):
    """The row-legend caption is emitted only after the table is written, so
    its presence means records reached the page."""
    text = _subject_tab_text(app_url)

    assert "Sale History" in text, "the card is missing entirely"
    assert "Each row reads chronologically" in text, (
        "no sale rows were rendered - the table legend is only emitted "
        "under a table that has records in it")


def test_the_rows_came_from_the_owners_file_not_county_records(app_url):
    """The discriminator. A county-sourced fallback prints its own
    provenance caption; a curated `sales.json` does not. This is what
    catches the folder being invisible while the card still looks alive."""
    text = _subject_tab_text(app_url)

    assert "county assessor's transfer record" not in text, (
        "the card fell back to county records, so the curated sales.json "
        "was never read")


def test_it_does_not_fall_back_to_a_data_feed_excuse(app_url):
    """The exact sentence the owner kept seeing. When a curated sales.json
    exists, blaming a nightly pull is a lie about a file on disk."""
    text = _subject_tab_text(app_url)

    assert not re.search(r"nightly data pull", text, re.I), (
        "the card blamed the data feed while sales.json was sitting on disk")
    assert "No deal folder for this property" not in text, (
        "the deal folder was not found, so its sales.json was never read")
