#!/usr/bin/env python3
"""Flask UI for the HCM2 Student File Assembler.

File naming convention: lastName_starsId_SECTION_fileName.ext
Roster is Drive-primary; persisted settings live in a JSON store.
"""

from __future__ import annotations

import os
import traceback
from dataclasses import asdict
from pathlib import Path
from urllib.parse import unquote

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_from_directory

import store
from assembler import assemble_one, check_existing_output, write_report
from checklist import CHECKLIST, checklist_by_key, section_filename_token
from drive import drive_file_link, get_drive_service, list_children, upsert_file
from suggest import suggest as suggest_section, validate_file

load_dotenv()

PROJECT_ROOT = Path(__file__).parent


def _decode_folder_name(name: str) -> str:
    """Decode URL-encoded folder names (Vercel may not decode path segments)."""
    return unquote(name)


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(PROJECT_ROOT / "templates"),
        static_folder=str(PROJECT_ROOT / "static"),
    )

    from auth import register_auth
    register_auth(app)
    return app


app = create_app()


def _resolve_output_dir() -> Path:
    raw = os.getenv("OUTPUT_DIR", "").strip()
    if not raw:
        raw = "/tmp/hcm2-output" if os.getenv("VERCEL") else "output"
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


CONFIG = {
    "drive_folder_id": os.getenv("GDRIVE_ROOT_FOLDER_ID", ""),
    "output_folder_id": os.getenv("GDRIVE_OUTPUT_FOLDER_ID", "").strip(),
    "output_dir": _resolve_output_dir(),
}


def _scan_folder_files(files: list[dict], student: dict) -> tuple[dict[str, int], int]:
    """Count valid files per section and invalid filenames in a Drive folder."""
    section_counts: dict[str, int] = {}
    invalid = 0
    last_name = student.get("last_name") or ""
    stars_id = student.get("stars_id") or ""

    for f in files:
        if f.get("mimeType") == "application/vnd.google-apps.folder":
            continue
        if f["name"].startswith("."):
            continue

        section = suggest_section(f["name"])
        if section is None:
            invalid += 1
            continue

        if last_name or stars_id:
            errs = validate_file(
                f["name"],
                expected_last_name=last_name or None,
                expected_stars_id=stars_id or None,
            )
            if errs:
                invalid += 1
                continue

        section_counts[section] = section_counts.get(section, 0) + 1

    return section_counts, invalid


def _student_compulsory_stats(row: dict, compulsory: list[str]) -> dict:
    """Derive compulsory coverage from synced section counts."""
    counts = row.get("section_counts") or {}
    missing = []
    present = 0
    for key in compulsory:
        if counts.get(key, 0) > 0:
            present += 1
        else:
            ck = checklist_by_key().get(key)
            missing.append({"key": key, "name": ck[1] if ck else key})

    return {
        "compulsory_count": len(compulsory),
        "compulsory_present": present,
        "missing_docs": missing,
        "total_files": sum(counts.values()),
    }


def _coverage(compulsory: list[str]) -> dict:
    """Compute section coverage metadata for a student."""
    cklist = checklist_by_key()
    compulsory_set = set(compulsory)

    sections_info = []
    for key, name, _s in CHECKLIST:
        sections_info.append({
            "key": key,
            "name": name,
            "compulsory": key in compulsory_set,
        })

    return {
        "section_count": len(sections_info),
        "sections": sections_info,
    }


def _drive_folder_link(folder_id: str | None) -> str | None:
    if not folder_id:
        return None
    return f"https://drive.google.com/drive/folders/{folder_id}"


def _list_drive_folders(drive) -> list[dict]:
    folders = list_children(drive, CONFIG["drive_folder_id"], only_folders=True)
    output_id = CONFIG["output_folder_id"]
    if output_id:
        folders = [f for f in folders if f["id"] != output_id]
    return sorted(folders, key=lambda f: f["name"].lower())


def _student_defaults(folder_name: str, drive_folder_id: str | None = None) -> dict:
    return {
        "folder_name": folder_name,
        "last_name": "",
        "stars_id": "",
        "na_documents": set(),
        "section_counts": {},
        "invalid_file_count": 0,
        "drive_folder_id": drive_folder_id,
        "drive_file_id": None,
        "drive_link": None,
    }


