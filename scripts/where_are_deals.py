"""Where is the app reading deal folders from, and do they carry sale history?

Replaces an inline-Python .bat that could not parse (2026-08-15). Multi-line
Python inside a batch file is not worth the escaping - a real script file is.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    from data.property_io import (PROPERTIES_ROOT, discover_property_folders,
                                  load_sales)

    print("=" * 64)
    print("DEAL FOLDERS")
    print("=" * 64)
    print(f"\nReading from: {PROPERTIES_ROOT}")
    print(f"  exists: {Path(PROPERTIES_ROOT).is_dir()}")

    folders = list(discover_property_folders())
    print(f"\n  deal folders found: {len(folders)}")
    if not folders:
        print("\n  NONE. The app is pointing at the wrong place. Set")
        print("  ER_PROPERTIES_ROOT to the folder that holds your property")
        print("  folders and restart.")
        return 1

    with_sales = []
    for f in folders:
        try:
            if load_sales(Path(f.path)):
                with_sales.append(f)
        except Exception:
            pass
    print(f"  with a saved sale history (sales.json): {len(with_sales)}")

    print("\n  first few folders:")
    for f in folders[:8]:
        has = "sale history" if f in with_sales else "no sales.json"
        print(f"    {f.folder_name:<44} {has}")

    if not with_sales:
        print("\n  Every folder was found, but NONE carries a saved sale")
        print("  history. That is why the card falls back to county records.")
        print("  If v2 showed verified sales here, those sales.json files")
        print("  are in a different copy of the Properties folder - search")
        print("  the machine for 'sales.json' and point ER_PROPERTIES_ROOT")
        print("  at the folder above the property folders that contain them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
