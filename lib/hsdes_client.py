"""Single-file HSD-ES client with 3 high-level operations.

Credentials are loaded from a local .env file in project root:

    HSDES_USERNAME=your_idsid
    HSDES_TOKEN=your_basic_auth_token
"""

from __future__ import annotations

import base64
import logging
import os
import time
from typing import Any

import requests
import urllib3
from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
load_dotenv()

logger = logging.getLogger(__name__)

_BASE_URL = "https://hsdes-api.intel.com/rest/auth"
_SUCCESS_MIN = 200
_SUCCESS_MAX = 299


class _HsdesApiError(RuntimeError):
    """Raised when HSD-ES returns non-success HTTP status."""


class HsdesClient:
    """HSD-ES client exposing exactly 3 high-level methods.

    Public methods:
    1) get_article
    2) update_article
    3) get_query
    """

    def __init__(
        self,
        username: str | None = None,
        token: str | None = None,
        timeout: int = 100,
        retry_count: int = 3,
    ):
        self.username = username or os.getenv("HSDES_USERNAME")
        self.token = token or os.getenv("HSDES_TOKEN")
        if not self.username or not self.token:
            raise ValueError(
                "HSDES credentials missing. Set HSDES_USERNAME and HSDES_TOKEN "
                "in .env or pass them explicitly."
            )

        self.timeout = timeout
        self.retry_count = retry_count
        self._session = self._build_session()

    def _build_session(self) -> requests.Session:
        auth_token = base64.b64encode(
            f"{self.username}:{self.token}".encode("utf-8")
        ).decode("utf-8")
        session = requests.Session()
        session.verify = False
        session.headers.update(
            {
                "Authorization": f"Basic {auth_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )
        return session

    @staticmethod
    def _to_field_values(field_values: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(field_values, dict) or not field_values:
            raise ValueError("field_values must be a non-empty dict")
        return [{k: v} for k, v in field_values.items()]

    @staticmethod
    def _clean_params(params: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in params.items() if v is not None}

    @staticmethod
    def _fields_to_param(fields: list[str] | None) -> str | None:
        """Convert a field-name list to HSD-ES query parameter format."""
        if fields is None:
            return None
        if not isinstance(fields, list):
            raise ValueError("fields must be a list of field names")
        cleaned = [name.strip() for name in fields if isinstance(name, str) and name.strip()]
        if not cleaned:
            return None
        return ",".join(cleaned)

    def _request_json(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if method not in {"GET", "POST", "PUT"}:
            raise ValueError(f"Unsupported HTTP method: {method}")

        url = f"{_BASE_URL}/{path.lstrip('/')}"
        params = self._clean_params(params or {})
        backoff = 1
        last_error: Exception | None = None

        for attempt in range(1, self.retry_count + 1):
            try:
                response = self._session.request(
                    method=method,
                    url=url,
                    params=params,
                    json=json_body,
                    timeout=self.timeout,
                )
                if _SUCCESS_MIN <= response.status_code <= _SUCCESS_MAX:
                    return response.json()

                if response.status_code < 500:
                    raise _HsdesApiError(
                        f"{method} {path} failed: status={response.status_code}, "
                        f"body={response.text[:300]}"
                    )

                last_error = _HsdesApiError(
                    f"{method} {path} failed: status={response.status_code}, "
                    f"body={response.text[:300]}"
                )
            except requests.RequestException as exc:
                last_error = exc

            logger.warning(
                "Attempt %d/%d failed for %s %s: %s",
                attempt,
                self.retry_count,
                method,
                path,
                last_error,
            )
            if attempt < self.retry_count:
                time.sleep(backoff)
                backoff *= 2

        raise RuntimeError(f"Request failed after retries: {last_error}")

    @staticmethod
    def _build_response(
        payload: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> dict[str, Any]:
        """Build a uniform response envelope for all public APIs."""
        if error is None:
            return {
                "ok": True,
                "payload": payload,
                "data": payload.get("data") if isinstance(payload, dict) else None,
                "error": None,
            }

        return {
            "ok": False,
            "payload": payload,
            "data": payload.get("data") if isinstance(payload, dict) else None,
            "error": {
                "type": type(error).__name__,
                "message": str(error),
            },
        }

    def _request_json_safe(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call _request_json and always return a uniform envelope."""
        try:
            payload = self._request_json(
                method=method,
                path=path,
                params=params,
                json_body=json_body,
            )
            return self._build_response(payload=payload)
        except Exception as exc:  # Keep public API return shape consistent.
            return self._build_response(error=exc)

    def get_article(
        self,
        article_id: int,
        fields: list[str] | None = None,
    ) -> dict[str, Any]:
        """Get one article by ID via ``GET /article/{id}``.

        Args:
            article_id: HSD-ES article ID (numeric record id).
            fields: Optional field-name list used to limit returned columns.
                Example: ["id", "title", "status"].
                When omitted, the server returns its default/full field set.

        Returns:
            A uniform response envelope dict with keys:
            ``ok`` (bool), ``payload`` (raw HSD-ES JSON or None),
            ``data`` (payload.data when present), and ``error`` (None or error
            object with ``type`` and ``message``).

        Raises:
            RuntimeError: Request failed after retries.
            _HsdesApiError: HSD-ES returned non-2xx status.
            ValueError: Invalid ``fields`` input type.
        """
        return self._request_json_safe(
            "GET",
            f"article/{article_id}",
            params={
                "fields": self._fields_to_param(fields),
            },
        )

    def update_article(
        self,
        article_id: int,
        tenant: str,
        subject: str,
        field_values: dict[str, Any],
        debug: bool = False,
    ) -> dict[str, Any]:
        """Update one article via ``PUT /article/{id}``.

        Args:
            article_id: HSD-ES article ID to update.
            tenant: Tenant of the target article (for example ``server_platf``).
                This is required by HSD-ES to resolve the correct view context.
            subject: Subject of the target article (for example ``test_result``).
                This is required by HSD-ES together with ``tenant``.
            field_values: Field updates as key-value pairs.
                Example: {"description": "new text", "status": "open"}.
            debug: If True, request is sent with ``debug=true`` to validate
                update logic without committing changes.

        Returns:
            A uniform response envelope dict with keys:
            ``ok``, ``payload``, ``data``, ``error``.

        Raises:
            RuntimeError: Request failed after retries.
            _HsdesApiError: HSD-ES returned non-2xx status.
            ValueError: ``field_values`` is empty or not a dict.
        """
        return self._request_json_safe(
            "PUT",
            f"article/{article_id}",
            params={
                "debug": str(debug).lower(),
            },
            json_body={
                "tenant": tenant,
                "subject": subject,
                "fieldValues": self._to_field_values(field_values),
            },
        )

    def get_query(
        self,
        query_id: int,
        include_text_fields: str = "Y",
        start_at: int = 1,
        max_results: int = 30000,
        fields: list[str] | None = None,
    ) -> dict[str, Any]:
        """Execute query by ID via ``GET /query/{id}``.

        Args:
            query_id: Saved HSD-ES query ID.
            include_text_fields: ``"Y"`` to include text fields in results,
                ``"N"`` to reduce payload size.
            start_at: 1-based start row index for pagination.
            max_results: Maximum rows to return for this call.
            fields: Optional field-name list to limit returned columns.
                Example: ["id", "title", "owner"].
                When omitted, HSD-ES returns default query columns.

        Returns:
            A uniform response envelope dict with keys:
            ``ok``, ``payload``, ``data``, ``error``.

        Raises:
            RuntimeError: Request failed after retries.
            _HsdesApiError: HSD-ES returned non-2xx status.
            ValueError: Invalid ``fields`` input type.
        """
        return self._request_json_safe(
            "GET",
            f"query/{query_id}",
            params={
                "include_text_fields": include_text_fields,
                "start_at": start_at,
                "max_results": max_results,
                "fields": self._fields_to_param(fields),
            },
        )
