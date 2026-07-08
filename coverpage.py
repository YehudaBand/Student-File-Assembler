"""Generate section cover pages per spec Section 4.3."""

from __future__ import annotations

import io

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


def cover_page(title: str) -> bytes:
    """White letter-size portrait page, centered bold title in ~36pt caps."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter

    text = title.upper()
    c.setFont("Helvetica-Bold", 36)
    text_width = c.stringWidth(text, "Helvetica-Bold", 36)
    x = (width - text_width) / 2
    y = height / 2
    c.drawString(x, y, text)
    c.showPage()
    c.save()
    return buf.getvalue()
