#!/usr/bin/env python3
"""CLI — reads the roster from roster.json (managed by the UI).

    python assemble.py                              # all students
    python assemble.py --student X                  # one
    python assemble.py --student X --student Y      # several
    python assemble.py --sync                       # refresh roster from Drive first
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

import store
from assembler import assemble_one, write_report
from drive import get_drive_service, list_children

load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(description="HCM2 Student File PDF Assembler")
    parser.add_argument("--drive-folder", default=os.getenv("GDRIVE_ROOT_FOLDER_ID"),
                        help="Root Drive folder ID (env: GDRIVE_ROOT_FOLDER_ID)")
    parser.add_argument("--output", default=os.getenv("OUTPUT_DIR", "./output/"),
                        help="Output directory")
    parser.add_argument("--student", action="append", default=[],
                        help="Process only this folder_name (repeatable)")
    parser.add_argument("--sync", action="store_true",
                        help="Refresh roster from Drive before running (adds new folders)")
    args = parser.parse_args()

    if not args.drive_folder:
        print("Error: --drive-folder (or GDRIVE_ROOT_FOLDER_ID) is required", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.output)

    print("Authenticating with Google Drive...")
    drive = get_drive_service()
    print("Authenticated.\n")

    if args.sync:
        folders = list_children(drive, args.drive_folder, only_folders=True)
        summary = store.sync_with_drive([f["name"] for f in folders])
        print(f"Synced {summary['total']} student(s); added {len(summary['added'])} new.")
        if summary["stale"]:
            print(f"  Stale (in roster, not in Drive): {', '.join(summary['stale'])}")

    roster = store.list_students()
    if not roster:
        print("Roster is empty. Run with --sync first, or add students via the UI.", file=sys.stderr)
        sys.exit(1)

    if args.student:
        wanted = set(args.student)
        targets = [r for r in roster if r["folder_name"] in wanted]
        missing = wanted - {r["folder_name"] for r in targets}
        for m in missing:
            print(f"Warning: student '{m}' not in roster — skipping", file=sys.stderr)
    else:
        targets = roster

    if not targets:
        print("No students to process.", file=sys.stderr)
        sys.exit(1)

    compulsory = store.get_compulsory()
    print(f"Processing {len(targets)} student(s) -> {output_dir.resolve()}\n")

    all_report_rows = []
    stats = {"OK": 0, "INCOMPLETE": 0, "ERROR": 0}

    for student in targets:
        result = assemble_one(
            drive, args.drive_folder, student, output_dir,
            force=True,
            compulsory_sections=compulsory,
        )
        all_report_rows.extend(result.report_rows)
        stats[result.status] = stats.get(result.status, 0) + 1

    report_path = write_report(all_report_rows, output_dir)

    print("\n--- Summary ---")
    for status, count in stats.items():
        print(f"  {status:12}  {count}")
    print(f"\nReport: {report_path}")


if __name__ == "__main__":
    main()
