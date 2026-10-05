from ofi.domain.models import FarmSnapshot, Observation
from ofi.science.engine import derive_water_balance_from_snapshot
from ofi.science.models import ScientificModel, ScientificModelRegistry


class FAO56WaterBalanceModel(ScientificModel):
    name = "ofi_fao56_water_balance"
    capabilities = frozenset({"water_balance", "water_stress"})

    def run(self, snapshot: FarmSnapshot) -> Observation | None:
        return derive_water_balance_from_snapshot(snapshot)


def default_scientific_registry() -> ScientificModelRegistry:
    return ScientificModelRegistry([FAO56WaterBalanceModel()])
