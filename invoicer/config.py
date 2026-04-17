"""Configuration: env-based Settings + YAML-based ClientRegistry.

Why pydantic-settings:
  - validates env vars on startup; bad config fails fast with a clear trace,
    not at runtime mid-send.
  - reads from .env automatically (dotenv style).
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, EmailStr, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# --- Env-based runtime settings --------------------------------------------------


class Mode(StrEnum):
    TEST = "test"
    PROD = "prod"


class Settings(BaseSettings):
    """Runtime settings loaded from env / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",  # we use mixed-prefix env names explicitly
        extra="ignore",
    )

    signwell_api_key: str = Field(..., alias="SIGNWELL_API_KEY", min_length=10)
    default_mode: Mode = Field(Mode.TEST, alias="INVOICER_DEFAULT_MODE")
    clients_path: Path = Field(Path("./clients.yaml"), alias="INVOICER_CLIENTS_PATH")
    db_path: Path = Field(Path("./sent.sqlite"), alias="INVOICER_DB_PATH")
    sender_name: str = Field("Andrew", alias="INVOICER_SENDER_NAME")
    sender_email: EmailStr = Field(..., alias="INVOICER_SENDER_EMAIL")


# --- YAML-based clients registry ------------------------------------------------

# SignWell-supported ISO 639-1 languages (as of 2026-04; see API docs). Keeping
# this as Literal so YAML typos fail loud at load time rather than at API call.
SupportedLanguage = Literal[
    "en", "fr", "es", "de", "pl", "pt", "da", "nl", "it", "ru", "sv", "ar", "el", "tr", "sk"
]


class SignatureField(BaseModel):
    """Inline signature field position for a specific client's PDF layout.

    x, y are in PDF points from the top-left corner (same as pdfplumber output).
    Page is 1-indexed (first page = 1). If not set on the client,
    with_signature_page is used instead.

    To get coordinates: open the PDF with pdfplumber and read word positions directly.
        words = page.extract_words()  # x0, top = x, y in points
    """

    x: float
    y: float
    page: int = 1


class Client(BaseModel):
    """One client's data for signature requests."""

    name: str = Field(..., min_length=1)
    email: EmailStr
    company: str | None = None
    cc: list[EmailStr] = Field(default_factory=list)
    language: SupportedLanguage = "en"
    custom_subject: str | None = None
    custom_message: str | None = None
    # If set, signature fields are placed inline at these coordinates instead of
    # appending a signature page. Useful when the PDF already has signature lines.
    signature_fields: list[SignatureField] | None = None


class ClientsRegistry(BaseModel):
    """Loaded clients.yaml as a dict of <key> -> Client."""

    clients: dict[str, Client]

    @field_validator("clients")
    @classmethod
    def validate_keys(cls, v: dict[str, Client]) -> dict[str, Client]:
        # Client keys must be slug-safe (lowercase, digits, hyphens) because
        # they appear in filenames. Enforce on load, not at send time.
        slug_re = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
        for key in v:
            if not slug_re.match(key):
                raise ValueError(
                    f"Client key {key!r} is not a valid slug. "
                    "Use lowercase letters, digits, and hyphens only "
                    "(e.g. 'acme-corp', not 'Acme_Corp')."
                )
        return v

    @classmethod
    def load(cls, path: Path) -> ClientsRegistry:
        if not path.exists():
            raise FileNotFoundError(
                f"Clients registry not found at {path}. "
                f"Copy clients.example.yaml to {path.name} and fill it in."
            )
        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        if not isinstance(raw, dict):
            raise ValueError(f"{path} must contain a top-level mapping of client keys.")
        return cls(clients=raw)

    def get(self, key: str) -> Client:
        if key not in self.clients:
            available = ", ".join(sorted(self.clients)) or "(none)"
            raise KeyError(
                f"Client {key!r} not found in registry. Available: {available}"
            )
        return self.clients[key]


# --- Filename parser ------------------------------------------------------------

# Match the client slug anywhere in the filename, as long as it's bounded by
# underscores/dots/start/end. This supports multiple conventions:
#   2026-04_acme-corp.pdf
#   2026-04_acme-corp_services.pdf
#   invoice_acme-corp_final.pdf
def infer_client_key(filename: str, registry: ClientsRegistry) -> str | None:
    """Find the client key whose slug appears anywhere in the filename.

    Keys are matched as substrings of the lowercased filename stem so that
    name-based keys like 'andrii-beilyi' match filenames like
    'invoice-Andrii-Beilyi-032026.pdf' without requiring an exact token boundary.
    Longest key wins to avoid prefix collisions (e.g. 'anna-ko' vs 'anna-kovalenko').
    """
    stem = Path(filename).stem.lower()
    candidates = sorted(registry.clients.keys(), key=len, reverse=True)
    for key in candidates:
        if key in stem:
            return key
    return None
