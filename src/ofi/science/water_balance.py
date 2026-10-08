from dataclasses import dataclass
from datetime import date
from math import acos, cos, exp, pi, sin, tan

@dataclass(frozen=True)
class WeatherDay:
    date: date
    tmin_c: float
    tmax_c: float
    rh_min_pct: float
    rh_max_pct: float
    wind_2m_ms: float
    solar_mj_m2_day: float

@dataclass(frozen=True)
class SoilWaterProfile:
    root_depth_m: float
    field_capacity_pct: float
    wilting_point_pct: float
    initial_moisture_pct: float

@dataclass(frozen=True)
class WaterBalanceResult:
    et0_mm_day: float
    kc: float
    etc_mm_day: float
    taw_mm: float
    raw_mm: float
    available_water_mm: float
    depletion_mm: float
    stress_fraction: float

CROP_KC = {"bajra": (0.30, 1.00, 0.55), "pearl_millet": (0.30, 1.00, 0.55), "wheat": (0.35, 1.15, 0.40), "mustard": (0.35, 1.05, 0.35)}

def extraterrestrial_radiation(lat_deg: float, day_of_year: int) -> float:
    lat = lat_deg * pi / 180
    dr = 1 + 0.033 * cos(2 * pi / 365 * day_of_year)
    declination = 0.409 * sin(2 * pi / 365 * day_of_year - 1.39)
    sunset = acos(max(-1.0, min(1.0, -tan(lat) * tan(declination))))
    return (24 * 60 / pi) * 0.0820 * dr * (sunset * sin(lat) * sin(declination) + cos(lat) * cos(declination) * sin(sunset))

def _svp(temp_c: float) -> float:
    return 0.6108 * exp(17.27 * temp_c / (temp_c + 237.3))

def fao56_et0(latitude_deg: float, weather: WeatherDay) -> float:
    tmean = (weather.tmin_c + weather.tmax_c) / 2
    es_tmin, es_tmax = _svp(weather.tmin_c), _svp(weather.tmax_c)
    es = (es_tmin + es_tmax) / 2
    ea = (es_tmin * weather.rh_max_pct / 100 + es_tmax * weather.rh_min_pct / 100) / 2
    delta = 4098 * _svp(tmean) / (tmean + 237.3) ** 2
    gamma = 0.000665 * 101.3
    ra = extraterrestrial_radiation(latitude_deg, weather.date.timetuple().tm_yday)
    rso = 0.75 * ra
    rs = min(max(0.0, weather.solar_mj_m2_day), rso)
    rns = 0.77 * rs
    rnl = 4.903e-9 * (((weather.tmax_c + 273.16) ** 4 + (weather.tmin_c + 273.16) ** 4) / 2) * (0.34 - 0.14 * max(0.0, ea) ** 0.5) * (1.35 * min(rs / rso if rso else 0.0, 1.0) - 0.35)
    rn = rns - rnl
    return max(0.0, (0.408 * delta * rn + gamma * (900 / (tmean + 273)) * weather.wind_2m_ms * (es - ea)) / (delta + gamma * (1 + 0.34 * weather.wind_2m_ms)))

def crop_coefficient(crop: str, stage_fraction: float) -> float:
    initial, mid, end = CROP_KC.get(crop.lower(), (0.35, 1.00, 0.50))
    x = max(0.0, min(1.0, stage_fraction))
    if x < 0.25:
        return initial + (mid - initial) * x / 0.25
    if x < 0.75:
        return mid
    return mid + (end - mid) * (x - 0.75) / 0.25

def root_zone_water_balance(*, et0_mm_day: float, kc: float, effective_rain_mm: float, irrigation_mm: float, soil: SoilWaterProfile) -> WaterBalanceResult:
    taw = max(0.0, (soil.field_capacity_pct - soil.wilting_point_pct) / 100 * 1000 * soil.root_depth_m)
    initial_available = max(0.0, (soil.initial_moisture_pct - soil.wilting_point_pct) / 100 * 1000 * soil.root_depth_m)
    etc = et0_mm_day * kc
    available = min(taw, initial_available + effective_rain_mm + irrigation_mm)
    available_after_et = max(0.0, available - etc)
    depletion = max(0.0, min(taw, taw - available_after_et))
    raw = 0.5 * taw
    stress = max(0.0, min(1.0, (raw - available_after_et) / raw)) if raw else 0.0
    return WaterBalanceResult(round(et0_mm_day,3), round(kc,3), round(etc,3), round(taw,3), round(raw,3), round(available_after_et,3), round(depletion,3), round(stress,3))
