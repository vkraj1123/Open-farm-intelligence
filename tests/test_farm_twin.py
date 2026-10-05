from datetime import date, datetime, timezone
from ofi.domain.models import CropCycle, Farm, GeoPoint, LandParty, Parcel, Observation
from ofi.twin.farm_twin import FarmTwinStore

def test_snapshot_is_time_aware():
    store=FarmTwinStore()
    cycle=CropCycle(id="c1",crop="bajra",sowing_date=date(2026,7,1))
    farm=Farm(id="f1",farmer_id="u1",parcel=Parcel(id="p1",location=GeoPoint(latitude=27,longitude=72)),
              crop_cycle=cycle,parties=[
                  LandParty(party_id="owner",role="owner",valid_from=date(2020,1,1)),
                  LandParty(party_id="cultivator",role="cultivator",valid_from=date(2026,7,1))
              ])
    store.upsert(farm)
    store.add_observation("f1",Observation(id="old",kind="soil",timestamp=datetime(2026,6,1,tzinfo=timezone.utc),value={"moisture_pct":20},source="soil_lab"))
    store.add_observation("f1",Observation(id="new",kind="soil",timestamp=datetime(2026,8,1,tzinfo=timezone.utc),value={"moisture_pct":12},source="soil_lab"))
    snap=store.snapshot("f1",datetime(2026,7,15,tzinfo=timezone.utc))
    assert [x.id for x in snap.recent_observations]==["old"]
    assert {x.party_id for x in snap.active_parties}=={"owner","cultivator"}
