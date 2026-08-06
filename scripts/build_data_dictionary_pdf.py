"""Generate docs/DATA-DICTIONARY.pdf — the owner-facing field-governance doc.

Owner ask 2026-08-07: the data dictionary (and the per-user edit behavior it
governs) as a PDF in the workbench folder, updated whenever the dictionary
changes. The PDF is GENERATED from ``core/field_policy.py`` (the machine
source), so re-tiering a field and re-running this script can never drift
from what the app enforces. ``tests/test_data_dictionary_pdf.py`` pins a
content hash embedded in the PDF metadata — if the policy or the prose here
changes without a rebuild, the suite goes red.

Rebuild (reportlab is a build-time tool, not an app dependency):

    uv run --with reportlab python scripts/build_data_dictionary_pdf.py
"""

from __future__ import annotations

import datetime as dt
import hashlib
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from core import field_policy  # noqa: E402
import config  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent.parent / "docs" / "DATA-DICTIONARY.pdf"

GOLD = config.COLORS.get("ac", "#C8900A")
DARK = "#1E1E24"

# Display labels + auto-source per card field (mirrors the Property Card).
CARD_FIELDS: list[tuple[str, str, str]] = [
    ("units",              "Units",            "Rent roll → T-12 → OM → county record"),
    ("year_built",         "Year Built",       "County assessor record"),
    ("last_remodel",       "Last Remodel",     "Deal documents"),
    ("asset_class",        "Class",            "Deal documents / analyst"),
    ("property_type",      "Type",             "Deal documents / analyst (dropdown)"),
    ("market",             "Market",           "City + Census / public data (auto)"),
    ("submarket",          "Submarket",        "City + Census / public data (auto)"),
    ("occupancy_pct",      "Occupancy",        "Rent roll → T-12 → OM"),
    ("avg_sqft",           "Avg Sqft",         "Rent roll → OM"),
    ("avg_rent",           "Avg Rent",         "Rent roll → listings → HUD-FMR blend"),
    ("rent_per_sqft",      "Rent / Sqft",      "Computed: Avg Rent ÷ Avg Sqft"),
    ("owner",              "Owner",            "County record (public)"),
    ("manager",            "Manager (person)", "Analyst"),
    ("management_company", "Mgmt Company",     "Analyst"),
    ("pm_software",        "PM Software",      "Analyst (dropdown)"),
    ("asset_or_fee",       "Asset/Fee",        "Analyst"),
]

TIER_LABELS = {
    field_policy.TIER_REFERENCE: ("Enterprise (locked)", "#8a8a8a"),
    field_policy.TIER_ORG:       ("Organization",        "#2e6da4"),
    field_policy.TIER_USER:      ("You (personal)",      GOLD),
}


def content_hash() -> str:
    """Hash of everything that should force a PDF rebuild when it changes:
    the field tiers and this script's own prose/structure."""
    h = hashlib.sha256()
    for k in sorted(field_policy.FIELD_TIERS):
        h.update(f"{k}={field_policy.FIELD_TIERS[k]};".encode())
    h.update(pathlib.Path(__file__).read_bytes())
    return h.hexdigest()[:16]


