from dataclasses import dataclass
from math import inf
from typing import Any

from ofi.services.service_directory import ProviderMatch, ServiceDirectory


@dataclass(frozen=True)
class ProviderSelectionPolicy:
    """Deterministic, explainable ranking for already-matched providers.

    Discovery is responsible for eligibility. This policy is responsible for
    choosing among eligible providers. Metadata is optional so existing
    providers remain valid while richer directories can progressively expose
    availability, health, trust, SLA and distance information.
    """

    def select(
        self,
        directory: ServiceDirectory,
        matches: list[ProviderMatch],
        *,
        region: str | None = None,
        language: str | None = None,
        urgency: str = "normal",
    ) -> tuple[ProviderMatch, str]:
        if not matches:
            raise LookupError("cannot select from an empty provider set")

        ranked = sorted(
            (
                (self._rank(directory, match, region=region, language=language, urgency=urgency), match)
                for match in matches
            ),
            key=lambda item: item[0],
            reverse=True,
        )

        best_rank, best_match = ranked[0]
        rationale = self._rationale(
            directory,
            best_match,
            best_rank,
            region=region,
            language=language,
            urgency=urgency,
        )
        return best_match, rationale

    def _rank(
        self,
        directory: ServiceDirectory,
        match: ProviderMatch,
        *,
        region: str | None,
        language: str | None,
        urgency: str,
    ) -> tuple[float, ...]:
        provider = directory.get(match.provider_id)
        metadata = provider.metadata

        availability = self._availability(metadata)
        health = self._bounded(metadata.get("health_score", 1.0))
        geography = self._context_specificity(
            provider,
            dimension="regions",
            requested=region,
        )
        language_fit = self._context_specificity(
            provider,
            dimension="languages",
            requested=language,
        )
        trust = self._bounded(metadata.get("trust_score", 0.0))
        sla = self._sla_score(metadata, urgency)
        distance = self._distance_score(metadata)

        # Earlier dimensions dominate later ones. This keeps selection
        # explainable and avoids hidden weight tuning.
        return (
            availability,
            health,
            geography,
            language_fit,
            trust,
            sla,
            distance,
            self._stable_id_score(provider.provider_id),
        )

    @staticmethod
    def _availability(metadata: dict[str, Any]) -> float:
        value = metadata.get("availability", True)
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if isinstance(value, (int, float)):
            return max(0.0, min(1.0, float(value)))
        if isinstance(value, str):
            return {"available": 1.0, "ready": 1.0, "active": 1.0,
                    "degraded": 0.5, "busy": 0.5, "unavailable": 0.0,
                    "offline": 0.0}.get(value.lower(), 1.0)
        return 1.0

    @staticmethod
    def _bounded(value: Any) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _context_specificity(
        provider: Any,
        *,
        dimension: str,
        requested: str | None,
    ) -> float:
        if requested is None:
            return 1.0
        values = {
            value
            for capability in provider.capabilities
            for value in getattr(capability, dimension)
        }
        if requested in values:
            return 1.0
        # An empty dimension means the provider is generic/global.
        if not values:
            return 0.5
        return 0.0

    @staticmethod
    def _sla_score(metadata: dict[str, Any], urgency: str) -> float:
        raw = metadata.get("sla_minutes")
        by_urgency = metadata.get("sla_by_urgency")
        if isinstance(by_urgency, dict):
            raw = by_urgency.get(urgency, raw)
        try:
            minutes = float(raw)
        except (TypeError, ValueError):
            return 0.5
        if minutes < 0:
            return 0.0
        # Lower response time is better; 0 min → 1, 24h+ → approximately 0.
        return max(0.0, min(1.0, 1.0 - minutes / 1440.0))

    @staticmethod
    def _distance_score(metadata: dict[str, Any]) -> float:
        try:
            km = float(metadata["distance_km"])
        except (KeyError, TypeError, ValueError):
            return 0.5
        if km < 0:
            return 0.0
        return max(0.0, min(1.0, 1.0 - km / 500.0))

    @staticmethod
    def _stable_id_score(provider_id: str) -> float:
        # Stable final tie-breaker without depending on registration order.
        # The primary dimensions remain human-readable in the rationale.
        if not provider_id:
            return 0.0
        return 1.0 / (1.0 + sum(ord(char) for char in provider_id))


    def _rationale(
        self,
        directory: ServiceDirectory,
        match: ProviderMatch,
        rank: tuple[float, ...],
        *,
        region: str | None,
        language: str | None,
        urgency: str,
    ) -> str:
        provider = directory.get(match.provider_id)
        metadata = provider.metadata
        reasons = list(match.reasons)
        if region and self._context_specificity(provider, dimension="regions", requested=region) == 1.0:
            reasons.append("exact geography")
        if language and self._context_specificity(provider, dimension="languages", requested=language) == 1.0:
            reasons.append("exact language")
        if self._availability(metadata) >= 1.0:
            reasons.append("available")
        if self._bounded(metadata.get("health_score", 1.0)) >= 0.8:
            reasons.append("healthy")
        if self._bounded(metadata.get("trust_score", 0.0)) >= 0.8:
            reasons.append("trusted")
        if metadata.get("sla_minutes") is not None or metadata.get("sla_by_urgency") is not None:
            reasons.append(f"{urgency} SLA considered")
        if metadata.get("distance_km") is not None:
            reasons.append("distance considered")
        return f"selected {match.provider_id} by deterministic policy ({', '.join(reasons)})"
