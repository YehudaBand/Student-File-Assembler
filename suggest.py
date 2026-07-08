"""Structured filename parser for HCM2 audit.

Expected format: lastName_starsId_SECTION_anyFileName.ext
Example:         Smith_12345_ISIR_transaction_2024.pdf

The parser extracts the section key from the third underscore-delimited
segment. Matching is case-insensitive; the canonical PascalCase key is
returned. Files that don't follow this pattern are rejected.
"""

from __future__ import annotations

import re
from pathlib import Path

from checklist import SECTION_LOOKUP, section_filename_token

_STARS_RE = re.compile(r"^\d{5}$")


def parse_folder_name(folder_name: str) -> dict | None:
    """Student Drive folders must be named lastName_starsId (5-digit STARS ID)."""
    parts = folder_name.strip().split("_", 1)
    if len(parts) != 2:
        return None
    last_name, stars_id = parts[0].strip(), parts[1].strip()
    if not last_name or not _STARS_RE.match(stars_id):
        return None
    return {"last_name": last_name, "stars_id": stars_id}


def parse_filename(filename: str) -> dict | None:
    stem = Path(filename).stem
    parts = stem.split("_", 3)
    if len(parts) < 3:
        return None

    last_name = parts[0].strip()
    stars_id = parts[1].strip()
    raw_section = parts[2].strip()
    section = SECTION_LOOKUP.get(raw_section.lower())

    if not last_name or not _STARS_RE.match(stars_id) or section is None:
        return None

    rest = parts[3] if len(parts) > 3 else ""
    return {
        "last_name": last_name,
        "stars_id": stars_id,
        "section": section,
        "rest": rest,
    }


def suggest(filename: str) -> str | None:
    parsed = parse_filename(filename)
    return parsed["section"] if parsed else None


def validate_file(filename: str, expected_last_name: str | None = None,
                  expected_stars_id: str | None = None) -> list[str]:
    errors = []
    parsed = parse_filename(filename)
    if parsed is None:
        errors.append(
            f"Filename does not match required pattern: "
            f"lastName_starsId_SECTION_fileName (got: {filename})"
        )
        return errors

    if expected_last_name and parsed["last_name"].lower() != expected_last_name.lower():
        errors.append(
            f"Last name mismatch: expected '{expected_last_name}', "
            f"got '{parsed['last_name']}'"
        )

    if expected_stars_id and parsed["stars_id"] != expected_stars_id:
        errors.append(
            f"STARS ID mismatch: expected '{expected_stars_id}', "
            f"got '{parsed['stars_id']}'"
        )

    return errors


def example_filename(last_name: str, stars_id: str, section: str, rest: str = "document") -> str:
    token = section_filename_token(section)
    return f"{last_name}_{stars_id}_{token}_{rest}.pdf"
