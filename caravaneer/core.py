"""
Core types, enums, and data structures for Caravaneer to the Stars.

Hex-grid coordinate system using axial coordinates (q, r).
All game entities and their fundamental properties are defined here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, NamedTuple


# ── Hex Grid ──────────────────────────────────────────────────────────────────

class HexDir(Enum):
    """The six directions on a flat-top hex grid."""
    NE = 0
    E = 1
    SE = 2
    SW = 3
    W = 4
    NW = 5


# Direction vectors for flat-top hexes (q, r axial)
_DIR_VECTORS = {
    HexDir.NE: (+1, -1),
    HexDir.E:  (+1,  0),
    HexDir.SE: ( 0, +1),
    HexDir.SW: (-1, +1),
    HexDir.W:  (-1,  0),
    HexDir.NW: ( 0, -1),
}


class Position(NamedTuple):
    """A hex position in axial coordinates (q, r)."""
    q: int
    r: int

    def neighbor(self, direction: HexDir) -> Position:
        dq, dr = _DIR_VECTORS[direction]
        return Position(self.q + dq, self.r + dr)

    def neighbors(self) -> list[Position]:
        return [self.neighbor(d) for d in HexDir]

    def distance_to(self, other: Position) -> int:
        """Hex distance (cube coordinate formula)."""
        dq = self.q - other.q
        dr = self.r - other.r
        ds = (-self.q - self.r) - (-other.q - other.r)
        return max(abs(dq), abs(dr), abs(ds))

    def ring(self, radius: int) -> list[Position]:
        """All positions exactly `radius` hexes away."""
        if radius <= 0:
            return [self]
        results = []
        pos = Position(self.q + radius, self.r - radius)
        for dq, dr in [(0, 1), (-1, 1), (-1, 0), (0, -1), (1, -1), (1, 0)]:
            for _ in range(radius):
                results.append(pos)
                pos = Position(pos.q + dq, pos.r + dr)
        return results

    def within(self, radius: int) -> list[Position]:
        """All positions within `radius` hexes (inclusive)."""
        results = []
        for r in range(radius + 1):
            results.extend(self.ring(r))
        return results

    def line_to(self, other: Position) -> list[Position]:
        """Bresenham-style hex line from self to other."""
        dist = self.distance_to(other)
        if dist == 0:
            return [self]
        results = []
        for i in range(dist + 1):
            t = i / dist if dist > 0 else 0
            # Linear interpolation in cube coords, then round
            s1 = -self.q - self.r
            s2 = -other.q - other.r
            qf = self.q + (other.q - self.q) * t
            rf = self.r + (other.r - self.r) * t
            sf = s1 + (s2 - s1) * t
            q, r, s = round(qf), round(rf), round(sf)
            # Fix rounding errors
            dq, dr, ds = abs(q - qf), abs(r - rf), abs(s - sf)
            if dq > dr and dq > ds:
                q = -r - s
            elif dr > ds:
                r = -q - s
            results.append(Position(q, r))
        return results


# ── Terrain & System Types ────────────────────────────────────────────────────

class TerrainType(Enum):
    EMPTY = auto()          # Uncharted void
    STAR_SYSTEM = auto()    # Habitable / trade-capable system
    NEBULA = auto()         # Slows travel, hides ships
    ASTEROID_FIELD = auto() # Hazard, possible mining
    BLACK_HOLE = auto()     # Impassable, dangerous near
    WORMHOLE = auto()       # Instant transit to paired wormhole
    SPACE_STATION = auto()  # Neutral trading post
    PIRATE_LAIR = auto()    # Hostile base
    DERELICT = auto()       # Salvage opportunity
    ANOMALY = auto()        # Strange phenomenon, random effect


class SystemClass(Enum):
    """Economic / population class of a star system."""
    CAPITAL = auto()        # Major hub, all goods, high prices
    COLONY = auto()         # Moderate population, diverse goods
    OUTPOST = auto()        # Small settlement, limited goods
    MINING = auto()         # Raw materials, cheap ore
    AGRICULTURAL = auto()   # Food production
    INDUSTRIAL = auto()     # Manufactured goods
    RESEARCH = auto()       # Tech goods, rare artifacts
    RUINS = auto()          # Ancient alien ruins, unique finds


class Faction(Enum):
    """Major factions in the galaxy."""
    TERRAN_ALLIANCE = auto()
    CENTAURI_COLLECTIVE = auto()
    KRAX_EMPIRE = auto()
    FREE_TRADERS_GUILD = auto()
    VOID_SYNDICATE = auto()
    ANCIENTS = auto()       # Long-gone precursor race


class DiscoveryType(Enum):
    """What you might find when exploring an uncharted system."""
    NOTHING = auto()
    TRADE_GOODS = auto()
    ANCIENT_TECH = auto()
    FUEL_CACHE = auto()
    DISTRESS_SIGNAL = auto()
    PIRATE_AMBUSH = auto()
    ALIEN_ARTIFACT = auto()
    WORMHOLE_PAIR = auto()
    HIDDEN_STATION = auto()
    STAR_CHART = auto()     # Reveals nearby systems


# ── Trade Goods ───────────────────────────────────────────────────────────────

class GoodCategory(Enum):
    RAW = auto()
    FOOD = auto()
    MANUFACTURED = auto()
    LUXURY = auto()
    TECH = auto()
    CONTRABAND = auto()


@dataclass
class TradeGood:
    name: str
    category: GoodCategory
    base_price: int          # Galactic average price
    weight: float = 1.0      # Cargo space per unit
    illegal_in: list[Faction] = field(default_factory=list)
    description: str = ""


# Standard trade goods catalog
TRADE_GOODS: dict[str, TradeGood] = {
    "ore": TradeGood("Ore", GoodCategory.RAW, 10, weight=2.0,
                     description="Raw mineral ore for industrial processing."),
    "fuel_cells": TradeGood("Fuel Cells", GoodCategory.RAW, 15, weight=1.5,
                            description="Standard starship fuel."),
    "food": TradeGood("Food", GoodCategory.FOOD, 8, weight=1.0,
                      description="Basic foodstuffs and rations."),
    "luxury_food": TradeGood("Luxury Food", GoodCategory.LUXURY, 25, weight=0.5,
                             description="Exotic delicacies from across the galaxy."),
    "textiles": TradeGood("Textiles", GoodCategory.MANUFACTURED, 12, weight=1.0,
                          description="Cloth and fabric goods."),
    "machinery": TradeGood("Machinery", GoodCategory.MANUFACTURED, 30, weight=3.0,
                           description="Industrial machinery and parts."),
    "electronics": TradeGood("Electronics", GoodCategory.TECH, 40, weight=0.5,
                             description="Consumer and industrial electronics."),
    "medicine": TradeGood("Medicine", GoodCategory.TECH, 50, weight=0.2,
                          description="Pharmaceuticals and medical supplies."),
    "weapons": TradeGood("Weapons", GoodCategory.MANUFACTURED, 60, weight=2.0,
                         description="Small arms and ship weaponry.",
                         illegal_in=[Faction.TERRAN_ALLIANCE]),
    "artifacts": TradeGood("Artifacts", GoodCategory.LUXURY, 100, weight=0.1,
                           description="Ancient alien artifacts of unknown purpose."),
    "spice": TradeGood("Spice", GoodCategory.LUXURY, 35, weight=0.3,
                       description="Psychoactive spice from the outer rim."),
    "data_crystals": TradeGood("Data Crystals", GoodCategory.TECH, 45, weight=0.1,
                               description="Encoded information of unknown value."),
    "slaves": TradeGood("Slaves", GoodCategory.CONTRABAND, 80, weight=1.0,
                        description="Sentient beings, illegal everywhere.",
                        illegal_in=list(Faction)),
    "narcotics": TradeGood("Narcotics", GoodCategory.CONTRABAND, 55, weight=0.2,
                           description="Illegal substances.",
                           illegal_in=list(Faction)),
}


# ── Ship Classes ──────────────────────────────────────────────────────────────

class ShipClass(Enum):
    SCOUT = auto()
    FREIGHTER = auto()
    CORVETTE = auto()
    CRUISER = auto()
    BATTLESHIP = auto()
    EXPLORER = auto()


@dataclass
class ShipTemplate:
    name: str
    ship_class: ShipClass
    cargo_capacity: int
    fuel_capacity: int
    jump_range: int          # Max hexes per jump
    hull_points: int
    shield_points: int
    speed: int               # Jumps per turn
    base_cost: int
    description: str = ""


SHIP_TEMPLATES: dict[str, ShipTemplate] = {
    "scout": ShipTemplate(
        "Scout", ShipClass.SCOUT,
        cargo_capacity=20, fuel_capacity=60, jump_range=4,
        hull_points=15, shield_points=5, speed=3, base_cost=5000,
        description="Fast and nimble, but light on cargo."
    ),
    "freighter": ShipTemplate(
        "Freighter", ShipClass.FREIGHTER,
        cargo_capacity=80, fuel_capacity=100, jump_range=3,
        hull_points=30, shield_points=10, speed=2, base_cost=15000,
        description="The workhorse of galactic trade."
    ),
    "corvette": ShipTemplate(
        "Corvette", ShipClass.CORVETTE,
        cargo_capacity=30, fuel_capacity=70, jump_range=4,
        hull_points=40, shield_points=20, speed=3, base_cost=25000,
        description="Armed escort with decent cargo space."
    ),
    "cruiser": ShipTemplate(
        "Cruiser", ShipClass.CRUISER,
        cargo_capacity=40, fuel_capacity=90, jump_range=5,
        hull_points=60, shield_points=30, speed=2, base_cost=50000,
        description="Heavy combat vessel with moderate cargo."
    ),
    "battleship": ShipTemplate(
        "Battleship", ShipClass.BATTLESHIP,
        cargo_capacity=20, fuel_capacity=80, jump_range=4,
        hull_points=100, shield_points=50, speed=1, base_cost=80000,
        description="Flying fortress. Not much room for cargo."
    ),
    "explorer": ShipTemplate(
        "Explorer", ShipClass.EXPLORER,
        cargo_capacity=50, fuel_capacity=120, jump_range=6,
        hull_points=25, shield_points=15, speed=3, base_cost=35000,
        description="Built for deep-space exploration and discovery."
    ),
}


# ── Ship Modules ──────────────────────────────────────────────────────────────

class ModuleSlot(Enum):
    ENGINE = auto()
    SHIELD = auto()
    WEAPON = auto()
    SENSOR = auto()
    CARGO = auto()
    UTILITY = auto()


@dataclass
class ModuleTemplate:
    name: str
    slot: ModuleSlot
    cost: int
    effect_value: float = 0.0
    description: str = ""


MODULE_TEMPLATES: dict[str, ModuleTemplate] = {
    "engine_mk1": ModuleTemplate("Engine Mk I", ModuleSlot.ENGINE, 2000, 1.0,
                                 "+1 jump range"),
    "engine_mk2": ModuleTemplate("Engine Mk II", ModuleSlot.ENGINE, 5000, 2.0,
                                 "+2 jump range"),
    "shield_mk1": ModuleTemplate("Shield Mk I", ModuleSlot.SHIELD, 1500, 10.0,
                                 "+10 shields"),
    "shield_mk2": ModuleTemplate("Shield Mk II", ModuleSlot.SHIELD, 4000, 25.0,
                                 "+25 shields"),
    "laser_cannon": ModuleTemplate("Laser Cannon", ModuleSlot.WEAPON, 3000, 15.0,
                                   "+15 attack power"),
    "plasma_turret": ModuleTemplate("Plasma Turret", ModuleSlot.WEAPON, 7000, 30.0,
                                    "+30 attack power"),
    "sensor_mk1": ModuleTemplate("Sensor Mk I", ModuleSlot.SENSOR, 1000, 3.0,
                                 "Reveal 3 hex radius"),
    "sensor_mk2": ModuleTemplate("Sensor Mk II", ModuleSlot.SENSOR, 3000, 6.0,
                                 "Reveal 6 hex radius"),
    "cargo_bay": ModuleTemplate("Cargo Bay", ModuleSlot.CARGO, 2000, 30.0,
                                "+30 cargo capacity"),
    "fuel_tank": ModuleTemplate("Fuel Tank", ModuleSlot.UTILITY, 1000, 40.0,
                                "+40 fuel capacity"),
    "wormhole_stabilizer": ModuleTemplate("Wormhole Stabilizer", ModuleSlot.UTILITY, 8000, 0.0,
                                          "Safe wormhole traversal"),
    "nebula_nav": ModuleTemplate("Nebula Navigator", ModuleSlot.UTILITY, 4000, 0.0,
                                 "Negates nebula drift"),
    "repair_drones": ModuleTemplate("Repair Drones", ModuleSlot.UTILITY, 2500, 5.0,
                                    "Repair 5 hull/turn"),
    "cloaking_device": ModuleTemplate("Cloaking Device", ModuleSlot.UTILITY, 12000, 0.0,
                                      "Hide from other players' sensors"),
}


# ── Events ────────────────────────────────────────────────────────────────────

class EventType(Enum):
    PIRATE_ATTACK = auto()
    MARKET_CRASH = auto()
    MARKET_BOOM = auto()
    SOLAR_FLARE = auto()     # Damages ships in a region
    TRADE_CONVOY = auto()    # Temporary good deals
    PLAGUE = auto()          # System quarantined
    GOLD_RUSH = auto()       # New resource discovered
    FACTION_WAR = auto()     # Two factions at war, prices spike
    ANCIENT_SIGNAL = auto()  # Leads to a discovery
    NOTHING = auto()


# ── Victory Conditions ────────────────────────────────────────────────────────

class VictoryType(Enum):
    NET_WORTH = auto()       # First to reach target net worth
    MOST_WEALTH = auto()     # Richest after N turns
    DISCOVERY = auto()       # First to discover N systems
    MONOPOLY = auto()        # Control trade in N goods
    OPEN = auto()            # No fixed end — sandbox play
