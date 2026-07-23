"""Rent-comp shopper template (B5).

Generates a printable mystery-shop checklist for the analyst to use when
calling/visiting comparable properties. Captures the data points an
underwriter NEEDS but a brochure WON'T tell you:

  - Vintage / year built / renovation date
  - Ceiling height
  - Building material (brick vs siding vs stucco)
  - W/D in unit / hookup / common laundry
  - Utility billing model (RUBS / submeter / flat / included)
  - Concessions (free month, waived deposit, gift card)
  - Pet rent + pet deposit + breed/weight restrictions
  - Application/admin/move-in fees
  - Late fee schedule
  - Lease term + early-termination penalty
  - Parking ($, assigned, garages)
  - Storage availability + cost
  - Move-in inspection process

Output: a per-comp printable card OR a downloadable Word/PDF.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

import config
from ui.components import v2_strip_icon


# Standardized field list. Each entry has a label + tooltip explaining WHY.
SHOPPER_FIELDS = [
    ("Vintage / Year built", "Vintage drives capex curve. 1980s = $/u upside on RUBS, W/D, kitchens. Pre-1994 = water savings via toilets."),
    ("Last major renovation", "Has the comp already done a value-add? Tells you how much upside is already captured into their asking rent."),
    ("Construction class", "Brick > frame for insurance + maintenance. Class C properties usually frame; brick adds 5-10% ceiling on rent."),
    ("Ceiling height", "9'+ ceilings are an unusual differentiator in vintage MF. Worth +$25-50/mo if subject can claim it."),
    ("Floor (1st / top / mid)", "1st floors discount 5-10%; top floors premium 5-10%. Skews the comp's avg rent."),
    ("Unit type / floorplan", "Match exactly to subject's mix. Avoid comparing 1BRs to your 2BRs."),
    ("Sqft", "Confirm advertised vs actual. Brokers round up."),
    ("Asking rent (today)", "What they ARE asking — not necessarily what they GET. Compare to your in-place actual."),
    ("Concessions on offer", "1 month free? $500 gift card? 50% off admin? Subtract from effective rent: monthly_eff = (12 × asking − concessions_$) / 12."),
    ("W/D status", "In-unit? Hookup-only? Common laundry? In-unit can carry $75-150/mo premium."),
    ("Utility billing", "RUBS / submeter / flat-rate / included? RUBS lifts cap-rate value ~$3K/u at 8% cap."),
    ("Pet rent + deposit", "$25-35/mo pet rent + $250-500 nonref. Required field — comps often hide this in fine print."),
    ("Application / admin fee", "Catches the fee program. $50-100 app + $250 admin is typical."),
    ("Move-in fee / Hold fee", "Often non-refundable. Adds $200-500 to effective revenue per move-in."),
    ("Late fee", "5% or $50 minimum is standard. Aggressive operators bill quickly."),
    ("Lease terms offered", "12 / 18 / 24 mo? Premium for shorter? MTM premium %?"),
    ("Early termination penalty", "2-3 months rent is standard. Some comps charge less to attract turnover."),
    ("Parking", "Assigned spot? Garage rental ($)? Covered? Reserved spaces?"),
    ("Storage", "On-site storage rental availability + monthly cost."),
    ("Trash service", "Valet trash ($25-35/mo) or dumpster only? Valet = ancillary income lever."),
    ("Pool / fitness / clubhouse", "Match to subject. Class C comps with pool can charge $25-50/mo more."),
    ("Security", "Gated? Cameras? Front desk? Crime perception drives pricing in HR submarkets."),
    ("Online payment / portal", "Modern PMS = lower delinquency. Telling sign of operator quality."),
    ("Reviews / ratings", "Google + ApartmentRatings.com. Below 3.5 stars = chronic operational issues."),
    ("Manager response time", "Score 1-5 on call quality. Slow / unfriendly = under-resourced."),
    ("Vacancy on tour", "How many units shown? How quickly can you move in? Long lead = full; immediate = soft."),
    ("Tour notes / red flags", "Free-form: roach signs, mold smell, dated finishes, bad smells, broken parking lot, etc."),
]


def render_comp_shopper_template(prop: dict[str, Any] | None = None) -> None:
    """Render the printable comp-call checklist with a download button."""
    c = config.COLORS
    st.markdown(v2_strip_icon("### 📋 Comp Call Checklist"))
    st.caption(
        "Standardized checklist for calling or visiting comp properties. "
        "Print one per comp. Captures the data points underwriters need but "
        "brochures hide."
    )

    # Property header for the print version
    if prop:
        prop_name = prop.get("name", "—")
        addr = prop.get("address", "")
        city = prop.get("city", "")
    else:
        prop_name = "Subject Property"
        addr = ""
        city = ""

    # Build the printable card as HTML
    fields_html = ""
    for label, tooltip in SHOPPER_FIELDS:
        fields_html += (
            f'<div style="display:flex;border-bottom:1px solid #ccc;'
            f'padding:8px 4px;font-size:12px">'
            f'<div style="flex:1;font-weight:600;color:#222" title="{tooltip}">'
            f'{label}</div>'
            f'<div style="flex:2;border-bottom:1px dotted #999;'
            f'min-height:18px"></div>'
            f'</div>'
        )

    printable_html = f"""
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Comp Shopper — {prop_name}</title>
<style>
  body {{ font-family: Arial, sans-serif; max-width: 700px; margin: 20px auto;
          padding: 20px; color: #222; }}
  h1 {{ font-size: 18px; border-bottom: 2px solid #D4A017; padding-bottom: 6px; }}
  h2 {{ font-size: 13px; color: #555; margin-top: 4px; font-weight: normal; }}
  .meta {{ font-size: 11px; color: #777; margin-bottom: 14px; }}
  .header-fields {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px;
                    margin-bottom: 18px; font-size: 12px; }}
  .header-fields div {{ border-bottom: 1px dotted #999; padding-bottom: 8px;
                         min-height: 22px; }}
  .header-fields b {{ display: block; color: #444; font-size: 10px;
                      text-transform: uppercase; letter-spacing: 0.5px; }}
</style></head>
<body>
  <h1>🏢 Rent Comp Call Card</h1>
  <h2>Subject: {prop_name} — {addr}, {city}</h2>
  <div class="meta">Per Eight Rock Capital Partners · Lindahl shopper methodology</div>
  <div class="header-fields">
    <div><b>Comp Property Name</b></div>
    <div><b>Comp Address</b></div>
    <div><b>Phone Number Called</b></div>
    <div><b>Date / Time of Call or Visit</b></div>
    <div><b>Shopper Name</b></div>
    <div><b>Shopped As (renter persona)</b></div>
  </div>
  {fields_html}
  <div style="margin-top:30px;font-size:10px;color:#777">
    Compiled at close: scan + save to property folder under
    <code>shopper_logs/&lt;date&gt;_&lt;comp&gt;.pdf</code> per file-naming convention.
  </div>
</body></html>
"""

    # Render the visual preview inline
    st.markdown(
        f'<div style="background:white;color:#222;padding:24px;border-radius:8px;'
        f'border:1px solid {c["bdr"]};font-family:Arial,sans-serif;'
        f'max-height:600px;overflow-y:auto;font-size:12px">'
        + printable_html.split("<body>", 1)[1].rsplit("</body>", 1)[0]
        + '</div>',
        unsafe_allow_html=True,
    )

    # Download button — saves the HTML which prints nicely from any browser
    st.download_button(
        "⬇️ Download printable HTML (Ctrl+P to save as PDF)",
        data=printable_html,
        file_name=f"comp-shopper-{prop_name.lower().replace(' ', '-')}.html",
        mime="text/html",
        help="Opens cleanly in any browser. Use browser's Print → Save as PDF for a printable card.",
    )