def build() -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=10,
                          leading=14.5, spaceAfter=8)
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=15,
                        textColor=colors.HexColor(DARK), spaceBefore=14,
                        spaceAfter=6)
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8,
                           textColor=colors.HexColor("#888888"))

    def header(canvas, doc):
        canvas.saveState()
        w, hpage = letter
        canvas.setFillColor(colors.HexColor(DARK))
        canvas.rect(0, hpage - 0.85 * inch, w, 0.85 * inch, fill=1, stroke=0)
        canvas.setFont("Helvetica-Bold", 15)
        canvas.setFillColor(colors.HexColor(GOLD))
        canvas.drawString(0.75 * inch, hpage - 0.55 * inch, "EIGHT ROCK")
        canvas.setFont("Helvetica", 15)
        canvas.setFillColor(colors.white)
        canvas.drawString(1.98 * inch, hpage - 0.55 * inch, "WORKBENCH")
        canvas.setFillColor(colors.HexColor(GOLD))
        canvas.rect(0, hpage - 0.88 * inch, w, 0.03 * inch, fill=1, stroke=0)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#888888"))
        canvas.drawRightString(w - 0.75 * inch, 0.5 * inch,
                               f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        str(OUT), pagesize=letter,
        topMargin=1.15 * inch, bottomMargin=0.8 * inch,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        title="Eight Rock Workbench — Data Dictionary & Field Governance",
        author="Eight Rock Workbench",
        subject=f"policy-hash:{content_hash()}",
    )

    story = []
    story.append(Paragraph("Data Dictionary &amp; Field Governance", h1))
    story.append(Paragraph(
        f"Version {config.WORKBENCH_VERSION} &middot; generated "
        f"{dt.date.today().isoformat()} &middot; this document is produced "
        "from the same policy file the app enforces — it cannot drift from "
        "the product.", small))
    story.append(Spacer(1, 10))

    # ---- Plain-English: what happens when you edit a property ----
    story.append(Paragraph("Your edits are yours — how property editing works", h1))
    for para in (
        "Every property starts from the <b>platform record</b>: county "
        "assessor data, municipal records, and your uploaded deal documents "
        "(rent roll, T-12, OM). Nobody edits that record directly.",

        "When you change a field on the Property Card (say Units, or Avg "
        "Rent), your change is saved <b>to your own profile</b> — a personal "
        "overlay on top of the platform record. <b>You see your values; "
        "your colleagues do not.</b> Their view of the same property is "
        "untouched, and their edits are equally invisible to you. Your "
        "assumptions stay yours until you choose to share them.",

        "One transition detail: edits made <i>before</i> this feature "
        "existed live in the deal folder and are treated as shared starting "
        "values — everyone still sees those. The first time you save your "
        "own edit on a property, your personal copy takes over (for you "
        "only) from that point on. Pressing <b>Reset</b> clears your "
        "personal values and returns you to the automatic ones.",

        "Not every field is editable. Fields are governed in three tiers, "
        "defined below, and the tier decides who can change a value and who "
        "sees the change.",
    ):
        story.append(Paragraph(para, body))

    # ---- Tiers ----
    story.append(Paragraph("The three governance tiers", h1))
    cell = ParagraphStyle("cell", parent=styles["Normal"], fontSize=8.5,
                          leading=11)
    cellb = ParagraphStyle("cellb", parent=cell, fontName="Helvetica-Bold",
                           textColor=colors.white)

    def _row(vals, style=cell):
        return [Paragraph(v, style) for v in vals]

    tier_rows = [_row(["Tier", "Who edits", "Who sees the change",
                       "Where it lives"], cellb)]
    tier_rows += [
        _row(["Enterprise (locked)",
              "Nobody — corrections flow through the data pipeline",
              "Everyone (identical for all)",
              "Platform reference layer, rebuilt nightly"]),
        _row(["Organization",
              "Org roles, per role preset (none assigned yet in v1)",
              "Everyone in your organization",
              "Org workspace (secured per org)"]),
        _row(["You (personal)",
              "You",
              "Only you",
              "Your profile (secured per user)"]),
    ]
    t = Table(tier_rows, colWidths=[1.45 * inch, 2.1 * inch, 1.7 * inch, 1.75 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(DARK)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f6f6f6")]),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(t)
    story.append(Spacer(1, 6))

    # ---- Card fields (generated from the live policy) ----
    story.append(Paragraph("Property Card fields", h1))
    rows = [["Field", "Tier", "Automatic value comes from"]]
    for key, label, source in CARD_FIELDS:
        tier_label, _ = TIER_LABELS[field_policy.tier_of(key)]
        rows.append([label, tier_label, source])
    ft = Table(rows, colWidths=[1.55 * inch, 1.55 * inch, 3.9 * inch])
    ft.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(DARK)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f6f6f6")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(ft)
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "v1 stance: every hand-editable field is personal. No field is "
        "organization-shared yet — fields get promoted to the Organization "
        "tier deliberately, one decision at a time, when the owner calls it.",
        body))

    # ---- Reference layer + resolution order ----
    story.append(Paragraph("The platform reference layer (all locked)", h1))
    story.append(Paragraph(
        "Parcel identity (APN, FIPS), address, city/state/zip, coordinates, "
        "use code, building form, assessed value, owner of record, county "
        "sale history, and public market data (HUD, Census, HMDA, FRED). "
        "Global, identical for every organization, read-only, refreshed "
        "nightly by the platform. Disagree with a value? Override it at "
        "your tier — the platform record itself is never edited by hand.",
        body))

    story.append(Paragraph("What you see for any field (resolution order)", h1))
    for i, line in enumerate((
        "1 &nbsp; Your own saved value for that field (if you ever saved one).",
        "2 &nbsp; The shared pre-existing folder value (edits made before "
        "per-user profiles, and single-user installs).",
        "3 &nbsp; The automatic value: rent roll → T-12 → OM → platform record.",
    )):
        story.append(Paragraph(line, body))

    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", color=colors.HexColor(GOLD),
                            thickness=1.5))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "Maintained automatically: this PDF is rebuilt whenever the field "
        "policy changes (a test fails if it goes stale). Source of truth: "
        "core/field_policy.py &middot; docs/DATA-DICTIONARY.md &middot; "
        f"policy hash {content_hash()}", small))

    doc.build(story, onFirstPage=header, onLaterPages=header)
    print(f"wrote {OUT}  (policy hash {content_hash()})")


if __name__ == "__main__":
    build()
