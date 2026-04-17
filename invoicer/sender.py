"""Business logic: PDF + client → signature request via SignWell.

Flow:
  1. Read + hash file, check tracker for prior send (dup prevention).
  2. Build payload (pydantic validated).
  3. Create DRAFT document (so we have an ID before committing to send).
  4. Record draft in SQLite.
  5. Call send endpoint.
  6. Update status to 'sent'.

If step 5 fails, we're left with a draft record; `invoicer resume` (future
command) can list these and retry the send without creating a second document.
That's the closest we can get to idempotency without SignWell supporting
idempotency keys.
"""

from __future__ import annotations

import base64
import hashlib
import re
import time
from dataclasses import dataclass
from pathlib import Path

from invoicer.config import Client, Settings
from invoicer.models import CopiedContact, CreateDocumentPayload, Field2D, FilePayload, Recipient
from invoicer.signwell import SignWellClient
from invoicer.tracking import Tracker


@dataclass
class SendResult:
    document_id: str
    status_url: str
    already_sent: bool = False


def _document_name(pdf_path: Path) -> str:
    """Return a human-readable document name for the SignWell email.

    SignWell shows this as "Please complete {name}", so we want something
    like "Invoice 03/2026" rather than the raw filename stem.

    Tries to detect MMYYYY (e.g. 032026) or YYYY-MM (e.g. 2026-03) in the
    filename. Falls back to the plain stem if no date is found.
    """
    stem = pdf_path.stem
    # Pattern: two digits immediately followed by four digits → MM YYYY
    match = re.search(r"\b(\d{2})(\d{4})\b", stem)
    if match:
        month, year = match.group(1), match.group(2)
        return f"Invoice {month}/{year}"
    # Pattern: YYYY-MM anywhere in the stem
    match = re.search(r"(\d{4})-(\d{2})\b", stem)
    if match:
        year, month = match.group(1), match.group(2)
        return f"Invoice {month}/{year}"
    return stem


def _default_subject(client: Client, file_name: str) -> str:
    who = client.company or client.name
    return f"Signature requested: {file_name} for {who}"


def _default_message(client: Client, sender_name: str) -> str:
    return "Hey there, Please review and complete this document. You can click on the document below to get started."


def build_payload(
    *,
    pdf_path: Path,
    client: Client,
    settings: Settings,
    test_mode: bool,
    draft: bool,
) -> CreateDocumentPayload:
    """Construct the SignWell payload from a PDF + client record."""
    pdf_bytes = pdf_path.read_bytes()
    file_b64 = base64.b64encode(pdf_bytes).decode("ascii")

    subject = client.custom_subject or _default_subject(client, pdf_path.name)
    message = client.custom_message or _default_message(client, settings.sender_name)

    if client.signature_fields:
        inline_fields = [
            [
                Field2D(type="signature", recipient_id="client", page=sf.page, x=sf.x, y=sf.y)
                for sf in client.signature_fields
            ]
        ]
        with_sig_page = False
    else:
        inline_fields = None
        with_sig_page = True

    return CreateDocumentPayload(
        test_mode=test_mode,
        draft=draft,
        name=_document_name(pdf_path),
        subject=subject,
        message=message,
        files=[FilePayload(name=pdf_path.name, file_base64=file_b64)],
        recipients=[
            Recipient(id="client", name=client.name, email=str(client.email)),
        ],
        with_signature_page=with_sig_page,
        fields=inline_fields,
        copied_contacts=[CopiedContact(email=cc_email) for cc_email in client.cc] or None,
        reminders=True,
        allow_decline=True,
        language=client.language,
    )


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def send_invoice(
    *,
    pdf_path: Path,
    client_key: str,
    client: Client,
    settings: Settings,
    tracker: Tracker,
    test_mode: bool,
    force: bool = False,
) -> SendResult:
    """End-to-end send. Returns the SignWell document ID.

    If the same PDF (by content hash) was already sent in the same mode,
    raises unless force=True.
    """
    file_hash = sha256_file(pdf_path)
    if not force:
        prior = tracker.find_by_file_hash(file_hash, test_mode=test_mode, client_key=client_key)
        if prior is not None and prior["status"] in ("sent", "completed"):
            return SendResult(
                document_id=prior["document_id"],
                status_url=_status_url(prior["document_id"]),
                already_sent=True,
            )

    payload = build_payload(
        pdf_path=pdf_path,
        client=client,
        settings=settings,
        test_mode=test_mode,
        draft=True,  # always create as draft first for idempotency
    )

    with SignWellClient(settings.signwell_api_key) as sw:
        create_resp = sw.create_document(payload)
        document_id = create_resp.get("id")
        if not document_id:
            raise RuntimeError(
                "SignWell create_document response did not contain 'id'. "
                f"Full response: {create_resp!r}"
            )

        tracker.insert_draft(
            document_id=document_id,
            client_key=client_key,
            client_email=str(client.email),
            file_path=str(pdf_path.resolve()),
            file_sha256=file_hash,
            test_mode=test_mode,
        )

        # Short pause: SignWell processes the uploaded PDF asynchronously;
        # calling /send/ immediately after /documents/ can race and return 422
        # "isn't draft" before the document is fully ready.
        time.sleep(2)
        sw.send_document(document_id, test_mode=test_mode)
        tracker.update_status(document_id, "sent")

    return SendResult(document_id=document_id, status_url=_status_url(document_id))


def _status_url(document_id: str) -> str:
    return f"https://www.signwell.com/documents/{document_id}/"
