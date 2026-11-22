"""Turn an uploaded CV file into plain text. The normal CV pipeline then reads that text."""

import io
import zipfile
from pathlib import Path

import docx
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader

from app.parse_cv import CvParseError

ALLOWED_SUFFIXES = {".pdf", ".docx", ".txt", ".md"}
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TEXT_CHARACTERS = 200_000
MAX_PDF_PAGES = 200
MAX_DOCX_UNZIPPED_BYTES = 50 * 1024 * 1024
MIN_READABLE_CHARACTERS = 20
SCANNED_PDF_MESSAGE = "This PDF is a scanned image; text could not be read. OCR is not supported yet."


class CvFileError(CvParseError):
    """The file could not be turned into text. The message is safe to show to the user."""


def _checked_length(text: str) -> str:
    if len(text) > MAX_TEXT_CHARACTERS:
        raise CvFileError("This file holds too much text to be a CV.")
    return text


def _pdf_text(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise CvFileError("This PDF is password-protected, so its text could not be read.")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise CvFileError("This PDF has too many pages to be a CV.")
        pages: list[str] = []
        total = 0
        for page in reader.pages:
            text = page.extract_text() or ""
            total += len(text)
            _checked_length("x" * min(total, MAX_TEXT_CHARACTERS + 1))
            pages.append(text)
    except CvFileError:
        raise
    except Exception as exc:  # pypdf raises many types on damaged files.
        raise CvFileError("This PDF could not be opened. It may be damaged.") from exc
    text = "\n".join(pages)
    if sum(character.isalnum() for character in text) < MIN_READABLE_CHARACTERS:
        raise CvFileError(SCANNED_PDF_MESSAGE)
    return text


def _cell_text(cell) -> str:
    parts: list[str] = []
    for item in cell.iter_inner_content():
        if isinstance(item, Paragraph):
            if item.text.strip():
                parts.append(" ".join(item.text.split()))
        else:
            parts.extend(line.replace(" | ", " ") for line in _table_lines(item))
    return " ".join(parts)


def _table_lines(table: Table) -> list[str]:
    """One line per row, cells joined with " | ". A merged cell appears once in its row."""
    lines: list[str] = []
    for row in table.rows:
        seen = []
        cells: list[str] = []
        for cell in row.cells:
            if any(cell._tc is known for known in seen):
                continue
            seen.append(cell._tc)
            text = _cell_text(cell)
            if text:
                cells.append(text)
        if cells:
            lines.append(" | ".join(cells))
    return lines


def _docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(item.file_size for item in archive.infolist()) > MAX_DOCX_UNZIPPED_BYTES:
                raise CvFileError("This Word file is too large to read.")
        document = docx.Document(io.BytesIO(data))
        lines: list[str] = []
        for item in document.iter_inner_content():
            if isinstance(item, Paragraph):
                if item.text.strip():
                    lines.append(item.text)
            else:
                lines.extend(_table_lines(item))
    except CvFileError:
        raise
    except Exception as exc:  # A renamed or damaged file can fail in several ways.
        raise CvFileError("This Word file could not be opened. It may be damaged, or not a .docx file.") from exc
    text = "\n".join(lines)
    if not text.strip():
        raise CvFileError("This Word file has no readable text.")
    return text


def extract_text(filename: str, data: bytes) -> str:
    """Return the text of a .pdf, .docx, .txt or .md file, or raise CvFileError."""
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise CvFileError("Please upload a .pdf, .docx, .txt or .md file.")
    if len(data) > MAX_FILE_BYTES:
        raise CvFileError("This file is larger than the 5 MB limit.")
    if suffix == ".pdf":
        text = _pdf_text(data)
    elif suffix == ".docx":
        text = _docx_text(data)
    else:
        text = data.decode("utf-8-sig", errors="replace")
    return _checked_length(text)
