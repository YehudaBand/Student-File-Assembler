# Deploying HCM2 Student File Assembler

This document covers **recommended platforms** and **Vercel-specific steps** (if you insist).

---

## Executive summary

| Concern | Local dev | Vercel serverless | Railway / Render / Cloud Run |
|--------|-----------|-------------------|------------------------------|
| Background sync/run threads | Works | **Broken** (no shared memory across invocations) | Works |
| Long PDF jobs (many students) | Works | **Risky** (5 min max timeout on Pro) | Works |
| `roster.json` persistence | Works | **Ephemeral** unless external store | Works (volume / disk) |
| `output/` PDF links in UI | Works | **Ephemeral** (use Drive upload) | Works |
| Drive OAuth (`run_local_server`) | Works | **Cannot run** (use env token) | Works once, or env token |
| Google login for team | Not built-in | Scaffold in `auth.py` | Same scaffold |
| Cold start + pikepdf/reportlab | N/A | **Slow / large bundle** | Fine |

**Recommendation:** Deploy to **Railway**, **Render**, or **Google Cloud Run** with a persistent disk/volume. Use Vercel only for a **read-only demo** or after refactoring jobs to a queue + external storage.

---

## Part A — Recommended: Railway or Render (minimal code change)

### 1. Prerequisites

- Git repo containing `student_file_assembler/`
- Google Cloud project with Drive API enabled
- A shared Google account (or service account) that owns/has access to the HCM2 Drive folders
- One-time OAuth token with Drive scope (your existing `token.json`)

### 2. Google Cloud — Drive API credentials

