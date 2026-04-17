from pathlib import Path
from unittest.mock import MagicMock

from invoicer.downloader import build_signed_pdf_path, download_signed_pdf


def test_build_signed_pdf_path_mmyyyy_in_filename():
    # e.g. invoice-Andrii-Beilyi-032026.pdf → 2026-03
    result = build_signed_pdf_path(
        signed_folder=Path("/signed"),
        original_filename="invoice-Andrii-Beilyi-032026.pdf",
    )
    assert result == Path("/signed/2026-03/invoice-Andrii-Beilyi-032026.pdf")


def test_build_signed_pdf_path_yyyy_mm_in_filename():
    # e.g. 2026-04_acme-corp.pdf → 2026-04
    result = build_signed_pdf_path(
        signed_folder=Path("/signed"),
        original_filename="2026-04_acme-corp.pdf",
    )
    assert result == Path("/signed/2026-04/2026-04_acme-corp.pdf")


def test_build_signed_pdf_path_no_date_falls_back_to_undated():
    result = build_signed_pdf_path(
        signed_folder=Path("/signed"),
        original_filename="contract.pdf",
    )
    assert result == Path("/signed/undated/contract.pdf")


def test_download_signed_pdf_saves_file_and_marks_downloaded(tmp_path):
    signed_folder = tmp_path / "signed"
    pdf_content = b"%PDF-1.4 fake"

    mock_sw_client = MagicMock()
    mock_sw_client.download_pdf.return_value = pdf_content

    mock_tracker = MagicMock()
    mock_tracker.get.return_value = {
        "file_path": "/invoices/invoice-Andrii-Beilyi-032026.pdf",
    }

    saved_path = download_signed_pdf(
        document_id="doc-abc",
        pdf_url="https://cdn.signwell.com/signed.pdf",
        signed_folder=signed_folder,
        tracker=mock_tracker,
        sw_client=mock_sw_client,
    )

    expected = signed_folder / "2026-03" / "invoice-Andrii-Beilyi-032026.pdf"
    assert saved_path == expected
    assert expected.read_bytes() == pdf_content
    mock_tracker.mark_downloaded.assert_called_once_with("doc-abc")


def test_download_signed_pdf_skips_if_file_exists(tmp_path):
    signed_folder = tmp_path / "signed"
    existing_file = signed_folder / "2026-03" / "invoice-Andrii-Beilyi-032026.pdf"
    existing_file.parent.mkdir(parents=True)
    existing_file.write_bytes(b"existing content")

    mock_sw_client = MagicMock()
    mock_tracker = MagicMock()
    mock_tracker.get.return_value = {
        "file_path": "/invoices/invoice-Andrii-Beilyi-032026.pdf",
    }

    saved_path = download_signed_pdf(
        document_id="doc-abc",
        pdf_url="https://cdn.signwell.com/signed.pdf",
        signed_folder=signed_folder,
        tracker=mock_tracker,
        sw_client=mock_sw_client,
    )

    assert saved_path == existing_file
    mock_sw_client.download_pdf.assert_not_called()
    mock_tracker.mark_downloaded.assert_called_once_with("doc-abc")
