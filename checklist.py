"""HCM2 audit document checklist — 13 sections.

File naming convention:  lastName_starsId_SECTION_fileName.ext
Example:                 Smith_12345_Credit-Balance_check.pdf

Section keys use spaces in canonical form (e.g. "Credit Balance").
Filename tokens use hyphens for multi-word sections.
"""

from __future__ import annotations

# (section_key, display_name, sub_documents)
CHECKLIST: list[tuple[str, str, list[str]]] = [
    ("ISIR", "ISIRs", [
        "ISIR upon which disbursement is based",
        "Prior ISIR",
        "Most recent ISIR transaction (if different)",
        "All pages, EFC, and all comment codes with related text",
    ]),
    ("Verification", "Complete Verification Documentation", [
        "Per FSA Handbook Application & Verification Guide",
        "For applicable award year(s) and verification group",
    ]),
    ("Conflict Resolving", "Conflicting & Discrepant Information", [
        "C-codes on the ISIR",
        "Name changes",
        "Gender ambiguity resolution",
    ]),
    ("Eligibility", "Institutional Intervention in Eligibility", [
        "Professional judgment",
        "SAP appeals",
        "Dependency overrides",
    ]),
    ("POE", "Proof of Academic Qualifications", [
        "High school diploma",
        "HS transcript showing graduation date",
        "Home schooling certification",
        "GED / State Certificate",
        "Academic transcript (completed 2-year program toward bachelor's)",
        "Ability-to-benefit test documentation",
    ]),
    ("FE", "Award Calculation", [
        "By specific payment period and disbursement",
    ]),
    ("Ledger", "Official Student Tuition Account Records", [
        "Each completed transaction (date, description, debit/credit)",
        "By cash payment or credit",
        "From initial enrollment through present",
        "Chronological historical sequence",
        "Title IV disbursements, R2T4, and paid credit balances",
    ]),
    ("Credit Balance", "Credit Balance Documentation", [
        "Electronic transfer to student bank account",
        "Front/back copies of cancelled check",
        "Receipt for cash disbursed",
        "Return of credit balance to Title IV program",
    ]),
    ("SAP", "Satisfactory Academic Progress", [
        "Academic transcript (entire history with institution)",
        "GPA / Cumulative GPA",
        "Hours/credits attempted & completed",
        "Payment period & transfer hours/credits",
        "Student appeal of failure to make SAP",
        "SAP measurement documentation",
    ]),
    ("Additional Files", "Additional Relevant Student File Documents", [
        "Leave of absence (LOA) documentation",
        "Eligibility checklist",
        "FA Director notations of eligibility changes",
        "Counseling records (academic & attendance progress)",
    ]),
    ("Attendance", "Attendance Documentation", [
        "Source documents or summary document",
        "As determined in consultation with SEOSB",
    ]),
    ("Title IV", "Return to Title IV Funds (R2T4)", [
        "R2T4 calculation worksheet",
        "Student withdrawal form (official withdrawals)",
        "Return of funds documentation (check copies, electronic confirmations, Form 270, COD negative disbursement)",
        "Post-Withdrawal Disbursement documentation",
        "NSLDS withdrawal info screen print",
    ]),
    ("Entrance Counseling", "Direct Loan Entrance Counseling", [
        "Including student signature and date",
    ]),
]

VALID_SECTIONS: set[str] = {entry[0] for entry in CHECKLIST}
SECTION_ORDER: list[str] = [entry[0] for entry in CHECKLIST]


def section_filename_token(section_key: str) -> str:
    """Hyphenated token for filenames (spaces -> hyphens)."""
    return section_key.replace(" ", "-")


def _lookup_variants(section_key: str) -> list[str]:
    return sorted({
        section_key.lower(),
        section_key.lower().replace(" ", ""),
        section_key.lower().replace(" ", "-"),
    })


SECTION_LOOKUP: dict[str, str] = {}
for key in VALID_SECTIONS:
    for variant in _lookup_variants(key):
        SECTION_LOOKUP[variant] = key


def normalize_section(raw: str) -> str | None:
    """Map any accepted section token to the canonical key."""
    return SECTION_LOOKUP.get(str(raw).strip().lower())


def checklist_by_key() -> dict[str, tuple]:
    return {entry[0]: entry for entry in CHECKLIST}


def applicable_sections(na_sections: set[str] | None = None) -> list[str]:
    """Return ordered list of section keys excluding those in na_sections."""
    na = na_sections or set()
    return [key for key, _name, _subs in CHECKLIST if key not in na]
