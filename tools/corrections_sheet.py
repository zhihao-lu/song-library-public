#!/usr/bin/env python3
"""Today's corrections as the rows of the three-source library's corrections Sheet.

The three-source library (LIBRARY_MODE=sources) reads its corrections from one Google Sheet,
"_corrections" in the '.cho Files' folder. This writes a CSV to import into that Sheet, with
today's fixes carried over unchanged:

  _original_keys.csv   each song's original key       -> id = its slug, original_key
  _exclude.txt         files never to take in          -> id = the file's path, leave_out = yes

Usage (reads the mirror's local copy of the '.cho Files' folder by default):
  .venv/bin/python tools/corrections_sheet.py [--from data/drive-library] [--out ~/Downloads/_corrections.csv]

Then, in Google Drive: open '.cho Files', New -> Google Sheets, name it _corrections, and
File -> Import -> Upload the CSV -> "Replace current sheet". See docs/corrections.md.
"""

import argparse
import csv
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COLUMNS = ["id", "title", "artist", "original_key", "label", "same_as", "different_song", "leave_out", "note"]


def rows_from(original_keys_csv="", exclude_txt=""):
    """The Sheet's rows for today's original keys (CSV text) and exclude list (text)."""
    rows = []
    for r in csv.DictReader(io.StringIO(original_keys_csv.lstrip("﻿"))):
        if (r.get("slug") or "").strip() and (r.get("original_key") or "").strip():
            rows.append({"id": r["slug"].strip(), "original_key": r["original_key"].strip(),
                         "note": (r.get("source") or "").strip()})
    note = ""
    for line in exclude_txt.splitlines():
        line = line.strip()
        if line.startswith("#"):
            note = line.lstrip("# ").strip()
        elif line:
            rows.append({"id": line, "leave_out": "yes", "note": note})
        else:
            note = ""
    return rows


def to_csv(rows):
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=COLUMNS, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return out.getvalue()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="src", type=Path, default=ROOT / "data" / "drive-library",
                    help="folder holding _original_keys.csv and _exclude.txt")
    ap.add_argument("--out", type=Path, default=Path.home() / "Downloads" / "_corrections.csv")
    args = ap.parse_args()
    keys, exclude = args.src / "_original_keys.csv", args.src / "_exclude.txt"
    if not keys.exists() and not exclude.exists():
        sys.exit(f"Neither _original_keys.csv nor _exclude.txt is in {args.src}")
    rows = rows_from(keys.read_text(encoding="utf-8-sig") if keys.exists() else "",
                     exclude.read_text(encoding="utf-8") if exclude.exists() else "")
    args.out.write_text(to_csv(rows), encoding="utf-8")
    print(f"Wrote {len(rows)} rows to {args.out}")


if __name__ == "__main__":
    main()
