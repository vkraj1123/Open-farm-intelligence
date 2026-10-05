from datetime import date, datetime, timezone
from ofi.domain.models import CropCycle, FarmSnapshot
from ofi.science.engine import crop_stage_fraction, water_balance_observation
from ofi.science.water_balance import SoilWaterProfile, WeatherDay, crop_coefficient, fao56_et0, root_zone_water_balance

def test_et0_is_positive_for_hot_day():
    weather = WeatherDay(date(2026,8,15),25,38,30,70,2.0,22.0)
    assert fao56_et0(27.0, weather) > 0

def test_crop_stage_and_kc():
    crop = CropCycle(id="c1", crop="bajra", sowing_date=date(2026,7,1), harvest_date=date(2026,10,15))
    stage = crop_stage_fraction(crop, date(2026,8,15))
    assert stage is not None and 0 < stage < 1
    assert 0.3 <= crop_coefficient("bajra", stage) <= 1.0

def test_root_zone_balance_exposes_stress():
    result = root_zone_water_balance(et0_mm_day=7.0, kc=1.0, effective_rain_mm=0, irrigation_mm=0, soil=SoilWaterProfile(1.0,28,12,15))
    assert result.etc_mm_day == 7.0
    assert result.stress_fraction > 0

def test_scientific_observation_is_traceable():
    snapshot = FarmSnapshot(
        farm_id="f1", as_of=datetime(2026,8,15,tzinfo=timezone.utc),
        active_crop=CropCycle(id="c1", crop="bajra", sowing_date=date(2026,7,1), harvest_date=date(2026,10,15)),
    )
    obs = water_balance_observation(
        snapshot, latitude_deg=27.0,
        weather=WeatherDay(date(2026,8,15),25,38,30,70,2.0,22.0),
        soil=SoilWaterProfile(1.0,28,12,15),
    )
    assert obs.kind == "model"
    assert obs.source == "ofi_fao56_water_balance"
    assert obs.value["stress_fraction"] > 0
