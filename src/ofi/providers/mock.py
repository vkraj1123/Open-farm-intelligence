from typing import Any

from ofi.providers.base import AgricultureProvider


class MockVistaarProvider(AgricultureProvider):
    def search(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"mock": True, "action": "search", "message": payload}

    def init(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"mock": True, "action": "init", "message": payload}
