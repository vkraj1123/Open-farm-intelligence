from typing import Any

import os
import uuid

import httpx

from ofi.providers.base import AgricultureProvider


class VistaarProvider(AgricultureProvider):
    """Configurable adapter for a VISTAAR/Beckn-compatible network endpoint.

    No government endpoint or credentials are hard-coded. Production onboarding
    can supply BAP_ENDPOINT through the environment.
    """

    def __init__(self, endpoint: str | None = None, timeout: float = 10.0):
        self.endpoint = (endpoint or os.getenv("BAP_ENDPOINT", "")).rstrip("/")
        self.timeout = timeout

    def _context(self, action: str) -> dict[str, Any]:
        return {
            "domain": "schemes:vistaar",
            "action": action,
            "version": "1.1.0",
            "transaction_id": str(uuid.uuid4()),
            "message_id": str(uuid.uuid4()),
        }

    def search(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.endpoint:
            raise RuntimeError("BAP_ENDPOINT is not configured")
        body = {"context": self._context("search"), "message": payload}
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(f"{self.endpoint}/search", json=body)
            response.raise_for_status()
            return response.json()

    def init(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.endpoint:
            raise RuntimeError("BAP_ENDPOINT is not configured")
        body = {"context": self._context("init"), "message": payload}
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(f"{self.endpoint}/init", json=body)
            response.raise_for_status()
            return response.json()
