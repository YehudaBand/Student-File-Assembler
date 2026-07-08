# Student File Assembler

Assembles one compliant PDF per student by merging individually-named documents from a student's Google Drive folder, in the order required by the SFA compliance audit.

Implements `student_file_assembler_spec_v2.docx`.

## Setup

```bash
cd student_file_assembler

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Copy OAuth credentials from the sibling project (reuses the same Drive app).
cp ../stars_doc_upload/credentials.json .

# Configure
cp .env.example .env
# Edit .env and set GDRIVE_ROOT_FOLDER_ID to the root folder's Drive ID.
```

Also install LibreOffice if any student folder contains `.xlsx` or `.csv` documents:

```bash
brew install --cask libreoffice
```

## Usage

```bash
python app.py
```

Open http://127.0.0.1:5055 and:

1. **Click "Sync from Drive"** — the tool lists every subfolder in the root Drive folder and adds each one to the roster as an `active_graduate` student with no N/A docs.
2. For withdrawn students, change the **Type** dropdown to `withdrawn`.
3. Click **edit** in the N/A column to open a checklist of all 22 documents and tick the ones that don't apply to that student (the "why" is explained in spec §5.2).
4. Tick the checkboxes of the students you want to assemble. Use **Select all** / **Select without output** as shortcuts.
5. Tick **Override existing output** if you want to rerun students whose PDF already exists.
6. Click **Run**. Watch the log; click through to the generated PDFs + report when done.

The roster is persisted to `roster.json` — all edits (type, N/A, additions, removals) happen in the UI.

### CLI (optional)

The same roster is usable from the CLI:

```bash
python assemble.py --sync                      # refresh roster from Drive
python assemble.py                             # run all
python assemble.py --student Abrams_Rayanna    # run one
python assemble.py --force                     # rerun & override existing
```

## File naming convention (inside each student folder)

Files in each student's Drive folder must be prefixed with the two-digit document number:

```
01_ledger.pdf
02_credit_balance_form.pdf
03_transcript.pdf
...
22_verification.pdf
```

Anything without that prefix is logged as `UNRECOGNIZED` and skipped. This is the only folder-preparation step required of the audit team.

## Output

- `LastName_FirstName_SFA_File.pdf` — all required (non-N/A) docs present
- `LastName_FirstName_SFA_File_INCOMPLETE.pdf` — at least one required doc was missing
- `missing_documents_report_YYYY-MM-DD.csv` — one row per student-doc pair with status `PRESENT` / `MISSING` / `N/A` / `UNRECOGNIZED`

## Auth

Uses OAuth user credentials (the same pattern as `stars_doc_upload/`). First run opens a browser for consent; `token.json` is cached after.
