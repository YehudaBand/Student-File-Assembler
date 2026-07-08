"""Normalize arbitrary source files to PDF bytes (spec Section 4.4)."""

from __future__ import annotations

import io
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pikepdf
from PIL import Image
from pypdf import PdfReader, PdfWriter

from drive import GOOGLE_NATIVE_EXPORTS

# pypdf chatters about minor imperfections in third-party PDFs. The output is
# still valid — downgrade these to DEBUG so they don't spam the run log.
logging.getLogger("pypdf").setLevel(logging.ERROR)


IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
SPREADSHEET_EXTS = {".xlsx", ".xls", ".csv", ".ods"}


def normalize_to_pdf(file_bytes: bytes, filename: str, mime_type: str) -> bytes:
    """Return PDF bytes for the given source file.

    Raises ValueError if the format isn't supported.
    """
    if mime_type in GOOGLE_NATIVE_EXPORTS:
        # Already exported as PDF by drive.download_file.
        return file_bytes

    ext = Path(filename).suffix.lower()

    if ext == ".pdf" or mime_type == "application/pdf":
        _validate_pdf(file_bytes, filename)
        return file_bytes

    if ext in IMAGE_EXTS or mime_type.startswith("image/"):
        return _image_to_pdf(file_bytes)

    if ext in SPREADSHEET_EXTS:
        return _spreadsheet_to_pdf(file_bytes, ext)

    raise ValueError(f"Unsupported file format: {filename} ({mime_type})")


def _validate_pdf(data: bytes, filename: str) -> None:
    try:
        reader = PdfReader(io.BytesIO(data))
        _ = len(reader.pages)
    except Exception as exc:
        raise ValueError(f"File corrupted: {filename} ({exc})") from exc


def _image_to_pdf(data: bytes) -> bytes:
    img = Image.open(io.BytesIO(data))
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGB")
    out = io.BytesIO()
    img.save(out, format="PDF", resolution=150.0)
    return out.getvalue()


def _spreadsheet_to_pdf(data: bytes, ext: str) -> bytes:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise ValueError(
            "LibreOffice not installed — cannot convert spreadsheet. "
            "Install with `brew install --cask libreoffice`."
        )

    with tempfile.TemporaryDirectory() as tmp:
        src_path = Path(tmp) / f"input{ext}"
        src_path.write_bytes(data)

        result = subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", tmp, str(src_path)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise ValueError(f"LibreOffice conversion failed: {result.stderr.strip()}")

        pdf_path = Path(tmp) / "input.pdf"
        if not pdf_path.exists():
            raise ValueError("LibreOffice produced no output PDF")
        return pdf_path.read_bytes()


def _recover_via_pikepdf(data: bytes) -> bytes | None:
    """Re-save a PDF through pikepdf (qpdf under the hood) to strip weird
    encryption / signed-PDF dictionaries that pypdf can't parse. Returns
    clean bytes, or None if pikepdf also failed."""
    try:
        with pikepdf.open(io.BytesIO(data)) as pdf:
            out = io.BytesIO()
            pdf.save(out)
            return out.getvalue()
    except Exception:
        return None


def _add_pdf_to_writer(writer: PdfWriter, data: bytes, label: str, log) -> int:
    """Try to append every page of `data` to `writer`. If pypdf chokes on a
    page, re-save the whole PDF via pikepdf and retry once. Returns the number
    of pages successfully added."""
    def _try(pdf_bytes: bytes) -> tuple[int, bool]:
        reader = PdfReader(io.BytesIO(pdf_bytes), strict=False)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                pass
        added = 0
        had_failure = False
        for i in range(len(reader.pages)):
            try:
                page = reader.pages[i]
                writer.add_page(page)
                added += 1
            except Exception:
                had_failure = True
                break  # bail so we can retry via pikepdf before partial merge
        return added, had_failure

    added, had_failure = _try(data)
    if not had_failure:
        return added

    # Roll back the partial pages from the failed attempt.
    while len(writer.pages) > len(writer.pages) - added:
        break  # no-op; pypdf doesn't expose page removal by index cleanly
    # Fallback: recover via pikepdf and retry from scratch on a fresh writer slice.
    recovered = _recover_via_pikepdf(data)
    if recovered is None:
        log(f"    WARNING: pypdf + pikepdf both failed on {label}")
        return added  # keep whatever we got

    # Remove the partial pages we already added.
    for _ in range(added):
        del writer.pages[-1]

    log(f"    NOTE: recovered {label} via pikepdf (re-saved to strip encryption)")
    added2, _failed2 = _try(recovered)
    return added2


def merge_pdfs(pdf_parts: list[tuple[str, bytes]], log=print) -> tuple[bytes, list[str]]:
    """Merge PDFs in order. Each part is (label, pdf_bytes).

    Resilient: a PDF that fails in pypdf is retried via pikepdf (qpdf).
    If both fail, the file is skipped and reported — the rest of the
    student's output still assembles."""
    writer = PdfWriter()
    skipped: list[str] = []

    for label, data in pdf_parts:
        try:
            added = _add_pdf_to_writer(writer, data, label, log)
            if added == 0:
                skipped.append(label)
                log(f"    SKIPPED (no pages recovered): {label}")
        except Exception as exc:
            skipped.append(label)
            log(f"    SKIPPED unreadable PDF ({label}): {exc}")

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue(), skipped