def _merge_drive_folder(folder: dict, stored: dict | None) -> dict:
    base = _student_defaults(folder["name"], folder["id"])
    if not stored:
        return base
    return {
        **base,
        "last_name": stored.get("last_name", ""),
        "stars_id": stored.get("stars_id", ""),
        "na_documents": stored.get("na_documents") or set(),
        "section_counts": dict(stored.get("section_counts") or {}),
        "invalid_file_count": int(stored.get("invalid_file_count") or 0),
        "drive_folder_id": folder["id"],
        "drive_file_id": stored.get("drive_file_id"),
        "drive_link": stored.get("drive_link") or drive_file_link(stored.get("drive_file_id")),
    }


def _serialize_student(row: dict, compulsory: list[str]) -> dict:
    existing, output_filename = check_existing_output(
        CONFIG["output_dir"], row["folder_name"]
    )
    coverage = _coverage(compulsory)
    comp_stats = _student_compulsory_stats(row, compulsory)
    drive_folder_id = row.get("drive_folder_id")

    return {
        "folder_name": row["folder_name"],
        "last_name": row.get("last_name", ""),
        "stars_id": row.get("stars_id", ""),
        "na_documents": sorted(row.get("na_documents") or set()),
        "section_counts": row.get("section_counts") or {},
        "invalid_file_count": row.get("invalid_file_count") or 0,
        "existing_output": existing,
        "output_filename": output_filename,
        "drive_folder_id": drive_folder_id,
        "drive_folder_link": _drive_folder_link(drive_folder_id),
        "drive_link": row.get("drive_link") or drive_file_link(row.get("drive_file_id")),
        **coverage,
        **comp_stats,
    }


def _students_from_drive(drive) -> list[dict]:
    folders = _list_drive_folders(drive)
    compulsory = store.get_compulsory()
    out = []
    for folder in folders:
        stored = store.get_student(folder["name"])
        row = _merge_drive_folder(folder, stored)
        out.append(_serialize_student(row, compulsory))
    return out


def _run_sync(drive) -> dict:
    folders = _list_drive_folders(drive)
    folder_names = [f["name"] for f in folders]
    summary = store.sync_with_drive(folder_names)

    errors: list[str] = []
    for idx, folder in enumerate(folders, 1):
        stored = store.get_student(folder["name"])
        student = stored or _student_defaults(folder["name"], folder["id"])
        try:
            files = list_children(drive, folder["id"])
        except Exception as exc:
            errors.append(f"{folder['name']}: {exc}")
            continue

        section_counts, invalid = _scan_folder_files(files, student)
        store.upsert(
            folder["name"],
            drive_folder_id=folder["id"],
            section_counts=section_counts,
            invalid_file_count=invalid,
        )

    students = _students_from_drive(drive)
    return {
        "total": len(folders),
        "scanned": len(folders) - len(errors),
        "added": len(summary["added"]),
        "stale": summary["stale"],
        "errors": errors,
        "students": students,
    }


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/config")
def api_config():
    return jsonify({
        "drive_folder_id": CONFIG["drive_folder_id"],
        "output_folder_id": CONFIG["output_folder_id"],
        "output_dir": str(CONFIG["output_dir"]),
        "drive_configured": bool(CONFIG["drive_folder_id"]),
        "upload_enabled": bool(CONFIG["output_folder_id"]),
    })


@app.get("/api/checklist")
def api_checklist():
    compulsory = set(store.get_compulsory())
    return jsonify([
        {
            "key": key,
            "number": idx,
            "name": name,
            "sub_docs": sub_docs,
            "filename_token": section_filename_token(key),
            "compulsory": key in compulsory,
        }
        for idx, (key, name, sub_docs) in enumerate(CHECKLIST, 1)
    ])


@app.get("/api/students")
def api_students():
    if not CONFIG["drive_folder_id"]:
        return jsonify({"error": "GDRIVE_ROOT_FOLDER_ID not set in .env"}), 400

    refresh = request.args.get("refresh") == "1"
    try:
        drive = get_drive_service()
    except Exception as exc:
        return jsonify({"error": f"Drive auth failed: {exc}"}), 500

    if refresh:
        result = _run_sync(drive)
        return jsonify(result["students"])

    return jsonify(_students_from_drive(drive))


