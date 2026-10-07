from ofi.intelligence.units import compatible_units, convert


def test_rainfall_units_convert_to_mm():
    result = convert(1.0, "inch", "mm")
    assert result is not None
    assert result.value == 25.4


def test_temperature_units_are_compatible():
    assert compatible_units("F", "C")


def test_speed_units_are_compatible():
    assert compatible_units("km/h", "m/s")


def test_unknown_units_are_not_silently_compatible():
    assert not compatible_units("kg/ha", "mm")
