from abc import ABC, abstractmethod
from ofi.domain.models import FarmSnapshot, Observation


class ScientificModel(ABC):
    """Provider-neutral interface for validated agricultural science models."""

    name: str
    capabilities: frozenset[str] = frozenset()

    @abstractmethod
    def run(self, snapshot: FarmSnapshot) -> Observation | None:
        raise NotImplementedError


class ScientificModelRegistry:
    def __init__(self, models: list[ScientificModel] | None = None):
        self._models: dict[str, ScientificModel] = {}
        for model in models or []:
            self.register(model)

    def register(self, model: ScientificModel) -> None:
        if model.name in self._models:
            raise ValueError(f"scientific model already registered: {model.name}")
        self._models[model.name] = model

    def get(self, name: str) -> ScientificModel:
        try:
            return self._models[name]
        except KeyError as exc:
            raise KeyError(f"scientific model not registered: {name}") from exc

    def models_for(self, capability: str) -> list[ScientificModel]:
        return [model for model in self._models.values() if capability in model.capabilities]

    def run(self, snapshot: FarmSnapshot) -> list[Observation]:
        results: list[Observation] = []
        for model in self._models.values():
            observation = model.run(snapshot)
            if observation is not None:
                results.append(observation)
        return results
