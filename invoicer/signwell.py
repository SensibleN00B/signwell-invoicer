"""Thin wrapper around the SignWell REST API.

Design decisions:
  - Sync httpx.Client (not async). The CLI sends one invoice at a time;
    async gives us nothing but complexity here.
  - No auto-retries on 5xx in V1. If SignWell is flaky, we want to know
    immediately rather than silently duplicate a document. Add `tenacity`
    with idempotency-conscious logic in V2 if needed.
  - Context manager for proper connection cleanup.

Reference: https://developers.signwell.com/reference/getting-started-with-your-api-1
"""

from __future__ import annotations

from types import TracebackType
from typing import Any

import httpx

from invoicer.models import CreateDocumentPayload

BASE_URL = "https://www.signwell.com/api/v1"


class SignWellError(RuntimeError):
    """Raised on any non-2xx API response."""

    def __init__(self, status_code: int, body: str, request_desc: str) -> None:
        super().__init__(f"SignWell API {status_code} on {request_desc}: {body}")
        self.status_code = status_code
        self.body = body


class SignWellClient:
    def __init__(self, api_key: str, timeout: float = 30.0) -> None:
        # X-Api-Key per https://developers.signwell.com/reference/getting-started-with-your-api-1
        self._http = httpx.Client(
            base_url=BASE_URL,
            headers={
                "X-Api-Key": api_key,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    def __enter__(self) -> SignWellClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._http.close()

    # --- Endpoints we use ------------------------------------------------------

    def create_document(self, payload: CreateDocumentPayload) -> dict[str, Any]:
        """POST /documents/ — create a document (as draft or send immediately)."""
        # model_dump(exclude_none=True) strips optional None fields so SignWell
        # doesn't reject the request (some fields reject null explicitly).
        body = payload.model_dump(exclude_none=True, mode="json")
        return self._post("/documents/", body, request_desc="create_document")

    def send_document(self, document_id: str, test_mode: bool) -> dict[str, Any]:
        """POST /documents/{id}/send/ — send a previously created draft."""
        return self._post(
            f"/documents/{document_id}/send/",
            {"test_mode": test_mode},
            request_desc=f"send_document({document_id})",
        )

    def get_document(self, document_id: str) -> dict[str, Any]:
        """GET /documents/{id}/ — fetch document status + metadata."""
        return self._get(f"/documents/{document_id}/", request_desc=f"get_document({document_id})")

    def me(self) -> dict[str, Any]:
        """GET /me — verifies the API key works. Good for a sanity check."""
        return self._get("/me", request_desc="me")

    # --- Internal --------------------------------------------------------------

    def _post(self, path: str, body: dict[str, Any], *, request_desc: str) -> dict[str, Any]:
        resp = self._http.post(path, json=body)
        return self._parse(resp, request_desc)

    def _get(self, path: str, *, request_desc: str) -> dict[str, Any]:
        resp = self._http.get(path)
        return self._parse(resp, request_desc)

    @staticmethod
    def _parse(resp: httpx.Response, request_desc: str) -> dict[str, Any]:
        if not resp.is_success:
            raise SignWellError(resp.status_code, resp.text, request_desc)
        if not resp.content:
            return {}
        return resp.json()
