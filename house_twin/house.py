"""
house.py — the building's geometry, as data.

One building, 30 ft x 33 ft, slab on grade, two occupied floors plus a standing
attic over the second floor. Every dimension below is in feet and is meant to be
edited: this module is the single source of truth for the model, and
:func:`House.to_dict` is what the Three.js front end consumes.

The room layout is a plausible starting point, not a survey. Rooms tile each
floor exactly (no gaps, no overlaps) so the floor plates render as solid slabs,
and the totals are asserted at import time to catch a bad edit.

Coordinate system
-----------------
* ``x`` runs 0 (west) to 30 (east)
* ``z`` runs 0 (north) to 33 (south)
* ``y`` is elevation; each level's floor sits at its ``elevation`` and grows
  upward to ``elevation + height``
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

# ── Building envelope ────────────────────────────────────────────────────────

FOOTPRINT_X = 30.0
FOOTPRINT_Z = 33.0

#: Floor-to-floor. Second floor deck sits 9 ft above the slab.
FLOOR_TO_FLOOR = 9.0

#: Interior clear height per level.
MAIN_CEILING = 9.0
SECOND_CEILING = 8.5
ATTIC_CEILING = 7.0

#: Attic knee-wall height — below this an adult cannot stand.
ATTIC_KNEE_WALL = 4.0


@dataclass(frozen=True)
class Room:
    """One rectangular space on one level."""

    id: str
    name: str
    level: int
    x: float
    z: float
    width: float
    depth: float
    kind: str = "living"  # living | service | circulation | storage | utility
    #: True when the space has full standing headroom (drives the ceiling mesh).
    standing: bool = True
    notes: str = ""

    @property
    def area(self) -> float:
        return self.width * self.depth

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2, self.z + self.depth / 2)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["area"] = round(self.area, 1)
        d["center"] = [round(v, 2) for v in self.center]
        return d


@dataclass(frozen=True)
class SensorPlacement:
    """Where a sensor sits and which Aqara device feeds it.

    ``match`` is a case-insensitive substring tested against the device name
    reported by ``query.device.info``. Anything unmatched shows up in the UI as
    an unassigned sensor so it can be mapped by hand.
    """

    id: str
    name: str
    room_id: str
    x: float
    z: float
    level: int
    height_ft: float = 5.0
    match: str = ""
    kind: str = "temperature_humidity"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Level:
    """One horizontal slice of the building."""

    index: int
    name: str
    elevation: float
    height: float
    rooms: list[Room] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "name": self.name,
            "elevation": self.elevation,
            "height": self.height,
            "rooms": [r.to_dict() for r in self.rooms],
        }


# ── Main floor ───────────────────────────────────────────────────────────────
# 18x16 living, 12x10 kitchen, 12x6 dining, 8x9 entry, 10x9 bath,
# 18x8 utility, 12x17 stair hall.

_MAIN_ROOMS = [
    Room("living", "Living Room", 0, 0, 0, 18, 16, "living",
         notes="Main conditioning zone; ERV supply registers nearby."),
    Room("kitchen", "Kitchen", 0, 18, 0, 12, 10, "living"),
    Room("dining", "Dining", 0, 18, 10, 12, 6, "living"),
    Room("entry", "Entry", 0, 0, 16, 8, 9, "circulation",
         notes="Exterior door. Draft sensor reads well here."),
    Room("bath_main", "Bath", 0, 8, 16, 10, 9, "service",
         notes="Highest humidity room; useful ERV trigger."),
    Room("utility", "Utility / Laundry", 0, 0, 25, 18, 8, "utility"),
    Room("stair_main", "Stair Hall", 0, 18, 16, 12, 17, "circulation",
         notes="Vertical circulation to second floor and attic."),
]

# ── Second floor ─────────────────────────────────────────────────────────────
# 15x16 primary, 15x12 and 15x12 bedrooms, 15x8 landing, 15x9 bath, 15x9 stair.

_SECOND_ROOMS = [
    Room("bedroom_1", "Bedroom 1", 1, 0, 0, 15, 16, "living",
         notes="Primary bedroom."),
    Room("bedroom_2", "Bedroom 2", 1, 15, 0, 15, 12, "living"),
    Room("bedroom_3", "Bedroom 3", 1, 15, 12, 15, 12, "living"),
    Room("landing", "Landing", 1, 0, 16, 15, 8, "circulation"),
    Room("bath_second", "Bath", 1, 0, 24, 15, 9, "service"),
    Room("stair_second", "Stair Hall", 1, 15, 24, 15, 9, "circulation"),
]

# ── Standing attic ───────────────────────────────────────────────────────────
# 24x25 of headroom ringed by knee-wall storage, so the level totals the full
# 30x33 while only the inner rectangle is habitable.

_ATTIC_ROOMS = [
    Room("attic", "Standing Attic", 2, 3, 4, 24, 25, "living",
         notes="Finished attic. Hottest zone in summer — watch the gradient."),
    Room("attic_store_n", "Storage North", 2, 0, 0, 30, 4, "storage",
         standing=False, notes="Knee wall."),
    Room("attic_store_s", "Storage South", 2, 0, 29, 30, 4, "storage",
         standing=False, notes="Knee wall."),
    Room("attic_store_w", "Storage West", 2, 0, 4, 3, 25, "storage",
         standing=False, notes="Knee wall."),
    Room("attic_store_e", "Storage East", 2, 27, 4, 3, 25, "storage",
         standing=False, notes="Knee wall."),
]

# ── Sensor placements ────────────────────────────────────────────────────────
# Positions are hand-placed near where a sensor would actually live: away from
# exterior walls, doors and supply registers, roughly 5 ft up.

_SENSORS = [
    SensorPlacement("aq_living", "Living Room", "living", 8.0, 9.0, 0, 5.0, "living"),
    SensorPlacement("aq_kitchen", "Kitchen", "kitchen", 24.0, 5.0, 0, 5.0, "kitchen"),
    SensorPlacement("aq_dining", "Dining", "dining", 24.0, 13.0, 0, 5.0, "dining"),
    SensorPlacement("aq_bath_main", "Bath (Main)", "bath_main", 13.0, 20.0, 0, 5.0, "bath"),
    SensorPlacement("aq_bedroom_1", "Bedroom 1", "bedroom_1", 7.0, 8.0, 1, 5.0, "bedroom"),
    SensorPlacement("aq_bedroom_2", "Bedroom 2", "bedroom_2", 22.0, 6.0, 1, 5.0, "bedroom"),
    SensorPlacement("aq_bedroom_3", "Bedroom 3", "bedroom_3", 22.0, 18.0, 1, 5.0, "bedroom"),
    SensorPlacement("aq_landing", "Landing", "landing", 7.0, 20.0, 1, 5.0, "landing"),
    SensorPlacement("aq_attic", "Standing Attic", "attic", 15.0, 16.0, 2, 5.0, "attic"),
]

LEVELS = [
    Level(0, "Main Floor", 0.0, MAIN_CEILING, _MAIN_ROOMS),
    Level(1, "Second Floor", FLOOR_TO_FLOOR, SECOND_CEILING, _SECOND_ROOMS),
    Level(2, "Attic", 2 * FLOOR_TO_FLOOR, ATTIC_CEILING, _ATTIC_ROOMS),
]


class House:
    """Aggregate view of the building, with the invariants enforced."""

    def __init__(
        self,
        levels: list[Level] | None = None,
        sensors: list[SensorPlacement] | None = None,
    ):
        self.levels = levels if levels is not None else LEVELS
        self.sensors = sensors if sensors is not None else _SENSORS
        self._validate()

    # ── lookups ────────────────────────────────────────────────────────────

    @property
    def rooms(self) -> list[Room]:
        return [room for level in self.levels for room in level.rooms]

    def room(self, room_id: str) -> Room | None:
        return next((r for r in self.rooms if r.id == room_id), None)

    def level(self, index: int) -> Level | None:
        return next((lv for lv in self.levels if lv.index == index), None)

    # ── validation ─────────────────────────────────────────────────────────

    def _validate(self) -> None:
        """Guard the invariants a careless edit could break."""
        for level in self.levels:
            covered = 0.0
            for room in level.rooms:
                if room.x < 0 or room.z < 0:
                    raise ValueError(f"{room.id}: negative origin")
                if room.x + room.width > FOOTPRINT_X + 1e-6:
                    raise ValueError(f"{room.id}: overflows footprint in x")
                if room.z + room.depth > FOOTPRINT_Z + 1e-6:
                    raise ValueError(f"{room.id}: overflows footprint in z")
                covered += room.area
            expected = FOOTPRINT_X * FOOTPRINT_Z
            if abs(covered - expected) > 0.5:
                raise ValueError(
                    f"level {level.index} ({level.name}): rooms cover "
                    f"{covered:.0f} sq ft, expected {expected:.0f}"
                )

        known = {r.id for r in self.rooms}
        for sensor in self.sensors:
            if sensor.room_id not in known:
                raise ValueError(f"sensor {sensor.id}: unknown room {sensor.room_id!r}")
            if not 0 <= sensor.x <= FOOTPRINT_X or not 0 <= sensor.z <= FOOTPRINT_Z:
                raise ValueError(f"sensor {sensor.id}: position outside footprint")

    # ── totals ─────────────────────────────────────────────────────────────

    @property
    def total_footprint(self) -> float:
        return FOOTPRINT_X * FOOTPRINT_Z

    @property
    def conditioned_area(self) -> float:
        """Floor area with standing headroom — what the HVAC actually serves."""
        return sum(r.area for r in self.rooms if r.standing)

    def summary(self) -> dict:
        return {
            "footprint_ft": [FOOTPRINT_X, FOOTPRINT_Z],
            "levels": len(self.levels),
            "footprint_sqft": round(self.total_footprint),
            "conditioned_sqft": round(self.conditioned_area),
            "rooms": len(self.rooms),
            "sensors": len(self.sensors),
            "ridge_height_ft": round(2 * FLOOR_TO_FLOOR + ATTIC_CEILING, 1),
        }

    # ── serialisation ──────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Everything the front end needs to build the scene."""
        return {
            "meta": {
                **self.summary(),
                "floor_to_floor": FLOOR_TO_FLOOR,
                "units": "ft",
                "knee_wall": ATTIC_KNEE_WALL,
            },
            "levels": [level.to_dict() for level in self.levels],
            "sensors": [s.to_dict() for s in self.sensors],
        }


if __name__ == "__main__":
    import json

    house = House()
    print(json.dumps(house.summary(), indent=2))
    for level in house.levels:
        print(f"\n{level.name} (y={level.elevation})")
        for room in level.rooms:
            flag = "  " if room.standing else "knee"
            print(f"  [{flag}] {room.name:<20} {room.width:>4.0f} x {room.depth:>4.0f}"
                  f"  = {room.area:>5.0f} sq ft")
