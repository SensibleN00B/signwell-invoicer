"""Download signed PDFs from SignWell CDN and save to monthly subfolders.

Folder structure:
    <signed_folder>/
        YYYY-MM/
            original-filename.pdf
        undated/          <- fallback when filename has no recognisable date
            ...

Month is parsed from the original PDF filename using the same regex patterns
as sender._document_name() — MMYYYY (e.g. 032026) or YYYY-MM (e.g. 2026-04).
"""

from __future__ import annotations

import re
from pathlib import Path

from invoicer.signwell import SignWellClient
from invoicer.tracking import Tracker

_MMYYYY_RE = re.compile(r"\b(\d{2})(\d{4})\b")
_YYYY_MM_RE = re.compile(r"(\d{4})-(\d{2})(?!\d)")


def _year_month_from_filename(filename: str) -> str:
    """Extract YYYY-MM from a PDF filename, or return 'undated' as fallback.

    Supported patterns (same as sender._document_name):
      032026  -> 2026-03   (MMYYYY — current naming convention)
      2026-04 -> 2026-04   (YYYY-MM — alternative convention)
    """
    stem = Path(filename).stem
    match = _MMYYYY_RE.search(stem)
    if match:
        month, year = match.group(1), match.group(2)
        return f"{year}-{month}"
    match = _YYYY_MM_RE.search(stem)
    if match:
        year, month = match.group(1), match.group(2)
        return f"{year}-{month}"
    return "undated"


def build_signed_pdf_path(
    signed_folder: Path,
    original_filename: str,
) -> Path:
    """Return the destination path for a signed PDF.

    Args:
        signed_folder: Root folder chosen by the user.
        original_filename: Basename of the original PDF (e.g. "invoice-Andrii-Beilyi-032026.pdf").

    Returns:
        Path like <signed_folder>/2026-03/invoice-Andrii-Beilyi-032026.pdf
    """
    year_month = _year_month_from_filename(original_filename)
    return signed_folder / year_month / original_filename


def download_signed_pdf(
    *,
    document_id: str,
    pdf_url: str,
    signed_folder: Path,
    tracker: Tracker,
    sw_client: SignWellClient,
) -> Path:
    """Download a signed PDF and save it to the correct monthly subfolder.

    If the destination file already exists, skips the download but still
    calls mark_downloaded (idempotent — safe to call multiple times).

    Returns:
        Absolute path to the saved (or already-existing) file.
    """
    row = tracker.get(document_id)
    if row is None:
        raise ValueError(f"No tracker record for document_id={document_id!r}")

    original_filename = Path(row["file_path"]).name
    destination = build_signed_pdf_path(
        signed_folder=signed_folder,
        original_filename=original_filename,
    )

    if not destination.exists():
        pdf_bytes = sw_client.download_pdf(pdf_url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(pdf_bytes)

    tracker.mark_downloaded(document_id)
    return destination
