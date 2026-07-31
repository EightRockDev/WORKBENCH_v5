"""Weekly market-calibration entry point.

Chained after the Monday ETL pull (`etl-weekly-monday`). Pulls the freshly-
refreshed FRED / property records / assessor data and rewrites every threshold's
`effective_value` in workbench.db.

Run manually with::

    cd python_workbench
    python scripts/recalibrate_thresholds.py

Or `--dry-run` to compute without persisting::

    python scripts/recalibrate_thresholds.py --dry-run
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

# Ensure `import core.calibration` works when run as a script
_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent.parent))

from core import calibration  # noqa: E402


def _fmt(t: calibration.Threshold) -> str:
    parts = [
        f"  {t.display_label:<32} {t.format_value():>10}",
        f"floor={calibration._format_for_units(t.floor_value, t.units):>10}",
    ]
    if t.market_value is not None:
        parts.append(
            f"market={calibration._format_for_units(t.market_value, t.units):>10}"
        )
    else:
        parts.append("market=        — ")
    parts.append(f"source={t.effective_source}")
    return "  ".join(parts)


def _summary_lines(thresholds: list[calibration.Threshold]) -> list[str]:
    lines: list[str] = []
    n_total = len(thresholds)
    n_market = sum(1 for t in thresholds if t.effective_source == "market")
    n_override = sum(1 for t in thresholds if t.effective_source == "override")
    n_floor = n_total - n_market - n_override

    lines.append(
        f"Calibrated {n_total} thresholds: "
        f"{n_market} market-widened · {n_override} overridden · {n_floor} at floor"
    )

    # Highlight the movement on the most-important bars
    headline_names = ("GO_CAP", "WATCH_CAP", "NOGO_CAP", "EXIT_CAP_DEFAULT",
                      "MIN_DEBT_YIELD", "VACANCY_DEFAULT")
    for name in headline_names:
        t = next((t for t in thresholds if t.name == name), None)
        if t is None:
            continue
        delta_30 = calibration.delta_bps_over(name, 30)
        delta_text = ""
        if delta_30 is not None and abs(delta_30) > 0.5:
            if t.units in ("pct", "ratio"):
                delta_text = f"  Δ30d {delta_30:+.0f}bp"
            elif t.units == "usd":
                delta_text = f"  Δ30d {delta_30:+,.0f}$"
        lines.append(_fmt(t) + delta_text)

    # Surface any thresholds where market wanted to compress (below floor)
    compress_attempts: list[str] = []
    for t in thresholds:
        if t.market_value is None or t.effective_source != "floor":
            continue
        if t.direction == "conservative_up" and t.market_value < t.floor_value:
            compress_attempts.append(
                f"  {t.display_label}: market suggested "
                f"{calibration._format_for_units(t.market_value, t.units)} "
                f"(< floor {calibration._format_for_units(t.floor_value, t.units)}); "
                f"held at floor"
            )
        elif t.direction == "conservative_down" and t.market_value > t.floor_value:
            compress_attempts.append(
                f"  {t.display_label}: market suggested "
                f"{calibration._format_for_units(t.market_value, t.units)} "
                f"(> floor {calibration._format_for_units(t.floor_value, t.units)}); "
                f"held at floor"
            )

    if compress_attempts:
        lines.append("")
        lines.append("Market wanted to compress below floor (held):")
        lines.extend(compress_attempts)

    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Compute without writing to calibration_current/history.",
    )
    args = parser.parse_args()

    start = dt.datetime.now()
    print(f"=== Recalibrate thresholds — {start.isoformat(timespec='seconds')} ===")
    print(f"Workbench DB: {calibration.WORKBENCH_DB_PATH}")
    print(f"ETL DB:       {calibration.ETL_DB_PATH}")
    print()

    if args.dry_run:
        proposed = calibration.compute_calibration()
        print(f"Computed {len(proposed)} candidate market values (dry-run, not persisted):")
        for c in proposed:
            val = (
                calibration._format_for_units(c.market_value, "pct")
                if c.market_value is not None else "—"
            )
            print(f"  {c.name:<32} {val:>10}  {c.market_source}")
        return 0

    applied = calibration.apply_calibration()
    print(f"Applied {len(applied)} thresholds to {calibration.WORKBENCH_DB_PATH}")
    print()

    for line in _summary_lines(applied):
        print(line)

    end = dt.datetime.now()
    print()
    print(f"=== Done in {(end - start).total_seconds():.1f}s ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
