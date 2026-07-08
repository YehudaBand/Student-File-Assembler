"""Google Drive access.

OAuth user-flow auth, adapted from ../stars_doc_upload/upload_to_stars.py.
The spec calls for a service account; we reuse the existing OAuth pattern here
so the audit team can use the same credentials.json they already have.
"""

from __future__ import annotations

import warnings
warnings.filterwarnings("ignore", category=FutureWarning, module="google")

import io
import json
import os
import sys
import time
from pathlib import Path

from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

# Upgraded from drive.readonly so the tool can upload assembled PDFs.
SCOPES = ["https://www.googleapis.com/auth/drive"]
PROJECT_ROOT = Path(__file__).parent
CREDENTIALS_FILE = PROJECT_ROOT / "credentials.json"
TOKEN_FILE = PROJECT_ROOT / "token.json"

GOOGLE_NATIVE_EXPORTS = {
    "application/vnd.google-apps.document": "application/pdf",
    "application/vnd.google-apps.spreadsheet": "application/pdf",
    "application/vnd.google-apps.presentation": "application/pdf",
    "application/vnd.google-apps.drawing": "application/pdf",
}


def _running_on_vercel() -> bool:
    return bool(os.getenv("VERCEL"))


def _load_creds_from_env() -> Credentials | None:
    """Load OAuth credentials from env (for serverless / CI)."""
    raw = os.getenv("GOOGLE_TOKEN_JSON", "").strip()
    if raw:
        info = json.loads(raw)
        return Credentials.from_authorized_user_info(info, SCOPES)

    refresh = os.getenv("GOOGLE_REFRESH_TOKEN", "").strip()
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    if refresh and client_id and client_secret:
        return Credentials(
            None,
            refresh_token=refresh,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=SCOPES,
        )
    return None


def get_drive_service():
    creds = _load_creds_from_env()
    if creds is None and TOKEN_FILE.exists():
        # Read the granted scopes straight from the token file — trusting
        # creds.scopes after from_authorized_user_file is unreliable because
        # that attribute reflects the requested scopes, not the granted ones.
        try:
            import json as _json
            with open(TOKEN_FILE) as f:
                granted = set(_json.load(f).get("scopes", []))
        except Exception:
            granted = set()

        if not set(SCOPES).issubset(granted):
            print(
                "Existing token has insufficient scopes — re-authenticating...",
                file=sys.stderr,
            )
            TOKEN_FILE.unlink()
        else:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(GoogleRequest())
        elif _running_on_vercel() or os.getenv("GOOGLE_REFRESH_TOKEN") or os.getenv("GOOGLE_TOKEN_JSON"):
            print(
                "Error: Google Drive credentials missing or invalid.\n"
                "Set GOOGLE_TOKEN_JSON or GOOGLE_REFRESH_TOKEN (+ client id/secret) in env.",
                file=sys.stderr,
            )
            sys.exit(1)
        else:
            if not CREDENTIALS_FILE.exists():
                print(
                    "Error: credentials.json not found.\n"
                    "Copy it from stars_doc_upload/ or download from Google Cloud Console.",
                    file=sys.stderr,
                )
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)

        if not _running_on_vercel():
            TOKEN_FILE.write_text(creds.to_json())

    return build("drive", "v3", credentials=creds)


def _retrying(fn, *, attempts: int = 5, base_delay: float = 1.0):
    """Call fn() with exponential backoff for 429/5xx; raise for other errors."""
    last_exc = None
    for i in range(attempts):
        try:
            return fn()
        except HttpError as exc:
            status = getattr(exc.resp, "status", None)
            if status == 401 or status == 403:
                raise
            if status == 429 or (status is not None and 500 <= int(status) < 600):
                last_exc = exc
                time.sleep(base_delay * (2 ** i))
                continue
            raise
    raise last_exc


def list_children(drive_service, parent_id: str, *, only_folders: bool = False) -> list[dict]:
    """List non-trashed children of a Drive folder."""
    query = f"'{parent_id}' in parents and trashed = false"
    if only_folders:
        query += " and mimeType = 'application/vnd.google-apps.folder'"

    results = []
    page_token = None
    while True:
        def call():
            return drive_service.files().list(
                q=query,
                fields="nextPageToken, files(id, name, mimeType, size)",
                pageSize=1000,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                corpora="allDrives",
            ).execute()

        resp = _retrying(call)
        results.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return results


def drive_file_link(file_id: str | None) -> str | None:
    if not file_id:
        return None
    return f"https://drive.google.com/file/d/{file_id}/view"


def find_folder_by_name(drive_service, parent_id: str, name: str) -> dict | None:
    children = list_children(drive_service, parent_id, only_folders=True)
    for child in children:
        if child["name"] == name:
            return child
    return None


def _escape_drive_name(name: str) -> str:
    return name.replace("\\", "\\\\").replace("'", "\\'")


def upsert_file(drive_service, folder_id: str, filename: str, data: bytes,
                mime_type: str = "application/pdf") -> dict:
    """Create or replace a file in `folder_id` whose name matches `filename`.

    If a non-trashed file with that exact name already exists in the folder,
    its content is updated in place (keeping the same file ID and share links).
    Otherwise a new file is created.

    Returns {"id", "webViewLink", "action"}.
    """
    safe_name = _escape_drive_name(filename)
    query = (
        f"'{folder_id}' in parents and name = '{safe_name}' and trashed = false"
    )

    def list_existing():
        return drive_service.files().list(
            q=query,
            fields="files(id, name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            corpora="allDrives",
        ).execute()

    result = _retrying(list_existing)
    existing = result.get("files", [])

    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime_type, resumable=False)

    if existing:
        file_id = existing[0]["id"]

        def do_update():
            return drive_service.files().update(
                fileId=file_id,
                media_body=media,
                fields="id, webViewLink",
                supportsAllDrives=True,
            ).execute()

        updated = _retrying(do_update)
        file_id = updated["id"]
        return {
            "id": file_id,
            "webViewLink": updated.get("webViewLink") or drive_file_link(file_id),
            "action": "updated",
        }

    def do_create():
        return drive_service.files().create(
            body={"name": filename, "parents": [folder_id]},
            media_body=media,
            fields="id, webViewLink",
            supportsAllDrives=True,
        ).execute()

    created = _retrying(do_create)
    file_id = created["id"]
    return {
        "id": file_id,
        "webViewLink": created.get("webViewLink") or drive_file_link(file_id),
        "action": "created",
    }


def download_file(drive_service, file_id: str, mime_type: str) -> bytes:
    buf = io.BytesIO()

    def make_request():
        if mime_type in GOOGLE_NATIVE_EXPORTS:
            return drive_service.files().export_media(
                fileId=file_id, mimeType=GOOGLE_NATIVE_EXPORTS[mime_type]
            )
        return drive_service.files().get_media(fileId=file_id, supportsAllDrives=True)

    def run():
        req = make_request()
        downloader = MediaIoBaseDownload(buf, req)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue()

    return _retrying(run, attempts=3, base_delay=2.0)
