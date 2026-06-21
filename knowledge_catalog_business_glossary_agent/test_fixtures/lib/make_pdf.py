#!/usr/bin/env python3
"""Stdlib-only minimal PDF writer.

Used by the synthetic-data seed scripts to generate a one-page PDF
from a Markdown file so Document AI Layout Parser gets exercised in
the eval. Pure standard library — no reportlab, no pandoc, no LaTeX
needed on the host.

Usage:
    python3 make_pdf.py "Title" path/to/input.md > path/to/output.pdf
"""

from __future__ import annotations

import sys
from typing import List


_PAGE_WIDTH = 612          # 8.5"
_PAGE_HEIGHT = 792         # 11"
_LEFT_MARGIN = 54
_RIGHT_MARGIN = 54
_TOP_MARGIN = 54
_LINE_HEIGHT = 14
_FONT_SIZE = 11
_HEADING_FONT_SIZE = 16
_MAX_CHARS_PER_LINE = 95


def _escape(s: str) -> str:
  """Escape a string for the PDF content stream."""
  return (
      s.replace("\\", "\\\\")
      .replace("(", "\\(")
      .replace(")", "\\)")
      # Drop any non-Latin-1 chars so the byte stream stays valid.
      .encode("latin-1", "replace")
      .decode("latin-1")
  )


def _wrap(text: str, width: int) -> List[str]:
  """Greedy word-wrap to a width in characters."""
  out: List[str] = []
  line = ""
  for word in text.split():
    if not line:
      line = word
    elif len(line) + 1 + len(word) <= width:
      line = f"{line} {word}"
    else:
      out.append(line)
      line = word
  if line:
    out.append(line)
  return out


def _layout_markdown(md: str) -> List[tuple[str, int]]:
  """Returns a list of (text_line, font_size) tuples."""
  lines: List[tuple[str, int]] = []
  for raw in md.splitlines():
    stripped = raw.rstrip()
    if not stripped:
      lines.append(("", _FONT_SIZE))
      continue
    if stripped.startswith("# "):
      lines.append((stripped[2:].strip(), _HEADING_FONT_SIZE))
      continue
    if stripped.startswith("## "):
      lines.append((stripped[3:].strip(), _HEADING_FONT_SIZE - 2))
      continue
    if stripped.startswith("### "):
      lines.append((stripped[4:].strip(), _HEADING_FONT_SIZE - 4))
      continue
    # Treat bullets / numbered lists as plain text wrapped to the line cap.
    wrapped = _wrap(stripped, _MAX_CHARS_PER_LINE)
    for chunk in wrapped:
      lines.append((chunk, _FONT_SIZE))
  return lines


def _build_content_stream(title: str, lines: List[tuple[str, int]]) -> bytes:
  """Builds the PDF content stream (PostScript-like draw commands)."""
  ops: List[str] = ["BT"]
  y = _PAGE_HEIGHT - _TOP_MARGIN
  # Title
  ops.append(f"/F1 {_HEADING_FONT_SIZE + 4} Tf")
  ops.append(f"{_LEFT_MARGIN} {y} Td")
  ops.append(f"({_escape(title)}) Tj")
  current_size = _HEADING_FONT_SIZE + 4
  y -= _LINE_HEIGHT * 2

  for text, size in lines:
    if y < _TOP_MARGIN:
      # Out of room on the single page — drop the rest.
      break
    if size != current_size:
      ops.append(f"/F1 {size} Tf")
      current_size = size
    # Move to (left margin, current y)
    ops.append(f"1 0 0 1 {_LEFT_MARGIN} {y} Tm")
    if text:
      ops.append(f"({_escape(text)}) Tj")
    # Smaller line-height for the very smallest text; larger for headings.
    y -= max(_LINE_HEIGHT, int(size * 1.2))
  ops.append("ET")
  return ("\n".join(ops) + "\n").encode("latin-1")


def make_pdf(title: str, markdown_text: str) -> bytes:
  """Returns a valid one-page PDF byte string for ``markdown_text``."""
  lines = _layout_markdown(markdown_text)
  content = _build_content_stream(title, lines)

  # Build PDF objects.
  objects: List[bytes] = []
  objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
  objects.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
  objects.append(
      b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 "
      + str(_PAGE_WIDTH).encode() + b" " + str(_PAGE_HEIGHT).encode() + b"]"
      b" /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
  )
  objects.append(
      b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica"
      b" /Encoding /WinAnsiEncoding >>"
  )
  objects.append(
      b"<< /Length "
      + str(len(content)).encode()
      + b" >>\nstream\n"
      + content
      + b"endstream"
  )

  header = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
  body = header
  offsets: List[int] = []
  for i, obj in enumerate(objects, start=1):
    offsets.append(len(body))
    body += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"

  xref_offset = len(body)
  xref = f"xref\n0 {len(objects) + 1}\n".encode()
  xref += b"0000000000 65535 f \n"
  for off in offsets:
    xref += f"{off:010d} 00000 n \n".encode()

  trailer = (
      f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
      f"startxref\n{xref_offset}\n%%EOF\n"
  ).encode()

  return body + xref + trailer


def main() -> int:
  if len(sys.argv) < 3:
    print("usage: make_pdf.py <title> <input.md>", file=sys.stderr)
    return 2
  title = sys.argv[1]
  with open(sys.argv[2], "r", encoding="utf-8") as f:
    md = f.read()
  sys.stdout.buffer.write(make_pdf(title, md))
  return 0


if __name__ == "__main__":
  sys.exit(main())