1. Open [Google Cloud Console](https://console.cloud.google.com/) → your project.
2. **APIs & Services → Library** → enable **Google Drive API**.
3. **OAuth consent screen**
   - User type: **Internal** (if the GCP project is under your Masterschool Workspace) *or* External with test users.
   - App name: `HCM2 Student File Assembler`.
   - Scopes: add `.../auth/drive` (full Drive — already used by this app).
4. **Credentials → Create credentials → OAuth client ID**
   - Application type: **Desktop app** (same as today — works on Railway/Render with one-time auth locally).
   - Download JSON → save as `credentials.json` locally.
5. Run locally once to refresh `token.json`:
   ```bash
   cd student_file_assembler
   python -c "from drive import get_drive_service; get_drive_service()"
   ```
6. Copy `refresh_token` from `token.json` into platform secrets (see step 4 below).

### 3. Google Cloud — Workspace login (optional but recommended)

Use a **second** OAuth client (Web application) for who can open the UI:

1. **Credentials → Create → OAuth client ID → Web application**
2. Authorized redirect URIs:
   - `https://<your-domain>/auth/callback`
   - `http://127.0.0.1:5055/auth/callback` (local testing)
3. OAuth consent screen scopes for login:
   - `openid`, `email`, `profile` (no Drive scope needed on login client if Drive uses shared token).
4. Set **Internal** user type so only `@masterschool.com` Workspace users can consent.

The app enforces domain in `auth.py` via `GOOGLE_ALLOWED_DOMAIN` and the `hd=` OAuth hint.

### 4. Deploy on Railway (example)

1. [railway.app](https://railway.app) → New Project → Deploy from GitHub.
2. Root directory: `student_file_assembler` (or monorepo path).
3. Start command:
   ```bash
   gunicorn app:app --bind 0.0.0.0:$PORT --timeout 600 --workers 1
   ```
4. Add `gunicorn` to `requirements.txt`.
5. Environment variables:

   | Variable | Value |
   |----------|--------|
   | `GDRIVE_ROOT_FOLDER_ID` | Your Drive root folder ID |
   | `GDRIVE_OUTPUT_FOLDER_ID` | Upload target folder ID |
   | `GOOGLE_CLIENT_ID` | Web client ID (login + refresh) |
   | `GOOGLE_CLIENT_SECRET` | Web client secret |
   | `GOOGLE_REFRESH_TOKEN` | From `token.json` |
   | `GOOGLE_AUTH_ENABLED` | `1` |
   | `GOOGLE_ALLOWED_DOMAIN` | `masterschool.com` (substitute your Workspace domain) |
   | `APP_BASE_URL` | `https://<your-app>.up.railway.app` |
   | `FLASK_SECRET_KEY` | Random 32+ char string |
   | `OUTPUT_DIR` | `/data/output` (attach Railway volume) |
   | `ROSTER_STORE_PATH` | `/data/roster.json` (same volume) |

6. Attach a **Volume** mounted at `/data` so roster and output survive restarts.

### 5. Deploy on Cloud Run (Masterschool already on GCP)

Similar env vars; use `--timeout=3600`, `--memory=2Gi`, min instances `1` if you need warm background jobs. Mount Cloud Storage or Filestore for `/data`.

---

## Part B — Vercel (possible, with major caveats)

Scaffolding added in this repo:

- `vercel.json` — rewrites all traffic to `api/index.py` (300s max on Pro)
- `pyproject.toml` — pins Vercel entrypoint to `api.index:app` (avoids dual-detection with root `app.py`)
- `api/index.py` — WSGI entry
- `auth.py` — Google Workspace login blueprint
- `.env.example` — all env vars documented

### Refactored for serverless (no background threads)

Sync and Run are **synchronous per request** — safe for Vercel's 300s timeout when processing **one student at a time**.

| Endpoint | Method | Behavior |
|----------|--------|----------|
| `/api/students` | GET | Lists Drive subfolders + merges persisted settings (`na_documents`, `last_name`, `stars_id`, `drive_link`, cached `section_counts`). Optional `?refresh=1` runs full file scan inline. |
| `/api/sync` | POST | Synchronous: lists folders, scans file counts, returns `{total, scanned, added, errors, students}`. |
| `/api/run/<folder_name>` | POST | Synchronous: assembles one student, uploads to Drive, returns `{folder_name, status, drive_link, log, messages, report_rows}`. |
| `/api/report` | POST | Writes CSV from accumulated `report_rows` (frontend calls after batch run). |
| `/api/students/<folder>` | PATCH/DELETE | Persist settings only (N/A sections, names). Delete clears saved settings; folder stays in Drive list. |

**Removed:** `/api/run` (batch), `/api/status`, `/api/sync/status` — no polling.

### Remaining caveats on Vercel

1. **Roster settings** — default `/tmp/roster.json` is wiped between cold starts unless you use Vercel Blob/KV/Postgres or seed via `ROSTER_JSON`.
2. **Local output links** (`/output/...`) — ephemeral; rely on `GDRIVE_OUTPUT_FOLDER_ID` and `drive_link` in UI.
3. **Interactive Drive OAuth** — `run_local_server()` cannot run on Vercel; inject `GOOGLE_TOKEN_JSON` or `GOOGLE_REFRESH_TOKEN`.
4. **Large dependencies** — pikepdf + reportlab push bundle size and cold-start time.
5. **Multi-student runs** — frontend loops one `/api/run/<folder>` per student; keep selections small to stay under 300s each.

### Step-by-step: Vercel deploy

#### Step 1 — Google Cloud (Drive + login clients)

Same as Part A steps 2–3, but for Drive use a **Web application** OAuth client (not Desktop) if you generate tokens via a redirect flow, *or* keep Desktop client and copy refresh token from local `token.json`.

For **login** client, redirect URI must be exactly:

```text
https://<your-vercel-app>.vercel.app/auth/callback
```

Domain restriction:

- Consent screen: **Internal** (Workspace only), **or**
- External + `auth.py` checks `hd` claim / email domain `masterschool.com`.

#### Step 2 — Prepare secrets locally

From your working local `token.json`, copy the entire JSON **or** at minimum:

- `refresh_token`
- `client_id` / `client_secret`

Never commit these to git.

#### Step 3 — Push code to GitHub

Ensure the repo root contains `vercel.json`, `api/index.py`, `app.py`, and `requirements.txt` (merged to `master`).

#### Step 4 — Create Vercel project

1. [vercel.com](https://vercel.com) → Add New Project → import repo **Masterschool-Team/Student-File-Assembler**.
2. **Root Directory:** leave **empty** (`.` / repo root — the app lives at the top level, not in a subfolder).
3. **Framework Preset:** Other (Vercel auto-detects Flask from `requirements.txt`).
4. **Build Command:** leave empty.
5. **Output Directory:** leave default.
6. **Production Branch:** `master`.

#### Step 5 — Environment variables (Vercel → Settings → Environment Variables)

| Variable | Required | Notes |
|----------|----------|-------|
| `GDRIVE_ROOT_FOLDER_ID` | Yes | |
| `GDRIVE_OUTPUT_FOLDER_ID` | Strongly yes | Primary delivery path on Vercel |
| `GOOGLE_TOKEN_JSON` | Yes* | Full one-line JSON from `token.json` |
| `GOOGLE_REFRESH_TOKEN` | Alt* | With `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` |
| `GOOGLE_AUTH_ENABLED` | Yes | `1` |
| `GOOGLE_ALLOWED_DOMAIN` | Yes | e.g. `masterschool.com` |
| `GOOGLE_CLIENT_ID` | Yes | Login web client |
| `GOOGLE_CLIENT_SECRET` | Yes | Login web client |
| `APP_BASE_URL` | Yes | `https://<project>.vercel.app` (no trailing slash) |
| `FLASK_SECRET_KEY` | Yes | Random secret for sessions |
| `ROSTER_STORE_PATH` | Yes | `/tmp/roster.json` |
| `OUTPUT_DIR` | Yes | `/tmp/hcm2-output` |
| `ROSTER_JSON` | Optional | Seed roster on cold start only |

\*Use one of the two Drive credential options.

Apply to **Production** (and Preview if desired).

#### Step 6 — Plan & limits

- Upgrade to **Pro** if you need `maxDuration: 300` (see `vercel.json`).
- Even then, **multi-student assembly may timeout**; run batches locally or move jobs off Vercel.

#### Step 7 — Deploy

After merging to `master`, trigger a **Redeploy** in Vercel (Deployments → ⋯ → Redeploy) so the Python function is built — an earlier deploy of README-only `master` will 404 until redeployed.

```bash
npx vercel --prod
```

Or push to `master` if Git integration is enabled.

#### Step 8 — Verify

1. Visit `https://<project>.vercel.app` → should redirect to Google login.
2. Sign in with `@masterschool.com` account → should land on UI.
3. Check `/auth/me` returns your email.
4. Sync and Run should work — each is a single synchronous request (Run: one student per call).

#### Step 9 — Custom domain (optional)

Vercel → Domains → add `hcm2-assembler.masterschool.com` (example). Update `APP_BASE_URL` and Google OAuth redirect URI to match.

---

## Two OAuth clients — how they fit together

| Purpose | Scopes | Client type | Where used |
|---------|--------|-------------|------------|
| **Who can open the app** | openid, email, profile | Web | `auth.py` |
| **Drive read/write for assembly** | `drive` | Desktop (local) or shared refresh token in env | `drive.py` |

You *can* use one Web client for both if you merge scopes, but the current code keeps them separate: team members log in with their Workspace account, while Drive operations use a **shared** service/bot refresh token that has access to the HCM2 shared drives.

Alternative for enterprise: **Google service account** with domain-wide delegation — not implemented in this repo; would replace `get_drive_service()`.

---

## Manual setup checklist

- [ ] GCP: Drive API enabled
- [ ] GCP: OAuth consent screen (Internal or domain-restricted)
- [ ] GCP: Web OAuth client for login + redirect URI
- [ ] GCP: Drive refresh token from trusted account with folder access
- [ ] Vercel/Railway: all env vars set
- [ ] (Railway/Render) persistent volume for roster + output
- [ ] Share Drive folders with the OAuth account (or service account)
- [ ] Test login with non-Workspace account → must be rejected

---

## Local development with auth

```bash
export GOOGLE_AUTH_ENABLED=1
export GOOGLE_ALLOWED_DOMAIN=masterschool.com
export APP_BASE_URL=http://127.0.0.1:5055
export FLASK_SECRET_KEY=dev-secret-change-me
export GOOGLE_CLIENT_ID=...
export GOOGLE_CLIENT_SECRET=...
python app.py
```

Add `http://127.0.0.1:5055/auth/callback` to OAuth redirect URIs.

---

## Next steps for production on Vercel

1. Store roster settings (`na_documents`, etc.) in **Vercel KV/Postgres** or **Google Sheets** instead of `/tmp/roster.json`.
2. Always set `GDRIVE_OUTPUT_FOLDER_ID` — local `/output` links are ephemeral.
3. Consider **service account** instead of user OAuth for Drive.
