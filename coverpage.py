"""Generate section cover pages per spec Section 4.3."""

from __future__ import annotations

import io

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

FONT = "Helvetica-Bold"
MARGIN_X = 54  # points (~0.75")
LINE_LEADING = 1.25
FONT_SIZES = (36, 30, 24, 20, 16)


def _wrap_lines(c: canvas.Canvas, text: str, font: str, size: float, max_width: float) -> list[str]:
    words = text.split()
    if not words:
        return [text]

    lines: list[str] = []
    current: list[str] = []
    for word in words:
        trial = " ".join(current + [word]) if current else word
        if c.stringWidth(trial, font, size) <= max_width:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines


def _fit_layout(c: canvas.Canvas, text: str, page_width: float, page_height: float) -> tuple[float, list[str], float]:
    max_width = page_width - (2 * MARGIN_X)
    max_height = page_height - (2 * MARGIN_X)

    for size in FONT_SIZES:
        lines = _wrap_lines(c, text, FONT, size, max_width)
        line_height = size * LINE_LEADING
        block_height = len(lines) * line_height
        if block_height <= max_height:
            return size, lines, line_height

    size = FONT_SIZES[-1]
    lines = _wrap_lines(c, text, FONT, size, max_width)
    return size, lines, size * LINE_LEADING


def cover_page(title: str) -> bytes:
    """White letter-size portrait page, centered bold title (wrapped/scaled to fit)."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter

    text = title.upper()
    size, lines, line_height = _fit_layout(c, text, width, height)
    block_height = len(lines) * line_height
    y_top = (height + block_height) / 2 - line_height

    c.setFont(FONT, size)
    for i, line in enumerate(lines):
        line_width = c.stringWidth(line, FONT, size)
        x = (width - line_width) / 2
        y = y_top - (i * line_height)
        c.drawString(x, y, line)

    c.showPage()
    c.save()
    return buf.getvalue()
