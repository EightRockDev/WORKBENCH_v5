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
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
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


@pytest.fixture(scope="module")
def app_url():
    """The app, running, with one property and one curated sale history."""
    pytest.importorskip("playwright.sync_api")

    from data import db as db_mod

    # --- the property record, as the owner's screenshot shows it ---------
    # 192 units on the record, 136 in the folder name: a real mismatch that
    # must not prevent the folder from matching.
    conn = sqlite3.connect(db_mod.DB_PATH)
    conn.execute("DELETE FROM properties WHERE property_id=?", (PROP_ID,))
    conn.execute(
        """INSERT INTO properties
           (property_id, name, address, city, state, zip, units, year_built,
            asset_class, asset_type, market, status, source_file)
           VALUES (?, 'Grand Hampton at Langley', '611 Michigan Dr',
                   'Hampton', 'VA', '23669', 192, 1967, 'C', 'Multifamily',
                   'Hampton Roads', 'Active', 'E2E-TEST')""",
        (PROP_ID,))
    conn.commit()
    conn.close()

    # --- the curated sale history, written exactly as the owner has it ---
    from data.property_io import PROPERTIES_ROOT
    folder = PROPERTIES_ROOT / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "sales.json").write_text(json.dumps(SALES), encoding="utf-8")

    port = _free_port()
    env = {**os.environ}
    # Empty, not absent: app.py calls load_dotenv(), which would otherwise
    # put the developer's real DATABASE_URL back and gate the app behind a
    # Postgres that is not running here. python-dotenv leaves a key alone
    # when it is already present in the environment, so "" wins and
    # pg.is_configured() reads False -> the app runs ungated.
    env["DATABASE_URL"] = ""
    env["ER_NO_AUTOPILOT"] = "1"
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "app.py",
         f"--server.port={port}", "--server.address=127.0.0.1",
         "--server.headless=true"],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 90
    import urllib.error
    import urllib.request
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            break
        except (urllib.error.URLError, OSError):
            time.sleep(1)
    else:
        proc.kill()
        pytest.fail("the app never came up")

    yield url

    proc.kill()
    proc.wait(timeout=30)
    shutil.rmtree(folder, ignore_errors=True)
    conn = sqlite3.connect(db_mod.DB_PATH)
    conn.execute("DELETE FROM properties WHERE property_id=?", (PROP_ID,))
    conn.commit()
    conn.close()


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
