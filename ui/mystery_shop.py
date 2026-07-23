"""Mystery-shop log per property (C4).

Stores comp-shop call/visit notes in `<folder>/mystery_shops.json`. Each
shop is one record: date, comp property name, address, asking rent,
concessions, floorplan shopped, shopper name, free-form notes. Renders
as a table on the property's Subject tab + a "Add new shop" form.

The standardized fields from `ui.comp_shopper.SHOPPER_FIELDS` are
optionally captured per shop — for fast logging the analyst can record
just the headline numbers, then come back later to enrich.
"""

from __future__ import annotations

import datetime as dt
import json
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

import config
from data.property_io import PropertyFolder, ensure_property_folder
from ui.components import v2_strip_icon


SHOP_LOG_FILENAME = "mystery_shops.json"


def _load_shop_log(folder: Path) -> list[dict]:
    path = folder / SHOP_LOG_FILENAME
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _save_shop_log(folder: Path, shops: list[dict]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / SHOP_LOG_FILENAME
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=str(folder),
        delete=False, suffix=".tmp",
    ) as tmp:
        json.dump(shops, tmp, indent=2, ensure_ascii=False)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def render_mystery_shop_log(
    prop: dict[str, Any],
    folder: PropertyFolder | None,
) -> None:
    """C4 — Per-property comp-call log on the Subject tab.

    Renamed from "Mystery-Shop Log" → "Rent Comp Calls" for clearer
    real-estate-native language (Brian 2026-05-08). Functionality is
    identical — every record still logs a call or visit to a comparable
    property to capture asking rent, concessions, and floorplan data.
    """
    c = config.COLORS
    # Header is provided by the parent `section_card("Rent Comp Calls", icon="📞")`
    # in `render_property_detail`, so we just emit the description caption
    # inline. (Avoids duplicate header rendering.)
    st.caption(
        "Log every call or visit you make to a comp property. Captures "
        "asking rent + concessions + floorplan so you can build a clean "
        "comp grid later."
    )

    target_folder = folder
    if target_folder is None:
        st.caption("Folder will be auto-created when you log the first call.")
        shops: list[dict] = []
        folder_path = None
    else:
        shops = _load_shop_log(target_folder.path)
        folder_path = target_folder.path

    pid = str(prop.get("property_id") or "noid").replace("-", "_")

    # ---- Existing shops table ----
    if shops:
        df = pd.DataFrame(shops)
        # Render in reverse-chronological order
        if "shop_date" in df.columns:
            df = df.sort_values("shop_date", ascending=False)
        # Pretty columns
        col_map = {
            "shop_date": "Date",
            "comp_name": "Comp",
            "comp_address": "Address",
            "floorplan": "Plan",
            "asking_rent": "Asking $",
            "concessions": "Concessions",
            "effective_rent": "Effective $",
            "shopper": "Shopper",
            "notes": "Notes",
        }
        keep = [c for c in col_map if c in df.columns]
        display = df[keep].rename(columns=col_map)
        # Format $ columns
        for col in ("Asking $", "Effective $"):
            if col in display.columns:
                display[col] = display[col].apply(
                    lambda v: f"${float(v):,.0f}" if pd.notna(v) and v else "—"
                )
        st.dataframe(display, use_container_width=True, hide_index=True)
    else:
        st.caption("No comp calls logged yet.")

    # ---- Add-new form ----
    with st.expander(v2_strip_icon("➕ Log a new comp call / visit"), expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            comp_name = st.text_input("Comp property name", key=f"shop_name_{pid}")
            comp_address = st.text_input("Comp address", key=f"shop_addr_{pid}")
            shopper = st.text_input(
                "Shopper", value="Brian", key=f"shop_shopper_{pid}",
            )
        with col2:
            shop_date = st.date_input(
                "Date", value=dt.date.today(), key=f"shop_date_{pid}",
            )
            floorplan = st.text_input(
                "Floorplan shopped (e.g. 2BR/1.5BA, 855sf)",
                key=f"shop_plan_{pid}",
            )

        col3, col4, col5 = st.columns(3)
        with col3:
            asking_rent = st.number_input(
                "Asking rent ($/mo)",
                min_value=0, max_value=10_000, value=0, step=25,
                key=f"shop_asking_{pid}",
            )
        with col4:
            concessions_str = st.text_input(
                "Concessions",
                placeholder="1 month free, $500 admin waived…",
                key=f"shop_conc_{pid}",
            )
        with col5:
            effective_rent = st.number_input(
                "Effective rent ($/mo, after concessions)",
                min_value=0, max_value=10_000, value=0, step=25,
                key=f"shop_eff_{pid}",
            )

        notes = st.text_area(
            "Notes (vintage, W/D, RUBS, pet rent, vacancy on tour, red flags...)",
            key=f"shop_notes_{pid}", height=120,
        )

        if st.button(
            "💾 Save comp call", type="primary", key=f"shop_save_{pid}",
        ):
            if not comp_name.strip():
                st.error("Comp property name is required.")
                return
            if folder_path is None:
                folder_obj = ensure_property_folder(prop)
                folder_path = folder_obj.path
                shops = _load_shop_log(folder_path)
            entry = {
                "shop_date": shop_date.isoformat() if hasattr(shop_date, "isoformat") else str(shop_date),
                "comp_name": comp_name.strip(),
                "comp_address": comp_address.strip(),
                "shopper": shopper.strip() or "Brian",
                "floorplan": floorplan.strip(),
                "asking_rent": float(asking_rent) if asking_rent else None,
                "concessions": concessions_str.strip(),
                "effective_rent": float(effective_rent) if effective_rent else None,
                "notes": notes.strip(),
                "logged_at": dt.datetime.now().isoformat(timespec="seconds"),
            }
            shops.append(entry)
            _save_shop_log(folder_path, shops)
            st.success(f"✓ Logged comp call for {comp_name}")
            st.rerun()
