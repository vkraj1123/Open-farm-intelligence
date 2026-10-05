from datetime import date
from ofi.domain.models import CropCycle, FarmSnapshot, Observation
from ofi.science.water_balance import SoilWaterProfile, WeatherDay, crop_coefficient, fao56_et0, root_zone_water_balance

def crop_stage_fraction(crop_cycle: CropCycle, as_of: date) -> float | None:
    if not crop_cycle.sowing_date or as_of < crop_cycle.sowing_date:
        return None
    if crop_cycle.harvest_date and as_of >= crop_cycle.harvest_date:
        return 1.0
    duration = ((crop_cycle.harvest_date - crop_cycle.sowing_date).days if crop_cycle.harvest_date else 120)
    return max(0.0, min(1.0, (as_of - crop_cycle.sowing_date).days / max(1, duration)))

def water_balance_observation(snapshot: FarmSnapshot, *, latitude_deg: float, weather: WeatherDay, soil: SoilWaterProfile, effective_rain_mm: float = 0.0, irrigation_mm: float = 0.0) -> Observation:
    stage = crop_stage_fraction(snapshot.active_crop, snapshot.as_of.date())
    stage = 0.5 if stage is None else stage
    result = root_zone_water_balance(
        et0_mm_day=fao56_et0(latitude_deg, weather),
        kc=crop_coefficient(snapshot.active_crop.crop, stage),
        effective_rain_mm=effective_rain_mm,
        irrigation_mm=irrigation_mm,
        soil=soil,
    )
    return Observation(
        id=f"ofi-water-balance-{snapshot.farm_id}-{int(snapshot.as_of.timestamp())}",
        kind="model", timestamp=snapshot.as_of, source="ofi_fao56_water_balance",
        value={
            "et0_mm_day": result.et0_mm_day, "kc": result.kc,
            "etc_mm_day": result.etc_mm_day, "taw_mm": result.taw_mm,
            "raw_mm": result.raw_mm, "available_water_mm": result.available_water_mm,
            "depletion_mm": result.depletion_mm, "stress_fraction": result.stress_fraction,
            "crop_stage_fraction": stage,
        },
        quality=0.8, confidence=0.65, crop_cycle_id=snapshot.active_crop.id,
    )
