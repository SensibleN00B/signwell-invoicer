"""Pydantic models for SignWell API payloads.

We wrap the API in typed models instead of raw dicts for three reasons:
  1. IDE autocomplete + mypy catches typos in field names.
  2. Required fields are enforced before we hit the network.
  3. When SignWell changes the API, the break is in one place.

Reference: https://developers.signwell.com/reference/createdocument
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class FilePayload(BaseModel):
    """One file in the `files` array. Exactly one of file_url/file_base64 must be set."""

    name: str
    # Exactly one of file_url or file_base64 must be set; enforced in build_payload.
    file_url: str | None = None
    file_base64: str | None = None

    model_config = ConfigDict(extra="forbid")


class Recipient(BaseModel):
    """One recipient in the `recipients` array."""

    id: str  # arbitrary string ID we assign; referenced by fields[].recipient_id
    name: str
    email: EmailStr
    model_config = ConfigDict(extra="forbid")


class Field2D(BaseModel):
    """One form field placed at specific coordinates on a document page."""

    type: str  # "signature" | "initials" | "date" | "text" | "checkbox"
    recipient_id: str
    page: int = 0
    x: float = 0
    y: float = 0
    width: float | None = None
    height: float | None = None
    required: bool = True
    api_id: str | None = None

    model_config = ConfigDict(extra="forbid")


class CopiedContact(BaseModel):
    """Email gets a copy of the completed document."""

    email: EmailStr
    name: str | None = None

    model_config = ConfigDict(extra="forbid")


class CreateDocumentPayload(BaseModel):
    """Full request body for POST /api/v1/documents/.

    Only fields we actually use are modeled. If you need more (e.g. metadata,
    labels, checkbox_groups), add them here — don't bypass to raw dicts.
    """

    test_mode: bool = False
    draft: bool = False
    name: str
    subject: str | None = None
    message: str | None = None

    files: list[FilePayload]
    recipients: list[Recipient]

    # V1 uses with_signature_page. For inline placement, set with_signature_page=False
    # and provide fields (one inner array per file).
    with_signature_page: bool = True
    fields: list[list[Field2D]] | None = None

    copied_contacts: list[CopiedContact] | None = None
    reminders: bool = True
    allow_decline: bool = True
    language: str = "en"
    expires_in: int | None = None  # days until expiry

    model_config = ConfigDict(extra="forbid")
