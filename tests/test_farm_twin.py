from datetime import date, datetime, timedelta, timezone

from ofi.domain.models import CropCycle, Farm, GeoPoint, LandParty, Parcel, ProductionContract
from ofi.twin.farm_twin import FarmTwinStore


def make_farm():
    return Farm(
        id="farm-1",
        farmer_id="farmer-1",
        parcel=Parcel(id="parcel-1", location=GeoPoint(latitude=27.0, longitude=72.0)),
        crop_cycle=CropCycle(
            id="crop-kharif-2026", crop="bajra", season="kharif",
            sowing_date=date(2026, 7, 1), harvest_date=date(2026, 10, 15)
        ),
        parties=[
            LandParty(party_id="owner-1", role="owner", valid_from=date(2020,1,1)),
            LandParty(party_id="cultivator-1", role="cultivator", valid_from=date(2026,7,1)),
        ],
        contracts=[
            ProductionContract(
                contract_id="contract-1", owner_id="owner-1", cultivator_id="cultivator-1",
                valid_from=date(2026,7,1), valid_to=date(2026,10,15),
                arrangement="owner_land_irrigation__cultivator_labor_inputs",
            )
        ],
    )


def test_time_aware_snapshot():
    twin = FarmTwinStore()
    twin.upsert(make_farm())
    twin.add_land_party(
        "farm-1",
        LandParty(party_id="old-manager", role="manager", valid_from=date(2025,1,1), valid_to=date(2026,6,30)),
    )
    july = datetime(2026,7,15,tzinfo=timezone.utc)
    snap = twin.snapshot("farm-1", july)
    assert snap.active_crop.id == "crop-kharif-2026"
    assert {p.party_id for p in snap.active_parties} == {"owner-1", "cultivator-1"}
    assert snap.active_contracts[0].contract_id == "contract-1"


def test_crop_history_switches_active_cycle():
    twin = FarmTwinStore()
    farm = make_farm()
    twin.upsert(farm)
    twin.register_crop_cycle("farm-1", CropCycle(
        id="crop-rabi-2026", crop="wheat", season="rabi",
        sowing_date=date(2026, 11, 1), harvest_date=date(2027, 3, 15)
    ))
    snap = twin.snapshot("farm-1", datetime(2026,12,1,tzinfo=timezone.utc))
    assert snap.active_crop.crop == "wheat"
