from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class UnitConversion:
    value: float
    unit: str


# Canonical units are intentionally small and explicit. Add domains only when a
# real provider requires them; silent guesses are worse than an unavailable unit.
_CONVERSIONS = {
    ("mm", "mm"): lambda x: x,
    ("cm", "mm"): lambda x: x * 10.0,
    ("m", "mm"): lambda x: x * 1000.0,
    ("in", "mm"): lambda x: x * 25.4,
    ("inch", "mm"): lambda x: x * 25.4,
    ("inches", "mm"): lambda x: x * 25.4,
    ("c", "c"): lambda x: x,
    ("°c", "c"): lambda x: x,
    ("degc", "c"): lambda x: x,
    ("f", "c"): lambda x: (x - 32.0) * 5.0 / 9.0,
    ("°f", "c"): lambda x: (x - 32.0) * 5.0 / 9.0,
    ("km/h", "m/s"): lambda x: x / 3.6,
    ("kph", "m/s"): lambda x: x / 3.6,
    ("m/s", "m/s"): lambda x: x,
    ("m2/m2", "m2/m2"): lambda x: x,
    ("%", "%"): lambda x: x,
}


def normalize_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    return unit.strip().lower().replace(" ", "")


def convert(value: float, from_unit: str | None, to_unit: str) -> UnitConversion | None:
    source = normalize_unit(from_unit)
    target = normalize_unit(to_unit)
    if source is None or target is None:
        return None
    transform = _CONVERSIONS.get((source, target))
    if transform is None:
        return None
    result = float(transform(float(value)))
    if not isfinite(result):
        raise ValueError("unit conversion produced a non-finite value")
    return UnitConversion(result, target)


def compatible_units(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return True
    if normalize_unit(left) == normalize_unit(right):
        return True
    return any(
        convert(1.0, left, right) is not None
        or convert(1.0, right, left) is not None
        for _ in (0,)
    )