@app.patch("/api/students/<path:folder_name>")
def api_update_student(folder_name: str):
    folder_name = _decode_folder_name(folder_name)
    body = request.get_json(force=True, silent=True) or {}

    try:
        updated = store.upsert(
            folder_name,
            last_name=body.get("last_name"),
            stars_id=body.get("stars_id"),
            na_documents=body.get("na_documents"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    compulsory = store.get_compulsory()
    row = _merge_drive_folder(
        {"name": folder_name, "id": updated.get("drive_folder_id")},
        updated,
    )
    return jsonify(_serialize_student(row, compulsory))


@app.delete("/api/students/<path:folder_name>")
def api_delete_student(folder_name: str):
    folder_name = _decode_folder_name(folder_name)
    ok = store.delete(folder_name)
    return jsonify({"deleted": ok})


@app.get("/api/compulsory")
def api_get_compulsory():
    return jsonify(store.get_compulsory())


@app.post("/api/compulsory")
def api_set_compulsory():
    body = request.get_json(force=True, silent=True) or {}
    sections = body.get("sections", [])
    result = store.set_compulsory(sections)
    return jsonify(result)


@app.post("/api/sync")
def api_sync():
    if not CONFIG["drive_folder_id"]:
        return jsonify({"error": "GDRIVE_ROOT_FOLDER_ID not set in .env"}), 400

    try:
        drive = get_drive_service()
        result = _run_sync(drive)
        return jsonify(result)
    except Exception as exc:
        tb = traceback.format_exc()
        print(tb, flush=True)
        return jsonify({"error": str(exc)}), 500


@app.post("/api/run/<path:folder_name>")
def api_run_one(folder_name: str):
    folder_name = _decode_folder_name(folder_name)
    if not CONFIG["drive_folder_id"]:
        return jsonify({"error": "GDRIVE_ROOT_FOLDER_ID not set in .env"}), 400

    log_lines: list[str] = []

    def log_line(line: str) -> None:
        log_lines.append(line)
        print(line, flush=True)

    student = store.get_student(folder_name) or {"folder_name": folder_name}
    compulsory = store.get_compulsory()

    try:
        drive = get_drive_service()
        log_line("Authenticating with Google Drive...")
        log_line("Authenticated.")

        result = assemble_one(
            drive,
            CONFIG["drive_folder_id"],
            student,
            CONFIG["output_dir"],
            force=True,
            upload_folder_id=CONFIG["output_folder_id"] or None,
            compulsory_sections=compulsory,
            log=log_line,
        )

        if result.drive_file_id or result.drive_link:
            store.upsert(
                result.folder_name,
                drive_file_id=result.drive_file_id,
                drive_link=result.drive_link or drive_file_link(result.drive_file_id),
            )

        report_rows = [asdict(r) for r in result.report_rows]
        return jsonify({
            "folder_name": result.folder_name,
            "status": result.status,
            "drive_link": result.drive_link,
            "output": result.output_path.name if result.output_path else None,
            "log": log_lines,
            "messages": result.messages,
            "report_rows": report_rows,
        })
    except Exception as exc:
        tb = traceback.format_exc()
        log_line(f"ERROR: {exc}")
        log_line(tb)
        return jsonify({
            "folder_name": folder_name,
            "status": "ERROR",
            "drive_link": None,
            "output": None,
            "log": log_lines,
            "messages": [str(exc)],
            "report_rows": [],
        }), 500


@app.post("/api/report")
def api_report():
    body = request.get_json(force=True, silent=True) or {}
    raw_rows = body.get("rows") or []
    if not raw_rows:
        return jsonify({"error": "No report rows provided"}), 400

    from assembler import ReportRow
    rows = [
        ReportRow(
            student_folder=r.get("student_folder", ""),
            section_key=r.get("section_key", ""),
            section_name=r.get("section_name", ""),
            status=r.get("status", ""),
            notes=r.get("notes", ""),
        )
        for r in raw_rows
    ]

    report_path = write_report(rows, CONFIG["output_dir"])
    report_drive_link = None
    upload_folder = CONFIG["output_folder_id"]
    if upload_folder:
        try:
            drive = get_drive_service()
            upload = upsert_file(
                drive, upload_folder, report_path.name,
                report_path.read_bytes(), mime_type="text/csv",
            )
            report_drive_link = upload.get("webViewLink")
        except Exception as exc:
            print(f"Report Drive upload FAILED: {exc}", flush=True)

    return jsonify({
        "report_path": str(report_path),
        "report_filename": report_path.name,
        "report_drive_link": report_drive_link,
    })


@app.get("/output/<path:filename>")
def serve_output(filename: str):
    return send_from_directory(CONFIG["output_dir"], filename, as_attachment=False)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5055"))
    app.run(host="127.0.0.1", port=port, debug=False)
