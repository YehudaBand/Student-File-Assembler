"""Core assembly pipeline for HCM2 audit.

Simplified flow: list files in the student's Drive folder, auto-map by
filename convention (lastName_starsId_SECTION_fileName), group by section
in checklist order, merge all valid PDFs into one output with cover pages.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from googleapiclient.errors import HttpError

from checklist import CHECKLIST, checklist_by_key
from coverpage import cover_page
from drive import download_file, drive_file_link, find_folder_by_name, list_children, upsert_file
from normalize import merge_pdfs, normalize_to_pdf
from suggest import suggest as suggest_section


@dataclass
class ReportRow:
    student_folder: str
    section_key: str
    section_name: str
    status: str  # PRESENT | MISSING | OPTIONAL | INVALID
    notes: str = ""


@dataclass
class StudentResult:
    folder_name: str
    status: str  # OK | INCOMPLETE | ERROR
    output_path: Path | None = None
    drive_link: str | None = None
    drive_file_id: str | None = None
    report_rows: list[ReportRow] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)


def _files_by_section_auto(files: list[dict]) -> tuple[dict[str, list[dict]], list[dict]]:
    """Map files to sections by parsing structured filenames."""
    by_section: dict[str, list[dict]] = {}
    unrecognized: list[dict] = []
    for f in files:
        if f.get("mimeType") == "application/vnd.google-apps.folder":
            continue
        if f["name"].startswith("."):
            continue
        section = suggest_section(f["name"])
        if section is None:
            unrecognized.append(f)
            continue
        by_section.setdefault(section, []).append(f)

    for section in by_section:
        by_section[section].sort(key=lambda f: f["name"].lower())

    return by_section, unrecognized


def assemble_one(
    drive_service,
    root_folder_id: str,
    student: dict,
    output_dir: Path,
    *,
    force: bool = True,
    upload_folder_id: str | None = None,
    compulsory_sections: list[str] | None = None,
    log=print,
) -> StudentResult:
    folder_name = student["folder_name"]
    na_sections = student.get("na_documents") or set()

    result = StudentResult(folder_name=folder_name, status="OK")
    cklist = checklist_by_key()
    compulsory = set(compulsory_sections or [])

    folder = find_folder_by_name(drive_service, root_folder_id, folder_name)
    if folder is None:
        for key, name, _s in CHECKLIST:
            result.report_rows.append(ReportRow(
                folder_name, key, name, "MISSING", "Folder not found in Drive"
            ))
        result.status = "ERROR"
        result.messages.append(f"Folder not found in Drive: {folder_name}")
        log(f"  [{folder_name}] ERROR: folder not found in Drive")
        return result

    try:
        files = list_children(drive_service, folder["id"])
    except HttpError as exc:
        status = getattr(exc.resp, "status", None)
        if status in (401, 403):
            raise
        result.status = "ERROR"
        result.messages.append(f"Drive error listing folder: {exc}")
        log(f"  [{folder_name}] ERROR listing folder: {exc}")
        return result

    by_section, unrecognized = _files_by_section_auto(files)

    # Always remove previous output (force override)
    expected_output = _output_path(output_dir, folder_name, incomplete=False)
    incomplete_output = _output_path(output_dir, folder_name, incomplete=True)
    expected_output.unlink(missing_ok=True)
    incomplete_output.unlink(missing_ok=True)

    missing_count = 0
    compulsory_missing = False
    pdf_parts: list[tuple[str, bytes]] = []

    for section_idx, (key, human_name, _sub_docs) in enumerate(CHECKLIST, 1):
        if key in na_sections:
            result.report_rows.append(ReportRow(
                folder_name, key, human_name, "N/A", "Marked N/A"
            ))
            continue

        sources = by_section.get(key, [])
        if not sources:
            if key in compulsory:
                result.report_rows.append(ReportRow(
                    folder_name, key, human_name, "MISSING", "Compulsory section"
                ))
                compulsory_missing = True
                log(f"  [{folder_name}] MISSING compulsory {key} — {human_name}")
            else:
                result.report_rows.append(ReportRow(
                    folder_name, key, human_name, "OPTIONAL", "No files"
                ))
            continue

        pdf_parts.append((
            f"cover:{section_idx}. {human_name}",
            cover_page(f"{section_idx}. {human_name}"),
        ))

        slot_ok = False
        slot_filenames = []
        for source in sources:
            try:
                raw = download_file(drive_service, source["id"], source.get("mimeType", ""))
            except HttpError as exc:
                status = getattr(exc.resp, "status", None)
                if status in (401, 403):
                    raise
                log(f"  [{folder_name}] download error for {source['name']}: {exc}")
                continue

            try:
                pdf_bytes = normalize_to_pdf(raw, source["name"], source.get("mimeType", ""))
            except ValueError as exc:
                log(f"  [{folder_name}] normalize error for {source['name']}: {exc}")
                continue

            pdf_parts.append((f"{key} {source['name']}", pdf_bytes))
            slot_filenames.append(source["name"])
            slot_ok = True

        if slot_ok:
            result.report_rows.append(ReportRow(
                folder_name, key, human_name, "PRESENT",
                "; ".join(slot_filenames)
            ))
        else:
            result.report_rows.append(ReportRow(
                folder_name, key, human_name, "MISSING",
                "Files failed to normalize"
            ))
            missing_count += 1
            if key in compulsory:
                compulsory_missing = True

    for bad in unrecognized:
        if bad["name"].startswith("."):
            continue
        result.report_rows.append(ReportRow(
            folder_name, "--", "(invalid filename)", "INVALID", bad["name"]
        ))
        log(f"  [{folder_name}] invalid filename: {bad['name']}")

    if not by_section:
        result.status = "ERROR"
        result.messages.append(
            "No files matched the naming convention. "
            "Files must be named: lastName_starsId_SECTION_fileName"
        )
        log(f"  [{folder_name}] {result.messages[-1]}")
        return result

    merged, skipped_pdfs = merge_pdfs(pdf_parts, log=log)
    if skipped_pdfs:
        for label in skipped_pdfs:
            result.report_rows.append(ReportRow(
                folder_name, "--", "(corrupted PDF)",
                "INVALID", f"Could not merge: {label}"
            ))

    incomplete = compulsory_missing
    output_path = _output_path(output_dir, folder_name, incomplete=incomplete)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(merged)

    result.output_path = output_path
    result.status = "INCOMPLETE" if incomplete else "OK"
    log(f"  [{folder_name}] {result.status} -> {output_path.name}")

    if upload_folder_id:
        try:
            upload = upsert_file(drive_service, upload_folder_id,
                                 output_path.name, merged)
            result.drive_file_id = upload.get("id")
            result.drive_link = upload.get("webViewLink") or drive_file_link(result.drive_file_id)
            action = upload.get("action", "uploaded")
            log(f"  [{folder_name}] Drive: {action} {output_path.name} ({result.drive_link})")
        except Exception as exc:
            log(f"  [{folder_name}] Drive upload FAILED: {exc}")
            result.messages.append(f"Drive upload failed: {exc}")

    return result


def _output_path(output_dir: Path, folder_name: str, *, incomplete: bool) -> Path:
    suffix = "_INCOMPLETE" if incomplete else ""
    return output_dir / f"{folder_name}_HCM2_File{suffix}.pdf"


def write_report(rows: list[ReportRow], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"hcm2_report_{date.today().isoformat()}.csv"
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["student_folder", "section_key",
                         "section_name", "status", "notes"])
        for r in rows:
            writer.writerow([r.student_folder, r.section_key,
                             r.section_name, r.status, r.notes])
    return path


def check_existing_output(output_dir: Path, folder_name: str) -> tuple[str | None, str | None]:
    """Return (status, filename) for an assembled PDF if one exists on disk."""
    candidates = [
        ("OK", f"{folder_name}_HCM2_File.pdf"),
        ("OK", f"{folder_name}_SFA_File.pdf"),
        ("INCOMPLETE", f"{folder_name}_HCM2_File_INCOMPLETE.pdf"),
        ("INCOMPLETE", f"{folder_name}_SFA_File_INCOMPLETE.pdf"),
    ]
    for status, filename in candidates:
        if (output_dir / filename).exists():
            return status, filename
    return None, None
