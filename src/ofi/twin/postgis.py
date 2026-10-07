"""Optional PostgreSQL/PostGIS persistence for the farm digital twin."""

from datetime import datetime, timezone
import json
from typing import Any, Callable

from ofi.domain.models import (
    CropCycle, Farm, FarmSnapshot, GeoPoint, LandParty, Observation,
    Parcel, ProductionContract,
)
from ofi.twin.repository import FarmTwinRepository


class PostGISFarmTwinStore(FarmTwinRepository):
    """Synchronous PostGIS implementation with an injected connection factory."""

    def __init__(self, connection_factory: Callable[[], Any]):
        self._connection_factory = connection_factory

    def upsert(self, farm: Farm) -> Farm:
        with self._connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO farms (id, farmer_id) VALUES (%s, %s)
                    ON CONFLICT (id) DO UPDATE SET farmer_id = EXCLUDED.farmer_id
                    """,
                    (farm.id, farm.farmer_id),
                )
                boundary_wkt = self._polygon_wkt(farm.parcel.boundary)
                cur.execute(
                    """
                    INSERT INTO parcels
                      (id, farm_id, area_ha, location, boundary, administrative_area)
                    VALUES (
                      %s, %s, %s,
                      ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                      CASE WHEN %s IS NULL THEN NULL ELSE ST_GeogFromText(%s) END,
                      %s::jsonb
                    )
                    ON CONFLICT (id) DO UPDATE SET
                      farm_id=EXCLUDED.farm_id, area_ha=EXCLUDED.area_ha,
                      location=EXCLUDED.location, boundary=EXCLUDED.boundary,
                      administrative_area=EXCLUDED.administrative_area
                    """,
                    (
                        farm.parcel.id, farm.id, farm.parcel.area_ha,
                        farm.parcel.location.longitude, farm.parcel.location.latitude,
                        boundary_wkt, boundary_wkt,
                        json.dumps(farm.parcel.administrative_area),
                    ),
                )
                self._upsert_crop_cycle(cur, farm.id, farm.crop_cycle)
                for party in farm.parties:
                    self._insert_party(cur, farm.id, party)
                for contract in farm.contracts:
                    self._insert_contract(cur, farm.id, contract)
            conn.commit()
        return farm

    def get(self, farm_id: str) -> Farm:
        with self._connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT f.id, f.farmer_id, p.id, p.area_ha,
                           ST_Y(p.location::geometry), ST_X(p.location::geometry),
                           ST_AsGeoJSON(p.boundary::geometry), p.administrative_area
                    FROM farms f JOIN parcels p ON p.farm_id = f.id
                    WHERE f.id = %s
                    """,
                    (farm_id,),
                )
                row = cur.fetchone()
                if row is None:
                    raise KeyError(farm_id)
                return self._farm_from_row(cur, row)

    def register_crop_cycle(self, farm_id: str, crop_cycle: CropCycle) -> None:
        self.get(farm_id)
        with self._connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM crop_cycles WHERE id=%s", (crop_cycle.id,))
                if cur.fetchone():
                    raise ValueError(f"crop cycle already registered: {crop_cycle.id}")
                self._upsert_crop_cycle(cur, farm_id, crop_cycle)
            conn.commit()

    def add_land_party(self, farm_id: str, party: LandParty) -> None:
        self.get(farm_id)
        with self._connection_factory() as conn:
            with conn.cursor() as cur:
                self._insert_party(cur, farm_id, party)
            conn.commit()

    def add_contract(self, farm_id: str, contract: ProductionContract) -> None:
        self.get(farm_id)
        with self._connection_factory() as conn:
            with conn.cursor() as cur:
                self._insert_contract(cur, farm_id, contract)
            conn.commit()

    def add_observation(self, farm_id: str, observation: Observation) -> None:
        self.get(farm_id)
        location_wkt = None
        if observation.location:
            location_wkt = (
                f"SRID=4326;POINT({observation.location.longitude} "
                f"{observation.location.latitude})"
            )
        with self._connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO observations (
                      id, farm_id, crop_cycle_id, kind, observed_at, location,
                      spatial_scope, unit, source, quality, confidence, value, provenance
                    )
                    VALUES (
                      %s,%s,%s,%s,%s,
                      CASE WHEN %s IS NULL THEN NULL ELSE ST_GeogFromText(%s) END,
                      %s,%s,%s,%s,%s,%s::jsonb,%s::jsonb
                    )
                    ON CONFLICT (id) DO UPDATE SET
                      crop_cycle_id=EXCLUDED.crop_cycle_id, kind=EXCLUDED.kind,
                      observed_at=EXCLUDED.observed_at, location=EXCLUDED.location,
                      spatial_scope=EXCLUDED.spatial_scope, unit=EXCLUDED.unit,
                      source=EXCLUDED.source, quality=EXCLUDED.quality,
                      confidence=EXCLUDED.confidence, value=EXCLUDED.value,
                      provenance=EXCLUDED.provenance
                    """,
                    (
                        observation.id, farm_id, observation.crop_cycle_id,
                        observation.kind, observation.timestamp,
                        location_wkt, location_wkt,
                        observation.spatial_scope, observation.unit, observation.source,
                        observation.quality, observation.confidence,
                        json.dumps(observation.value), json.dumps(observation.provenance),
                    ),
                )
            conn.commit()

    def snapshot(self, farm_id: str, as_of: datetime | None = None) -> FarmSnapshot:
        moment = self._utc(as_of or datetime.now(timezone.utc))
        farm = self.get(farm_id)
        with self._connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id,crop,season,sowing_date,harvest_date,irrigation_method,status
                    FROM crop_cycles
                    WHERE farm_id=%s
                      AND (sowing_date IS NULL OR sowing_date<=%s::date)
                      AND (harvest_date IS NULL OR harvest_date>=%s::date)
                    ORDER BY sowing_date DESC NULLS LAST
                    """,
                    (farm_id, moment.date(), moment.date()),
                )
                cycle_rows = cur.fetchall()
                if not cycle_rows:
                    raise ValueError(f"no active crop cycle for {farm_id} at {moment.date()}")
                active_crop = self._cycle(cycle_rows[0])

                cur.execute(
                    """
                    SELECT party_id,role,valid_from,valid_to,verification
                    FROM land_parties
                    WHERE farm_id=%s AND valid_from<=%s::date
                      AND (valid_to IS NULL OR valid_to>=%s::date)
                    """,
                    (farm_id, moment.date(), moment.date()),
                )
                parties = [
                    LandParty(**dict(zip(
                        ("party_id","role","valid_from","valid_to","verification"), row
                    )))
                    for row in cur.fetchall()
                ]

                cur.execute(
                    """
                    SELECT contract_id,owner_id,cultivator_id,valid_from,valid_to,arrangement
                    FROM production_contracts
                    WHERE farm_id=%s AND valid_from<=%s::date AND valid_to>=%s::date
                    """,
                    (farm_id, moment.date(), moment.date()),
                )
                contracts = [
                    ProductionContract(**dict(zip(
                        ("contract_id","owner_id","cultivator_id","valid_from","valid_to","arrangement"), row
                    )))
                    for row in cur.fetchall()
                ]

                cur.execute(
                    """
                    SELECT id,kind,observed_at,value,source,quality,confidence,
                           ST_Y(location::geometry),ST_X(location::geometry),
                           crop_cycle_id,unit,spatial_scope,provenance
                    FROM observations
                    WHERE farm_id=%s AND observed_at<=%s
                      AND (crop_cycle_id IS NULL OR crop_cycle_id=%s)
                    ORDER BY observed_at DESC
                    """,
                    (farm_id, moment, active_crop.id),
                )
                observations = [self._observation(row) for row in cur.fetchall()]

        return FarmSnapshot(
            farm_id=farm_id, as_of=moment, active_crop=active_crop,
            parcel_location=farm.parcel.location,
            parcel_boundary=list(farm.parcel.boundary),
            active_parties=parties, active_contracts=contracts,
            recent_observations=observations,
        )

    @staticmethod
    def _polygon_wkt(points: list[GeoPoint]) -> str | None:
        if len(points) < 3:
            return None
        closed = points + ([] if points[0] == points[-1] else [points[0]])
        coords = ", ".join(f"{p.longitude} {p.latitude}" for p in closed)
        return f"SRID=4326;POLYGON(({coords}))"

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _upsert_crop_cycle(cur: Any, farm_id: str, cycle: CropCycle) -> None:
        cur.execute(
            """
            INSERT INTO crop_cycles
              (id,farm_id,crop,season,sowing_date,harvest_date,irrigation_method,status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (id) DO UPDATE SET
              farm_id=EXCLUDED.farm_id,crop=EXCLUDED.crop,season=EXCLUDED.season,
              sowing_date=EXCLUDED.sowing_date,harvest_date=EXCLUDED.harvest_date,
              irrigation_method=EXCLUDED.irrigation_method,status=EXCLUDED.status
            """,
            (cycle.id,farm_id,cycle.crop,cycle.season,cycle.sowing_date,
             cycle.harvest_date,cycle.irrigation_method,cycle.status),
        )

    @staticmethod
    def _insert_party(cur: Any, farm_id: str, party: LandParty) -> None:
        cur.execute(
            """
            INSERT INTO land_parties
              (farm_id,party_id,role,valid_from,valid_to,verification)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT DO NOTHING
            """,
            (farm_id,party.party_id,party.role,party.valid_from,party.valid_to,party.verification),
        )

    @staticmethod
    def _insert_contract(cur: Any, farm_id: str, contract: ProductionContract) -> None:
        cur.execute(
            """
            INSERT INTO production_contracts
              (contract_id,farm_id,owner_id,cultivator_id,valid_from,valid_to,arrangement)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (contract_id) DO UPDATE SET
              farm_id=EXCLUDED.farm_id,owner_id=EXCLUDED.owner_id,
              cultivator_id=EXCLUDED.cultivator_id,valid_from=EXCLUDED.valid_from,
              valid_to=EXCLUDED.valid_to,arrangement=EXCLUDED.arrangement
            """,
            (contract.contract_id,farm_id,contract.owner_id,contract.cultivator_id,
             contract.valid_from,contract.valid_to,contract.arrangement),
        )

    @staticmethod
    def _cycle(row: tuple[Any, ...]) -> CropCycle:
        return CropCycle(**dict(zip(
            ("id","crop","season","sowing_date","harvest_date","irrigation_method","status"), row
        )))

    @staticmethod
    def _observation(row: tuple[Any, ...]) -> Observation:
        keys=("id","kind","timestamp","value","source","quality","confidence",
              "latitude","longitude","crop_cycle_id","unit","spatial_scope","provenance")
        data=dict(zip(keys,row))
        lat,lon=data.pop("latitude"),data.pop("longitude")
        data["location"]=GeoPoint(latitude=lat,longitude=lon) if lat is not None else None
        return Observation(**data)

    @staticmethod
    def _farm_from_row(cur: Any, row: tuple[Any, ...]) -> Farm:
        boundary=[]
        if row[6]:
            coords=json.loads(row[6])["coordinates"][0]
            boundary=[GeoPoint(latitude=lat,longitude=lon) for lon,lat in coords[:-1]]
        cur.execute(
            """
            SELECT id,crop,season,sowing_date,harvest_date,irrigation_method,status
            FROM crop_cycles WHERE farm_id=%s
            ORDER BY sowing_date DESC NULLS LAST LIMIT 1
            """,
            (row[0],),
        )
        cycle_row=cur.fetchone()
        if cycle_row is None:
            raise ValueError(f"farm has no crop cycle: {row[0]}")
        return Farm(
            id=row[0],farmer_id=row[1],
            parcel=Parcel(
                id=row[2],area_ha=row[3],
                location=GeoPoint(latitude=row[4],longitude=row[5]),
                boundary=boundary,administrative_area=row[7] or {},
            ),
            crop_cycle=PostGISFarmTwinStore._cycle(cycle_row),
        )
