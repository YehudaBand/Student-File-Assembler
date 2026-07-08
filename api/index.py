"""Vercel serverless entry point for the Flask app.

Exposes the WSGI `app` object expected by @vercel/python.
See DEPLOY_VERCEL.md for limitations and required env vars.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Project root must be on sys.path so imports like `import store` resolve.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app  # noqa: E402  (module-level app includes all routes)
