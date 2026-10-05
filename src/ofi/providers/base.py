from abc import ABC, abstractmethod
from typing import Any


class AgricultureProvider(ABC):
    @abstractmethod
    def search(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def init(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError
