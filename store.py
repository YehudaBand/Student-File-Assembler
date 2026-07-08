"""JSON-backed student roster store for HCM2 audit."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from checklist import SECTION_LOOKUP, VALID_SECTIONS
from suggest import parse_folder_name

STORE_PATH = Path(os.getenv("ROSTER_STORE_PATH", str(Path(__file__).parent / "roster.json")))
_LOCK = threading.Lock()


def _empty() -> dict:
    return {"compulsory_sections": [], "students": {}}


def load() -> dict:
    with _LOCK:
        env_seed = os.getenv("ROSTER_JSON", "").strip()
        if env_seed and not STORE_PATH.exists():
            STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
            STORE_PATH.write_text(env_seed)
        if not STORE_PATH.exists():
            return _empty()
        data = json.loads(STORE_PATH.read_text())
        data.setdefault("compulsory_sections", [])
        data.setdefault("students", {})
        return data


def save(data: dict) -> None:
    with _LOCK:
        STORE_PATH.write_text(json.dumps(data, indent=2))


def _materialize(folder_name: str, entry: dict) -> dict:
    return {
        "folder_name": folder_name,
        "last_name": entry.get("last_name", ""),
        "stars_id": entry.get("stars_id", ""),
        "na_documents": set(entry.get("na_documents", [])),
        "section_counts": dict(entry.get("section_counts") or {}),
        "invalid_file_count": int(entry.get("invalid_file_count") or 0),
        "drive_folder_id": entry.get("drive_folder_id"),
        "drive_file_id": entry.get("drive_file_id"),
        "drive_link": entry.get("drive_link"),
    }


def list_students() -> list[dict]:
    data = load()
    return [_materialize(name, entry) for name, entry in sorted(data["students"].items())]


def get_student(folder_name: str) -> dict | None:
    data = load()
    entry = data["students"].get(folder_name)
    return _materialize(folder_name, entry) if entry else None


def upsert(folder_name: str,
           last_name: str | None = None,
           stars_id: str | None = None,
           na_documents: list[str] | None = None,
           section_counts: dict | None = None,
           invalid_file_count: int | None = None,
           drive_folder_id: str | None = None,
           drive_file_id: str | None = None,
           drive_link: str | None = None) -> dict:

    data = load()
    entry = data["students"].get(folder_name, {
        "last_name": "",
        "stars_id": "",
        "na_documents": [],
        "section_counts": {},
        "invalid_file_count": 0,
    })

    if last_name is not None:
        entry["last_name"] = last_name.strip()
    if stars_id is not None:
        entry["stars_id"] = stars_id.strip()

    if na_documents is not None:
        normalized = []
        for s in na_documents:
            canonical = SECTION_LOOKUP.get(str(s).strip().lower())
            if canonical is not None:
                normalized.append(canonical)
        entry["na_documents"] = sorted(set(normalized))

    if section_counts is not None:
        cleaned = {}
        for section, count in section_counts.items():
            canonical = SECTION_LOOKUP.get(str(section).strip().lower())
            if canonical is None:
                continue
            try:
                n = int(count)
            except (TypeError, ValueError):
                continue
            if n > 0:
                cleaned[canonical] = n
        entry["section_counts"] = cleaned

    if invalid_file_count is not None:
        entry["invalid_file_count"] = max(0, int(invalid_file_count))

    if drive_folder_id is not None:
        entry["drive_folder_id"] = drive_folder_id
    if drive_file_id is not None:
        entry["drive_file_id"] = drive_file_id
    if drive_link is not None:
        entry["drive_link"] = drive_link

    data["students"][folder_name] = entry
    save(data)
    return _materialize(folder_name, entry)


def delete(folder_name: str) -> bool:
    data = load()
    if folder_name in data["students"]:
        del data["students"][folder_name]
        save(data)
        return True
    return False


def sync_with_drive(folder_names: list[str]) -> dict:
    data = load()
    added = []
    for name in folder_names:
        if name not in data["students"]:
            parsed = parse_folder_name(name) or {"last_name": "", "stars_id": ""}
            data["students"][name] = {
                "last_name": parsed["last_name"],
                "stars_id": parsed["stars_id"],
                "na_documents": [],
                "section_counts": {},
                "invalid_file_count": 0,
            }
            added.append(name)
    stale = sorted(set(data["students"]) - set(folder_names))
    save(data)
    return {"added": added, "stale": stale, "total": len(data["students"])}


def get_compulsory_sections() -> list[str]:
    data = load()
    return [s for s in data.get("compulsory_sections", []) if s in VALID_SECTIONS]


def set_compulsory_sections(sections: list[str]) -> list[str]:
    data = load()
    validated = []
    for s in sections:
        canonical = SECTION_LOOKUP.get(str(s).strip().lower())
        if canonical is not None:
            validated.append(canonical)
    data["compulsory_sections"] = sorted(set(validated))
    save(data)
    return data["compulsory_sections"]


get_compulsory = get_compulsory_sections
set_compulsory = set_compulsory_sections
